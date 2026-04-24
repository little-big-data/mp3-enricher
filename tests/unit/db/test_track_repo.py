from __future__ import annotations

import sqlite3

import pytest

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository


@pytest.fixture
def repos(db_conn: sqlite3.Connection) -> tuple[AlbumRepository, TrackRepository]:
    run_migrations(db_conn)
    return AlbumRepository(db_conn), TrackRepository(db_conn)


@pytest.mark.unit
def test_upsert_track(repos: tuple[AlbumRepository, TrackRepository]) -> None:
    # Arrange
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/1"))
    album = album_repo.get_by_folder_path("/path/1")

    track = TrackRecord(
        album_id=album.id,
        file_path="/path/1/track.mp3",
        filename="track.mp3",
        existing_title="Old Title",
    )

    # Act
    track_repo.upsert(track)

    # Assert
    saved = track_repo.get_by_file_path("/path/1/track.mp3")
    assert saved is not None
    assert saved.existing_title == "Old Title"
    assert saved.album_id == album.id


@pytest.mark.unit
def test_delete_stale_removes_tracks_not_in_current_set(
    repos: tuple[AlbumRepository, TrackRepository],
) -> None:
    """Tracks whose file_path is not in the current scanned set are removed."""
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/1"))
    album = album_repo.get_by_folder_path("/path/1")

    track_repo.upsert(TrackRecord(album_id=album.id, file_path="/path/1/a.mp3", filename="a.mp3"))
    track_repo.upsert(TrackRecord(album_id=album.id, file_path="/path/1/b.mp3", filename="b.mp3"))
    track_repo.upsert(TrackRecord(album_id=album.id, file_path="/path/1/c.mp3", filename="c.mp3"))

    # Only a.mp3 and c.mp3 exist on disk now (b.mp3 was renamed/removed)
    track_repo.delete_stale(album.id, {"/path/1/a.mp3", "/path/1/c.mp3"})

    remaining = track_repo.get_by_album(album.id)
    assert len(remaining) == 2
    paths = {t.file_path for t in remaining}
    assert paths == {"/path/1/a.mp3", "/path/1/c.mp3"}


@pytest.mark.unit
def test_delete_stale_does_not_affect_other_albums(
    repos: tuple[AlbumRepository, TrackRepository],
) -> None:
    """delete_stale only removes tracks belonging to the given album_id."""
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/1"))
    album_repo.upsert(AlbumRecord(folder_path="/path/2"))
    album1 = album_repo.get_by_folder_path("/path/1")
    album2 = album_repo.get_by_folder_path("/path/2")

    track_repo.upsert(TrackRecord(album_id=album1.id, file_path="/p1/t.mp3", filename="t.mp3"))
    track_repo.upsert(TrackRecord(album_id=album2.id, file_path="/p2/t.mp3", filename="t.mp3"))

    # Stale for album1 only — album2's track must survive
    track_repo.delete_stale(album1.id, set())

    assert track_repo.get_by_album(album1.id) == []
    assert len(track_repo.get_by_album(album2.id)) == 1


@pytest.mark.unit
def test_get_tracks_by_album(repos: tuple[AlbumRepository, TrackRepository]) -> None:
    # Arrange
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/1"))
    album = album_repo.get_by_folder_path("/path/1")

    track_repo.upsert(TrackRecord(album_id=album.id, file_path="t1", filename="t1"))
    track_repo.upsert(TrackRecord(album_id=album.id, file_path="t2", filename="t2"))

    # Act
    tracks = track_repo.get_by_album(album.id)

    # Assert
    assert len(tracks) == 2


@pytest.mark.unit
def test_reset_written_status_for_album(repos: tuple[AlbumRepository, TrackRepository]) -> None:
    """reset_written_status_for_album sets written_status='pending' for enriched tracks only."""
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/1"))
    album = album_repo.get_by_folder_path("/path/1")

    assert album.id is not None

    # One enriched + written track, one enriched + pending, one not enriched
    track_repo.upsert(
        TrackRecord(
            album_id=album.id,
            file_path="t1",
            filename="t1",
            enrichment_status="found",
            written_status="done",
        )
    )
    track_repo.upsert(
        TrackRecord(
            album_id=album.id,
            file_path="t2",
            filename="t2",
            enrichment_status="found",
            written_status="pending",
        )
    )
    track_repo.upsert(
        TrackRecord(
            album_id=album.id,
            file_path="t3",
            filename="t3",
            enrichment_status="not_found",
            written_status="done",
        )
    )

    with track_repo._conn:
        track_repo.reset_written_status_for_album(album.id)

    tracks = {t.file_path: t for t in track_repo.get_by_album(album.id)}
    # Enriched tracks reset to pending
    assert tracks["t1"].written_status == "pending"
    assert tracks["t2"].written_status == "pending"
    # Not-enriched track unchanged
    assert tracks["t3"].written_status == "done"


@pytest.mark.unit
def test_get_pending_write_folder_prefix_filters_by_path(
    repos: tuple[AlbumRepository, TrackRepository],
) -> None:
    """get_pending_write with folder_prefix returns only tracks whose file_path starts with it."""
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/music/ArtistA/Album1"))
    album_repo.upsert(AlbumRecord(folder_path="/music/ArtistB/Album2"))
    a1 = album_repo.get_by_folder_path("/music/ArtistA/Album1")
    a2 = album_repo.get_by_folder_path("/music/ArtistB/Album2")

    track_repo.upsert(
        TrackRecord(
            album_id=a1.id,
            file_path="/music/ArtistA/Album1/01 Track.mp3",
            filename="01 Track.mp3",
            enrichment_status="found",
            written_status="pending",
        )
    )
    track_repo.upsert(
        TrackRecord(
            album_id=a2.id,
            file_path="/music/ArtistB/Album2/01 Track.mp3",
            filename="01 Track.mp3",
            enrichment_status="found",
            written_status="pending",
        )
    )

    results = track_repo.get_pending_write(folder_prefix="/music/ArtistA")
    assert len(results) == 1
    assert results[0].file_path == "/music/ArtistA/Album1/01 Track.mp3"


@pytest.mark.unit
def test_get_pending_write_folder_prefix_with_force(
    repos: tuple[AlbumRepository, TrackRepository],
) -> None:
    """folder_prefix combines with force=True to include already-written tracks."""
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/music/ArtistA/Album1"))
    a1 = album_repo.get_by_folder_path("/music/ArtistA/Album1")

    track_repo.upsert(
        TrackRecord(
            album_id=a1.id,
            file_path="/music/ArtistA/Album1/01 Track.mp3",
            filename="01 Track.mp3",
            enrichment_status="found",
            written_status="done",  # already written
        )
    )

    # Without force: excluded (already written)
    assert track_repo.get_pending_write(folder_prefix="/music/ArtistA") == []
    # With force: included
    results = track_repo.get_pending_write(force=True, folder_prefix="/music/ArtistA")
    assert len(results) == 1


@pytest.mark.unit
def test_upsert_track_persists_disc_number(repos: tuple[AlbumRepository, TrackRepository]) -> None:
    """disc_number is persisted and retrieved correctly for multi-disc albums."""
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/multi"))
    album = album_repo.get_by_folder_path("/path/multi")

    track = TrackRecord(
        album_id=album.id,
        file_path="/path/multi/2-01 Track.mp3",
        filename="2-01 Track.mp3",
        disc_number=2,
        track_number=1,
    )
    with track_repo._conn:
        track_repo.upsert(track)

    saved = track_repo.get_by_file_path("/path/multi/2-01 Track.mp3")
    assert saved is not None
    assert saved.disc_number == 2


@pytest.mark.unit
def test_upsert_track_disc_number_defaults_to_none(
    repos: tuple[AlbumRepository, TrackRepository],
) -> None:
    """Single-disc tracks with no disc_number are stored and returned as None."""
    album_repo, track_repo = repos
    album_repo.upsert(AlbumRecord(folder_path="/path/single"))
    album = album_repo.get_by_folder_path("/path/single")

    track = TrackRecord(
        album_id=album.id,
        file_path="/path/single/01 Track.mp3",
        filename="01 Track.mp3",
    )
    with track_repo._conn:
        track_repo.upsert(track)

    saved = track_repo.get_by_file_path("/path/single/01 Track.mp3")
    assert saved is not None
    assert saved.disc_number is None
