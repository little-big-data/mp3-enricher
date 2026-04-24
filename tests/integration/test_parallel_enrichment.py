"""Integration tests for parallel enrichment and write phases."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.enricher.runner import EnrichmentRunner
from tagger.writer.id3_writer import ID3Writer


def _seed_albums(
    conn: sqlite3.Connection, folder_paths: list[str]
) -> tuple[AlbumRepository, list[AlbumRecord]]:
    run_migrations(conn)
    repo = AlbumRepository(conn)
    for path in folder_paths:
        with conn:
            repo.upsert(AlbumRecord(folder_path=path))
    albums = repo.get_pending()
    return repo, albums


def _seed_mp3_tracks(
    tmp_path: Path, conn: sqlite3.Connection, count: int
) -> tuple[TrackRepository, list[TrackRecord]]:
    """Create `count` minimal MP3 files and matching pending TrackRecords."""
    from mutagen.id3 import ID3, TIT2

    run_migrations(conn)
    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)

    with conn:
        album_repo.upsert(AlbumRecord(folder_path=str(tmp_path)))
    album = album_repo.get_by_folder_path(str(tmp_path))
    assert album is not None
    assert album.id is not None

    tracks = []
    for i in range(1, count + 1):
        mp3 = tmp_path / f"{i:02d}.mp3"
        mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Original"))
        tags.save(str(mp3))

        record = TrackRecord(
            album_id=album.id,
            file_path=str(mp3),
            filename=mp3.name,
            track_number=i,
            title=f"Track {i}",
            artist="Artist",
            album_artist="Artist",
            album_title="Album",
            year=2000,
            track_num=f"{i:02d}/{count}",
            genre="Electronic",
            enrichment_status="found",
            written_status="pending",
        )
        with conn:
            track_repo.upsert(record)
        tracks.append(track_repo.get_by_file_path(str(mp3)))  # type: ignore[arg-type]

    return track_repo, tracks  # type: ignore[return-value]


@pytest.mark.integration
def test_parallel_enrichment_runner_processes_all_albums(
    tmp_path: Path, db_conn: sqlite3.Connection
) -> None:
    """EnrichmentRunner processes every pending album, updating statuses in SQLite."""
    paths = [str(tmp_path / f"album{i}") for i in range(4)]
    album_repo, _albums = _seed_albums(db_conn, paths)

    pipeline = MagicMock()

    runner = EnrichmentRunner(pipeline, album_repo, workers=2)
    success, errors = runner.run_enrichment()

    assert success == 4
    assert errors == 0
    assert pipeline.enrich_album.call_count == 4


@pytest.mark.integration
def test_parallel_enrichment_runner_counts_errors(
    tmp_path: Path, db_conn: sqlite3.Connection
) -> None:
    """Errors on individual albums are counted without aborting the run."""
    from tagger.exceptions import EnrichmentError

    paths = [str(tmp_path / f"album{i}") for i in range(3)]
    album_repo, _albums = _seed_albums(db_conn, paths)

    pipeline = MagicMock()
    call_count = 0

    def enrich_side_effect(album: AlbumRecord) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise EnrichmentError("simulated failure")

    pipeline.enrich_album.side_effect = enrich_side_effect

    runner = EnrichmentRunner(pipeline, album_repo, workers=2)
    success, errors = runner.run_enrichment()

    assert success == 2
    assert errors == 1


@pytest.mark.integration
def test_parallel_write_phase_all_tracks_written(
    tmp_path: Path, db_conn: sqlite3.Connection
) -> None:
    """ID3Writer.write_pending with workers>1 writes all tracks without DB corruption."""
    track_repo, _ = _seed_mp3_tracks(tmp_path, db_conn, count=4)

    writer = ID3Writer(track_repo)
    success, errors = writer.write_pending(workers=4)

    assert success == 4
    assert errors == 0

    # Verify all tracks marked done in DB
    for track in track_repo.get_pending_write():
        pytest.fail(f"Track still pending after write: {track.file_path}")


@pytest.mark.integration
def test_parallel_write_is_thread_safe(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    """Concurrent DB writes from multiple threads do not raise errors."""
    track_repo, _ = _seed_mp3_tracks(tmp_path, db_conn, count=6)

    errors: list[Exception] = []

    def write_batch() -> None:
        try:
            writer = ID3Writer(track_repo, force=True)
            writer.write_pending(workers=3)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_batch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
