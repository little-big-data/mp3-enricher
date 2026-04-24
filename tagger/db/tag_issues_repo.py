"""Repository for tag_issues table — stores scan-integrity findings."""

from __future__ import annotations

import sqlite3

import structlog

from tagger.integrity.models import IssueKind, TagIssue

log = structlog.get_logger(__name__)


class TagIssuesRepository:
    """Data-access layer for the tag_issues table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert_batch(self, issues: list[TagIssue]) -> int:
        """Insert issues, ignoring duplicates.

        Duplicate key: (artist_folder, album_folder, issue_kind, detail).
        Returns the number of rows actually inserted (duplicates are silently ignored).
        """
        before: int = self._conn.execute("SELECT COUNT(*) FROM tag_issues").fetchone()[0]
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO tag_issues
                (artist_folder, album_folder, folder_path, issue_kind, detail, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    issue.artist_folder,
                    issue.album_folder,
                    issue.folder_path,
                    issue.issue_kind.value,
                    issue.detail,
                    issue.status,
                )
                for issue in issues
            ],
        )
        after: int = self._conn.execute("SELECT COUNT(*) FROM tag_issues").fetchone()[0]
        inserted = after - before
        log.info(
            "tag_issues.upsert_batch",
            total=len(issues),
            inserted=inserted,
            skipped=len(issues) - inserted,
        )
        return inserted

    def get_pending(self) -> list[TagIssue]:
        """Return all tag issues with status='pending'."""
        cursor = self._conn.execute(
            "SELECT * FROM tag_issues WHERE status = 'pending' ORDER BY artist_folder, album_folder"
        )
        return [TagIssue.model_validate(dict(row)) for row in cursor.fetchall()]

    def get_by_album(self, artist_folder: str, album_folder: str) -> list[TagIssue]:
        """Return all tag issues for a specific album folder."""
        cursor = self._conn.execute(
            "SELECT * FROM tag_issues WHERE artist_folder = ? AND album_folder = ?",
            (artist_folder, album_folder),
        )
        return [TagIssue.model_validate(dict(row)) for row in cursor.fetchall()]

    def resolve(self, issue_id: int) -> None:
        """Mark a tag issue as resolved."""
        self._conn.execute(
            "UPDATE tag_issues SET status = 'resolved', resolved_at = datetime('now') WHERE id = ?",
            (issue_id,),
        )
        log.info("tag_issues.resolved", issue_id=issue_id)

    def count_by_status(self) -> dict[str, int]:
        """Return a mapping of status → count for all tag issues."""
        cursor = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tag_issues GROUP BY status"
        )
        return {row["status"]: row["cnt"] for row in cursor.fetchall()}

    def get_all_issue_kinds(self) -> list[IssueKind]:
        """Return the distinct IssueKind values present in the table."""
        cursor = self._conn.execute("SELECT DISTINCT issue_kind FROM tag_issues")
        return [IssueKind(row["issue_kind"]) for row in cursor.fetchall()]
