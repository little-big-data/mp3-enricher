"""Integration tests for the scan phase: filesystem walk, ID3 read, and DB seed."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TRCK

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.scanner.folder_parser import parse_folder_names
from tagger.scanner.id3_reader import read_id3_tags
from tagger.scanner.walker import find_mp3_files


def _make_mp3(path: Path, title: str = "Track", artist: str = "Artist") -> Path:
    """Write a minimal valid MP3 with basic ID3 tags.

    Uses a 32kbps/32000Hz mono MPEG1 Layer3 frame sequence that mutagen's
    MP3() parser can sync to (unlike the shorter stub used in unit tests).
    """
    # Four 144-byte MPEG1 Layer3 frames (32kbps, 32000Hz, mono)
    frame = b"\xff\xfb\x18\xc0" + b"\x00" * 140
    path.write_bytes(frame * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text="Album"))
    tags.add(TRCK(encoding=3, text="1/10"))
    tags.save(str(path))
    return path


@pytest.mark.integration
def test_find_mp3_files_discovers_all_mp3s(tmp_path: Path) -> None:
    """find_mp3_files returns all .mp3 files under the root, sorted."""
    album_dir = tmp_path / "Nine Inch Nails - Pretty Hate Machine"
    album_dir.mkdir()
    _make_mp3(album_dir / "01 - Head Like A Hole.mp3")
    _make_mp3(album_dir / "02 - Terrible Lie.mp3")
    (album_dir / "cover.jpg").write_bytes(b"image")  # non-MP3 should be skipped

    found = find_mp3_files(tmp_path)

    assert len(found) == 2
    assert all(p.suffix.lower() == ".mp3" for p in found)
    assert found == sorted(found)


@pytest.mark.integration
def test_find_mp3_files_ignores_hidden_directories(tmp_path: Path) -> None:
    """find_mp3_files skips files inside hidden directories."""
    visible = tmp_path / "Artist - Album"
    visible.mkdir()
    _make_mp3(visible / "01.mp3")

    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    _make_mp3(hidden / "02.mp3")

    found = find_mp3_files(tmp_path)
    assert len(found) == 1
    assert found[0].parent.name == "Artist - Album"


@pytest.mark.integration
def test_find_mp3_files_empty_directory(tmp_path: Path) -> None:
    assert find_mp3_files(tmp_path) == []


@pytest.mark.integration
def test_find_mp3_files_nonexistent_directory(tmp_path: Path) -> None:
    assert find_mp3_files(tmp_path / "does_not_exist") == []


@pytest.mark.integration
def test_parse_folder_names_artist_dash_album(tmp_path: Path) -> None:
    """'Artist - Album' folder yields correct guesses."""
    folder = tmp_path / "Nine Inch Nails - Pretty Hate Machine"
    folder.mkdir()

    result = parse_folder_names(folder)
    assert result["artist_guess"] == "Nine Inch Nails"
    assert result["album_guess"] == "Pretty Hate Machine"


@pytest.mark.integration
def test_parse_folder_names_no_separator(tmp_path: Path) -> None:
    """Folder with no separator falls back to using folder name as album guess."""
    folder = tmp_path / "PrettyHateMachine"
    folder.mkdir()

    result = parse_folder_names(folder)
    assert result["album_guess"] == "PrettyHateMachine"


@pytest.mark.integration
def test_read_id3_tags_returns_expected_fields(tmp_path: Path) -> None:
    """read_id3_tags extracts title, artist, album, and track number."""
    mp3 = _make_mp3(tmp_path / "track.mp3", title="Head Like A Hole", artist="Nine Inch Nails")

    tags = read_id3_tags(mp3)

    assert tags["title"] == "Head Like A Hole"
    assert tags["artist"] == "Nine Inch Nails"
    assert tags["album"] == "Album"
    assert tags["track_number"] == 1


@pytest.mark.integration
def test_read_id3_tags_missing_tags(tmp_path: Path) -> None:
    """read_id3_tags returns empty dict for a file with no tags."""
    mp3 = tmp_path / "bare.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)

    tags = read_id3_tags(mp3)
    assert tags == {}


@pytest.mark.integration
def test_scan_phase_seeds_db(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    """Full scan phase: find MP3s, parse folder, read tags, insert into SQLite."""
    run_migrations(db_conn)
    album_dir = tmp_path / "Nine Inch Nails - Pretty Hate Machine"
    album_dir.mkdir()
    _make_mp3(album_dir / "01 - Head Like A Hole.mp3", title="Head Like A Hole")
    _make_mp3(album_dir / "02 - Terrible Lie.mp3", title="Terrible Lie")

    album_repo = AlbumRepository(db_conn)
    track_repo = TrackRepository(db_conn)

    # Scan
    mp3_files = find_mp3_files(tmp_path)
    guesses = parse_folder_names(album_dir)

    album = AlbumRecord(
        folder_path=str(album_dir),
        artist_guess=guesses.get("artist_guess"),
        album_guess=guesses.get("album_guess"),
    )
    with db_conn:
        album_repo.upsert(album)
    saved_album = album_repo.get_by_folder_path(str(album_dir))
    assert saved_album is not None
    assert saved_album.artist_guess == "Nine Inch Nails"
    assert saved_album.album_guess == "Pretty Hate Machine"
    album_id = saved_album.id
    assert album_id is not None

    for mp3 in mp3_files:
        id3 = read_id3_tags(mp3)
        record = TrackRecord(
            album_id=album_id,
            file_path=str(mp3),
            filename=mp3.name,
            track_number=id3.get("track_number"),
            existing_title=id3.get("title"),
            existing_artist=id3.get("artist"),
        )
        with db_conn:
            track_repo.upsert(record)

    tracks = track_repo.get_by_album(album_id)
    assert len(tracks) == 2
    titles = {t.existing_title for t in tracks}
    assert titles == {"Head Like A Hole", "Terrible Lie"}


@pytest.mark.integration
def test_scan_phase_idempotent(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    """Running the scan phase twice does not create duplicate DB records."""
    run_migrations(db_conn)
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()
    _make_mp3(album_dir / "01.mp3")

    album_repo = AlbumRepository(db_conn)
    track_repo = TrackRepository(db_conn)

    for _ in range(2):
        mp3_files = find_mp3_files(tmp_path)
        guesses = parse_folder_names(album_dir)
        album = AlbumRecord(
            folder_path=str(album_dir),
            artist_guess=guesses.get("artist_guess"),
            album_guess=guesses.get("album_guess"),
        )
        with db_conn:
            album_repo.upsert(album)
        saved = album_repo.get_by_folder_path(str(album_dir))
        assert saved is not None
        for mp3 in mp3_files:
            id3 = read_id3_tags(mp3)
            record = TrackRecord(
                album_id=saved.id,  # type: ignore[arg-type]
                file_path=str(mp3),
                filename=mp3.name,
                track_number=id3.get("track_number"),
                existing_title=id3.get("title"),
                existing_artist=id3.get("artist"),
            )
            with db_conn:
                track_repo.upsert(record)

    # Only one album and one track record should exist
    all_albums = album_repo.get_pending()
    assert len(all_albums) == 1
    saved_album = album_repo.get_by_folder_path(str(album_dir))
    assert saved_album is not None
    tracks = track_repo.get_by_album(saved_album.id)  # type: ignore[arg-type]
    assert len(tracks) == 1
