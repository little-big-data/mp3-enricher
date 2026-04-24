from __future__ import annotations

import sqlite3

from tagger.db.models import AlbumRecord


class AlbumRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, album: AlbumRecord) -> None:
        """Inserts or updates an album record by folder_path."""
        query = """
            INSERT INTO albums (
                folder_path, artist_guess, album_guess,
                discogs_release_id, discogs_url, enrichment_status,
                written_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(folder_path) DO UPDATE SET
                artist_guess = excluded.artist_guess,
                album_guess = excluded.album_guess,
                discogs_release_id = excluded.discogs_release_id,
                discogs_url = excluded.discogs_url,
                enrichment_status = excluded.enrichment_status,
                written_status = excluded.written_status,
                notes = excluded.notes
        """
        self._conn.execute(
            query,
            (
                album.folder_path,
                album.artist_guess,
                album.album_guess,
                album.discogs_release_id,
                album.discogs_url,
                album.enrichment_status,
                album.written_status,
                album.notes,
            ),
        )

    def get_by_id(self, album_id: int) -> AlbumRecord | None:
        """Retrieves an album by its primary key."""
        row = self._conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
        return AlbumRecord.model_validate(dict(row)) if row else None

    def get_by_folder_path(self, folder_path: str) -> AlbumRecord | None:
        """Retrieves an album by its folder path."""
        row = self._conn.execute(
            "SELECT * FROM albums WHERE folder_path = ?", (folder_path,)
        ).fetchone()
        return AlbumRecord.model_validate(dict(row)) if row else None

    def get_pending(self) -> list[AlbumRecord]:
        """Retrieves all albums with 'pending' enrichment status."""
        cursor = self._conn.execute("SELECT * FROM albums WHERE enrichment_status = 'pending'")
        return [AlbumRecord.model_validate(dict(row)) for row in cursor.fetchall()]

    def mark_found(self, album_id: int, release_id: int, url: str) -> None:
        """Updates an album's status to 'found' and sets Discogs metadata."""
        self._conn.execute(
            """
            UPDATE albums
            SET enrichment_status = 'found',
                discogs_release_id = ?,
                discogs_url = ?
            WHERE id = ?
            """,
            (release_id, url, album_id),
        )

    def get_enriched(self, artist_prefix: str | None = None) -> list[AlbumRecord]:
        """Return all albums with enrichment_status='found'.

        Optionally filter to those whose artist_guess starts with *artist_prefix*
        (case-insensitive). Used by the link-scan command.
        """
        if artist_prefix:
            cursor = self._conn.execute(
                "SELECT * FROM albums WHERE enrichment_status = 'found'"
                " AND LOWER(artist_guess) LIKE ?",
                (artist_prefix.lower() + "%",),
            )
        else:
            cursor = self._conn.execute("SELECT * FROM albums WHERE enrichment_status = 'found'")
        return [AlbumRecord.model_validate(dict(row)) for row in cursor.fetchall()]

    def get_not_found(self) -> list[AlbumRecord]:
        """Retrieves all albums with 'not_found' enrichment status."""
        cursor = self._conn.execute("SELECT * FROM albums WHERE enrichment_status = 'not_found'")
        return [AlbumRecord.model_validate(dict(row)) for row in cursor.fetchall()]

    def mark_not_found(self, album_id: int) -> None:
        """Updates an album's enrichment status to 'not_found'."""
        self._conn.execute(
            "UPDATE albums SET enrichment_status = 'not_found' WHERE id = ?",
            (album_id,),
        )
