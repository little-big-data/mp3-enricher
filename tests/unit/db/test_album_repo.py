from __future__ import annotations

import sqlite3

import pytest

from tagger.db.album_repo import AlbumRepository
from tagger.db.models import AlbumRecord

# Test data constants
album_data_new = AlbumRecord(
    folder_path="/path/to/new_artist - new_album/01 - new_track.mp3",
    artist_guess="New Artist",
    album_guess="New Album",
    discogs_release_id=12345,
    discogs_url="http://example.com/release/12345",
    enrichment_status="found",
    written_status="done",
    notes="Initial import",
)

album_data_update = AlbumRecord(
    folder_path="/path/to/artist - album/01 - track.mp3",
    artist_guess="Artist",
    album_guess="Album",
    discogs_release_id=67890,
    discogs_url="http://example.com/release/67890",
    enrichment_status="found",
    written_status="done",
    notes="Updated info",
)

album_data_nones = AlbumRecord(
    folder_path="/path/to/artist - album/01 - track.mp3",
    artist_guess="Artist",
    album_guess="Album",
    discogs_release_id=None,
    discogs_url=None,
    enrichment_status="pending",
    written_status="pending",
    notes=None,
)


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    """Fixture to create a fresh in-memory SQLite database for each test."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_path TEXT NOT NULL UNIQUE,
            artist_guess TEXT,
            album_guess TEXT,
            discogs_release_id INTEGER,
            discogs_url TEXT,
            enrichment_status TEXT NOT NULL DEFAULT 'pending',
            written_status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT
        );
        """
    )
    conn.commit()
    yield conn
    conn.close()


def test_album_repo_upsert_new_record(in_memory_db: sqlite3.Connection) -> None:
    """Tests upserting a completely new album record into the database."""
    repo = AlbumRepository(in_memory_db)

    repo.upsert(album_data_new)
    in_memory_db.commit()

    cursor = in_memory_db.cursor()
    cursor.execute(
        "SELECT * FROM albums WHERE folder_path = ?",
        (album_data_new.folder_path,),
    )
    row = cursor.fetchone()

    assert row is not None
    retrieved_album = AlbumRecord(
        id=row[0],
        folder_path=row[1],
        artist_guess=row[2],
        album_guess=row[3],
        discogs_release_id=row[4],
        discogs_url=row[5],
        enrichment_status=row[6],
        written_status=row[7],
        notes=row[8],
    )
    assert retrieved_album.folder_path == album_data_new.folder_path
    assert retrieved_album.artist_guess == album_data_new.artist_guess
    assert retrieved_album.album_guess == album_data_new.album_guess


def test_album_repo_upsert_update_record(in_memory_db: sqlite3.Connection) -> None:
    """Tests updating an existing record."""
    repo = AlbumRepository(in_memory_db)
    repo.upsert(album_data_nones)
    in_memory_db.commit()

    repo.upsert(album_data_update)
    in_memory_db.commit()

    retrieved = repo.get_by_folder_path(album_data_nones.folder_path)
    assert retrieved is not None
    assert retrieved.artist_guess == album_data_update.artist_guess
    assert retrieved.notes == album_data_update.notes


def test_album_repo_get_by_folder_path_found(in_memory_db: sqlite3.Connection) -> None:
    """Tests retrieving an existing album record by its folder path."""
    repo = AlbumRepository(in_memory_db)
    repo.upsert(album_data_new)
    in_memory_db.commit()

    retrieved_data = repo.get_by_folder_path(album_data_new.folder_path)

    assert retrieved_data is not None
    assert retrieved_data.folder_path == album_data_new.folder_path
    assert retrieved_data.artist_guess == album_data_new.artist_guess


def test_album_repo_get_by_folder_path_not_found(in_memory_db: sqlite3.Connection) -> None:
    """Tests that retrieving a non-existent album record returns None."""
    repo = AlbumRepository(in_memory_db)
    retrieved_data = repo.get_by_folder_path("/non/existent/path.mp3")
    assert retrieved_data is None


def test_album_repo_get_pending(in_memory_db: sqlite3.Connection) -> None:
    """Tests retrieving albums with 'pending' enrichment status."""
    repo = AlbumRepository(in_memory_db)

    album1 = AlbumRecord(folder_path="/path/to/album1", enrichment_status="pending")
    album2 = AlbumRecord(folder_path="/path/to/album2", enrichment_status="found")
    album3 = AlbumRecord(folder_path="/path/to/album3", enrichment_status="pending")

    repo.upsert(album1)
    repo.upsert(album2)
    repo.upsert(album3)
    in_memory_db.commit()

    pending_records = repo.get_pending()

    assert len(pending_records) == 2
    pending_paths = {rec.folder_path for rec in pending_records}
    assert pending_paths == {"/path/to/album1", "/path/to/album3"}
