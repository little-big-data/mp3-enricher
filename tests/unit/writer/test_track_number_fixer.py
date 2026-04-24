"""Unit tests for tagger.writer.track_number_fixer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mutagen.id3 import ID3, TPOS, TRCK

from tagger.writer.track_number_fixer import _fix_file, fix_track_numbers


def _make_mp3(
    tmp_path: Path, filename: str, trck: str | None = None, tpos: str | None = None
) -> Path:
    """Write a minimal MP3 with optional TRCK/TPOS tags."""
    path = tmp_path / filename
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    if trck is not None:
        tags.add(TRCK(encoding=3, text=trck))
    if tpos is not None:
        tags.add(TPOS(encoding=3, text=tpos))
    tags.save(str(path))
    return path


class TestFixFile:
    """Unit tests for _fix_file — the per-file fix logic."""

    def test_no_prefix_in_filename_skips(self, tmp_path: Path) -> None:
        """Files without a track-number prefix are skipped."""
        path = _make_mp3(tmp_path, "Song Without Number.mp3")
        result = _fix_file(path, dry_run=False)
        assert result == "skipped"

    def test_vinyl_prefix_skips(self, tmp_path: Path) -> None:
        """Vinyl-style prefixes (A1, B2) are not parsed — file is skipped."""
        path = _make_mp3(tmp_path, "A1 Some Track.mp3")
        result = _fix_file(path, dry_run=False)
        assert result == "skipped"

    def test_missing_trck_is_written(self, tmp_path: Path) -> None:
        """When TRCK is absent but filename has a prefix, write the tag."""
        path = _make_mp3(tmp_path, "03 My Song.mp3", trck=None)
        result = _fix_file(path, dry_run=False)
        assert result == "updated"
        tags = ID3(str(path))
        assert str(tags["TRCK"]) == "3"

    def test_matching_trck_is_not_rewritten(self, tmp_path: Path) -> None:
        """When existing TRCK matches the filename, nothing is written."""
        path = _make_mp3(tmp_path, "03 My Song.mp3", trck="3")
        result = _fix_file(path, dry_run=False)
        assert result == "skipped"

    def test_wrong_trck_is_corrected(self, tmp_path: Path) -> None:
        """When existing TRCK differs from the filename, it is corrected."""
        path = _make_mp3(tmp_path, "05 My Song.mp3", trck="3")
        result = _fix_file(path, dry_run=False)
        assert result == "updated"
        tags = ID3(str(path))
        assert str(tags["TRCK"]) == "5"

    def test_trck_total_is_preserved(self, tmp_path: Path) -> None:
        """Existing '/N' total part of TRCK is preserved when correcting."""
        path = _make_mp3(tmp_path, "03 My Song.mp3", trck="7/12")
        result = _fix_file(path, dry_run=False)
        assert result == "updated"
        tags = ID3(str(path))
        assert str(tags["TRCK"]) == "3/12"

    def test_matching_trck_with_total_is_not_rewritten(self, tmp_path: Path) -> None:
        """'3/12' counts as matching filename track 3 — no write."""
        path = _make_mp3(tmp_path, "03 My Song.mp3", trck="3/12")
        result = _fix_file(path, dry_run=False)
        assert result == "skipped"

    def test_multidisc_writes_trck_and_tpos(self, tmp_path: Path) -> None:
        """Multi-disc filename writes both TRCK (within-disc) and TPOS (disc)."""
        path = _make_mp3(tmp_path, "2-01 Opening.mp3", trck=None, tpos=None)
        result = _fix_file(path, dry_run=False)
        assert result == "updated"
        tags = ID3(str(path))
        assert str(tags["TRCK"]) == "1"
        assert str(tags["TPOS"]) == "2"

    def test_multidisc_matching_tpos_not_rewritten(self, tmp_path: Path) -> None:
        """When both TRCK and TPOS already match the filename, nothing is written."""
        path = _make_mp3(tmp_path, "2-03 Song.mp3", trck="3", tpos="2")
        result = _fix_file(path, dry_run=False)
        assert result == "skipped"

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """In dry-run mode, tags are NOT written even when a change is needed."""
        path = _make_mp3(tmp_path, "05 Song.mp3", trck=None)
        result = _fix_file(path, dry_run=True)
        assert result == "updated"  # reports what would be done
        tags = ID3(str(path))
        assert "TRCK" not in tags  # file unchanged


class TestFixTrackNumbers:
    """Integration-level tests for fix_track_numbers — walks a directory tree."""

    def test_returns_counts(self, tmp_path: Path) -> None:
        """fix_track_numbers returns a dict with checked/updated/skipped/errors keys."""
        _make_mp3(tmp_path, "01 Track One.mp3", trck=None)
        _make_mp3(tmp_path, "02 Track Two.mp3", trck="2")
        _make_mp3(tmp_path, "Song No Number.mp3")

        counts = fix_track_numbers(tmp_path, dry_run=False)
        assert counts["checked"] == 3
        assert counts["updated"] == 1  # 01 Track One.mp3 — TRCK missing
        assert counts["skipped"] == 2  # 02 already correct + Song No Number
        assert counts["errors"] == 0

    def test_walks_subdirectories(self, tmp_path: Path) -> None:
        """Files in nested subdirectories are also processed."""
        sub = tmp_path / "Artist" / "Album"
        sub.mkdir(parents=True)
        _make_mp3(sub, "01 Track.mp3", trck=None)
        counts = fix_track_numbers(tmp_path, dry_run=False)
        assert counts["updated"] == 1

    def test_non_mp3_files_ignored(self, tmp_path: Path) -> None:
        """Non-MP3 files are not counted or touched."""
        (tmp_path / "cover.jpg").write_bytes(b"\xff\xd8\xff")
        (tmp_path / "info.txt").write_text("notes")
        _make_mp3(tmp_path, "01 Track.mp3", trck="1")
        counts = fix_track_numbers(tmp_path, dry_run=False)
        assert counts["checked"] == 1
        assert counts["updated"] == 0

    def test_unprocessable_file_counted_as_error(self, tmp_path: Path) -> None:
        """A file that raises during processing is counted as error, not a crash."""
        _make_mp3(tmp_path, "01 Bad.mp3", trck=None)
        with patch("tagger.writer.track_number_fixer.ID3", side_effect=OSError("disk error")):
            counts = fix_track_numbers(tmp_path, dry_run=False)
        assert counts["errors"] == 1
        assert counts["checked"] == 1
