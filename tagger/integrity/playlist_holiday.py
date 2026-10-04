"""Tag an iTunes playlist's MP3s with a ``Holiday:`` grouping segment and audit their tags."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from mutagen import MutagenError
from mutagen.id3 import GRP1, ID3, TIT1
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from tagger.enricher.grouping import ensure_grouping_segment
from tagger.scanner.id3_reader import read_id3_tags
from tagger.writer.id3_writer import save_id3

if TYPE_CHECKING:
    from tagger.db.track_repo import TrackRepository
    from tagger.integrity.itunes_comparator import ItunesLibrary, ItunesTrack

log = structlog.get_logger(__name__)

_MISMATCH_SUFFIX = "_mismatch"
_ERROR_ISSUES = frozenset({"file_missing", "write_error"})


class PlaylistAuditRow(BaseModel):
    """One CSV row: a playlist track with at least one issue."""

    file_path: str
    itunes_name: str | None = None
    id3_title: str | None = None
    title_score: int | None = None
    itunes_artist: str | None = None
    id3_artist: str | None = None
    artist_score: int | None = None
    itunes_album: str | None = None
    id3_album: str | None = None
    album_score: int | None = None
    issues: list[str] = Field(default_factory=list)


class PlaylistAuditResult(BaseModel):
    """Outcome of one ``apply_holiday_and_audit`` run."""

    rows: list[PlaylistAuditRow] = Field(default_factory=list)
    tagged: int = 0
    already_tagged: int = 0
    mismatches: int = 0
    errors: int = 0
    skipped: int = 0


def _score(itunes_value: str | None, id3_value: str | None) -> int:
    """Case-insensitive token-set similarity; two blank values count as identical."""
    a = (itunes_value or "").strip().lower()
    b = (id3_value or "").strip().lower()
    if not a and not b:
        return 100
    return int(fuzz.token_set_ratio(a, b))


def _process_track(
    track: ItunesTrack,
    holiday: str,
    track_repo: TrackRepository,
    db_lock: threading.Lock,
    *,
    threshold: int,
    dry_run: bool,
    id3_version: int,
) -> tuple[PlaylistAuditRow | None, str]:
    """Read, compare and (unless dry run) tag one track.

    Returns the audit row (None when the track has no issue) and an outcome of
    ``tagged``, ``already_tagged``, ``not_mp3``, ``file_missing`` or ``write_error``.
    """
    path = Path(track.file_path)
    if path.suffix.lower() != ".mp3":
        # ID3 frames must never be written into other containers (e.g. .m4a).
        log.warning("playlist_holiday.not_mp3", file_path=track.file_path)
        row = PlaylistAuditRow(
            file_path=track.file_path,
            itunes_name=track.name,
            itunes_artist=track.artist,
            itunes_album=track.album,
            issues=["not_mp3"],
        )
        return row, "not_mp3"
    if not path.is_file():
        log.warning("playlist_holiday.file_missing", file_path=track.file_path)
        row = PlaylistAuditRow(
            file_path=track.file_path,
            itunes_name=track.name,
            itunes_artist=track.artist,
            itunes_album=track.album,
            issues=["file_missing"],
        )
        return row, "file_missing"

    tags = read_id3_tags(path)
    id3_title = tags.get("title")
    id3_artist = tags.get("artist")
    id3_album = tags.get("album")
    scores = {
        "title": _score(track.name, id3_title),
        "artist": _score(track.artist, id3_artist),
        "album": _score(track.album, id3_album),
    }
    issues = [f"{field}{_MISMATCH_SUFFIX}" for field, s in scores.items() if s < threshold]

    old_grouping = tags.get("grouping")
    new_grouping = ensure_grouping_segment(old_grouping, "Holiday", holiday)
    outcome = "already_tagged"
    if new_grouping != old_grouping:
        outcome = "tagged"
        if not dry_run:
            try:
                _write_grouping(path, new_grouping, id3_version)
            except (PermissionError, OSError, MutagenError) as exc:
                log.error("playlist_holiday.write_error", file_path=track.file_path, error=str(exc))
                issues.append("write_error")
                outcome = "write_error"

    if outcome != "write_error" and not dry_run:
        with db_lock:
            if not track_repo.update_grouping_by_file_path(track.file_path, new_grouping):
                log.info("playlist_holiday.not_in_db", file_path=track.file_path)

    if not issues:
        return None, outcome
    row = PlaylistAuditRow(
        file_path=track.file_path,
        itunes_name=track.name,
        id3_title=id3_title,
        title_score=scores["title"],
        itunes_artist=track.artist,
        id3_artist=id3_artist,
        artist_score=scores["artist"],
        itunes_album=track.album,
        id3_album=id3_album,
        album_score=scores["album"],
        issues=issues,
    )
    return row, outcome


def _write_grouping(path: Path, grouping: str, id3_version: int) -> None:
    """Set GRP1 and TIT1 to ``grouping``, preserving all other frames."""
    tags = ID3(str(path))
    tags.setall("GRP1", [GRP1(encoding=3, text=grouping)])
    tags.setall("TIT1", [TIT1(encoding=3, text=grouping)])
    save_id3(tags, path, id3_version)


def apply_holiday_and_audit(
    library: ItunesLibrary,
    playlist: str,
    holiday: str,
    track_repo: TrackRepository,
    *,
    threshold: int = 90,
    dry_run: bool = False,
    workers: int = 4,
    id3_version: str = "2.3",
) -> PlaylistAuditResult:
    """Ensure every MP3 in ``playlist`` carries ``Holiday:<holiday>`` and audit its tags.

    Raises ``PlaylistNotFoundError`` when the playlist path cannot be resolved.
    """
    tracks = library.playlist_tracks(playlist)
    version = 4 if id3_version == "2.4" else 3
    db_lock = threading.Lock()
    result = PlaylistAuditResult()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = pool.map(
            lambda t: _process_track(
                t,
                holiday,
                track_repo,
                db_lock,
                threshold=threshold,
                dry_run=dry_run,
                id3_version=version,
            ),
            tracks,
        )
        for row, outcome in outcomes:
            if outcome == "tagged":
                result.tagged += 1
            elif outcome == "already_tagged":
                result.already_tagged += 1
            elif outcome == "not_mp3":
                result.skipped += 1
            if row is None:
                continue
            result.rows.append(row)
            if any(i.endswith(_MISMATCH_SUFFIX) for i in row.issues):
                result.mismatches += 1
            if _ERROR_ISSUES.intersection(row.issues):
                result.errors += 1

    log.info(
        "playlist_holiday.done",
        playlist=playlist,
        tagged=result.tagged,
        already_tagged=result.already_tagged,
        mismatches=result.mismatches,
        errors=result.errors,
        skipped=result.skipped,
    )
    return result
