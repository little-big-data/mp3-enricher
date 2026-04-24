"""Tests for tagger.manual.csv_handler.CsvHandler."""

from __future__ import annotations

from pathlib import Path

import pytest

from tagger.manual.csv_handler import CsvHandler

PENDING_ROWS = [
    {
        "album_id": 1,
        "folder_path": "/music/Artist/Album",
        "artist_guess": "Artist",
        "album_guess": "Album",
        "reason": "No Discogs match",
        "user_discogs_url": "",
    },
    {
        "album_id": 2,
        "folder_path": "/music/Other/Record",
        "artist_guess": "Other",
        "album_guess": "Record",
        "reason": "Low confidence",
        "user_discogs_url": "",
    },
]


@pytest.fixture
def handler() -> CsvHandler:
    return CsvHandler()


def test_export_pending_writes_headers(handler: CsvHandler, tmp_path: Path) -> None:
    csv_path = tmp_path / "manual_review.csv"
    handler.export_pending([], csv_path)

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "album_id,folder_path,artist_guess,album_guess,reason,user_discogs_url"


def test_export_pending_writes_rows(handler: CsvHandler, tmp_path: Path) -> None:
    csv_path = tmp_path / "manual_review.csv"
    handler.export_pending(PENDING_ROWS, csv_path)

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # header + 2 rows
    assert "Artist" in lines[1]
    assert "No Discogs match" in lines[1]


def test_export_pending_empty_still_writes_headers(handler: CsvHandler, tmp_path: Path) -> None:
    csv_path = tmp_path / "manual_review.csv"
    handler.export_pending([], csv_path)

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # header only


def test_export_pending_overwrites_existing_file(handler: CsvHandler, tmp_path: Path) -> None:
    csv_path = tmp_path / "manual_review.csv"
    csv_path.write_text("old content", encoding="utf-8")

    handler.export_pending(PENDING_ROWS, csv_path)

    content = csv_path.read_text(encoding="utf-8")
    assert "old content" not in content
    assert "album_id" in content


def test_import_corrections_returns_only_rows_with_url(handler: CsvHandler, tmp_path: Path) -> None:
    csv_path = tmp_path / "manual_review.csv"
    handler.export_pending(PENDING_ROWS, csv_path)

    # Simulate user filling in one URL (line already ends with trailing comma)
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1] + "https://www.discogs.com/release/123"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    corrections = handler.import_corrections(csv_path)

    assert len(corrections) == 1
    assert corrections[0]["album_id"] == "1"
    assert corrections[0]["user_discogs_url"] == "https://www.discogs.com/release/123"


def test_import_corrections_skips_empty_url(handler: CsvHandler, tmp_path: Path) -> None:
    csv_path = tmp_path / "manual_review.csv"
    handler.export_pending(PENDING_ROWS, csv_path)

    corrections = handler.import_corrections(csv_path)

    assert corrections == []


def test_import_corrections_skips_whitespace_only_url(handler: CsvHandler, tmp_path: Path) -> None:
    csv_path = tmp_path / "manual_review.csv"
    rows = [{**PENDING_ROWS[0], "user_discogs_url": "   "}]
    handler.export_pending(rows, csv_path)

    corrections = handler.import_corrections(csv_path)

    assert corrections == []


@pytest.mark.parametrize(
    ("url", "expected_id"),
    [
        ("https://www.discogs.com/release/123456", 123456),
        ("https://www.discogs.com/Artist-Album/release/789", 789),
        ("http://discogs.com/release/1", 1),
    ],
)
def test_extract_release_id_valid_urls(url: str, expected_id: int) -> None:
    assert CsvHandler.extract_release_id(url) == expected_id


def test_extract_release_id_invalid_url_returns_none() -> None:
    assert CsvHandler.extract_release_id("https://www.discogs.com/artist/123") is None


def test_extract_release_id_empty_string_returns_none() -> None:
    assert CsvHandler.extract_release_id("") is None


@pytest.mark.parametrize(
    ("url", "expected_id"),
    [
        ("https://www.discogs.com/master/933719", 933719),
        ("https://www.discogs.com/master/933719-Josefin-Horse-Dance", 933719),
        ("http://discogs.com/master/1", 1),
    ],
)
def test_extract_master_id_valid_urls(url: str, expected_id: int) -> None:
    assert CsvHandler.extract_master_id(url) == expected_id


def test_extract_master_id_release_url_returns_none() -> None:
    assert CsvHandler.extract_master_id("https://www.discogs.com/release/123456") is None


def test_extract_master_id_empty_string_returns_none() -> None:
    assert CsvHandler.extract_master_id("") is None
