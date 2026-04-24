from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3


class ManualReviewRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, album_id: int, reason: str) -> None:
        """Adds a manual review entry for an album.

        No-ops if an unresolved entry for this album already exists, so
        repeated enrichment runs do not accumulate duplicate rows.
        """
        self._conn.execute(
            """
            INSERT INTO manual_review (album_id, reason)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM manual_review WHERE album_id = ? AND resolved = 0
            )
            """,
            (album_id, reason, album_id),
        )

    def get_pending(self) -> list[dict[str, Any]]:
        """Retrieves unresolved manual review entries with album folder paths.

        Returns one row per album (the most recent entry), so repeated
        enrichment runs that created duplicate rows are collapsed.
        """
        query = """
            SELECT mr.*, a.folder_path, a.artist_guess, a.album_guess
            FROM manual_review mr
            JOIN albums a ON mr.album_id = a.id
            WHERE mr.resolved = 0
              AND mr.id = (
                  SELECT MAX(id) FROM manual_review
                  WHERE album_id = mr.album_id AND resolved = 0
              )
            ORDER BY a.folder_path
        """
        cursor = self._conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def resolve(self, album_id: int, user_discogs_url: str | None = None) -> None:
        """Marks a manual review entry as resolved."""
        self._conn.execute(
            """
            UPDATE manual_review
            SET resolved = 1, user_discogs_url = ?
            WHERE album_id = ?
            """,
            (user_discogs_url, album_id),
        )
