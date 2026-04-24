from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tagger.scanner.walker import find_album_dirs, find_mp3_files

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_find_mp3_files_basic(tmp_path: Path) -> None:
    """Tests finding MP3 files in a simple directory structure."""
    # Create some files
    (tmp_path / "song1.mp3").write_text("mp3 content")
    (tmp_path / "song2.MP3").write_text("mp3 content")
    (tmp_path / "readme.txt").write_text("not an mp3")

    mp3s = find_mp3_files(tmp_path)

    assert len(mp3s) == 2
    assert any(p.name == "song1.mp3" for p in mp3s)
    assert any(p.name == "song2.MP3" for p in mp3s)


def test_find_mp3_files_recursive(tmp_path: Path) -> None:
    """Tests finding MP3 files recursively."""
    album1 = tmp_path / "Album 1"
    album1.mkdir()
    (album1 / "track1.mp3").write_text("mp3 content")

    album2 = tmp_path / "Album 2"
    album2.mkdir()
    (album2 / "track2.mp3").write_text("mp3 content")

    mp3s = find_mp3_files(tmp_path)

    assert len(mp3s) == 2


def test_find_mp3_files_ignores_hidden(tmp_path: Path) -> None:
    """Tests that hidden files and directories are ignored."""
    (tmp_path / "visible.mp3").write_text("mp3 content")
    (tmp_path / ".hidden.mp3").write_text("mp3 content")

    hidden_dir = tmp_path / ".hidden_dir"
    hidden_dir.mkdir()
    (hidden_dir / "hidden_track.mp3").write_text("mp3 content")

    mp3s = find_mp3_files(tmp_path)

    assert len(mp3s) == 1
    assert mp3s[0].name == "visible.mp3"


def test_find_mp3_files_invalid_dir(tmp_path: Path) -> None:
    """Tests behavior with invalid directory."""
    non_existent = tmp_path / "non_existent"
    mp3s = find_mp3_files(non_existent)
    assert mp3s == []


def test_find_mp3_files_exception(tmp_path: Path, mocker: MockerFixture) -> None:
    """Tests error handling when rglob fails."""
    # Mock rglob to raise an exception
    mocker.patch.object(Path, "rglob", side_effect=Exception("rglob failed"))

    mp3s = find_mp3_files(tmp_path)
    assert mp3s == []


# ---------------------------------------------------------------------------
# find_album_dirs tests
# ---------------------------------------------------------------------------


def test_find_album_dirs_returns_subdirs_with_mp3s(tmp_path: Path) -> None:
    """Returns subdirectories that directly contain at least one MP3 file, sorted."""
    (tmp_path / "Album A").mkdir()
    (tmp_path / "Album A" / "01.mp3").write_bytes(b"")
    (tmp_path / "Album B").mkdir()
    (tmp_path / "Album B" / "01.mp3").write_bytes(b"")

    result = find_album_dirs(tmp_path)

    assert [d.name for d in result] == ["Album A", "Album B"]


def test_find_album_dirs_ignores_dirs_without_mp3s(tmp_path: Path) -> None:
    """Subdirectories with no MP3 files are excluded."""
    (tmp_path / "Album A").mkdir()
    (tmp_path / "Album A" / "01.mp3").write_bytes(b"")
    (tmp_path / "No Music").mkdir()
    (tmp_path / "No Music" / "cover.jpg").write_bytes(b"")

    result = find_album_dirs(tmp_path)

    assert [d.name for d in result] == ["Album A"]


def test_find_album_dirs_ignores_hidden_dirs(tmp_path: Path) -> None:
    """Directories starting with '.' are excluded."""
    (tmp_path / "Album").mkdir()
    (tmp_path / "Album" / "01.mp3").write_bytes(b"")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "01.mp3").write_bytes(b"")

    result = find_album_dirs(tmp_path)

    assert [d.name for d in result] == ["Album"]


def test_find_album_dirs_returns_empty_when_root_has_no_subdirs(tmp_path: Path) -> None:
    """Root containing only files (no subdirs) returns empty list."""
    (tmp_path / "01.mp3").write_bytes(b"")

    result = find_album_dirs(tmp_path)

    assert result == []


def test_find_album_dirs_returns_empty_for_nonexistent_dir(tmp_path: Path) -> None:
    """Non-existent root returns empty list."""
    result = find_album_dirs(tmp_path / "nonexistent")
    assert result == []
