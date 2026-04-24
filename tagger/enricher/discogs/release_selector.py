from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

import httpx
import structlog
from rapidfuzz import fuzz

if TYPE_CHECKING:
    from tagger.enricher.discogs.client import DiscogsClient
    from tagger.enricher.discogs.models import DiscogsSearchResult

log = structlog.get_logger(__name__)

# Used as a sort key when a release has no year, so undated releases sort last.
_MISSING_YEAR_SENTINEL: int = 9999

# Formats that indicate a video product — always excluded
_VIDEO_FORMATS: frozenset[str] = frozenset(
    {
        "VHS",
        "DVD",
        "DVD-Video",
        "Blu-ray",
        "Laserdisc",
        "Video",
        "Videocassette",
        "Betamax",
        "Hi8",
        "VCD",
        "SVCD",
        "UMD",
    }
)

# Formats we actively prefer (higher = more preferred, used as tiebreaker)
_FORMAT_PREFERENCE: dict[str, int] = {
    "CD": 3,
    "Vinyl": 3,
    "FLAC": 3,
    "Digital Media": 2,
    "MP3": 2,
    "Cassette": 1,
}


def _is_video(result: DiscogsSearchResult) -> bool:
    """Return True if any format token indicates a video product."""
    return bool(_VIDEO_FORMATS.intersection(result.format))


def _format_score(result: DiscogsSearchResult) -> int:
    """Higher score = more preferred audio format."""
    return max((_FORMAT_PREFERENCE.get(f, 0) for f in result.format), default=0)


class ReleaseSelector:
    def __init__(self, client: DiscogsClient, threshold: int = 85) -> None:
        self._client = client
        self._threshold = threshold

    def find_best_release(
        self,
        artist: str,
        album: str,
        track_count: int | None = None,
    ) -> DiscogsSearchResult | None:
        """Find the best Discogs release for an artist/album.

        When track_count is provided, prefer master versions whose track count
        matches over the oldest version. Falls back to oldest audio version if
        no version matches.
        """
        log.info("discogs.search", artist=artist, album=album)
        results = self._client.search_album(artist, album)

        if not results:
            log.info("discogs.search_fallback", album=album)
            results = self._client.search_album("", album)

        if not results:
            log.warning("discogs.no_results", artist=artist, album=album)
            return None

        # Exclude video products
        results = [r for r in results if not _is_video(r)]
        if not results:
            log.warning("discogs.only_video_results", artist=artist, album=album)
            return None

        # Fuzzy-match against artist+album and album-only targets
        target_full = f"{artist} {album}".lower()
        target_album = album.lower()

        matches: list[tuple[DiscogsSearchResult, float]] = []
        for result in results:
            score_full = fuzz.token_set_ratio(target_full, result.title.lower())
            score_album = fuzz.token_set_ratio(target_album, result.title.lower())
            score = max(score_full, score_album)
            if score >= self._threshold:
                matches.append((result, score))

        if not matches:
            log.warning(
                "discogs.no_matches_above_threshold",
                artist=artist,
                album=album,
                threshold=self._threshold,
            )
            return None

        # Sort by fuzzy score desc, then by format preference desc
        matches.sort(key=lambda x: (x[1], _format_score(x[0])), reverse=True)
        best_result, _ = matches[0]

        if best_result.master_id:
            log.debug("discogs.fetching_master_versions", master_id=best_result.master_id)
            versions = self._client.get_master_releases(best_result.master_id)
            if versions:
                audio_versions = [v for v in versions if not _is_video(v)]
                valid = [v for v in audio_versions if v.year is not None]
                if valid:
                    if track_count is not None:
                        matched = self._find_version_by_track_count(valid, track_count)
                        if matched is not None:
                            return matched
                    oldest: DiscogsSearchResult = min(
                        valid,
                        key=lambda v: v.year if v.year is not None else _MISSING_YEAR_SENTINEL,
                    )
                    log.info(
                        "discogs.selected_oldest_from_master",
                        oldest_id=oldest.id,
                        year=oldest.year,
                    )
                    return oldest

        log.info("discogs.selected_direct_match", release_id=best_result.id, year=best_result.year)
        return best_result

    def _find_version_by_track_count(
        self,
        versions: list[DiscogsSearchResult],
        target_count: int,
    ) -> DiscogsSearchResult | None:
        """Fetch master versions in parallel and return the oldest one with target_count tracks.

        Uses up to 4 concurrent threads to amortise network latency, while the shared
        rate-limiter inside DiscogsClient naturally serialises token acquisition.
        """
        candidates = sorted(
            versions,
            key=lambda v: (
                v.year if v.year is not None else _MISSING_YEAR_SENTINEL,
                -_format_score(v),
            ),
        )

        def _fetch_count(version: DiscogsSearchResult) -> tuple[DiscogsSearchResult, int]:
            release = self._client.get_release(version.id)
            return version, sum(1 for t in release.tracklist if t.position)

        track_counts: dict[int, int] = {}  # version.id → real track count
        workers = min(len(candidates), 4)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_count, v): v for v in candidates}
            for future in as_completed(futures):
                version = futures[future]
                try:
                    _, real_count = future.result()
                    track_counts[version.id] = real_count
                    log.debug(
                        "discogs.checking_version_tracks",
                        release_id=version.id,
                        track_count=real_count,
                        target=target_count,
                    )
                except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException):
                    log.warning("discogs.version_fetch_failed", release_id=version.id)

        # Return the oldest candidate whose track count matches
        for version in candidates:
            if track_counts.get(version.id) == target_count:
                log.info(
                    "discogs.found_version_with_matching_tracks",
                    release_id=version.id,
                    track_count=target_count,
                )
                return version
        return None
