"""ID3 tag writer — writes enriched track data to MP3 files via mutagen."""

from __future__ import annotations

import contextlib
import mimetypes
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog
from alive_progress import alive_bar
from mutagen import MutagenError
from mutagen.id3 import (
    APIC,
    GRP1,
    ID3,
    TALB,
    TCMP,
    TCON,
    TDRC,
    TIT1,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    ID3NoHeaderError,
)

from tagger.enricher.formatter import normalize_artist, normalize_title
from tagger.exceptions import FileProcessError

if TYPE_CHECKING:
    from tagger.db.models import TrackRecord
    from tagger.db.track_repo import TrackRepository

log = structlog.get_logger(__name__)


class ID3Writer:
    """Writes enriched ID3 tags from the database to MP3 files."""

    def __init__(
        self,
        track_repo: TrackRepository,
        id3_version: Literal["2.3", "2.4"] = "2.3",
        dry_run: bool = False,
        force: bool = False,
        show_progress: bool = False,
    ) -> None:
        self._track_repo = track_repo
        self._id3_version = id3_version
        self._dry_run = dry_run
        self._force = force
        self._show_progress = show_progress
        self._db_lock = threading.Lock()

    def write_track(self, track: TrackRecord) -> bool:
        """Write ID3 tags for a single track.

        Returns True if tags were (or would be, in dry-run) written, False if skipped.
        Raises FileProcessError on I/O failure (and marks the DB record as 'error').
        """
        if track.enrichment_status != "found":
            log.debug("writer.skip_not_enriched", file_path=track.file_path)
            return False

        if track.written_status == "done" and not self._force:
            log.debug("writer.skip_already_written", file_path=track.file_path)
            return False

        if self._dry_run:
            log.info("writer.dry_run", file_path=track.file_path)
            return True

        try:
            tags = self._load_or_create_tags(track.file_path)
            self._apply_tags(tags, track)
            self._save_tags(tags, track.file_path)
        except (PermissionError, OSError, MutagenError) as exc:
            log.error("writer.io_error", file_path=track.file_path, error=str(exc))
            if track.id is None:
                raise ValueError(f"track.id must be set before writing: {track!r}") from exc
            with self._db_lock:
                self._track_repo.update_written_status(track.id, "error")
            raise FileProcessError(str(exc)) from exc

        if track.id is None:
            raise ValueError(f"track.id must be set before writing: {track!r}")
        with self._db_lock:
            self._track_repo.update_written_status(track.id, "done")

        log.info("writer.written", file_path=track.file_path)
        return True

    def write_pending(self, workers: int = 1, folder_prefix: str | None = None) -> tuple[int, int]:
        """Write all pending (or all, if force) tracks and return (success, error) counts.

        Args:
            workers: number of parallel threads.  1 (default) writes sequentially.
            folder_prefix: if given, only write tracks whose file_path starts with this string.
        """
        tracks = self._track_repo.get_pending_write(force=self._force, folder_prefix=folder_prefix)
        if workers <= 1:
            return self._write_sequential(tracks)
        return self._write_parallel(tracks, workers)

    def _write_sequential(self, tracks: list[TrackRecord]) -> tuple[int, int]:
        success = 0
        errors = 0
        with alive_bar(len(tracks), title="Writing tags", disable=not self._show_progress) as bar:
            for track in tracks:
                try:
                    if self.write_track(track):
                        success += 1
                except FileProcessError:
                    errors += 1
                finally:
                    bar()
        return success, errors

    def _write_parallel(self, tracks: list[TrackRecord], workers: int) -> tuple[int, int]:
        success = 0
        errors = 0
        with (
            alive_bar(len(tracks), title="Writing tags", disable=not self._show_progress) as bar,
            ThreadPoolExecutor(max_workers=workers) as pool,
        ):
            futures = {pool.submit(self.write_track, track): track for track in tracks}
            for future in as_completed(futures):
                try:
                    if future.result():
                        success += 1
                except FileProcessError:
                    errors += 1
                finally:
                    bar()
        return success, errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_or_create_tags(self, file_path: str) -> ID3:
        """Load existing ID3 tags or create a new tag header."""
        try:
            return ID3(file_path)
        except ID3NoHeaderError:
            return ID3()

    def _apply_tags(self, tags: ID3, track: TrackRecord) -> None:
        """Populate mutagen ID3 frame objects from the track record."""
        title = track.title or ""
        artist = track.artist or ""

        # Normalize: extract feat. from artist into title, then normalise title format
        if artist or title:
            artist, title = normalize_artist(artist, title)
            title = normalize_title(title)

        if title:
            tags["TIT2"] = TIT2(encoding=3, text=title)
        if artist:
            tags["TPE1"] = TPE1(encoding=3, text=artist)
        if track.compilation:
            tags["TCMP"] = TCMP(encoding=0, text="1")
        elif track.album_artist:
            tags["TPE2"] = TPE2(encoding=3, text=track.album_artist)
        if track.album_title:
            tags["TALB"] = TALB(encoding=3, text=track.album_title)
        if track.genre:
            tags["TCON"] = TCON(encoding=3, text=track.genre)
        if track.track_num:
            tags["TRCK"] = TRCK(encoding=3, text=track.track_num)
        if track.grouping:
            tags["TIT1"] = TIT1(encoding=3, text=track.grouping)
            tags["GRP1"] = GRP1(encoding=3, text=track.grouping)
        if track.year:
            tags["TDRC"] = TDRC(encoding=3, text=str(track.year))
        if track.disc_number:
            tags["TPOS"] = TPOS(encoding=3, text=str(track.disc_number))
        if track.art_path:
            self._embed_art(tags, track.art_path)

    def _embed_art(self, tags: ID3, art_path: str) -> None:
        """Embed album art as an APIC (front cover) frame, skipping on I/O error."""
        try:
            with open(art_path, "rb") as fh:
                data = fh.read()
            mime = mimetypes.guess_type(art_path)[0] or "image/jpeg"
            tags["APIC:"] = APIC(encoding=0, mime=mime, type=3, desc="", data=data)
        except OSError as exc:
            log.warning("writer.art_missing", art_path=art_path, error=str(exc))

    def _save_tags(self, tags: ID3, file_path: str) -> None:
        """Save tags to disk at the configured ID3 version.

        Falls back to a copy-locally-then-replace strategy when the direct
        save raises OSError (e.g. errno 22 EINVAL on Windows SMB shares,
        where mutagen's insert_bytes fails due to seek restrictions).
        """
        v2_version = 4 if self._id3_version == "2.4" else 3
        try:
            tags.save(file_path, v2_version=v2_version)
        except OSError:
            self._save_tags_via_temp(tags, file_path, v2_version)

    def _save_tags_via_temp(self, tags: ID3, file_path: str, v2_version: int) -> None:
        """Write tags to a local temp file, then replace the original.

        Avoids mutagen's in-place insert_bytes on network shares.
        """
        suffix = Path(file_path).suffix
        # Keep the temp file on the same drive/share to avoid a cross-device move
        # (critical on Windows SMB shares where shutil.move across drives is slow or fails).
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=Path(file_path).parent)
        try:
            os.close(tmp_fd)
            shutil.copy2(file_path, tmp_path)
            tags.save(tmp_path, v2_version=v2_version)
            shutil.move(tmp_path, file_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
