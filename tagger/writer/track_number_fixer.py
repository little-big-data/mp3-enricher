"""Walk an MP3 library and fix TRCK/TPOS tags from filename prefixes.

For every MP3 whose filename begins with a track-number prefix
(e.g. ``01 Song.mp3`` or ``2-01 Song.mp3``), this module compares the
parsed track/disc numbers against the existing ID3 TRCK/TPOS tags and
writes corrections where they differ.

Only TRCK and TPOS are touched.  All other frames are left unchanged
because ``mutagen`` preserves unspecified frames on ``save()``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog
from mutagen.id3 import ID3, TPOS, TRCK, ID3NoHeaderError

if TYPE_CHECKING:
    from collections.abc import Iterator

from tagger.enricher.formatter import parse_track_from_filename

log = structlog.get_logger(__name__)

# Return type for _fix_file: what happened to this file.
_Outcome = Literal["updated", "skipped", "error"]


def _parse_existing_trck(tags: ID3) -> tuple[int | None, str | None]:
    """Return ``(track_number, total_suffix)`` from the existing TRCK frame.

    For ``"3/12"`` returns ``(3, "/12")``.
    For ``"3"`` returns ``(3, None)``.
    For a missing or unparseable tag returns ``(None, None)``.
    """
    raw = str(tags["TRCK"]) if "TRCK" in tags else None
    if not raw:
        return None, None
    parts = raw.split("/", 1)
    try:
        track_num = int(parts[0])
    except ValueError:
        return None, None
    total_suffix = f"/{parts[1]}" if len(parts) == 2 else None
    return track_num, total_suffix


def _parse_existing_tpos(tags: ID3) -> int | None:
    """Return the disc number from the existing TPOS frame, or ``None``."""
    raw = str(tags["TPOS"]) if "TPOS" in tags else None
    if not raw:
        return None
    try:
        return int(raw.split("/")[0])
    except ValueError:
        return None


def _fix_file(path: Path, dry_run: bool) -> _Outcome:
    """Inspect one MP3 file and write TRCK/TPOS corrections if needed.

    Returns:
        ``"updated"``  — a change was (or would be, in dry-run) applied.
        ``"skipped"``  — the file had no track-number prefix, or tags already match.
        ``"error"``    — the file could not be read/written.
    """
    filename = path.name
    fn_track, fn_disc = parse_track_from_filename(filename)

    if fn_track is None:
        return "skipped"

    try:
        try:
            tags: ID3 = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()

        existing_track, total_suffix = _parse_existing_trck(tags)
        existing_disc = _parse_existing_tpos(tags)

        track_matches = existing_track == fn_track
        disc_matches = (fn_disc is None) or (existing_disc == fn_disc)

        if track_matches and disc_matches:
            return "skipped"

        log.debug(
            "track_fixer.fix",
            file=filename,
            fn_track=fn_track,
            fn_disc=fn_disc,
            existing_track=existing_track,
            existing_disc=existing_disc,
        )

        if not dry_run:
            new_trck = f"{fn_track}{total_suffix}" if total_suffix else str(fn_track)
            tags["TRCK"] = TRCK(encoding=3, text=new_trck)
            if fn_disc is not None:
                tags["TPOS"] = TPOS(encoding=3, text=str(fn_disc))
            tags.save(str(path))

        return "updated"

    except Exception as exc:
        log.warning("track_fixer.error", file=str(path), error=str(exc))
        return "error"


def _iter_mp3s(root: Path) -> Iterator[Path]:
    """Yield MP3 paths under *root* using os.walk (streams dir-by-dir, no pre-sorting)."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden directories in-place so os.walk doesn't descend into them.
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            if filename.lower().endswith(".mp3"):
                yield Path(dirpath) / filename


def fix_track_numbers(
    library_root: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """Walk *library_root* recursively and fix TRCK/TPOS tags from filename prefixes.

    Uses ``os.walk`` so files are streamed directory-by-directory rather than
    pre-collected — this is significantly faster on large network (SMB) shares
    where enumerating all files upfront would stall for minutes.

    Returns a summary dict::

        {"checked": N, "updated": N, "skipped": N, "errors": N}
    """
    counts: dict[str, int] = {"checked": 0, "updated": 0, "skipped": 0, "errors": 0}

    for mp3_path in _iter_mp3s(library_root):
        counts["checked"] += 1
        outcome = _fix_file(mp3_path, dry_run=dry_run)
        if outcome == "updated":
            counts["updated"] += 1
        elif outcome == "skipped":
            counts["skipped"] += 1
        else:
            counts["errors"] += 1
        if counts["checked"] % 500 == 0:
            log.info(
                "track_fixer.progress",
                checked=counts["checked"],
                updated=counts["updated"],
                errors=counts["errors"],
            )

    return counts
