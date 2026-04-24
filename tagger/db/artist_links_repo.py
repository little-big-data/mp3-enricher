"""Repository for artist_links table — caches LLM/heuristic link (affiliation) mappings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import sqlite3

log = structlog.get_logger(__name__)


class ArtistLinksRepository:
    """Data-access layer for the artist_links table.

    Stores artist → link mappings from LLM, heuristic, and manual sources.
    ``get_link_tag_value`` returns a comma-separated string suitable for
    direct use as the ``link:`` value in GRP1/TIT1 tags.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(
        self,
        artist_name: str,
        link: str,
        *,
        source: str = "llm",
        confidence: float = 1.0,
    ) -> None:
        """Insert or update an artist → link mapping.

        On conflict (same artist_name + link), the source and confidence
        are updated so higher-quality sources can overwrite lower-quality ones.
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO artist_links (artist_name, link, source, confidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(artist_name, link)
                DO UPDATE SET source = excluded.source,
                              confidence = excluded.confidence
                """,
                (artist_name, link, source, confidence),
            )
        log.debug(
            "artist_links.upsert",
            artist=artist_name,
            link=link,
            source=source,
        )

    def get_links(self, artist_name: str) -> list[str]:
        """Return all link names associated with *artist_name*.

        Returns an empty list when the artist is unknown.
        """
        cursor = self._conn.execute(
            "SELECT link FROM artist_links WHERE artist_name = ? ORDER BY id",
            (artist_name,),
        )
        return [row[0] for row in cursor.fetchall()]

    def get_link_tag_value(self, artist_name: str) -> str | None:
        """Return a comma-separated link string for use as the GRP1 ``link:`` value.

        Returns ``None`` when the artist has no known links.
        """
        links = self.get_links(artist_name)
        if not links:
            return None
        return ", ".join(links)

    def get_all(self) -> list[tuple[str, str, str]]:
        """Return all rows as (artist_name, link, source) tuples.

        Useful for exporting to CSV or Google Sheets.
        """
        cursor = self._conn.execute(
            "SELECT artist_name, link, source FROM artist_links ORDER BY artist_name, link"
        )
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]
