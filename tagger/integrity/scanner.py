"""Integrity scanner — walks a music library and detects ID3 tag / folder-name mismatches."""

from __future__ import annotations

import re
from pathlib import Path  # noqa: TC003 — used at runtime for directory traversal

import structlog
from mutagen.id3 import ID3, ID3NoHeaderError
from rapidfuzz import fuzz

from tagger.integrity.models import IssueKind, TagIssue

log = structlog.get_logger(__name__)

# Strip a leading track number from a filename stem, e.g.:
#   "01 Old Gods Returning"   -> "Old Gods Returning"
#   "01 - Old Gods Returning" -> "Old Gods Returning"
#   "01. Old Gods Returning"  -> "Old Gods Returning"
_TRACK_PREFIX_RE: re.Pattern[str] = re.compile(r"^(?:track\s*)?\d+[\s.\-_]+", re.IGNORECASE)

_CATCHALL_FOLDERS: frozenset[str] = frozenset(
    {
        "compilations",
        "compilation",
        "soundtracks",
        "soundtrack",
        "various artists",
        "various",
        "va",
    }
)

_VARIOUS_VALUES: frozenset[str] = frozenset(
    {
        "various",
        "various artists",
        "va",
        "v/a",
    }
)

# Fraction of tracks in an album that must have useless titles before the album
# is classified as ALL_UNTITLED (e.g. 0.8 = 80% or more).
_UNTITLED_TRACK_RATIO: float = 0.8

# Title values that convey no useful information.
_USELESS_TITLE_RE: re.Pattern[str] = re.compile(
    r"^(untitled\s*\d*|track\s*\d+|)$",
    re.IGNORECASE,
)


def _is_useless_title(title: str) -> bool:
    return bool(_USELESS_TITLE_RE.match(title.strip()))


class IntegrityScanner:
    """Scans a music library directory tree for ID3 tag integrity issues.

    Detects:
    - AlbumArtist (TPE2) / Album (TALB) not matching the folder name (fuzzy)
    - Inconsistent AlbumArtist or Album tag across tracks in the same folder
    - Compilation artist mismatch (real TPE2 but Various Artists TPE1 in catchall)
    - All-untitled albums (≥80% of tracks have generic/empty titles)
    - Track title / filename divergence (only surfaced when other issues exist)
    """

    def __init__(self, threshold: int = 75) -> None:
        self._threshold = threshold

    def scan_library(self, library_path: Path) -> list[TagIssue]:
        """Walk *library_path* and return a list of :class:`TagIssue` objects.

        Expected structure: ``library_path/<artist_folder>/<album_folder>/*.mp3``
        """
        issues: list[TagIssue] = []
        albums_checked = 0

        artist_dirs = sorted(d for d in library_path.iterdir() if d.is_dir())
        log.info(
            "integrity.scan.start",
            library=str(library_path),
            artist_dir_count=len(artist_dirs),
        )

        for artist_dir in artist_dirs:
            artist_folder = artist_dir.name
            is_catchall = artist_folder.lower() in _CATCHALL_FOLDERS

            for album_dir in sorted(d for d in artist_dir.iterdir() if d.is_dir()):
                mp3s = sorted(album_dir.glob("*.mp3"))
                if not mp3s:
                    continue

                albums_checked += 1
                folder_issues = self._check_album(mp3s, artist_folder, album_dir.name, is_catchall)
                for kind, detail in folder_issues:
                    issues.append(
                        TagIssue(
                            artist_folder=artist_folder,
                            album_folder=album_dir.name,
                            folder_path=str(album_dir),
                            issue_kind=kind,
                            detail=detail,
                        )
                    )

        log.info(
            "integrity.scan.complete",
            albums_checked=albums_checked,
            issues_found=len(issues),
        )
        return issues

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _collect_folder_tags(
        self, mp3s: list[Path]
    ) -> tuple[set[str], set[str], set[str], list[str], int]:
        """Read ID3 tags from all MP3s in a folder.

        Returns a tuple of:
        - album_artists: unique TPE2 values
        - albums: unique TALB values
        - artists: unique TPE1 values
        - track_title_mismatches: detail strings for filename/TIT2 divergence
        - useless_title_count: number of tracks with generic/missing titles
        """
        album_artists: set[str] = set()
        albums: set[str] = set()
        artists: set[str] = set()
        track_title_mismatches: list[str] = []
        useless_title_count = 0

        for mp3 in mp3s:
            try:
                tags = ID3(str(mp3))
                if "TPE2" in tags:
                    album_artists.add(str(tags["TPE2"]))
                if "TPE1" in tags:
                    artists.add(str(tags["TPE1"]))
                if "TALB" in tags:
                    albums.add(str(tags["TALB"]))

                tag_title = str(tags["TIT2"]) if "TIT2" in tags else ""

                if _is_useless_title(tag_title):
                    useless_title_count += 1
                elif tag_title:
                    name_title = _TRACK_PREFIX_RE.sub("", mp3.stem).strip()
                    if name_title:
                        score = fuzz.token_sort_ratio(name_title.lower(), tag_title.lower())
                        if score < self._threshold:
                            track_title_mismatches.append(
                                f"TrackTitle: filename suggests {name_title!r} "
                                f"but TIT2={tag_title!r} (score={int(score)}) "
                                f"[{mp3.name}]"
                            )
            except ID3NoHeaderError:
                log.debug("integrity.scan.no_id3", file=str(mp3))
            except Exception:
                log.warning("integrity.scan.read_error", file=str(mp3), exc_info=True)

        return album_artists, albums, artists, track_title_mismatches, useless_title_count

    def _check_album_consistency(
        self,
        album_artists: set[str],
        albums: set[str],
    ) -> list[tuple[IssueKind, str]]:
        """Detect inconsistent tags across tracks within the same folder."""
        reasons: list[tuple[IssueKind, str]] = []
        if len(album_artists) > 1:
            reasons.append(
                (
                    IssueKind.INCONSISTENT_ALBUM_ARTIST,
                    f"Inconsistent AlbumArtist across tracks: {sorted(album_artists)}",
                )
            )
        if len(albums) > 1:
            reasons.append(
                (
                    IssueKind.INCONSISTENT_ALBUM,
                    f"Inconsistent Album tag across tracks: {sorted(albums)}",
                )
            )
        return reasons

    def _check_folder_mismatches(
        self,
        album_artists: set[str],
        albums: set[str],
        artists: set[str],
        artist_folder: str,
        album_folder: str,
        is_catchall: bool,
    ) -> list[tuple[IssueKind, str]]:
        """Detect mismatches between ID3 tags and the folder-name hierarchy."""
        reasons: list[tuple[IssueKind, str]] = []

        if is_catchall:
            for aa in album_artists:
                if aa.lower() not in _VARIOUS_VALUES:
                    various_artists = {a for a in artists if a.lower() in _VARIOUS_VALUES}
                    if various_artists:
                        detail = (
                            f"CompilationArtist: TPE2={aa!r} but"
                            f" TPE1={various_artists!r}"
                            f" — Artist should match AlbumArtist"
                        )
                        reasons.append((IssueKind.COMPILATION_ARTIST, detail))
        else:
            for aa in album_artists:
                score = fuzz.token_sort_ratio(aa.lower(), artist_folder.lower())
                if score < self._threshold and aa.lower() not in _VARIOUS_VALUES:
                    detail = f"AlbumArtist={aa!r} vs folder={artist_folder!r} (score={score})"
                    reasons.append((IssueKind.ALBUM_ARTIST_MISMATCH, detail))

        for alb in albums:
            score = fuzz.token_sort_ratio(alb.lower(), album_folder.lower())
            if score < self._threshold:
                reasons.append(
                    (
                        IssueKind.ALBUM_MISMATCH,
                        f"Album tag={alb!r} vs folder={album_folder!r} (score={score})",
                    )
                )

        return reasons

    def _check_content_issues(
        self,
        mp3_count: int,
        useless_title_count: int,
        track_title_mismatches: list[str],
        has_other_issues: bool,
    ) -> list[tuple[IssueKind, str]]:
        """Detect content-level issues: all-untitled albums and title/filename divergence."""
        reasons: list[tuple[IssueKind, str]] = []

        if useless_title_count >= max(1, round(mp3_count * _UNTITLED_TRACK_RATIO)):
            reasons.append(
                (
                    IssueKind.ALL_UNTITLED,
                    f"AllUntitled: {useless_title_count}/{mp3_count} tracks have "
                    f"generic/missing titles",
                )
            )

        # Track title mismatches are only surfaced when the album has other issues.
        if has_other_issues:
            for detail in track_title_mismatches:
                reasons.append((IssueKind.TRACK_TITLE, detail))

        return reasons

    def _check_album(
        self,
        mp3s: list[Path],
        artist_folder: str,
        album_folder: str,
        is_catchall: bool,
    ) -> list[tuple[IssueKind, str]]:
        """Run all integrity checks for a single album folder."""
        album_artists, albums, artists, track_title_mismatches, useless_title_count = (
            self._collect_folder_tags(mp3s)
        )

        if not album_artists and not albums:
            return []

        reasons: list[tuple[IssueKind, str]] = []
        reasons.extend(
            self._check_folder_mismatches(
                album_artists, albums, artists, artist_folder, album_folder, is_catchall
            )
        )
        reasons.extend(self._check_album_consistency(album_artists, albums))
        reasons.extend(
            self._check_content_issues(
                len(mp3s), useless_title_count, track_title_mismatches, bool(reasons)
            )
        )
        return reasons
