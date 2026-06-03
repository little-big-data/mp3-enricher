"""Compare MP3 ID3 tags against an iTunes Music Library XML ground-truth record."""

from __future__ import annotations

import plistlib
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

from tagger.scanner.id3_reader import read_id3_tags
from tagger.scanner.walker import find_mp3_files

log = structlog.get_logger(__name__)

# Issue string constants surfaced in AuditDiscrepancy.issues
ARTIST_MISMATCH = "artist_mismatch"
ALBUM_ARTIST_MISMATCH = "album_artist_mismatch"
ALBUM_MISMATCH = "album_mismatch"
MISSING_TRACK_NUMBER = "missing_track_number"
ITUNES_NOT_FOUND = "itunes_not_found"


class AuditDiscrepancy(BaseModel):
    """One row in the iTunes audit report — emitted only when issues exist."""

    model_config = ConfigDict(from_attributes=True)

    file_path: str
    artist_folder: str
    album_folder: str
    mp3_artist: str | None
    mp3_album_artist: str | None
    mp3_album: str | None
    mp3_track_number: int | None
    itunes_artist: str | None
    itunes_album_artist: str | None
    itunes_album: str | None
    itunes_track_number: int | None
    issues: list[str]
    artist_score: int | None
    album_artist_score: int | None
    album_score: int | None


class ItunesLibrary:
    """Parses an iTunes Music Library XML and supports path-based lookup."""

    def __init__(self, xml_path: Path) -> None:
        with xml_path.open("rb") as fh:
            plist: dict[str, Any] = plistlib.load(fh, fmt=plistlib.FMT_XML)

        tracks: dict[str, dict[str, Any]] = plist.get("Tracks", {})
        self._index: dict[str, dict[str, Any]] = {}

        for entry in tracks.values():
            loc: str = entry.get("Location", "")
            if not loc:
                continue
            # Decode file:// URL — iTunes uses file://localhost/M:/... on Windows
            no_scheme = re.sub(r"^file://(?:localhost/)?", "", loc)
            raw = urllib.parse.unquote(no_scheme)
            # Path() normalises separators; lower() gives case-insensitive lookup
            win_path = str(Path(raw)).lower()
            self._index[win_path] = entry

        log.debug("itunes_library.loaded", track_count=len(self._index))

    def lookup(self, mp3_path: Path) -> dict[str, Any] | None:
        """Return the iTunes track dict for *mp3_path*, or None if not indexed."""
        key = str(Path(str(mp3_path))).lower()
        return self._index.get(key)


def compare_library(
    library_path: Path,
    itunes_xml: Path,
    threshold: int = 75,
    workers: int = 4,
) -> list[AuditDiscrepancy]:
    """Walk *library_path*, read ID3 tags, and compare against the iTunes record.

    Returns one AuditDiscrepancy per MP3 that has at least one issue.
    """
    log.info(
        "itunes_comparator.start",
        library=str(library_path),
        xml=str(itunes_xml),
        threshold=threshold,
        workers=workers,
    )

    library = ItunesLibrary(itunes_xml)
    log.info("itunes_comparator.library_loaded", track_count=len(library._index))

    mp3_files = find_mp3_files(library_path)
    log.info("itunes_comparator.files_found", count=len(mp3_files))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        tag_results: list[dict[str, Any]] = list(pool.map(read_id3_tags, mp3_files))

    results: list[AuditDiscrepancy] = []

    for mp3_path, tags in zip(mp3_files, tag_results, strict=True):
        # Derive folder names — library structure: <root>/<artist>/<album>/<track>
        artist_folder = mp3_path.parent.parent.name
        album_folder = mp3_path.parent.name

        mp3_artist: str | None = tags.get("artist")
        mp3_album_artist: str | None = tags.get("album_artist")
        mp3_album: str | None = tags.get("album")
        mp3_track_number: int | None = tags.get("track_number")

        itunes_entry = library.lookup(mp3_path)
        issues: list[str] = []

        if itunes_entry is None:
            issues.append(ITUNES_NOT_FOUND)
            if mp3_track_number is None:
                issues.append(MISSING_TRACK_NUMBER)
            results.append(
                AuditDiscrepancy(
                    file_path=str(mp3_path),
                    artist_folder=artist_folder,
                    album_folder=album_folder,
                    mp3_artist=mp3_artist,
                    mp3_album_artist=mp3_album_artist,
                    mp3_album=mp3_album,
                    mp3_track_number=mp3_track_number,
                    itunes_artist=None,
                    itunes_album_artist=None,
                    itunes_album=None,
                    itunes_track_number=None,
                    issues=issues,
                    artist_score=None,
                    album_artist_score=None,
                    album_score=None,
                )
            )
            continue

        itunes_artist: str | None = itunes_entry.get("Artist")
        itunes_album_artist: str | None = itunes_entry.get("Album Artist")
        itunes_album: str | None = itunes_entry.get("Album")
        itunes_track_number: int | None = itunes_entry.get("Track Number")

        artist_score = int(
            fuzz.token_set_ratio((mp3_artist or "").lower(), (itunes_artist or "").lower())
        )
        album_artist_score = int(
            fuzz.token_set_ratio(
                (mp3_album_artist or "").lower(), (itunes_album_artist or "").lower()
            )
        )
        album_score = int(
            fuzz.token_set_ratio((mp3_album or "").lower(), (itunes_album or "").lower())
        )

        if artist_score < threshold:
            issues.append(ARTIST_MISMATCH)
        if album_artist_score < threshold:
            issues.append(ALBUM_ARTIST_MISMATCH)
        if album_score < threshold:
            issues.append(ALBUM_MISMATCH)
        if mp3_track_number is None:
            issues.append(MISSING_TRACK_NUMBER)

        if issues:
            results.append(
                AuditDiscrepancy(
                    file_path=str(mp3_path),
                    artist_folder=artist_folder,
                    album_folder=album_folder,
                    mp3_artist=mp3_artist,
                    mp3_album_artist=mp3_album_artist,
                    mp3_album=mp3_album,
                    mp3_track_number=mp3_track_number,
                    itunes_artist=itunes_artist,
                    itunes_album_artist=itunes_album_artist,
                    itunes_album=itunes_album,
                    itunes_track_number=itunes_track_number,
                    issues=issues,
                    artist_score=artist_score,
                    album_artist_score=album_artist_score,
                    album_score=album_score,
                )
            )

    log.info(
        "itunes_comparator.complete",
        mp3_count=len(mp3_files),
        discrepancy_count=len(results),
    )
    return results
