"""Integration tests for the fix_track_numbers pipeline.

Tests use real MP3 files in a temporary directory and verify that
TRCK/TPOS frames are correctly written or left unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import ID3, TPOS, TRCK

from tagger.writer.track_number_fixer import fix_track_numbers


def _make_mp3(
    directory: Path, filename: str, trck: str | None = None, tpos: str | None = None
) -> Path:
    """Write a minimal valid MP3 with optional TRCK/TPOS tags."""
    path = directory / filename
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    if trck is not None:
        tags.add(TRCK(encoding=3, text=trck))
    if tpos is not None:
        tags.add(TPOS(encoding=3, text=tpos))
    tags.save(str(path))
    return path


@pytest.mark.integration
def test_full_single_disc_album(tmp_path: Path) -> None:
    """A full album folder where TRCK is missing gets all tags written."""
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    for n in range(1, 6):
        _make_mp3(album, f"0{n} Track {n}.mp3")

    counts = fix_track_numbers(tmp_path, dry_run=False)

    assert counts["checked"] == 5
    assert counts["updated"] == 5
    assert counts["skipped"] == 0
    assert counts["errors"] == 0

    # Verify tags were written correctly
    for n in range(1, 6):
        tags = ID3(str(album / f"0{n} Track {n}.mp3"))
        assert str(tags["TRCK"]) == str(n)


@pytest.mark.integration
def test_full_multidisc_album(tmp_path: Path) -> None:
    """Multi-disc naming: TRCK gets within-disc number, TPOS gets disc number."""
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    _make_mp3(album, "1-01 First.mp3")
    _make_mp3(album, "1-02 Second.mp3")
    _make_mp3(album, "2-01 Third.mp3")
    _make_mp3(album, "2-02 Fourth.mp3")

    counts = fix_track_numbers(tmp_path, dry_run=False)

    assert counts["updated"] == 4

    t1 = ID3(str(album / "1-01 First.mp3"))
    assert str(t1["TRCK"]) == "1"
    assert str(t1["TPOS"]) == "1"

    t3 = ID3(str(album / "2-01 Third.mp3"))
    assert str(t3["TRCK"]) == "1"
    assert str(t3["TPOS"]) == "2"


@pytest.mark.integration
def test_already_tagged_files_not_rewritten(tmp_path: Path) -> None:
    """Files whose TRCK already matches the filename are skipped."""
    album = tmp_path / "Album"
    album.mkdir()
    _make_mp3(album, "01 Track.mp3", trck="1")
    _make_mp3(album, "02 Track.mp3", trck="2")

    counts = fix_track_numbers(tmp_path, dry_run=False)

    assert counts["updated"] == 0
    assert counts["skipped"] == 2


@pytest.mark.integration
def test_dry_run_leaves_files_unchanged(tmp_path: Path) -> None:
    """dry_run=True reports changes but writes nothing."""
    album = tmp_path / "Album"
    album.mkdir()
    path = _make_mp3(album, "05 Track.mp3")  # no TRCK tag

    counts = fix_track_numbers(tmp_path, dry_run=True)

    assert counts["updated"] == 1  # would have changed

    # File should be unchanged
    tags = ID3(str(path))
    assert "TRCK" not in tags


@pytest.mark.integration
def test_mixed_library(tmp_path: Path) -> None:
    """Library with tracks needing fixes, already-correct tracks, and no-prefix tracks."""
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    _make_mp3(album, "01 Fix Me.mp3")  # needs TRCK written
    _make_mp3(album, "02 Im Fine.mp3", trck="2")  # already correct
    _make_mp3(album, "No Number.mp3")  # skip — no prefix

    counts = fix_track_numbers(tmp_path, dry_run=False)

    assert counts["checked"] == 3
    assert counts["updated"] == 1
    assert counts["skipped"] == 2
    assert counts["errors"] == 0
