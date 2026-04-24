from __future__ import annotations

from typing import TYPE_CHECKING

from tagger.db.models import TrackRecord

if TYPE_CHECKING:
    import sqlite3


class TrackRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, track: TrackRecord) -> None:
        """Inserts or updates a track record by file_path."""
        query = """
            INSERT INTO tracks (
                album_id, file_path, filename, track_number, disc_number,
                existing_title, existing_artist, title, artist,
                album_artist, album_title, year, track_num,
                genre, grouping, art_path, compilation, enrichment_status,
                written_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                album_id = excluded.album_id,
                filename = excluded.filename,
                track_number = excluded.track_number,
                disc_number = excluded.disc_number,
                existing_title = excluded.existing_title,
                existing_artist = excluded.existing_artist,
                title = excluded.title,
                artist = excluded.artist,
                album_artist = excluded.album_artist,
                album_title = excluded.album_title,
                year = excluded.year,
                track_num = excluded.track_num,
                genre = excluded.genre,
                grouping = excluded.grouping,
                art_path = excluded.art_path,
                compilation = excluded.compilation,
                enrichment_status = excluded.enrichment_status,
                written_status = excluded.written_status,
                notes = excluded.notes
        """
        self._conn.execute(
            query,
            (
                track.album_id,
                track.file_path,
                track.filename,
                track.track_number,
                track.disc_number,
                track.existing_title,
                track.existing_artist,
                track.title,
                track.artist,
                track.album_artist,
                track.album_title,
                track.year,
                track.track_num,
                track.genre,
                track.grouping,
                track.art_path,
                int(track.compilation),
                track.enrichment_status,
                track.written_status,
                track.notes,
            ),
        )

    def delete_stale(self, album_id: int, current_file_paths: set[str]) -> None:
        """Delete tracks for album_id whose file_path is not in current_file_paths.

        Call this after upserting the current scan results so that renamed or
        deleted files are removed from the database.
        """
        if current_file_paths:
            placeholders = ",".join("?" * len(current_file_paths))
            self._conn.execute(
                f"DELETE FROM tracks WHERE album_id = ? AND file_path NOT IN ({placeholders})",
                (album_id, *current_file_paths),
            )
        else:
            self._conn.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))

    def get_by_file_path(self, file_path: str) -> TrackRecord | None:
        """Retrieves a track by its file path."""
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE file_path = ?", (file_path,)
        ).fetchone()
        return TrackRecord.model_validate(dict(row)) if row else None

    def get_by_album(self, album_id: int) -> list[TrackRecord]:
        """Retrieves all tracks for a given album ID."""
        cursor = self._conn.execute("SELECT * FROM tracks WHERE album_id = ?", (album_id,))
        return [TrackRecord.model_validate(dict(row)) for row in cursor.fetchall()]

    def get_pending_write(
        self, *, force: bool = False, folder_prefix: str | None = None
    ) -> list[TrackRecord]:
        """Retrieves tracks ready to be written.

        Without force: enrichment_status='found' AND written_status='pending'.
        With force: enrichment_status='found' (includes already-written tracks).
        With folder_prefix: further filters to tracks whose file_path starts with that prefix.
        """
        conditions = ["enrichment_status = 'found'"]
        params: list[str] = []

        if not force:
            conditions.append("written_status = 'pending'")
        if folder_prefix is not None:
            conditions.append("file_path LIKE ?")
            params.append(folder_prefix.rstrip("/\\") + "%")

        where = " AND ".join(conditions)
        cursor = self._conn.execute(f"SELECT * FROM tracks WHERE {where}", params)
        return [TrackRecord.model_validate(dict(row)) for row in cursor.fetchall()]

    def update_written_status(self, track_id: int, status: str) -> None:
        """Updates the written_status for a track by ID."""
        self._conn.execute(
            "UPDATE tracks SET written_status = ? WHERE id = ?",
            (status, track_id),
        )

    def reset_written_status_for_album(self, album_id: int) -> None:
        """Reset written_status to 'pending' for all enriched tracks in an album."""
        self._conn.execute(
            "UPDATE tracks SET written_status = 'pending'"
            " WHERE album_id = ? AND enrichment_status = 'found'",
            (album_id,),
        )

    def get_titles_for_albums(self, album_ids: list[int]) -> list[str]:
        """Return all track titles for the given album IDs.

        Used to extract featured artist signals for link detection.
        """
        if not album_ids:
            return []
        placeholders = ",".join("?" * len(album_ids))
        cursor = self._conn.execute(
            f"SELECT title FROM tracks WHERE album_id IN ({placeholders})",
            album_ids,
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    def update_grouping_for_album(self, album_id: int, grouping: str) -> None:
        """Overwrite the grouping field for all tracks in an album."""
        with self._conn:
            self._conn.execute(
                "UPDATE tracks SET grouping = ? WHERE album_id = ?",
                (grouping, album_id),
            )
