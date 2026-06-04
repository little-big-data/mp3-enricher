"""Restore MP3 ID3 tags from iTunes ground-truth data.

Reads discrepancies produced by compare_library() and writes the correct
iTunes values back to the MP3 files using mutagen. Only the affected frames
are overwritten — all other existing tags are preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from mutagen.id3 import ID3, TALB, TPE1, TPE2, TPOS, TRCK, ID3NoHeaderError
from pydantic import BaseModel, ConfigDict

from tagger.enricher.formatter import parse_track_from_filename
from tagger.integrity.itunes_comparator import (
    ALBUM_ARTIST_MISMATCH,
    ALBUM_MISMATCH,
    ARTIST_MISMATCH,
    ITUNES_NOT_FOUND,
    MISSING_TRACK_NUMBER,
    AuditDiscrepancy,
    compare_library,
)

log = structlog.get_logger(__name__)


class RestoreError(Exception):
    """Raised when a tag write fails for a specific file."""


class RestoreResult(BaseModel):
    """One row in the restore report — emitted for every file acted on."""

    model_config = ConfigDict(from_attributes=True)

    file_path: str
    artist_folder: str
    album_folder: str
    fields_restored: list[str]
    old_artist: str | None
    new_artist: str | None
    old_album_artist: str | None
    new_album_artist: str | None
    old_album: str | None
    new_album: str | None
    old_track_number: int | None
    new_track_number: int | None
    dry_run: bool


def _write_frames(path: Path, frames: dict[str, Any], dry_run: bool) -> None:
    """Write *frames* to *path* in place, leaving all other tags untouched."""
    if dry_run:
        return

    try:
        try:
            tags: ID3 = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()

        for frame_id, value in frames.items():
            if frame_id == "TPE1":
                tags["TPE1"] = TPE1(encoding=3, text=value)
            elif frame_id == "TPE2":
                tags["TPE2"] = TPE2(encoding=3, text=value)
            elif frame_id == "TALB":
                tags["TALB"] = TALB(encoding=3, text=value)
            elif frame_id == "TRCK":
                tags["TRCK"] = TRCK(encoding=3, text=value)
            elif frame_id == "TPOS":
                tags["TPOS"] = TPOS(encoding=3, text=value)

        tags.save(str(path), v2_version=3)

    except (PermissionError, OSError) as exc:
        log.warning("itunes_restorer.write_failed", file=str(path), error=str(exc))
        raise RestoreError(str(path)) from exc


def _resolve_track_number(
    d: AuditDiscrepancy,
) -> tuple[int | None, int | None]:
    """Return (track_number, disc_number) to restore, or (None, None) if unresolvable."""
    if d.itunes_track_number is not None:
        return d.itunes_track_number, None

    fn_track, fn_disc = parse_track_from_filename(Path(d.file_path).name)
    return fn_track, fn_disc


def restore_from_itunes(
    itunes_xml: Path,
    library_path: Path,
    threshold: int = 75,
    workers: int = 4,
    dry_run: bool = False,
) -> list[RestoreResult]:
    """Compare library against iTunes and write correct tag values to MP3 files.

    Returns one RestoreResult per file where at least one field was (or would
    be) restored. Files matching iTunes or flagged only as itunes_not_found
    are skipped entirely.
    """
    log.info(
        "itunes_restorer.start",
        library=str(library_path),
        xml=str(itunes_xml),
        dry_run=dry_run,
    )

    discrepancies = compare_library(
        library_path=library_path,
        itunes_xml=itunes_xml,
        threshold=threshold,
        workers=workers,
    )

    results: list[RestoreResult] = []

    for d in discrepancies:
        if ITUNES_NOT_FOUND in d.issues:
            continue

        frames: dict[str, Any] = {}
        fields_restored: list[str] = []
        new_track_number: int | None = None

        if ARTIST_MISMATCH in d.issues and d.itunes_artist is not None:
            frames["TPE1"] = d.itunes_artist
            fields_restored.append("artist")

        if ALBUM_ARTIST_MISMATCH in d.issues and d.itunes_album_artist is not None:
            frames["TPE2"] = d.itunes_album_artist
            fields_restored.append("album_artist")

        if ALBUM_MISMATCH in d.issues and d.itunes_album is not None:
            frames["TALB"] = d.itunes_album
            fields_restored.append("album")

        if MISSING_TRACK_NUMBER in d.issues:
            track_num, disc_num = _resolve_track_number(d)
            if track_num is not None:
                frames["TRCK"] = str(track_num)
                if disc_num is not None and disc_num > 1:
                    frames["TPOS"] = str(disc_num)
                fields_restored.append("track_number")
                new_track_number = track_num

        if not fields_restored:
            continue

        try:
            if not dry_run:
                _write_frames(Path(d.file_path), frames, dry_run=False)
        except RestoreError:
            log.error("itunes_restorer.skipped_on_error", file=d.file_path)
            continue

        results.append(
            RestoreResult(
                file_path=d.file_path,
                artist_folder=d.artist_folder,
                album_folder=d.album_folder,
                fields_restored=fields_restored,
                old_artist=d.mp3_artist if "artist" in fields_restored else None,
                new_artist=d.itunes_artist if "artist" in fields_restored else None,
                old_album_artist=d.mp3_album_artist if "album_artist" in fields_restored else None,
                new_album_artist=(
                    d.itunes_album_artist if "album_artist" in fields_restored else None
                ),
                old_album=d.mp3_album if "album" in fields_restored else None,
                new_album=d.itunes_album if "album" in fields_restored else None,
                old_track_number=(
                    d.mp3_track_number if "track_number" in fields_restored else None
                ),
                new_track_number=new_track_number,
                dry_run=dry_run,
            )
        )

    log.info(
        "itunes_restorer.complete",
        restored=len(results),
        dry_run=dry_run,
    )
    return results
