from __future__ import annotations

import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import click
import structlog
from click.testing import CliRunner

# Mock implementations for scanner components
mock_find_mp3_files = MagicMock()
mock_parse_folder_names = MagicMock()
mock_read_id3_tags = MagicMock()
mock_db_connection = MagicMock()
mock_album_repo_upsert = MagicMock()


class MockScanner:
    def __init__(self) -> None:
        self.find_mp3_files = mock_find_mp3_files
        self.parse_folder_names = mock_parse_folder_names
        self.read_id3_tags = mock_read_id3_tags


class MockDB:
    def __enter__(self) -> MagicMock:
        return mock_db_connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        pass

    def commit(self) -> None:
        mock_db_connection.commit()


class MockAlbumRepository:
    def __init__(self, conn: MockDB) -> None:
        pass

    def upsert(self, album_data: dict[str, Any]) -> None:
        mock_album_repo_upsert(album_data)


# --- Mocking the CLI entry point ---
@click.group()
def cli() -> None:
    """CLI for mp3-enricher."""
    pass


@cli.command()
@click.option(
    "--library-root",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True),
    required=True,
    help="Root directory of the MP3 library.",
)
@click.option(
    "--working-dir",
    type=click.Path(file_okay=False, dir_okay=True, writable=True),
    default="tagger_workdir",
    help="Directory for temporary files and database.",
)
@click.option("--dry-run", is_flag=True, help="Simulate the scan without making changes.")
def scan(library_root: str, working_dir: str, dry_run: bool) -> None:
    """Scans the MP3 library and populates the database."""
    scanner = MockScanner()
    db_conn = MockDB()
    album_repo = MockAlbumRepository(db_conn)

    settings = MagicMock()
    settings.library_root = Path(library_root)
    settings.working_dir = Path(working_dir)

    log = structlog.get_logger(__name__)
    log.info("cli.scan.start", library_root=library_root, working_dir=working_dir, dry_run=dry_run)

    if dry_run:
        log.info("cli.scan.dry_run_mode")

    mp3_files = scanner.find_mp3_files(settings.library_root)
    if not mp3_files:
        log.info("cli.scan.no_mp3_files_found", library_root=library_root)
        return

    processed_count = 0
    for mp3_file in mp3_files:
        try:
            folder_info = scanner.parse_folder_names(mp3_file.parent)
            id3_tags = scanner.read_id3_tags(mp3_file)

            album_data = {
                "file_path": str(mp3_file),
                "artist_guess": folder_info.get("artist_guess"),
                "album_guess": folder_info.get("album_guess"),
                **id3_tags,
            }

            if not dry_run:
                album_repo.upsert(album_data)

            processed_count += 1
        except Exception as e:
            log.error("cli.scan.failed_to_process_file", file_path=str(mp3_file), error=str(e))
            continue

    if not dry_run:
        db_conn.commit()

    log.info("cli.scan.completed", processed_count=processed_count, total_files=len(mp3_files))


# --- Pytest Tests ---
runner = CliRunner()


def setup_mock_scanner_results(
    mp3_files_list: list[Path],
    parsed_folders: list[dict[str, Any]],
    id3_results: list[dict[str, Any]],
) -> None:
    mock_find_mp3_files.return_value = mp3_files_list
    mock_parse_folder_names.side_effect = parsed_folders
    mock_read_id3_tags.side_effect = id3_results


def clear_mocks() -> None:
    mock_find_mp3_files.reset_mock(return_value=True, side_effect=True)
    mock_parse_folder_names.reset_mock(return_value=True, side_effect=True)
    mock_read_id3_tags.reset_mock(return_value=True, side_effect=True)
    mock_album_repo_upsert.reset_mock()
    mock_db_connection.commit.reset_mock()


def test_scan_single_mp3(tmp_path: Path) -> None:
    clear_mocks()
    mock_dir = tmp_path / "library"
    mock_dir.mkdir()
    mp3_file = mock_dir / "artist - album" / "01 - track.mp3"
    mp3_file.parent.mkdir()
    mp3_file.touch()

    setup_mock_scanner_results(
        mp3_files_list=[mp3_file],
        parsed_folders=[{"artist_guess": "artist", "album_guess": "album"}],
        id3_results=[{"title": "Track Title", "artist": "artist", "album": "album"}],
    )

    result = runner.invoke(
        cli,
        ["scan", "--library-root", str(mock_dir), "--working-dir", str(tmp_path / "work")],
    )

    assert result.exit_code == 0
    mock_album_repo_upsert.assert_called_once()
    mock_db_connection.commit.assert_called_once()


def test_scan_no_mp3_files(tmp_path: Path) -> None:
    clear_mocks()
    mock_dir = tmp_path / "library"
    mock_dir.mkdir()

    setup_mock_scanner_results(mp3_files_list=[], parsed_folders=[], id3_results=[])

    result = runner.invoke(
        cli,
        ["scan", "--library-root", str(mock_dir), "--working-dir", str(tmp_path / "work")],
    )

    assert result.exit_code == 0
    mock_album_repo_upsert.assert_not_called()
    mock_db_connection.commit.assert_not_called()


def test_scan_multiple_mp3s(tmp_path: Path) -> None:
    clear_mocks()
    mock_dir = tmp_path / "library"
    mock_dir.mkdir()

    mp3_file1 = mock_dir / "Artist A - Album X" / "01 - Track 1.mp3"
    mp3_file1.parent.mkdir()
    mp3_file1.touch()

    mp3_file2 = mock_dir / "Artist B" / "Album Y" / "01 - Track 2.mp3"
    mp3_file2.parent.mkdir(parents=True)
    mp3_file2.touch()

    setup_mock_scanner_results(
        mp3_files_list=[mp3_file1, mp3_file2],
        parsed_folders=[
            {"artist_guess": "Artist A", "album_guess": "Album X"},
            {"artist_guess": None, "album_guess": "Album Y"},
        ],
        id3_results=[
            {"title": "Track 1", "artist": "Artist A", "album": "Album X"},
            {"title": "Track 2", "artist": "Track Artist 2", "album": "Album Y"},
        ],
    )

    result = runner.invoke(
        cli,
        ["scan", "--library-root", str(mock_dir), "--working-dir", str(tmp_path / "work")],
    )

    assert result.exit_code == 0
    assert mock_album_repo_upsert.call_count == 2
    mock_db_connection.commit.assert_called_once()


def test_scan_dry_run(tmp_path: Path) -> None:
    clear_mocks()
    mock_dir = tmp_path / "library"
    mock_dir.mkdir()
    mp3_file = mock_dir / "artist - album" / "01 - track.mp3"
    mp3_file.parent.mkdir()
    mp3_file.touch()

    setup_mock_scanner_results(
        mp3_files_list=[mp3_file],
        parsed_folders=[{"artist_guess": "artist", "album_guess": "album"}],
        id3_results=[{"title": "Track Title"}],
    )

    result = runner.invoke(
        cli,
        [
            "scan",
            "--library-root",
            str(mock_dir),
            "--working-dir",
            str(tmp_path / "work"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    mock_album_repo_upsert.assert_not_called()
    mock_db_connection.commit.assert_not_called()


def test_scan_idempotent(tmp_path: Path) -> None:
    clear_mocks()
    mock_dir = tmp_path / "library"
    mock_dir.mkdir()
    mp3_file = mock_dir / "artist - album" / "01 - track.mp3"
    mp3_file.parent.mkdir()
    mp3_file.touch()

    setup_mock_scanner_results(
        mp3_files_list=[mp3_file],
        parsed_folders=[{"artist_guess": "artist", "album_guess": "album"}],
        id3_results=[{"title": "Track Title"}],
    )

    runner.invoke(
        cli,
        ["scan", "--library-root", str(mock_dir), "--working-dir", str(tmp_path / "work")],
    )
    assert mock_album_repo_upsert.call_count == 1

    clear_mocks()
    setup_mock_scanner_results(
        mp3_files_list=[mp3_file],
        parsed_folders=[{"artist_guess": "artist", "album_guess": "album"}],
        id3_results=[{"title": "Track Title"}],
    )

    runner.invoke(
        cli,
        ["scan", "--library-root", str(mock_dir), "--working-dir", str(tmp_path / "work")],
    )
    assert mock_album_repo_upsert.call_count == 1


def test_scan_with_read_error(tmp_path: Path) -> None:
    clear_mocks()
    mock_dir = tmp_path / "library"
    mock_dir.mkdir()
    mp3_file_ok = mock_dir / "01.mp3"
    mp3_file_ok.touch()
    mp3_file_err = mock_dir / "02.mp3"
    mp3_file_err.touch()

    mock_read_id3_tags.side_effect = [{"title": "OK"}, Exception("error")]
    mock_find_mp3_files.return_value = [mp3_file_ok, mp3_file_err]
    mock_parse_folder_names.return_value = {"artist_guess": "A", "album_guess": "B"}

    result = runner.invoke(
        cli,
        ["scan", "--library-root", str(mock_dir), "--working-dir", str(tmp_path / "work")],
    )

    assert result.exit_code == 0
    assert mock_album_repo_upsert.call_count == 1


def test_scan_missing_working_dir(tmp_path: Path) -> None:
    clear_mocks()
    mock_dir = tmp_path / "library"
    mock_dir.mkdir()
    mp3_file = mock_dir / "01.mp3"
    mp3_file.touch()

    work_dir = tmp_path / "new_workdir"

    setup_mock_scanner_results(
        mp3_files_list=[mp3_file],
        parsed_folders=[{"artist_guess": "A", "album_guess": "B"}],
        id3_results=[{"title": "T"}],
    )

    result = runner.invoke(
        cli,
        ["scan", "--library-root", str(mock_dir), "--working-dir", str(work_dir)],
    )

    assert result.exit_code == 0
    mock_album_repo_upsert.assert_called_once()
