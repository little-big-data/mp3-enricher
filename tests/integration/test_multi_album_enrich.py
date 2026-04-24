"""Integration test: enrich CLI command processes multiple album subfolders.

When called with an artist root directory (containing album subfolders rather
than MP3 files directly), enrich should discover each album subfolder and
process them all in sequence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_httpx import HTTPXMock

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import get_db_connection
from tagger.mp3_tagger import cli


def _make_mp3(path: Path, title: str, track_num: int) -> Path:
    """Write a minimal valid MP3 with ID3 title and track-number tags."""
    from mutagen.id3 import ID3, TIT2, TRCK

    frame = b"\xff\xfb\x18\xc0" + b"\x00" * 140
    path.write_bytes(frame * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TRCK(encoding=3, text=str(track_num)))
    tags.save(str(path))
    return path


def _register_no_results(httpx_mock: HTTPXMock, artist: str, album: str) -> None:
    """Register a Discogs search mock that returns no results."""
    artist_q = artist.replace(" ", "+")
    album_q = album.replace(" ", "+")
    httpx_mock.add_response(
        url=(
            f"https://api.discogs.com/database/search"
            f"?artist={artist_q}&release_title={album_q}&type=release"
        ),
        json={"results": []},
    )
    # Fallback search (empty artist)
    httpx_mock.add_response(
        url=(
            f"https://api.discogs.com/database/search?artist=&release_title={album_q}&type=release"
        ),
        json={"results": []},
    )


@pytest.mark.integration
def test_enrich_multi_album_processes_each_subfolder(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """enrich on an artist root discovers and processes each album subfolder."""
    db_path = tmp_path / "test.db"

    # --- Artist root with two album subfolders ---
    artist_root = tmp_path / "Chelsea Wolfe"
    artist_root.mkdir()

    album1_dir = artist_root / "Chelsea Wolfe - Abyss"
    album1_dir.mkdir()
    _make_mp3(album1_dir / "01.mp3", title="Carrion Flowers", track_num=1)
    _make_mp3(album1_dir / "02.mp3", title="Iron Moon", track_num=2)

    album2_dir = artist_root / "Chelsea Wolfe - Pain Is Beauty"
    album2_dir.mkdir()
    _make_mp3(album2_dir / "01.mp3", title="Feral Love", track_num=1)
    _make_mp3(album2_dir / "02.mp3", title="We Hit a Wall", track_num=2)

    # Discogs finds no match for either album (simplest mock)
    _register_no_results(httpx_mock, "Chelsea Wolfe", "Abyss")
    _register_no_results(httpx_mock, "Chelsea Wolfe", "Pain Is Beauty")

    # Wikipedia / MusicBrainz calls will raise TimeoutException (unregistered)
    # and are silently swallowed by the enrichment pipeline, so no extra mocks needed.

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "enrich",
            str(artist_root),
            "--db-path",
            str(db_path),
            "--token",
            "test_token",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    # Both albums should appear in the DB
    conn = get_db_connection(db_path)
    album_repo = AlbumRepository(conn)

    album1 = album_repo.get_by_folder_path(str(album1_dir))
    album2 = album_repo.get_by_folder_path(str(album2_dir))

    assert album1 is not None, "Abyss album not found in DB"
    assert album2 is not None, "Pain Is Beauty album not found in DB"
    assert album1.album_guess == "Abyss"
    assert album2.album_guess == "Pain Is Beauty"
    assert album1.artist_guess == "Chelsea Wolfe"
    assert album2.artist_guess == "Chelsea Wolfe"


@pytest.mark.integration
def test_enrich_single_album_folder_still_works(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """enrich on a folder with direct MP3 files (no subfolders) still works as before."""
    db_path = tmp_path / "test.db"

    album_dir = tmp_path / "Chelsea Wolfe - Abyss"
    album_dir.mkdir()
    _make_mp3(album_dir / "01.mp3", title="Carrion Flowers", track_num=1)

    _register_no_results(httpx_mock, "Chelsea Wolfe", "Abyss")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "enrich",
            str(album_dir),
            "--db-path",
            str(db_path),
            "--token",
            "test_token",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    conn = get_db_connection(db_path)
    album_repo = AlbumRepository(conn)
    saved = album_repo.get_by_folder_path(str(album_dir))
    assert saved is not None
    assert saved.album_guess == "Abyss"


# ---------------------------------------------------------------------------
# --starts-with filter tests
# ---------------------------------------------------------------------------


def _no_results_mocks(httpx_mock: HTTPXMock, artist: str, album: str) -> None:
    """Convenience alias so callers don't need to import _register_no_results."""
    _register_no_results(httpx_mock, artist, album)


@pytest.mark.integration
def test_starts_with_filters_flat_album_folders(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """--starts-with only processes top-level album folders whose name begins with that prefix."""
    db_path = tmp_path / "test.db"
    root = tmp_path / "Shared Music"
    root.mkdir()

    # Two albums starting with A, one starting with B
    for folder in ["ABBA - Gold", "Adele - 21", "Beatles - Abbey Road"]:
        d = root / folder
        d.mkdir()
        _make_mp3(d / "01.mp3", title="Track 1", track_num=1)

    # Only mock Discogs searches for the two A albums
    _no_results_mocks(httpx_mock, "ABBA", "Gold")
    _no_results_mocks(httpx_mock, "Adele", "21")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "enrich",
            str(root),
            "--db-path",
            str(db_path),
            "--token",
            "test_token",
            "--starts-with",
            "A",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    conn = get_db_connection(db_path)
    album_repo = AlbumRepository(conn)

    assert album_repo.get_by_folder_path(str(root / "ABBA - Gold")) is not None
    assert album_repo.get_by_folder_path(str(root / "Adele - 21")) is not None
    # Beatles should NOT have been processed
    assert album_repo.get_by_folder_path(str(root / "Beatles - Abbey Road")) is None


@pytest.mark.integration
def test_starts_with_filters_two_level_artist_folders(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """--starts-with works on artist/album two-level structures."""
    db_path = tmp_path / "test.db"
    root = tmp_path / "Shared Music"
    root.mkdir()

    # Two-level: artist folder -> album subfolder -> MP3s
    cw_dir = root / "Chelsea Wolfe"
    cw_dir.mkdir()
    abyss_dir = cw_dir / "Chelsea Wolfe - Abyss"
    abyss_dir.mkdir()
    _make_mp3(abyss_dir / "01.mp3", title="Carrion Flowers", track_num=1)

    beatles_dir = root / "Beatles"
    beatles_dir.mkdir()
    abbey_dir = beatles_dir / "Beatles - Abbey Road"
    abbey_dir.mkdir()
    _make_mp3(abbey_dir / "01.mp3", title="Come Together", track_num=1)

    # Only mock for Chelsea Wolfe (starts with C)
    _no_results_mocks(httpx_mock, "Chelsea Wolfe", "Abyss")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "enrich",
            str(root),
            "--db-path",
            str(db_path),
            "--token",
            "test_token",
            "--starts-with",
            "C",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    conn = get_db_connection(db_path)
    album_repo = AlbumRepository(conn)

    assert album_repo.get_by_folder_path(str(abyss_dir)) is not None
    # Beatles should NOT have been processed
    assert album_repo.get_by_folder_path(str(abbey_dir)) is None


@pytest.mark.integration
def test_starts_with_no_match_exits_cleanly(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """--starts-with exits cleanly when no subfolders match the prefix."""
    db_path = tmp_path / "test.db"
    root = tmp_path / "Shared Music"
    root.mkdir()
    d = root / "Beatles - Abbey Road"
    d.mkdir()
    _make_mp3(d / "01.mp3", title="Come Together", track_num=1)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "enrich",
            str(root),
            "--db-path",
            str(db_path),
            "--token",
            "test_token",
            "--starts-with",
            "Z",
        ],
    )

    assert result.exit_code == 0
    assert "No subfolders" in result.output
