from __future__ import annotations

import sqlite3

import pytest

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import run_migrations
from tagger.db.manual_review_repo import ManualReviewRepository
from tagger.db.models import AlbumRecord


@pytest.fixture
def repos(db_conn: sqlite3.Connection) -> tuple[AlbumRepository, ManualReviewRepository]:
    run_migrations(db_conn)
    return AlbumRepository(db_conn), ManualReviewRepository(db_conn)


@pytest.mark.unit
def test_add_manual_review(repos: tuple[AlbumRepository, ManualReviewRepository]) -> None:
    # Arrange
    album_repo, manual_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/1"))
    album = album_repo.get_by_folder_path("/path/1")

    # Act
    manual_repo.add(album.id, "No Discogs match")

    # Assert
    pending = manual_repo.get_pending()
    assert len(pending) == 1
    assert pending[0]["reason"] == "No Discogs match"
    assert pending[0]["folder_path"] == "/path/1"


@pytest.mark.unit
def test_resolve_manual_review(repos: tuple[AlbumRepository, ManualReviewRepository]) -> None:
    # Arrange
    album_repo, manual_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/1"))
    album = album_repo.get_by_folder_path("/path/1")
    manual_repo.add(album.id, "No match")

    # Act
    manual_repo.resolve(album.id, "http://discogs.com/release/123")

    # Assert
    pending = manual_repo.get_pending()
    assert len(pending) == 0
