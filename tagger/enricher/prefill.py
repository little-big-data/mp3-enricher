"""Discogs master URL pre-filling logic for manual review CSVs.

Extracted from scripts/prefill_master_urls.py into a testable module.
"""

from __future__ import annotations

import sqlite3  # noqa: TC003 — used at runtime for conn.execute() calls
from collections import Counter
from typing import TYPE_CHECKING

import structlog
from rapidfuzz import fuzz

if TYPE_CHECKING:
    from tagger.enricher.discogs.client import DiscogsClient
    from tagger.enricher.discogs.models import DiscogsSearchResult

log = structlog.get_logger(__name__)


def best_master_url(artist: str, album: str, client: DiscogsClient) -> str | None:
    """Return the best Discogs master URL for the given artist/album, or None.

    Searches Discogs for releases matching *artist* and *album*, picks the
    result with the highest fuzzy-match score against *album*, and returns
    the master URL (preferred) or release URL.  Returns ``None`` when the
    search fails, produces no results, or the best score is below 40.
    """
    try:
        results: list[DiscogsSearchResult] = client.search_album(artist=artist, album=album)
    except Exception:
        log.warning("prefill.search_failed", artist=artist, album=album, exc_info=True)
        return None

    if not results:
        return None

    scored = sorted(
        results,
        key=lambda r: fuzz.token_sort_ratio(album.lower(), r.title.lower()),
        reverse=True,
    )
    best = scored[0]
    if fuzz.token_sort_ratio(album.lower(), best.title.lower()) < 40:
        log.debug("prefill.score_too_low", artist=artist, album=album, best_title=best.title)
        return None

    if best.master_id:
        return f"https://www.discogs.com/master/{best.master_id}"
    return f"https://www.discogs.com/release/{best.id}"


def get_track_artist(folder_path: str, conn: sqlite3.Connection) -> str | None:
    """Return the most common existing_artist across tracks in *folder_path*.

    Queries the tracks table via the albums table.  Returns ``None`` when the
    album is not in the DB or all existing_artist values are blank/NULL.
    """
    rows = conn.execute(
        """
        SELECT existing_artist FROM tracks
        WHERE album_id = (SELECT id FROM albums WHERE folder_path = ?)
          AND existing_artist IS NOT NULL AND existing_artist != ''
        """,
        (folder_path,),
    ).fetchall()
    if not rows:
        return None
    counts: Counter[str] = Counter(row[0] for row in rows)
    return counts.most_common(1)[0][0]


def prefill_pass1(
    rows: list[dict[str, str]],
    client: DiscogsClient,
) -> int:
    """Pre-fill URLs for rows whose reason indicates a track-count mismatch.

    Mutates *rows* in place, setting ``user_discogs_url`` where a match is found.
    Returns the number of rows that were filled.
    """
    targets = [
        r
        for r in rows
        if r["reason"].startswith("No Discogs version with")
        and not r.get("user_discogs_url", "").strip()
    ]
    log.info("prefill.pass1.start", target_count=len(targets))

    filled = 0
    for row in targets:
        artist = row["artist_guess"].strip()
        album = row["album_guess"].strip()
        url = best_master_url(artist, album, client)
        if url:
            row["user_discogs_url"] = url
            filled += 1
            log.info("prefill.pass1.filled", artist=artist, album=album, url=url)
        else:
            log.debug("prefill.pass1.no_match", artist=artist, album=album)

    log.info("prefill.pass1.complete", filled=filled, total=len(targets))
    return filled


def prefill_pass2(
    rows: list[dict[str, str]],
    client: DiscogsClient,
    conn: sqlite3.Connection,
) -> int:
    """Pre-fill URLs for 'No Discogs match' rows using the track-level artist.

    Useful for artist aliases and side projects where the album artist folder
    name differs from the artist name on the actual Discogs release.  Only
    attempts a search when the track artist is meaningfully different from the
    album artist (similarity < 85).

    Mutates *rows* in place.  Returns the number of rows that were filled.
    """
    targets = [
        r
        for r in rows
        if r["reason"] == "No Discogs match" and not r.get("user_discogs_url", "").strip()
    ]
    log.info("prefill.pass2.start", target_count=len(targets))

    filled = 0
    skipped = 0
    for row in targets:
        album_artist = row["artist_guess"].strip()
        album = row["album_guess"].strip()
        folder_path = row.get("folder_path", "").strip()

        track_artist = get_track_artist(folder_path, conn) if folder_path else None

        if (
            not track_artist
            or fuzz.token_sort_ratio(track_artist.lower(), album_artist.lower()) > 85
        ):
            skipped += 1
            continue

        log.debug(
            "prefill.pass2.alias_search",
            album_artist=album_artist,
            track_artist=track_artist,
            album=album,
        )
        url = best_master_url(track_artist, album, client)
        if url:
            row["user_discogs_url"] = url
            filled += 1
            log.info("prefill.pass2.filled", track_artist=track_artist, album=album, url=url)
        else:
            log.debug("prefill.pass2.no_match", track_artist=track_artist, album=album)

    log.info("prefill.pass2.complete", filled=filled, skipped=skipped, total=len(targets))
    return filled
