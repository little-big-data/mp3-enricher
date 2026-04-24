"""Integration tests for the `fix` CLI command.

Verifies that `fix` re-enriches already-found albums (correcting album_artist
and origin data), resets written_status to 'pending', and honours --starts-with.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_httpx import HTTPXMock

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.mp3_tagger import cli

# ---------------------------------------------------------------------------
# Shared HTTP payloads
# ---------------------------------------------------------------------------

_RELEASE_PAYLOAD = {
    "id": 99001,
    "title": "Scientist Rids the World of the Evil Curse of the Vampires",
    "year": 1981,
    "artists": [{"name": "Scientist", "id": 5001}],
    "images": [],
    "tracklist": [
        {"position": "1", "title": "Plague of Zombies", "duration": "4:00"},
        {"position": "2", "title": "The Mummy's Shroud", "duration": "3:45"},
    ],
    "labels": [{"name": "Greensleeves", "id": 100}],
    "resource_url": "https://api.discogs.com/releases/99001",
}

_ARTIST_PAYLOAD = {
    "id": 5001,
    "name": "Scientist",
    "realname": "Hopeton Overton Brown",
    "profile": "Jamaican dub producer.",
    "resource_url": "https://api.discogs.com/artists/5001",
}

_MB_EMPTY = {"artists": []}


def _mock_standard_http(httpx_mock: HTTPXMock) -> None:
    """Register the standard HTTP mocks used by most fix-command tests."""
    httpx_mock.add_response(url="https://api.discogs.com/releases/99001", json=_RELEASE_PAYLOAD)
    httpx_mock.add_response(url="https://api.discogs.com/artists/5001", json=_ARTIST_PAYLOAD)
    httpx_mock.add_response(
        url="https://en.wikipedia.org/wiki/Scientist",
        text='<div id="mw-content-text"><p>Scientist is a dub producer.</p></div>',
    )
    # MusicBrainz: pipeline calls find_links + find_area (2 searches)
    httpx_mock.add_response(
        url="https://musicbrainz.org/ws/2/artist?query=Scientist&fmt=json&limit=5",
        json=_MB_EMPTY,
    )
    httpx_mock.add_response(
        url="https://musicbrainz.org/ws/2/artist?query=Scientist&fmt=json&limit=5",
        json=_MB_EMPTY,
    )


def _make_dummy_mp3(folder: Path, name: str = "01.mp3") -> Path:
    """Write a minimal valid MP3 frame so find_album_dirs can detect the folder."""
    path = folder / name
    path.write_bytes(b"\xff\xfb\x18\xc0" + b"\x00" * 140)
    return path


def _setup_db(
    db_path: Path,
    folder_path: str,
    discogs_release_id: int = 99001,
    written_status: str = "done",
) -> tuple[sqlite3.Connection, AlbumRecord, list[TrackRecord]]:
    """Create a DB with one already-enriched album and two written tracks."""
    conn = get_db_connection(db_path)
    run_migrations(conn)

    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)

    album = AlbumRecord(
        folder_path=folder_path,
        artist_guess="Scientist",
        album_guess="Scientist Rids the World",
        enrichment_status="found",
        discogs_release_id=discogs_release_id,
    )
    with conn:
        album_repo.upsert(album)
    saved_album = album_repo.get_by_folder_path(folder_path)
    assert saved_album is not None
    assert saved_album.id is not None

    tracks = [
        TrackRecord(
            album_id=saved_album.id,
            file_path=f"{folder_path}/0{i}.mp3",
            filename=f"0{i}.mp3",
            track_number=i,
            enrichment_status="found",
            written_status=written_status,
            # Old (wrong) album_artist set by previous run
            album_artist="Hopeton Overton Brown",
        )
        for i in range(1, 3)
    ]
    with conn:
        for t in tracks:
            track_repo.upsert(t)

    return conn, saved_album, tracks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_fix_updates_album_artist_in_db(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """fix command re-enriches album and corrects album_artist from realname to credited name."""
    folder = tmp_path / "Scientist - Rids the World"
    folder.mkdir()
    _make_dummy_mp3(folder)

    db_path = tmp_path / "test.db"
    conn, saved_album, _ = _setup_db(db_path, str(folder))
    conn.close()

    _mock_standard_http(httpx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fix", str(tmp_path), "--db-path", str(db_path), "--token", "fake_token"],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    conn2 = get_db_connection(db_path)
    track_repo = TrackRepository(conn2)
    tracks = track_repo.get_by_album(saved_album.id)  # type: ignore[arg-type]

    assert all(t.album_artist == "Scientist" for t in tracks), (
        f"Expected album_artist='Scientist', got {[t.album_artist for t in tracks]}"
    )


@pytest.mark.integration
def test_fix_resets_written_status_to_pending(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """fix command resets written_status to 'pending' for enriched tracks."""
    folder = tmp_path / "Scientist - Rids the World"
    folder.mkdir()
    _make_dummy_mp3(folder)

    db_path = tmp_path / "test.db"
    conn, saved_album, _ = _setup_db(db_path, str(folder), written_status="done")
    conn.close()

    _mock_standard_http(httpx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fix", str(tmp_path), "--db-path", str(db_path), "--token", "fake_token"],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    conn2 = get_db_connection(db_path)
    track_repo = TrackRepository(conn2)
    tracks = track_repo.get_by_album(saved_album.id)  # type: ignore[arg-type]

    assert all(t.written_status == "pending" for t in tracks), (
        f"Expected all written_status='pending', got {[t.written_status for t in tracks]}"
    )


@pytest.mark.integration
def test_fix_starts_with_filter_targets_matching_folders(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """--starts-with only re-enriches folders whose name starts with the given prefix."""
    folder_s = tmp_path / "Scientist - Rids the World"
    folder_s.mkdir()
    _make_dummy_mp3(folder_s)
    folder_n = tmp_path / "Nine Inch Nails - Pretty Hate Machine"
    folder_n.mkdir()
    _make_dummy_mp3(folder_n)

    db_path = tmp_path / "test.db"
    conn, album_s, _ = _setup_db(db_path, str(folder_s))

    # Album N is also found + written
    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)
    album_n_rec = AlbumRecord(
        folder_path=str(folder_n),
        artist_guess="Nine Inch Nails",
        album_guess="Pretty Hate Machine",
        enrichment_status="found",
        discogs_release_id=75544,
    )
    with conn:
        album_repo.upsert(album_n_rec)
    album_n = album_repo.get_by_folder_path(str(folder_n))
    assert album_n is not None
    assert album_n.id is not None
    with conn:
        track_repo.upsert(
            TrackRecord(
                album_id=album_n.id,
                file_path=f"{folder_n}/01.mp3",
                filename="01.mp3",
                enrichment_status="found",
                written_status="done",
            )
        )
    conn.close()

    _mock_standard_http(httpx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "fix",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--token",
            "fake_token",
            "--starts-with",
            "S",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    conn2 = get_db_connection(db_path)
    track_repo2 = TrackRepository(conn2)

    # Scientist tracks should be reset to pending
    s_tracks = track_repo2.get_by_album(album_s.id)  # type: ignore[arg-type]
    assert all(t.written_status == "pending" for t in s_tracks)

    # NIN tracks should still be 'done' — not touched by --starts-with S
    n_tracks = track_repo2.get_by_album(album_n.id)
    assert all(t.written_status == "done" for t in n_tracks)
