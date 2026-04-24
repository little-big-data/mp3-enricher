"""Integration tests for the scan-integrity CLI command and IntegrityScanner.

Uses real SQLite, real filesystem (tmp_path), and minimal MP3 fixtures
created via mutagen.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from click.testing import CliRunner
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2

from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.tag_issues_repo import TagIssuesRepository
from tagger.integrity.models import IssueKind
from tagger.integrity.scanner import IntegrityScanner
from tagger.mp3_tagger import cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mp3(
    path: Path,
    *,
    tpe2: str | None = None,
    tpe1: str | None = None,
    talb: str | None = None,
    tit2: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    if tpe2 is not None:
        tags.add(TPE2(encoding=3, text=tpe2))
    if tpe1 is not None:
        tags.add(TPE1(encoding=3, text=tpe1))
    if talb is not None:
        tags.add(TALB(encoding=3, text=talb))
    if tit2 is not None:
        tags.add(TIT2(encoding=3, text=tit2))
    tags.save(str(path))


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A small library with two artist dirs and three album dirs, one with issues."""
    # Clean album — tags match folders
    _make_mp3(
        tmp_path / "Pink Floyd" / "The Wall" / "01 In The Flesh.mp3",
        tpe2="Pink Floyd",
        tpe1="Pink Floyd",
        talb="The Wall",
        tit2="In The Flesh",
    )
    # Broken album — AlbumArtist tag is completely wrong
    _make_mp3(
        tmp_path / "Radiohead" / "OK Computer" / "01 Airbag.mp3",
        tpe2="Ennio Morricone",
        tpe1="Radiohead",
        talb="OK Computer",
        tit2="Airbag",
    )
    # Another broken album — inconsistent Album tag across tracks
    _make_mp3(
        tmp_path / "Miles Davis" / "Kind of Blue" / "01 So What.mp3",
        tpe2="Miles Davis",
        talb="Kind of Blue",
        tit2="So What",
    )
    _make_mp3(
        tmp_path / "Miles Davis" / "Kind of Blue" / "02 Freddie Freeloader.mp3",
        tpe2="Miles Davis",
        talb="Kind of Blue (Remastered)",  # inconsistent
        tit2="Freddie Freeloader",
    )
    return tmp_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


# ---------------------------------------------------------------------------
# Scanner produces correct issues
# ---------------------------------------------------------------------------


def test_scanner_finds_issues_in_library(library: Path) -> None:
    issues = IntegrityScanner().scan_library(library)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.ALBUM_ARTIST_MISMATCH in kinds
    assert IssueKind.INCONSISTENT_ALBUM in kinds


def test_scanner_clean_album_has_no_issues(library: Path) -> None:
    issues = IntegrityScanner().scan_library(library)
    pink_floyd_issues = [
        i for i in issues if i.artist_folder == "Pink Floyd" and i.album_folder == "The Wall"
    ]
    assert pink_floyd_issues == []


# ---------------------------------------------------------------------------
# CLI: scan-integrity writes CSV and DB
# ---------------------------------------------------------------------------


def test_scan_integrity_cli_writes_csv(library: Path, db_path: Path, tmp_path: Path) -> None:
    out_csv = tmp_path / "issues.csv"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "scan-integrity",
            str(library),
            "--db-path",
            str(db_path),
            "--out",
            str(out_csv),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_csv.exists()

    with out_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) > 0
    assert "artist_folder" in rows[0]
    assert "album_folder" in rows[0]
    assert "issue_kind" in rows[0]
    assert "detail" in rows[0]


def test_scan_integrity_cli_writes_db(library: Path, db_path: Path, tmp_path: Path) -> None:
    out_csv = tmp_path / "issues.csv"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "scan-integrity",
            str(library),
            "--db-path",
            str(db_path),
            "--out",
            str(out_csv),
        ],
    )
    assert result.exit_code == 0, result.output

    conn = get_db_connection(db_path)
    run_migrations(conn)
    repo = TagIssuesRepository(conn)
    pending = repo.get_pending()
    assert len(pending) > 0
    conn.close()


def test_scan_integrity_cli_no_db_flag_skips_db_write(
    library: Path, db_path: Path, tmp_path: Path
) -> None:
    out_csv = tmp_path / "issues.csv"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "scan-integrity",
            str(library),
            "--db-path",
            str(db_path),
            "--out",
            str(out_csv),
            "--no-db",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_csv.exists()

    # DB file may not even exist, or if it does the table is empty
    if db_path.exists():
        conn = get_db_connection(db_path)
        run_migrations(conn)
        repo = TagIssuesRepository(conn)
        assert repo.get_pending() == []
        conn.close()


def test_scan_integrity_csv_matches_db(library: Path, db_path: Path, tmp_path: Path) -> None:
    out_csv = tmp_path / "issues.csv"
    runner = CliRunner()
    runner.invoke(
        cli,
        [
            "scan-integrity",
            str(library),
            "--db-path",
            str(db_path),
            "--out",
            str(out_csv),
        ],
    )

    with out_csv.open(encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))

    conn = get_db_connection(db_path)
    run_migrations(conn)
    repo = TagIssuesRepository(conn)
    db_rows = repo.get_pending()
    conn.close()

    assert len(csv_rows) == len(db_rows)


def test_scan_integrity_idempotent(library: Path, db_path: Path, tmp_path: Path) -> None:
    """Running scan-integrity twice does not duplicate DB rows."""
    out_csv = tmp_path / "issues.csv"
    runner = CliRunner()
    for _ in range(2):
        runner.invoke(
            cli,
            [
                "scan-integrity",
                str(library),
                "--db-path",
                str(db_path),
                "--out",
                str(out_csv),
            ],
        )

    conn = get_db_connection(db_path)
    run_migrations(conn)
    repo = TagIssuesRepository(conn)
    count_after_two_runs = len(repo.get_pending())
    conn.close()

    # Also run once and compare
    db_path2 = tmp_path / "test2.db"
    runner.invoke(
        cli,
        [
            "scan-integrity",
            str(library),
            "--db-path",
            str(db_path2),
            "--out",
            str(out_csv),
        ],
    )
    conn2 = get_db_connection(db_path2)
    run_migrations(conn2)
    count_after_one_run = len(TagIssuesRepository(conn2).get_pending())
    conn2.close()

    assert count_after_two_runs == count_after_one_run
