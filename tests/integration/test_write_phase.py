"""Integration tests for the ID3 write phase using real MP3 files and SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from mutagen.id3 import ID3

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.writer.id3_writer import ID3Writer


def _make_mp3(path: Path, title: str = "Original") -> Path:
    """Write a minimal valid MP3 with an existing title tag."""
    from mutagen.id3 import TIT2

    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.save(str(path))
    return path


def _seed_db(
    tmp_path: Path, conn: sqlite3.Connection, mp3_path: Path
) -> tuple[AlbumRepository, TrackRepository, TrackRecord]:
    run_migrations(conn)
    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)

    with conn:
        album_repo.upsert(AlbumRecord(folder_path=str(tmp_path)))
    album = album_repo.get_by_folder_path(str(tmp_path))
    assert album is not None
    assert album.id

    record = TrackRecord(
        album_id=album.id,
        file_path=str(mp3_path),
        filename=mp3_path.name,
        track_number=1,
        title="Pretty Hate Machine",
        artist="Nine Inch Nails",
        album_artist="Nine Inch Nails",
        album_title="Pretty Hate Machine",
        year=1989,
        track_num="01/10",
        genre="Industrial",
        grouping="Origin:Cleveland, US | Gender:Male | Label:TVT Records",
        enrichment_status="found",
        written_status="pending",
    )
    with conn:
        track_repo.upsert(record)

    return album_repo, track_repo, track_repo.get_by_file_path(str(mp3_path))  # type: ignore[return-value]


@pytest.mark.integration
def test_write_phase_tags_written_correctly(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    mp3 = _make_mp3(tmp_path / "01.mp3")
    _, track_repo, _track = _seed_db(tmp_path, db_conn, mp3)

    writer = ID3Writer(track_repo)
    success, errors = writer.write_pending()

    assert success == 1
    assert errors == 0

    tags = ID3(str(mp3))
    assert str(tags["TIT2"]) == "Pretty Hate Machine"
    assert str(tags["TPE1"]) == "Nine Inch Nails"
    assert str(tags["TPE2"]) == "Nine Inch Nails"
    assert str(tags["TALB"]) == "Pretty Hate Machine"
    assert str(tags["TCON"]) == "Industrial"
    assert str(tags["TRCK"]) == "01/10"
    assert str(tags["TIT1"]) == "Origin:Cleveland, US | Gender:Male | Label:TVT Records"


@pytest.mark.integration
def test_write_phase_idempotent(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    """Running write_pending twice should not error or re-write on the second pass."""
    mp3 = _make_mp3(tmp_path / "01.mp3")
    _, track_repo, _ = _seed_db(tmp_path, db_conn, mp3)

    writer = ID3Writer(track_repo)
    writer.write_pending()
    success, errors = writer.write_pending()  # second pass — nothing pending

    assert success == 0
    assert errors == 0


@pytest.mark.integration
def test_write_phase_dry_run_leaves_file_unchanged(
    tmp_path: Path, db_conn: sqlite3.Connection
) -> None:
    mp3 = _make_mp3(tmp_path / "01.mp3", title="Original Title")
    _, track_repo, _ = _seed_db(tmp_path, db_conn, mp3)

    ID3Writer(track_repo, dry_run=True).write_pending()

    tags = ID3(str(mp3))
    assert str(tags["TIT2"]) == "Original Title"  # unchanged


@pytest.mark.integration
def test_write_phase_force_rewrites_done_tracks(
    tmp_path: Path, db_conn: sqlite3.Connection
) -> None:
    mp3 = _make_mp3(tmp_path / "01.mp3")
    _, track_repo, track = _seed_db(tmp_path, db_conn, mp3)

    ID3Writer(track_repo).write_pending()  # marks as done

    # Update title in DB and force re-write
    updated = track.model_copy(update={"title": "Forced New Title"})
    with track_repo._conn:
        track_repo.upsert(updated)

    success, _errors = ID3Writer(track_repo, force=True).write_pending()

    assert success == 1
    assert str(ID3(str(mp3))["TIT2"]) == "Forced New Title"
