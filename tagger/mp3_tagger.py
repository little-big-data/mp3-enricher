from __future__ import annotations

import csv
import io
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from tagger.enricher.llm.base import LLMClient

import click
import structlog

from tagger.config import Settings
from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.manual_review_repo import ManualReviewRepository
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.tag_issues_repo import TagIssuesRepository
from tagger.db.track_repo import TrackRepository
from tagger.enricher.discogs.art_downloader import ArtDownloader
from tagger.enricher.discogs.client import DiscogsClient
from tagger.enricher.heuristic_enricher import HeuristicEnricher
from tagger.enricher.musicbrainz.client import MusicBrainzClient
from tagger.enricher.pipeline import EnrichmentPipeline
from tagger.enricher.prefill import prefill_pass1, prefill_pass2
from tagger.enricher.web.scraper import WebScraper
from tagger.integrity.scanner import IntegrityScanner
from tagger.manual.csv_handler import CsvHandler
from tagger.scanner.folder_parser import parse_folder_names
from tagger.scanner.id3_reader import read_id3_tags
from tagger.scanner.walker import find_album_dirs, find_mp3_files
from tagger.utils.rate_limiter import TokenBucket
from tagger.writer.id3_writer import ID3Writer
from tagger.writer.track_number_fixer import fix_track_numbers

# Discogs allows ~60 authenticated requests/minute.  We pace to 55/min
# (~0.92 req/s) to stay comfortably under the limit.  The retry decorator
# in DiscogsClient handles any 429s that still slip through.
_DISCOGS_RATE_LIMITER = TokenBucket(rate=0.92, capacity=5)

# MusicBrainz asks all clients to stay at or below 1 req/s.  We use a
# capacity of 1 so we never fire a burst of requests on startup.
_MB_RATE_LIMITER = TokenBucket(rate=1.0, capacity=1)

log = structlog.get_logger(__name__)


def _resolve_album_dirs(folder_path: Path, starts_with: str | None) -> list[Path]:
    """Return album directories within *folder_path*, optionally filtered by prefix.

    Handles three layouts:
    - Single album dir: *folder_path* itself contains MP3 files directly.
    - Artist dir: *folder_path* contains album sub-directories.
    - Library root: *folder_path* contains artist dirs which contain album dirs.

    When *starts_with* is given, only top-level sub-directories whose names begin
    with that prefix (case-insensitive) are considered.
    """
    # Single-album case: folder_path itself contains MP3s (only applies without prefix filter).
    if starts_with is None:
        direct_mp3s = [
            f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() == ".mp3"
        ]
        if direct_mp3s:
            return [folder_path]

    top_dirs = sorted(d for d in folder_path.iterdir() if d.is_dir() and not d.name.startswith("."))
    if starts_with:
        top_dirs = [d for d in top_dirs if d.name.lower().startswith(starts_with.lower())]

    album_dirs: list[Path] = []
    for top in top_dirs:
        direct = [f for f in top.iterdir() if f.is_file() and f.suffix.lower() == ".mp3"]
        if direct:
            album_dirs.append(top)
        else:
            album_dirs.extend(find_album_dirs(top))

    return album_dirs


_DB_PATH_OPTION = click.option(
    "--db-path", type=Path, default=Path("tester.db"), help="Path to the SQLite database"
)
_TOKEN_OPTION = click.option("--token", help="Discogs Personal Access Token (overrides config)")


@click.group()
def cli() -> None:
    """mp3-enricher test runner."""
    # Ensure stdout/stderr replace non-ASCII characters on Windows cp1252 terminals.
    _non_utf8 = {"utf-8", "utf-8-sig"}
    if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.encoding.lower() not in _non_utf8:
        sys.stdout.reconfigure(errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper) and sys.stderr.encoding.lower() not in _non_utf8:
        sys.stderr.reconfigure(errors="replace")


@cli.command()
@click.argument("folder_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@_DB_PATH_OPTION
@_TOKEN_OPTION
@click.option(
    "--starts-with",
    default=None,
    metavar="PREFIX",
    help=(
        "Only process top-level subfolders whose name begins with PREFIX (case-insensitive). "
        "Useful for batching a large library: run once with 'A', once with 'B', etc."
    ),
)
@click.option(
    "--skip-enriched",
    is_flag=True,
    help=(
        "Skip albums that already have enrichment_status='found' or 'written' in the DB. "
        "Useful for re-running enrichment over a full library to pick up only unenriched albums "
        "(e.g. bands whose names start with digits that were missed by earlier --starts-with runs)."
    ),
)
def enrich(
    folder_path: Path,
    db_path: Path,
    token: str | None,
    starts_with: str | None,
    skip_enriched: bool,
) -> None:
    """Scan a folder, enrich metadata via Discogs/Wikipedia/MusicBrainz, and save to DB.

    FOLDER_PATH may be a single album directory (containing MP3 files directly),
    an artist directory (containing album subfolders), or a library root.
    Use --starts-with to batch-process a large library one letter at a time.

    Run `write` afterwards to apply the enriched tags to the MP3 files on disk.
    """
    settings = Settings()
    discogs_token = token or settings.discogs_token
    if not discogs_token:
        click.echo("Error: Discogs token not found in config or provided as option.")
        return

    click.echo(f"[*] Initializing database at {db_path}...")
    conn = get_db_connection(db_path)
    run_migrations(conn)

    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)
    manual_repo = ManualReviewRepository(conn)

    client = DiscogsClient(token=discogs_token, rate_limiter=_DISCOGS_RATE_LIMITER)
    pipeline = EnrichmentPipeline(
        album_repo=album_repo,
        track_repo=track_repo,
        discogs_client=client,
        scraper=WebScraper(),
        enricher=HeuristicEnricher(),
        mb_client=MusicBrainzClient(rate_limiter=_MB_RATE_LIMITER),
        manual_review_repo=manual_repo,
    )
    art_cache = settings.working_dir / "art"
    downloader = ArtDownloader(cache_dir=art_cache)

    # Determine which album directories to process.
    album_dirs = _resolve_album_dirs(folder_path, starts_with)
    if not album_dirs:
        if starts_with:
            click.echo(
                f"[-] No subfolders starting with '{starts_with}' found in {folder_path.name}."
            )
        else:
            click.echo("[-] No MP3 files or album subfolders found.")
        return

    multi = len(album_dirs) > 1
    if multi:
        click.echo(f"[+] Found {len(album_dirs)} album(s) to process under {folder_path.name}.")

    for album_dir in album_dirs:
        if multi:
            click.echo(f"\n{'-' * 60}")
            click.echo(f"[*] Album: {album_dir.name}")

        # --- Skip already-enriched albums when requested ---
        if skip_enriched:
            existing = album_repo.get_by_folder_path(str(album_dir.absolute()))
            if existing and existing.enrichment_status in ("found", "written"):
                log.info("enrich.skip_enriched", folder=str(album_dir))
                if multi:
                    click.echo(f"  [skip] Already enriched: {album_dir.name}")
                continue

        # --- Scan ---
        mp3_files = find_mp3_files(album_dir)
        if not mp3_files:
            click.echo(f"  [-] No MP3 files found in {album_dir.name}, skipping.")
            continue

        click.echo(f"[+] Found {len(mp3_files)} MP3 file(s).")

        guesses = parse_folder_names(album_dir)
        artist_guess = guesses.get("artist_guess")
        album_guess = guesses.get("album_guess")
        click.echo(f"[+] Guessed Artist: {artist_guess}")
        click.echo(f"[+] Guessed Album:  {album_guess}")

        album_record = AlbumRecord(
            folder_path=str(album_dir.absolute()),
            artist_guess=artist_guess,
            album_guess=album_guess,
        )
        with conn:
            album_repo.upsert(album_record)
            saved_album = album_repo.get_by_folder_path(str(album_dir.absolute()))
            assert saved_album is not None
            album_id = saved_album.id
            assert album_id is not None

            current_paths: set[str] = set()
            for mp3 in mp3_files:
                abs_path = str(mp3.absolute())
                current_paths.add(abs_path)
                tags = read_id3_tags(mp3)
                track_repo.upsert(
                    TrackRecord(
                        album_id=album_id,
                        file_path=abs_path,
                        filename=mp3.name,
                        track_number=tags.get("track_number"),
                        disc_number=tags.get("disc_number"),
                        existing_title=tags.get("title"),
                        existing_artist=tags.get("artist"),
                    )
                )
            track_repo.delete_stale(album_id, current_paths)

        click.echo("[+] Tracks inserted into database.")

        # --- Enrich ---
        click.echo("[*] Starting enrichment (Discogs -> Wikipedia -> MusicBrainz -> heuristics)...")
        pipeline.enrich_album(saved_album)

        updated_album = album_repo.get_by_folder_path(str(album_dir.absolute()))
        assert updated_album is not None

        if updated_album.enrichment_status == "not_found":
            click.echo("[-] No suitable Discogs release found  -  added to manual review.")
            click.echo("    Run export-manual / process-manual to handle it manually.")
            continue

        enriched_tracks = [
            t for t in track_repo.get_by_album(album_id) if t.enrichment_status == "found"
        ]
        click.echo(f"[+] Enriched {len(enriched_tracks)}/{len(mp3_files)} tracks.")

        if enriched_tracks:
            sample = enriched_tracks[0]
            click.echo(f"[+] Album Artist: {sample.album_artist}")
            click.echo(f"[+] Album Title:  {sample.album_title}  ({sample.year})")
            click.echo(f"[+] Genre:        {sample.genre}")
            click.echo(f"[+] Grouping:     {sample.grouping}")

        # --- Art Download ---
        if updated_album.discogs_release_id:
            try:
                release = client.get_release(updated_album.discogs_release_id)
                if release.images:
                    primary_art = next(
                        (img for img in release.images if img.type == "primary"), release.images[0]
                    )
                    click.echo(f"[*] Downloading album art from: {primary_art.resource_url}")
                    art_path = downloader.download(
                        str(primary_art.resource_url), album_id=updated_album.discogs_release_id
                    )
                    if art_path:
                        click.echo(f"[+] Album art saved to: {art_path}")
                        with conn:
                            for track in enriched_tracks:
                                track.art_path = str(art_path)
                                track_repo.upsert(track)
                    else:
                        click.echo("[-] Failed to download album art.")
                else:
                    click.echo("[!] No images found for this release.")
            except Exception as exc:
                click.echo(f"[!] Art download failed: {exc}")

    click.echo("\n[SUCCESS] Enrichment complete. Run `write` to apply tags to MP3 files.")


@cli.command()
@click.argument("folder_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@_DB_PATH_OPTION
@_TOKEN_OPTION
@click.option(
    "--starts-with",
    default=None,
    metavar="PREFIX",
    help="Only process top-level subfolders whose name begins with PREFIX (case-insensitive).",
)
def fix(folder_path: Path, db_path: Path, token: str | None, starts_with: str | None) -> None:
    """Re-enrich already-found albums to correct metadata and reset written_status.

    Useful after a bug fix: re-runs enrichment for each album that already has a
    Discogs release ID, updates the DB with corrected metadata, and resets
    written_status to 'pending' so `write` will re-apply the corrected tags.
    """
    settings = Settings()
    discogs_token = token or settings.discogs_token
    if not discogs_token:
        click.echo("Error: Discogs token not found in config or provided as option.")
        return

    conn = get_db_connection(db_path)
    run_migrations(conn)

    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)

    client = DiscogsClient(token=discogs_token, rate_limiter=_DISCOGS_RATE_LIMITER)
    pipeline = EnrichmentPipeline(
        album_repo=album_repo,
        track_repo=track_repo,
        discogs_client=client,
        scraper=WebScraper(),
        enricher=HeuristicEnricher(),
        mb_client=MusicBrainzClient(rate_limiter=_MB_RATE_LIMITER),
    )

    # Determine which album directories to process.
    album_dirs = _resolve_album_dirs(folder_path, starts_with)
    if not album_dirs:
        if starts_with:
            click.echo(
                f"[-] No subfolders starting with '{starts_with}' found in {folder_path.name}."
            )
        else:
            click.echo("[-] No MP3 files or album subfolders found.")
        return

    fixed = 0
    skipped = 0
    for album_dir in album_dirs:
        album_record = album_repo.get_by_folder_path(str(album_dir.absolute()))
        if album_record is None:
            click.echo(f"  [-] {album_dir.name}: not in DB, skipping.")
            skipped += 1
            continue
        if album_record.enrichment_status != "found" or not album_record.discogs_release_id:
            click.echo(f"  [-] {album_dir.name}: not enriched, skipping.")
            skipped += 1
            continue

        click.echo(f"  [*] Fixing: {album_dir.name}")
        try:
            pipeline.enrich_album_from_release_id(album_record, album_record.discogs_release_id)
            assert album_record.id is not None
            with conn:
                track_repo.reset_written_status_for_album(album_record.id)
            fixed += 1
        except Exception as exc:
            click.echo(f"  [-] {album_dir.name}: error  -  {exc}")
            log.error("fix.error", folder=str(album_dir), error=str(exc))

    click.echo(f"\n[SUCCESS] Fixed {fixed} album(s), skipped {skipped}.")
    click.echo("Run `write` to apply corrected tags to MP3 files.")


@cli.command("export-manual")
@_DB_PATH_OPTION
@click.option(
    "--csv-path",
    type=Path,
    default=Path("manual_review.csv"),
    help="Output CSV file path",
)
def export_manual(db_path: Path, csv_path: Path) -> None:
    """Export pending manual-review albums to a CSV for human review.

    Open the CSV, fill in the ``user_discogs_url`` column for any album
    you can identify, then run ``process-manual`` to continue enrichment.
    """
    conn = get_db_connection(db_path)
    run_migrations(conn)
    manual_repo = ManualReviewRepository(conn)

    pending = manual_repo.get_pending()
    if not pending:
        click.echo("[!] No pending manual reviews found.")
    else:
        click.echo(f"[+] Exporting {len(pending)} pending review(s) to {csv_path}...")

    CsvHandler().export_pending(pending, csv_path)
    click.echo(f"[SUCCESS] Written to {csv_path}")


@cli.command("process-manual")
@_DB_PATH_OPTION
@_TOKEN_OPTION
@click.option(
    "--csv-path",
    type=Path,
    default=Path("manual_review.csv"),
    help="CSV file with user-supplied Discogs URLs",
)
def process_manual(db_path: Path, token: str | None, csv_path: Path) -> None:
    """Process user-corrected manual_review.csv and enrich matched albums.

    Reads rows where ``user_discogs_url`` has been filled in, fetches the
    release from Discogs, runs heuristic enrichment, and marks each entry
    as resolved in the database.
    """
    settings = Settings()
    discogs_token = token or settings.discogs_token
    if not discogs_token:
        click.echo("Error: Discogs token not found in config or provided as option.")
        return

    if not csv_path.exists():
        click.echo(f"[-] CSV not found: {csv_path}. Run export-manual first.")
        return

    handler = CsvHandler()
    corrections = handler.import_corrections(csv_path)
    if not corrections:
        click.echo("[!] No rows with a user_discogs_url found in CSV.")
        return

    click.echo(f"[+] Processing {len(corrections)} correction(s)...")

    conn = get_db_connection(db_path)
    run_migrations(conn)
    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)
    manual_repo = ManualReviewRepository(conn)
    client = DiscogsClient(token=discogs_token, rate_limiter=_DISCOGS_RATE_LIMITER)
    scraper = WebScraper()
    enricher = HeuristicEnricher()
    mb_client = MusicBrainzClient(rate_limiter=_MB_RATE_LIMITER)

    pipeline = EnrichmentPipeline(
        album_repo=album_repo,
        track_repo=track_repo,
        discogs_client=client,
        scraper=scraper,
        enricher=enricher,
        mb_client=mb_client,
    )
    art_cache = settings.working_dir / "art"
    downloader = ArtDownloader(cache_dir=art_cache)

    for row in corrections:
        csv_album_id = int(row["album_id"])
        discogs_url = row["user_discogs_url"].strip()
        artist_guess = row.get("artist_guess", "Unknown")
        album_guess = row.get("album_guess", "Unknown")
        folder_path = row.get("folder_path", "").strip()

        release_id = handler.extract_release_id(discogs_url)
        if release_id is None:
            master_id = handler.extract_master_id(discogs_url)
            if master_id is None:
                click.echo(
                    f"  [-] csv_album_id={csv_album_id}: could not parse release/master ID"
                    f" from {discogs_url}"
                )
                continue
            # Resolve master → oldest audio release
            versions = client.get_master_releases(master_id)
            audio = [
                v
                for v in versions
                if not any(
                    f in {"VHS", "DVD", "DVD-Video", "Blu-ray", "Video", "Videocassette"}
                    for f in v.format
                )
            ]
            dated = [v for v in audio if v.year is not None]
            pool = dated or audio or versions
            if not pool:
                click.echo(
                    f"  [-] csv_album_id={csv_album_id}: no versions found for master {master_id}"
                )
                continue
            oldest = min(pool, key=lambda v: v.year if v.year is not None else 9999)
            release_id = oldest.id
            click.echo(f"  [*] master {master_id} -> oldest release {release_id} ({oldest.year})")

        # Look up album by folder_path first (robust across DB rebuilds), falling
        # back to album_id only when no folder_path is in the CSV.
        album_record = None
        if folder_path:
            album_record = album_repo.get_by_folder_path(folder_path)
            if album_record is None:
                click.echo(f"  [-] folder_path not in DB, skipping: {folder_path}")
                continue
            assert album_record.id is not None  # always set for persisted records
            album_id = album_record.id
            # Safety check: warn if the CSV album_id doesn't match the DB record
            # for this folder (indicates the CSV was from a different DB snapshot).
            if album_id != csv_album_id:
                click.echo(
                    f"  [!] album_id mismatch — CSV has {csv_album_id}, "
                    f"DB has {album_record.id} for {folder_path}. Using DB id."
                )
        else:
            album_id = csv_album_id
            album_record = album_repo.get_by_id(album_id)

        click.echo(f"  [*] album_id={album_id} ({artist_guess}  -  {album_guess})")
        try:
            if album_record is None:
                click.echo(f"  [-] album_id={album_id}: not found in DB")
                continue

            pipeline.enrich_album_from_release_id(album_record, release_id)

            # Resolve the manual review entry now that enrichment succeeded
            with conn:
                manual_repo.resolve(album_id, discogs_url)

            # Art download  -  same logic as the regular enrich command
            updated_album = album_repo.get_by_id(album_id)
            if updated_album and updated_album.discogs_release_id:
                try:
                    release = client.get_release(updated_album.discogs_release_id)
                    if release.images:
                        primary_art = next(
                            (img for img in release.images if img.type == "primary"),
                            release.images[0],
                        )
                        click.echo(f"  [*] Downloading album art from: {primary_art.resource_url}")
                        art_path = downloader.download(
                            str(primary_art.resource_url),
                            album_id=updated_album.discogs_release_id,
                        )
                        if art_path:
                            click.echo(f"  [+] Album art saved to: {art_path}")
                            enriched_tracks = [
                                t
                                for t in track_repo.get_by_album(album_id)
                                if t.enrichment_status == "found"
                            ]
                            with conn:
                                for track in enriched_tracks:
                                    track.art_path = str(art_path)
                                    track_repo.upsert(track)
                        else:
                            click.echo("  [-] Failed to download album art.")
                    else:
                        click.echo("  [!] No images found for this release.")
                except Exception as art_exc:
                    click.echo(f"  [!] Art download failed: {art_exc}")

            click.echo(f"  [+] Done  -  album_id={album_id}")

        except Exception as exc:
            click.echo(f"  [-] album_id={album_id}: error  -  {exc}")
            log.error("process_manual.error", album_id=album_id, error=str(exc))

    # Write enriched tags to MP3 files
    writer = ID3Writer(track_repo, show_progress=True)
    success, errors = writer.write_pending()
    click.echo(f"\n[+] Written: {success} | Errors: {errors}")
    if errors:
        click.echo("[!] Some tracks could not be written. Check the log for details.")

    click.echo("\n[SUCCESS] Manual processing complete.")


@cli.command()
@_DB_PATH_OPTION
@click.option("--dry-run", is_flag=True, help="Preview writes without modifying any files.")
@click.option("--force", is_flag=True, help="Re-write tracks that have already been written.")
@click.option("--workers", default=1, show_default=True, help="Number of parallel write threads.")
@click.option(
    "--id3-version",
    type=click.Choice(["2.3", "2.4"]),
    default="2.3",
    show_default=True,
    help="ID3 tag version to write.",
)
@click.option(
    "--folder",
    default=None,
    metavar="PATH",
    help="Limit writes to tracks whose file path starts with PATH (e.g. an artist folder).",
)
def write(
    db_path: Path, dry_run: bool, force: bool, workers: int, id3_version: str, folder: str | None
) -> None:  # id3_version is constrained to "2.3"|"2.4" by click.Choice above
    """Write enriched ID3 tags to MP3 files.

    Reads all tracks with enrichment_status='found' and written_status='pending'
    from the database and writes their enriched metadata back to the MP3 files on
    disk.  Use --force to re-write tracks that have already been written.
    Use --folder to limit writes to a specific artist or album directory.
    """
    conn = get_db_connection(db_path)
    run_migrations(conn)

    track_repo = TrackRepository(conn)
    writer = ID3Writer(
        track_repo,
        id3_version=cast("Literal['2.3', '2.4']", id3_version),
        dry_run=dry_run,
        force=force,
        show_progress=True,
    )

    if dry_run:
        click.echo("[*] Dry-run mode  -  no files will be modified.")
    if force:
        click.echo("[*] Force mode  -  previously written tracks will be re-written.")
    if folder:
        click.echo(f"[*] Folder filter: {folder}")

    success, errors = writer.write_pending(workers=workers, folder_prefix=folder)

    click.echo(f"[+] Written: {success} | Errors: {errors}")
    if errors:
        click.echo("[!] Some tracks could not be written. Check the log for details.")


@cli.command("fix-track-numbers")
@click.argument("library_root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Report changes without writing any files.")
@click.option(
    "--workers",
    default=1,
    show_default=True,
    help="Reserved for future parallel processing (currently single-threaded).",
)
def fix_track_numbers_cmd(library_root: Path, dry_run: bool, workers: int) -> None:
    """Fix TRCK/TPOS tags on MP3s where the filename has a track-number prefix.

    Walks LIBRARY_ROOT recursively.  For each MP3 whose filename starts with
    a track-number prefix (e.g. ``01 Song.mp3`` or ``2-01 Song.mp3``), compares
    the parsed number against the existing TRCK/TPOS ID3 tags and writes a
    correction if they differ.  Files without a numeric prefix are skipped.

    Use --dry-run to preview changes without writing anything.
    """
    if dry_run:
        click.echo("[*] Dry-run mode — no files will be modified.")

    click.echo(f"[*] Scanning {library_root} ...")
    counts = fix_track_numbers(library_root, dry_run=dry_run)

    click.echo(
        f"[+] Checked: {counts['checked']} | "
        f"Updated: {counts['updated']} | "
        f"Skipped: {counts['skipped']} | "
        f"Errors: {counts['errors']}"
    )
    if counts["errors"]:
        click.echo("[!] Some files could not be processed. Check the log for details.")
    action = "Would update" if dry_run else "Updated"
    click.echo(f"\n[SUCCESS] {action} {counts['updated']} file(s).")


@cli.command("scan-integrity")
@click.argument("library_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@_DB_PATH_OPTION
@click.option(
    "--out",
    type=Path,
    default=None,
    metavar="CSV_PATH",
    help="Output CSV path (default: <library>/_tag_issues.csv).",
)
@click.option(
    "--threshold",
    type=int,
    default=75,
    show_default=True,
    help="Fuzzy-match threshold for album artist / album tag comparisons.",
)
@click.option(
    "--no-db",
    is_flag=True,
    help="Write CSV only — do not write findings to the SQLite database.",
)
def scan_integrity(
    library_path: Path,
    db_path: Path,
    out: Path | None,
    threshold: int,
    no_db: bool,
) -> None:
    """Scan LIBRARY_PATH for ID3 tag / folder-name mismatches.

    Detects: AlbumArtist or Album tags that don't match the folder name
    (fuzzy), inconsistent tags across tracks, all-untitled albums, and
    compilation artist mismatches.  Writes a CSV report and (by default)
    persists findings to the SQLite database so they can feed into the
    manual-review workflow.

    Re-running is idempotent — duplicate findings are silently ignored.
    """
    out_path = out or (library_path / "_tag_issues.csv")

    click.echo(f"[*] Scanning {library_path} (threshold={threshold}) ...")
    issues = IntegrityScanner(threshold=threshold).scan_library(library_path)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["artist_folder", "album_folder", "folder_path", "issue_kind", "detail"]
        )
        writer.writeheader()
        for issue in issues:
            writer.writerow(
                {
                    "artist_folder": issue.artist_folder,
                    "album_folder": issue.album_folder,
                    "folder_path": issue.folder_path or "",
                    "issue_kind": issue.issue_kind.value,
                    "detail": issue.detail,
                }
            )

    click.echo(f"[+] {len(issues)} issue(s) found. Report: {out_path}")

    if no_db:
        click.echo("[*] --no-db: skipping database write.")
        return

    conn = get_db_connection(db_path)
    run_migrations(conn)
    repo = TagIssuesRepository(conn)
    with conn:
        inserted = repo.upsert_batch(issues)
    click.echo(f"[+] DB: {inserted} new row(s) inserted (duplicates ignored).")
    click.echo("\n[SUCCESS] Integrity scan complete.")


@cli.command("retry-not-found")
@_DB_PATH_OPTION
@_TOKEN_OPTION
@click.option(
    "--starts-with",
    default=None,
    metavar="PREFIX",
    help="Only retry albums whose artist folder begins with PREFIX (case-insensitive).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show which albums would be retried without running enrichment.",
)
def retry_not_found(
    db_path: Path,
    token: str | None,
    starts_with: str | None,
    dry_run: bool,
) -> None:
    """Re-run enrichment on all not_found albums in the database.

    Queries the database for albums with enrichment_status='not_found',
    groups them by parent artist directory (to avoid redundant API calls),
    and re-runs the enrichment pipeline for each.  Albums that still can't be
    matched remain not_found for a future manual-review pass.

    Use --starts-with to limit retries to a specific letter/prefix.
    Use --dry-run to preview what would be retried.
    """
    settings = Settings()
    discogs_token = token or settings.discogs_token
    if not discogs_token:
        click.echo("Error: Discogs token not found in config or provided as option.")
        return

    conn = get_db_connection(db_path)
    run_migrations(conn)

    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)

    # Fetch all not_found albums, optionally filtered by artist-dir prefix.
    not_found = album_repo.get_not_found()

    if starts_with:
        prefix_lower = starts_with.lower()
        not_found = [
            a for a in not_found if Path(a.folder_path).parent.name.lower().startswith(prefix_lower)
        ]

    if not not_found:
        click.echo("[!] No not_found albums found in the database.")
        return

    # Group by parent artist dir.
    dir_albums: dict[str, list[AlbumRecord]] = defaultdict(list)
    for album in not_found:
        dir_albums[str(Path(album.folder_path).parent)].append(album)

    click.echo(f"[+] {len(not_found)} not_found album(s) across {len(dir_albums)} artist dir(s).")

    if dry_run:
        for artist_dir, albums in dir_albums.items():
            click.echo(f"  {artist_dir}")
            for a in albums:
                click.echo(f"    - {a.artist_guess} / {a.album_guess}")
        click.echo("[*] Dry-run — no enrichment performed.")
        return

    client = DiscogsClient(token=discogs_token, rate_limiter=_DISCOGS_RATE_LIMITER)
    manual_repo = ManualReviewRepository(conn)
    pipeline = EnrichmentPipeline(
        album_repo=album_repo,
        track_repo=track_repo,
        discogs_client=client,
        scraper=WebScraper(),
        enricher=HeuristicEnricher(),
        mb_client=MusicBrainzClient(rate_limiter=_MB_RATE_LIMITER),
        manual_review_repo=manual_repo,
    )

    newly_found = 0
    still_not_found = 0
    for i, (artist_dir, albums) in enumerate(dir_albums.items(), 1):
        click.echo(f"\n[{i}/{len(dir_albums)}] {artist_dir}")
        for a in albums:
            click.echo(f"  - {a.artist_guess} / {a.album_guess}")
        artist_path = Path(artist_dir)
        if not artist_path.is_dir():
            click.echo("  [-] Directory not found on disk, skipping.")
            still_not_found += len(albums)
            continue

        for album_subdir in find_album_dirs(artist_path):
            album_record = album_repo.get_by_folder_path(str(album_subdir.absolute()))
            if album_record is None or album_record.enrichment_status != "not_found":
                continue
            pipeline.enrich_album(album_record)
            refreshed = album_repo.get_by_folder_path(str(album_subdir.absolute()))
            if refreshed and refreshed.enrichment_status == "found":
                newly_found += 1
            else:
                still_not_found += 1

    click.echo(f"\n[RESULTS] Newly found: {newly_found} | Still not_found: {still_not_found}")
    click.echo("[SUCCESS] retry-not-found complete.")


@cli.command("prefill-master-urls")
@click.option(
    "--csv",
    "csv_path",
    required=True,
    type=Path,
    help="Path to the manual review CSV to pre-fill.",
)
@_DB_PATH_OPTION
@_TOKEN_OPTION
def prefill_master_urls_cmd(csv_path: Path, db_path: Path, token: str | None) -> None:
    """Pre-fill user_discogs_url for unmatched albums in a manual review CSV.

    Pass 1 — rows where the reason starts with 'No Discogs version with':
    Re-searches Discogs and writes the master URL so reviewers only need to
    verify rather than look each one up manually.

    Pass 2 — rows with 'No Discogs match':
    Checks whether the track-level Artist tag differs from the album artist.
    If so, tries a Discogs search using the track artist — useful for
    aliases and side projects (e.g. album artist 'Steven R. Smith',
    track artist 'Ulaan Kohl').

    Rows that already have a user_discogs_url are left untouched.
    The CSV is written back in place.
    """
    settings = Settings()
    discogs_token = token or settings.discogs_token
    if not discogs_token:
        click.echo("Error: Discogs token not found in config or provided as option.")
        return

    if not csv_path.exists():
        click.echo(f"[-] CSV not found: {csv_path}")
        return

    client = DiscogsClient(token=discogs_token, rate_limiter=_DISCOGS_RATE_LIMITER)
    conn = get_db_connection(db_path)
    run_migrations(conn)

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        click.echo("[!] CSV is empty — nothing to pre-fill.")
        return

    fieldnames = list(rows[0].keys())

    click.echo(f"[+] Loaded {len(rows)} rows from {csv_path}")

    total = 0
    total += prefill_pass1(rows, client)
    total += prefill_pass2(rows, client, conn)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    conn.close()
    click.echo(f"\n[+] Pre-filled: {total} row(s).")
    click.echo(f"[SUCCESS] Updated CSV written to {csv_path}")


@cli.command("enrich-missing")
@click.option(
    "--csv",
    "csv_path",
    required=True,
    type=Path,
    help="Reviewed manual review CSV whose folder_path entries to check.",
)
@_DB_PATH_OPTION
@_TOKEN_OPTION
def enrich_missing(csv_path: Path, db_path: Path, token: str | None) -> None:
    """Scan artist dirs for albums referenced in CSV but missing from the DB.

    Reads *csv_path*, finds rows whose folder_path is not yet in the database
    (or whose artist directory has never been scanned), and runs the
    enrichment pipeline on the parent artist directory for each.

    This is a preparation step before running ``process-manual``: it ensures
    the DB contains all the albums the reviewer has already identified so that
    ``process-manual`` can apply the Discogs URLs they supplied.

    After this command, run ``process-manual --csv-path <csv>`` to apply the
    user-supplied Discogs URLs from the same CSV.
    """
    settings = Settings()
    discogs_token = token or settings.discogs_token
    if not discogs_token:
        click.echo("Error: Discogs token not found in config or provided as option.")
        return

    if not csv_path.exists():
        click.echo(f"[-] CSV not found: {csv_path}")
        return

    with csv_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    conn = get_db_connection(db_path)
    run_migrations(conn)

    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)
    manual_repo = ManualReviewRepository(conn)

    # Find folder_paths not in DB whose parent dir exists on disk.
    unmatched_dirs: set[str] = set()
    for row in rows:
        fp = row.get("folder_path", "").strip()
        if not fp:
            continue
        if not album_repo.get_by_folder_path(fp) and Path(fp).is_dir():
            unmatched_dirs.add(str(Path(fp).parent))

    artist_dirs = sorted(unmatched_dirs)
    if not artist_dirs:
        click.echo("[!] All albums in the CSV are already in the database.")
        return

    click.echo(f"[+] {len(artist_dirs)} artist dir(s) to scan.")

    client = DiscogsClient(token=discogs_token, rate_limiter=_DISCOGS_RATE_LIMITER)
    pipeline = EnrichmentPipeline(
        album_repo=album_repo,
        track_repo=track_repo,
        discogs_client=client,
        scraper=WebScraper(),
        enricher=HeuristicEnricher(),
        mb_client=MusicBrainzClient(rate_limiter=_MB_RATE_LIMITER),
        manual_review_repo=manual_repo,
    )
    art_cache = settings.working_dir / "art"
    downloader = ArtDownloader(cache_dir=art_cache)

    albums_added = 0
    for i, artist_dir_str in enumerate(artist_dirs, 1):
        artist_dir = Path(artist_dir_str)
        if not artist_dir.is_dir():
            click.echo(f"  [{i}/{len(artist_dirs)}] Not on disk, skipping: {artist_dir_str}")
            continue
        click.echo(f"\n  [{i}/{len(artist_dirs)}] {artist_dir_str}")

        album_subdirs = find_album_dirs(artist_dir)
        for album_dir in album_subdirs:
            mp3_files = find_mp3_files(album_dir)
            if not mp3_files:
                continue

            guesses = parse_folder_names(album_dir)
            album_record = AlbumRecord(
                folder_path=str(album_dir.absolute()),
                artist_guess=guesses.get("artist_guess"),
                album_guess=guesses.get("album_guess"),
            )
            with conn:
                album_repo.upsert(album_record)
                saved = album_repo.get_by_folder_path(str(album_dir.absolute()))
                assert saved is not None
                album_id = saved.id
                assert album_id is not None
                for mp3 in mp3_files:
                    abs_path = str(mp3.absolute())
                    tags = read_id3_tags(mp3)
                    track_repo.upsert(
                        TrackRecord(
                            album_id=album_id,
                            file_path=abs_path,
                            filename=mp3.name,
                            track_number=tags.get("track_number"),
                            disc_number=tags.get("disc_number"),
                            existing_title=tags.get("title"),
                            existing_artist=tags.get("artist"),
                        )
                    )

            pipeline.enrich_album(saved)
            updated = album_repo.get_by_folder_path(str(album_dir.absolute()))
            status = updated.enrichment_status if updated else "unknown"
            click.echo(f"    {album_dir.name} → {status}")
            albums_added += 1

            # Download album art when found
            if updated and updated.discogs_release_id:
                try:
                    release = client.get_release(updated.discogs_release_id)
                    if release.images:
                        primary_art = next(
                            (img for img in release.images if img.type == "primary"),
                            release.images[0],
                        )
                        art_path = downloader.download(
                            str(primary_art.resource_url),
                            album_id=updated.discogs_release_id,
                        )
                        if art_path:
                            enriched_tracks = [
                                t
                                for t in track_repo.get_by_album(album_id)
                                if t.enrichment_status == "found"
                            ]
                            with conn:
                                for track in enriched_tracks:
                                    track.art_path = str(art_path)
                                    track_repo.upsert(track)
                except Exception as exc:
                    log.warning("enrich_missing.art_download_failed", error=str(exc))

    click.echo(f"\n[+] Scanned {albums_added} album(s) across {len(artist_dirs)} artist dir(s).")
    click.echo("[*] Run 'process-manual --csv-path <csv>' to apply user-supplied Discogs URLs.")
    click.echo("[SUCCESS] enrich-missing complete.")


@cli.command("link-scan")
@_DB_PATH_OPTION
@click.option(
    "--artist",
    default=None,
    metavar="ARTIST",
    help="Only scan albums by this exact artist name (case-insensitive prefix match).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Detect link affiliations and print results without writing to the DB or ID3 tags.",
)
@click.option(
    "--no-llm",
    is_flag=True,
    help=(
        "Use the built-in heuristic client (MusicBrainz + keyword lookup) instead of the "
        "Claude API.  No Anthropic API key required.  Covers well-known collectives such as "
        "Wu-Tang Clan, Soulquarians, Native Tongues, and Underground Resistance."
    ),
)
def link_scan(db_path: Path, artist: str | None, dry_run: bool, no_llm: bool) -> None:
    """Detect musical link affiliations and update GRP1/TIT1 tags.

    By default uses the Claude API to identify which groups, supergroups, or label
    families each artist belongs to.  Pass --no-llm to use the free heuristic
    client (MusicBrainz + keyword lookup) instead — no API credits required.

    Results are cached in the ``artist_links`` table to avoid repeated lookups.

    Featured artists extracted from track titles (e.g. "feat. RZA") are sent
    as affiliation signals.
    """
    from collections import defaultdict

    from tagger.db.artist_links_repo import ArtistLinksRepository
    from tagger.enricher.link_scanner import LinkScanner
    from tagger.enricher.llm.heuristic_client import HeuristicLinkClient

    settings = Settings()

    conn = get_db_connection(db_path)
    run_migrations(conn)

    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)
    links_repo = ArtistLinksRepository(conn)

    llm_client: LLMClient
    if no_llm:
        mb_client = MusicBrainzClient(rate_limiter=_MB_RATE_LIMITER)
        llm_client = HeuristicLinkClient(mb_client=mb_client)
        click.echo("[*] Using heuristic client (MusicBrainz + keyword lookup, no API key needed).")
    else:
        from tagger.enricher.llm.claude_client import ClaudeLinkClient

        if not settings.anthropic_api_key:
            click.echo(
                "[ERROR] ANTHROPIC_API_KEY is not set. "
                "Add it to .env or use --no-llm for the free heuristic client."
            )
            raise SystemExit(1)
        llm_client = ClaudeLinkClient(api_key=settings.anthropic_api_key)
    scanner = LinkScanner(
        links_repo=links_repo,
        track_repo=track_repo,
        llm_client=llm_client,
    )

    albums = album_repo.get_enriched(artist_prefix=artist)
    if not albums:
        click.echo("[!] No enriched albums found matching criteria.")
        return

    click.echo(f"[+] {len(albums)} enriched album(s) to scan.")

    # Build a set of every artist name in the FULL library (not just the current
    # prefix) so cross-artist links are resolved correctly even when scanning a
    # subset.  e.g. when scanning "--artist G", Godflesh should still find
    # Greymachine because Greymachine also lives in the library.
    all_library_albums = album_repo.get_enriched()
    library_artists: frozenset[str] = frozenset(
        a.artist_guess for a in all_library_albums if a.artist_guess
    )

    # Group albums by artist so we aggregate all album context per artist
    albums_by_artist: dict[str, list[int]] = defaultdict(list)
    for album in albums:
        artist_name = album.artist_guess or "Unknown"
        assert album.id is not None
        albums_by_artist[artist_name].append(album.id)

    updated = 0
    for artist_name, ids in albums_by_artist.items():
        # scan_artist caches the full network in artist_links; filter here so
        # only artists present in this library end up in the GRP1 tag.
        all_links = scanner.scan_artist(artist=artist_name, album_ids=ids)
        links = LinkScanner.filter_to_library(all_links, library_artists)
        if not links:
            continue

        link_str = ", ".join(links)
        click.echo(f"  {artist_name} → link:{link_str}")

        if dry_run:
            continue

        # Update grouping for all tracks of these albums
        for album_id in ids:
            tracks = track_repo.get_by_album(album_id)
            for track in tracks:
                new_grouping = LinkScanner.update_grouping_tag(track.grouping or "", link_str)
                if new_grouping != track.grouping:
                    track.grouping = new_grouping
                    with conn:
                        track_repo.upsert(track)
                    updated += 1

    if dry_run:
        click.echo("[DRY RUN] No changes written.")
    else:
        click.echo(f"[SUCCESS] Updated grouping on {updated} track(s).")
    click.echo("[*] Run 'write' to flush changes to ID3 tags.")


@cli.command("audit-itunes")
@click.option(
    "--library",
    "library_path",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help=r"Path to the music library root (e.g. M:\Shared Music).",
)
@click.option(
    "--itunes-xml",
    "itunes_xml",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to iTunes Music Library.xml.",
)
@click.option(
    "--out",
    type=Path,
    default=Path("itunes_audit.csv"),
    show_default=True,
    help="Output CSV path.",
)
@click.option(
    "--threshold",
    type=int,
    default=75,
    show_default=True,
    help="Fuzzy-match score below which a field is flagged as mismatched.",
)
@click.option(
    "--workers",
    type=int,
    default=4,
    show_default=True,
    help="Number of threads for parallel ID3 tag reading.",
)
def audit_itunes(
    library_path: Path,
    itunes_xml: Path,
    out: Path,
    threshold: int,
    workers: int,
) -> None:
    """Compare MP3 ID3 tags against an iTunes XML library record.

    Surfaces Artist / Album Artist / Album discrepancies and missing track
    numbers by fuzzy-matching current ID3 tags against the iTunes ground-truth
    record.  Writes a CSV report to --out.
    """
    import csv as _csv

    from tagger.integrity.itunes_comparator import compare_library

    click.echo(f"[*] Loading iTunes library from {itunes_xml} ...")
    click.echo(f"[*] Scanning {library_path} with {workers} worker(s) ...")

    discrepancies = compare_library(
        library_path=library_path,
        itunes_xml=itunes_xml,
        threshold=threshold,
        workers=workers,
    )

    fieldnames = [
        "file_path",
        "artist_folder",
        "album_folder",
        "mp3_artist",
        "mp3_album_artist",
        "mp3_album",
        "mp3_track_number",
        "itunes_artist",
        "itunes_album_artist",
        "itunes_album",
        "itunes_track_number",
        "issues",
        "artist_score",
        "album_artist_score",
        "album_score",
    ]

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for d in discrepancies:
            row = d.model_dump()
            row["issues"] = "|".join(d.issues)
            writer.writerow(row)

    total = len(discrepancies)
    by_issue: dict[str, int] = {}
    for d in discrepancies:
        for issue in d.issues:
            by_issue[issue] = by_issue.get(issue, 0) + 1

    click.echo(f"[+] {total} discrepancy row(s) found.")
    for issue_name, count in sorted(by_issue.items()):
        click.echo(f"    {issue_name}: {count}")
    click.echo(f"[+] Report written to {out}")
    click.echo("\n[SUCCESS] audit-itunes complete.")


@cli.command("restore-from-itunes")
@click.option(
    "--library",
    "library_path",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help=r"Path to the music library root (e.g. M:\Shared Music).",
)
@click.option(
    "--itunes-xml",
    "itunes_xml",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to iTunes Music Library.xml.",
)
@click.option(
    "--out",
    type=Path,
    default=Path("itunes_restore_report.csv"),
    show_default=True,
    help="Output CSV path for the restore report.",
)
@click.option(
    "--threshold",
    type=int,
    default=75,
    show_default=True,
    help="Fuzzy-match score below which a field is considered mismatched.",
)
@click.option(
    "--workers",
    type=int,
    default=4,
    show_default=True,
    help="Number of threads for parallel ID3 tag reading.",
)
@click.option("--dry-run", is_flag=True, help="Preview changes without writing any files.")
def restore_from_itunes_cmd(
    library_path: Path,
    itunes_xml: Path,
    out: Path,
    threshold: int,
    workers: int,
    dry_run: bool,
) -> None:
    """Restore corrupted MP3 tags from an iTunes Music Library XML record.

    Compares current ID3 tags against the iTunes ground-truth and writes the
    correct Artist, Album Artist, Album, and Track Number values back to each
    affected MP3.  Only the mismatched frames are overwritten; all other tags
    are preserved.  Use --dry-run to preview without modifying any files.
    """
    import csv as _csv

    from tagger.integrity.itunes_restorer import restore_from_itunes

    if dry_run:
        click.echo("[*] Dry-run mode — no files will be modified.")
    click.echo(f"[*] Loading iTunes library from {itunes_xml} ...")
    click.echo(f"[*] Scanning {library_path} with {workers} worker(s) ...")

    results = restore_from_itunes(
        itunes_xml=itunes_xml,
        library_path=library_path,
        threshold=threshold,
        workers=workers,
        dry_run=dry_run,
    )

    fieldnames = [
        "file_path",
        "artist_folder",
        "album_folder",
        "fields_restored",
        "old_artist",
        "new_artist",
        "old_album_artist",
        "new_album_artist",
        "old_album",
        "new_album",
        "old_track_number",
        "new_track_number",
        "dry_run",
    ]

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = r.model_dump()
            row["fields_restored"] = "|".join(r.fields_restored)
            writer.writerow(row)

    total = len(results)
    by_field: dict[str, int] = {}
    for r in results:
        for field in r.fields_restored:
            by_field[field] = by_field.get(field, 0) + 1

    action = "Would restore" if dry_run else "Restored"
    click.echo(f"[+] {action} {total} file(s).")
    for field, count in sorted(by_field.items()):
        click.echo(f"    {field}: {count}")
    click.echo(f"[+] Report written to {out}")
    click.echo(f"\n[SUCCESS] restore-from-itunes {'(dry run) ' if dry_run else ''}complete.")


if __name__ == "__main__":
    cli()
