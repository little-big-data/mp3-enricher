"""Tests for the `write` command in tagger.mp3_tagger."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from tagger.mp3_tagger import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke_write(runner: CliRunner, db_path: Path, extra_args: list[str] | None = None) -> object:
    args = ["write", "--db-path", str(db_path)] + (extra_args or [])
    return runner.invoke(cli, args)


def test_write_calls_write_pending(runner: CliRunner, tmp_path: Path) -> None:
    """write command calls ID3Writer.write_pending and reports counts."""
    db = tmp_path / "test.db"
    mock_writer = MagicMock()
    mock_writer.write_pending.return_value = (3, 0)

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ID3Writer", return_value=mock_writer),
    ):
        result = _invoke_write(runner, db)

    assert result.exit_code == 0
    mock_writer.write_pending.assert_called_once()
    assert "3" in result.output
    assert "0" in result.output


def test_write_dry_run_flag(runner: CliRunner, tmp_path: Path) -> None:
    """--dry-run passes dry_run=True to ID3Writer."""
    db = tmp_path / "test.db"
    mock_writer = MagicMock()
    mock_writer.write_pending.return_value = (1, 0)

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ID3Writer", return_value=mock_writer) as mock_cls,
    ):
        result = _invoke_write(runner, db, ["--dry-run"])

    assert result.exit_code == 0
    _, kwargs = mock_cls.call_args
    assert kwargs.get("dry_run") is True


def test_write_force_flag(runner: CliRunner, tmp_path: Path) -> None:
    """--force passes force=True to ID3Writer."""
    db = tmp_path / "test.db"
    mock_writer = MagicMock()
    mock_writer.write_pending.return_value = (2, 0)

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ID3Writer", return_value=mock_writer) as mock_cls,
    ):
        result = _invoke_write(runner, db, ["--force"])

    assert result.exit_code == 0
    _, kwargs = mock_cls.call_args
    assert kwargs.get("force") is True


def test_write_workers_option(runner: CliRunner, tmp_path: Path) -> None:
    """--workers is forwarded to write_pending."""
    db = tmp_path / "test.db"
    mock_writer = MagicMock()
    mock_writer.write_pending.return_value = (5, 0)

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ID3Writer", return_value=mock_writer),
    ):
        result = _invoke_write(runner, db, ["--workers", "4"])

    assert result.exit_code == 0
    mock_writer.write_pending.assert_called_once_with(workers=4, folder_prefix=None)


def test_write_reports_errors(runner: CliRunner, tmp_path: Path) -> None:
    """Non-zero error count is reported in output."""
    db = tmp_path / "test.db"
    mock_writer = MagicMock()
    mock_writer.write_pending.return_value = (2, 1)

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ID3Writer", return_value=mock_writer),
    ):
        result = _invoke_write(runner, db)

    assert result.exit_code == 0
    assert "2" in result.output
    assert "1" in result.output


def test_write_nothing_pending(runner: CliRunner, tmp_path: Path) -> None:
    """Zero successes and errors is reported clearly."""
    db = tmp_path / "test.db"
    mock_writer = MagicMock()
    mock_writer.write_pending.return_value = (0, 0)

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ID3Writer", return_value=mock_writer),
    ):
        result = _invoke_write(runner, db)

    assert result.exit_code == 0
    assert "0" in result.output
