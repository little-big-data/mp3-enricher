from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx
import structlog
from rapidfuzz import fuzz

from tagger.enricher.discogs.release_selector import ReleaseSelector
from tagger.enricher.formatter import (
    format_track_number,
    normalize_artist,
    normalize_title,
    strip_discogs_number,
)

if TYPE_CHECKING:
    from tagger.db.album_repo import AlbumRepository
    from tagger.db.manual_review_repo import ManualReviewRepository
    from tagger.db.models import AlbumRecord, TrackRecord
    from tagger.db.track_repo import TrackRepository
    from tagger.enricher.discogs.client import DiscogsClient
    from tagger.enricher.discogs.models import DiscogsRelease, DiscogsTrack
    from tagger.enricher.heuristic_enricher import HeuristicEnricher
    from tagger.enricher.musicbrainz.client import MusicBrainzClient
    from tagger.enricher.web.scraper import WebScraper

log = structlog.get_logger(__name__)

_LEADING_NUMBER_RE = re.compile(r"^(\d+)")
_DISC_PREFIX_RE = re.compile(r"^(\d+)-(\d{1,2})\b")
_DISC_POSITION_RE = re.compile(r"^(\d+)-\d")
_VARIOUS_ARTISTS: frozenset[str] = frozenset({"various", "various artists", "va"})

# Reject a Discogs match when folder-artist vs release-artist similarity is below this.
# A score of 40 catches clearly wrong matches (e.g. Cat Stevens vs. Astrud Gilberto)
# while allowing legitimate aliases and transliterations.
_ARTIST_SIMILARITY_FLOOR = 40


def _extract_leading_number(filename: str) -> int | None:
    """Return the leading integer from a filename, e.g. '01 Song.mp3' → 1."""
    m = _LEADING_NUMBER_RE.match(filename)
    return int(m.group(1)) if m else None


def _track_artist_name(track: DiscogsTrack) -> str | None:
    """Return a display artist string from track.artists, or None if the list is empty.

    Prefers anv (artist name variation) over name when non-empty.
    Strips Discogs disambiguation suffixes like '(2)' from the final name.
    Joins multiple artists using their 'join' separator field.
    """
    if not track.artists:
        return None
    parts: list[str] = []
    for artist in track.artists:
        display = (artist.anv if artist.anv else artist.name).strip()
        display = strip_discogs_number(display)
        if display:
            parts.append(display)
        if artist.join.strip():
            parts.append(artist.join.strip())
    return " ".join(parts) or None


def _real_tracks(tracklist: list[DiscogsTrack]) -> list[DiscogsTrack]:
    """Filter out heading entries (empty position) from a Discogs tracklist."""
    return [t for t in tracklist if t.position]


def _is_various_artist(release: DiscogsRelease) -> bool:
    """Return True when the release artist indicates a Various Artists compilation."""
    return any("various" in a.name.lower() for a in release.artists)


class EnrichmentPipeline:
    def __init__(
        self,
        album_repo: AlbumRepository,
        track_repo: TrackRepository,
        discogs_client: DiscogsClient,
        scraper: WebScraper,
        enricher: HeuristicEnricher,
        mb_client: MusicBrainzClient | None = None,
        manual_review_repo: ManualReviewRepository | None = None,
        title_fuzzy_threshold: int = 75,
    ) -> None:
        self._album_repo = album_repo
        self._track_repo = track_repo
        self._discogs_client = discogs_client
        self._selector = ReleaseSelector(discogs_client)
        self._scraper = scraper
        self._enricher = enricher
        self._mb_client = mb_client
        self._manual_review_repo = manual_review_repo
        self._title_fuzzy_threshold = title_fuzzy_threshold

    def enrich_album(self, album_record: AlbumRecord) -> None:
        """Enrich a single album: Discogs → Wikipedia → MusicBrainz → heuristics → DB."""
        artist = album_record.artist_guess or "Unknown"
        album = album_record.album_guess or "Unknown"
        if album_record.id is None:
            raise ValueError(f"album_record.id must be set before enrichment: {album_record!r}")

        log.info("pipeline.enrich_album", artist=artist, album=album)

        # Load db tracks first so we can pass a track-count hint to the selector
        db_tracks = self._track_repo.get_by_album(album_record.id)
        file_count = len(db_tracks)

        # 1. Discogs search (with track-count hint so selector can pick the right version)
        best_release_meta = self._selector.find_best_release(
            artist, album, track_count=file_count if file_count > 0 else None
        )
        if not best_release_meta:
            log.warning("pipeline.no_discogs_match", album_id=album_record.id)
            with self._album_repo._conn:
                self._album_repo.mark_not_found(album_record.id)
                if self._manual_review_repo is not None:
                    self._manual_review_repo.add(album_record.id, "No Discogs match")
            return

        # 2. Fetch the selected release and filter heading entries
        try:
            release = self._discogs_client.get_release(best_release_meta.id)
        except httpx.HTTPStatusError as exc:
            log.warning(
                "pipeline.release_fetch_failed",
                album_id=album_record.id,
                release_id=best_release_meta.id,
                status_code=exc.response.status_code,
            )
            with self._album_repo._conn:
                self._album_repo.mark_not_found(album_record.id)
                if self._manual_review_repo is not None:
                    self._manual_review_repo.add(
                        album_record.id,
                        f"Discogs release {best_release_meta.id} "
                        f"returned HTTP {exc.response.status_code}",
                    )
            return
        discogs_real = _real_tracks(release.tracklist)

        # 3a. Artist name sanity check — reject clearly wrong matches before enriching.
        #     Skip validation for Various Artists compilations and when no artist guess exists.
        if release.artists and not _is_various_artist(release) and album_record.artist_guess:
            release_name = strip_discogs_number(release.artists[0].name)
            sim = fuzz.token_set_ratio(album_record.artist_guess.lower(), release_name.lower())
            if sim < _ARTIST_SIMILARITY_FLOOR:
                log.warning(
                    "pipeline.artist_name_mismatch",
                    folder_artist=album_record.artist_guess,
                    release_artist=release_name,
                    similarity=sim,
                    release_id=best_release_meta.id,
                )
                with self._album_repo._conn:
                    self._album_repo.mark_not_found(album_record.id)
                    if self._manual_review_repo is not None:
                        self._manual_review_repo.add(
                            album_record.id,
                            f"Artist mismatch: folder='{album_record.artist_guess}' "
                            f"vs Discogs='{release_name}' (sim={sim})",
                        )
                return

        # 3b. Final track-count check — selector already tried to match on track count,
        #     so a persistent mismatch means no suitable version exists → manual review.
        #     For multi-disc albums the file_count is the total across all discs.
        if file_count > 0 and len(discogs_real) != file_count:
            log.warning(
                "pipeline.no_version_matches_track_count",
                album_id=album_record.id,
                file_count=file_count,
                discogs_count=len(discogs_real),
            )
            with self._album_repo._conn:
                self._album_repo.mark_not_found(album_record.id)
                if self._manual_review_repo is not None:
                    self._manual_review_repo.add(
                        album_record.id,
                        f"No Discogs version with {file_count} tracks",
                    )
            return

        self._enrich_with_release(album_record, release, db_tracks)

    def enrich_album_from_release_id(self, album_record: AlbumRecord, release_id: int) -> None:
        """Enrich an album using an explicitly supplied Discogs release ID.

        Bypasses the selector entirely — useful for manual-review corrections where
        the user has already identified the correct release.
        """
        if album_record.id is None:
            raise ValueError(f"album_record.id must be set before enrichment: {album_record!r}")
        artist = album_record.artist_guess or "Unknown"
        album = album_record.album_guess or "Unknown"
        log.info(
            "pipeline.enrich_album_from_release_id",
            artist=artist,
            album=album,
            release_id=release_id,
        )

        release = self._discogs_client.get_release(release_id)
        db_tracks = self._track_repo.get_by_album(album_record.id)
        self._enrich_with_release(album_record, release, db_tracks)

    def _enrich_with_release(
        self,
        album_record: AlbumRecord,
        release: DiscogsRelease,
        db_tracks: list[TrackRecord],
    ) -> None:
        """Run heuristic enrichment and persist all track data for a resolved release."""
        if album_record.id is None:
            raise ValueError(f"album_record.id must be set before enrichment: {album_record!r}")
        artist = album_record.artist_guess or "Unknown"
        album = album_record.album_guess or "Unknown"
        discogs_real = _real_tracks(release.tracklist)

        # Fetch artist details (best-effort; failures are non-fatal)
        discogs_artist = None
        if release.artists and release.artists[0].id is not None:
            try:
                discogs_artist = self._discogs_client.get_artist(release.artists[0].id)
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException):
                log.warning("pipeline.artist_fetch_failed", artist_id=release.artists[0].id)

        # Web scraping & heuristic enrichment
        wiki_text = self._scraper.fetch_wikipedia_summary(artist)

        release_artist_name = (
            strip_discogs_number(release.artists[0].name) if release.artists else None
        )

        mb_links: list[str] = []
        mb_origin: tuple[str | None, str | None] | None = None
        if self._mb_client is not None:
            mb_links = self._mb_client.find_links(artist)
            mb_origin = self._mb_client.find_area(artist)

        enrichment_data = self._enricher.enrich_album(
            artist,
            album,
            wiki_text,
            discogs_release=release,
            discogs_artist=discogs_artist,
            mb_links=mb_links or None,
            release_artist=release_artist_name,
            mb_origin=mb_origin,
        )

        # Persist to DB
        with self._album_repo._conn:
            self._album_repo.mark_found(album_record.id, release.id, str(release.uri or ""))

            discogs_by_pos = {t.position: t for t in discogs_real}
            # 1-based sequential index for each position — used to convert non-numeric
            # Discogs positions (vinyl "A1"/"B2", roman numerals, etc.) to TRCK values.
            pos_to_seq: dict[str, int] = {t.position: i + 1 for i, t in enumerate(discogs_real)}
            db_tracks_sorted = sorted(db_tracks, key=lambda t: t.filename)

            for idx, db_track in enumerate(db_tracks_sorted):
                d_track = self._match_discogs_track(
                    db_track, discogs_by_pos, discogs_real, idx, self._title_fuzzy_threshold
                )
                if d_track is None:
                    continue

                track_artist = _track_artist_name(d_track) or release_artist_name or artist
                norm_artist, norm_title = normalize_artist(track_artist, d_track.title)
                norm_title = normalize_title(norm_title)

                override = next(
                    (o for o in enrichment_data.track_overrides if o.position == d_track.position),
                    None,
                )

                effective_album_artist = enrichment_data.album_artist_canonical or artist
                is_compilation = effective_album_artist.lower().strip() in _VARIOUS_ARTISTS

                # Extract disc number from Discogs position (e.g. "2-1" → disc 2)
                disc_pos_match = _DISC_POSITION_RE.match(d_track.position or "")
                db_track.disc_number = int(disc_pos_match.group(1)) if disc_pos_match else None

                # Sequential index: fallback for non-numeric positions (vinyl/roman/etc.)
                seq_idx = pos_to_seq.get(d_track.position or "", idx + 1)

                db_track.title = norm_title
                db_track.artist = norm_artist
                db_track.album_title = release.title
                db_track.album_artist = None if is_compilation else effective_album_artist
                db_track.compilation = is_compilation
                db_track.year = release.year
                db_track.track_num = format_track_number(
                    d_track.position, len(discogs_real), sequential_index=seq_idx
                )
                db_track.genre = enrichment_data.genre
                db_track.grouping = enrichment_data.to_grp1(override)
                db_track.enrichment_status = "found"

                self._track_repo.upsert(db_track)

        log.info("pipeline.enrich_success", album_id=album_record.id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _match_discogs_track(
        self,
        db_track: TrackRecord,
        discogs_by_pos: dict[str, DiscogsTrack],
        discogs_ordered: list[DiscogsTrack],
        idx: int,
        fuzzy_threshold: int,
    ) -> DiscogsTrack | None:
        """Return the best matching Discogs track for a DB track using five strategies."""
        # Strategy 0: multi-disc filename prefix, e.g. "2-01 Song.mp3" → disc 2, track 1.
        # Discogs position format for multi-disc CDs: "2-1", "2-2", etc.
        m_disc = _DISC_PREFIX_RE.match(db_track.filename)
        if m_disc:
            disc_num = int(m_disc.group(1))
            track_num = int(m_disc.group(2))
            for pos in (f"{disc_num}-{track_num}", f"{disc_num}-{track_num:02d}"):
                match = discogs_by_pos.get(pos)
                if match:
                    return match

        # Strategy 1: existing TRCK tag value
        if db_track.track_number is not None:
            match = discogs_by_pos.get(str(db_track.track_number))
            if match:
                return match

        # Strategy 2: leading number extracted from filename ("01 Song.mp3" → "1")
        fn_num = _extract_leading_number(db_track.filename)
        if fn_num is not None:
            match = discogs_by_pos.get(str(fn_num))
            if match:
                return match

        # Strategy 3: fuzzy match existing ID3 title against Discogs track titles
        if db_track.existing_title:
            best_score: float = 0.0
            best_track: DiscogsTrack | None = None
            for d_track in discogs_ordered:
                score = fuzz.token_set_ratio(db_track.existing_title.lower(), d_track.title.lower())
                if score > best_score:
                    best_score = score
                    best_track = d_track
            if best_track is not None and best_score >= fuzzy_threshold:
                log.debug(
                    "pipeline.fuzzy_title_match",
                    existing_title=db_track.existing_title,
                    discogs_title=best_track.title,
                    score=best_score,
                )
                return best_track

        # Strategy 4: positional fallback — nth file → nth Discogs track
        if idx < len(discogs_ordered):
            return discogs_ordered[idx]

        return None
