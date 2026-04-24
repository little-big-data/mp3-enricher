"""Unit tests for tagger.db.tag_issues_repo.TagIssuesRepository."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.tag_issues_repo import TagIssuesRepository
from tagger.integrity.models import IssueKind, TagIssue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """File-based SQLite connection with all migrations applied."""
    c = get_db_connection(tmp_path / "test.db")
    run_migrations(c)
    yield c
    c.close()


@pytest.fixture
def repo(conn: sqlite3.Connection) -> TagIssuesRepository:
    return TagIssuesRepository(conn)


def _make_issue(
    artist: str = "The Artist",
    album: str = "The Album",
    kind: IssueKind = IssueKind.ALBUM_ARTIST_MISMATCH,
    detail: str = "AlbumArtist='X' vs folder='Y' (score=40)",
) -> TagIssue:
    return TagIssue(
        artist_folder=artist,
        album_folder=album,
        folder_path=f"/music/{artist}/{album}",
        issue_kind=kind,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# upsert_batch
# ---------------------------------------------------------------------------


def test_upsert_batch_inserts_new_issues(
    repo: TagIssuesRepository, conn: sqlite3.Connection
) -> None:
    issues = [
        _make_issue(detail="AlbumArtist='A' vs folder='B' (score=30)"),
        _make_issue(kind=IssueKind.ALBUM_MISMATCH, detail="Album='X' vs folder='Y' (score=50)"),
    ]
    with conn:
        inserted = repo.upsert_batch(issues)
    assert inserted == 2


def test_upsert_batch_duplicate_is_ignored(
    repo: TagIssuesRepository, conn: sqlite3.Connection
) -> None:
    issue = _make_issue()
    with conn:
        first = repo.upsert_batch([issue])
    with conn:
        second = repo.upsert_batch([issue])
    assert first == 1
    assert second == 0  # duplicate ignored


def test_upsert_batch_same_album_different_kinds(
    repo: TagIssuesRepository, conn: sqlite3.Connection
) -> None:
    issues = [
        _make_issue(kind=IssueKind.ALBUM_ARTIST_MISMATCH, detail="detail-a"),
        _make_issue(kind=IssueKind.ALBUM_MISMATCH, detail="detail-b"),
        _make_issue(kind=IssueKind.ALL_UNTITLED, detail="detail-c"),
    ]
    with conn:
        inserted = repo.upsert_batch(issues)
    assert inserted == 3


def test_upsert_batch_empty_list(repo: TagIssuesRepository, conn: sqlite3.Connection) -> None:
    with conn:
        inserted = repo.upsert_batch([])
    assert inserted == 0


# ---------------------------------------------------------------------------
# get_pending / get_by_album
# ---------------------------------------------------------------------------


def test_get_pending_returns_only_pending(
    repo: TagIssuesRepository, conn: sqlite3.Connection
) -> None:
    pending = _make_issue(detail="pending-detail")
    resolved = _make_issue(album="Other Album", detail="resolved-detail")

    with conn:
        repo.upsert_batch([pending, resolved])
        # Manually mark the second row resolved
        row_id = conn.execute(
            "SELECT id FROM tag_issues WHERE album_folder='Other Album'"
        ).fetchone()[0]
        repo.resolve(row_id)

    result = repo.get_pending()
    assert len(result) == 1
    assert result[0].detail == "pending-detail"


def test_get_pending_empty_when_no_issues(repo: TagIssuesRepository) -> None:
    assert repo.get_pending() == []


def test_get_by_album_returns_matching_rows(
    repo: TagIssuesRepository, conn: sqlite3.Connection
) -> None:
    issues = [
        _make_issue(artist="Artist A", album="Album X", detail="d1"),
        _make_issue(artist="Artist A", album="Album X", kind=IssueKind.ALBUM_MISMATCH, detail="d2"),
        _make_issue(artist="Artist B", album="Album Y", detail="d3"),
    ]
    with conn:
        repo.upsert_batch(issues)

    result = repo.get_by_album("Artist A", "Album X")
    assert len(result) == 2
    details = {r.detail for r in result}
    assert details == {"d1", "d2"}


def test_get_by_album_returns_empty_for_unknown(repo: TagIssuesRepository) -> None:
    assert repo.get_by_album("Nobody", "Nothing") == []


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_sets_resolved_status(repo: TagIssuesRepository, conn: sqlite3.Connection) -> None:
    with conn:
        repo.upsert_batch([_make_issue()])

    row_id = conn.execute("SELECT id FROM tag_issues").fetchone()[0]
    with conn:
        repo.resolve(row_id)

    row = conn.execute(
        "SELECT status, resolved_at FROM tag_issues WHERE id=?", (row_id,)
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_at"] is not None


def test_resolve_does_not_affect_other_rows(
    repo: TagIssuesRepository, conn: sqlite3.Connection
) -> None:
    issues = [
        _make_issue(album="A1", detail="d1"),
        _make_issue(album="A2", detail="d2"),
    ]
    with conn:
        repo.upsert_batch(issues)

    row_id = conn.execute("SELECT id FROM tag_issues WHERE album_folder='A1'").fetchone()[0]
    with conn:
        repo.resolve(row_id)

    other = conn.execute("SELECT status FROM tag_issues WHERE album_folder='A2'").fetchone()
    assert other["status"] == "pending"


# ---------------------------------------------------------------------------
# count_by_status
# ---------------------------------------------------------------------------


def test_count_by_status(repo: TagIssuesRepository, conn: sqlite3.Connection) -> None:
    issues = [
        _make_issue(album="A1", detail="d1"),
        _make_issue(album="A2", detail="d2"),
    ]
    with conn:
        repo.upsert_batch(issues)

    row_id = conn.execute("SELECT id FROM tag_issues WHERE album_folder='A1'").fetchone()[0]
    with conn:
        repo.resolve(row_id)

    counts = repo.count_by_status()
    assert counts["pending"] == 1
    assert counts["resolved"] == 1
