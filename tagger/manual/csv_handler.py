"""Handles reading and writing of the manual_review.csv file."""

from __future__ import annotations

import csv
import re
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_RELEASE_ID_RE = re.compile(r"/release/(\d+)")
_MASTER_ID_RE = re.compile(r"/master/(\d+)")


class CsvHandler:
    """Manages the manual_review.csv workflow.

    The CSV is designed to be opened by a human reviewer who fills in the
    ``user_discogs_url`` column for albums they can identify manually.
    After saving, the file is passed back to ``process-manual`` to
    continue enrichment with the user-supplied release URLs.
    """

    FIELDNAMES: ClassVar[list[str]] = [
        "album_id",
        "folder_path",
        "artist_guess",
        "album_guess",
        "reason",
        "user_discogs_url",
    ]

    def export_pending(self, pending: list[dict[str, Any]], csv_path: Path) -> None:
        """Write pending manual-review rows to a CSV file.

        Overwrites any existing file. Writes headers even when ``pending``
        is empty so the reviewer sees the expected column names.
        """
        log.info("csv.export", path=str(csv_path), count=len(pending))
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(pending)

    def import_corrections(self, csv_path: Path) -> list[dict[str, str]]:
        """Return rows from a user-edited CSV that have a ``user_discogs_url`` filled in."""
        log.info("csv.import", path=str(csv_path))
        with csv_path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            return [row for row in reader if row.get("user_discogs_url", "").strip()]

    @staticmethod
    def extract_release_id(discogs_url: str) -> int | None:
        """Extract the numeric Discogs release ID from a URL.

        Supports both ``/release/12345`` and ``/Some-Title/release/12345``
        URL formats. Returns ``None`` if no release ID can be found.
        """
        match = _RELEASE_ID_RE.search(discogs_url)
        if not match:
            log.warning("csv.bad_discogs_url", url=discogs_url)
            return None
        return int(match.group(1))

    @staticmethod
    def extract_master_id(discogs_url: str) -> int | None:
        """Extract the numeric Discogs master ID from a ``/master/12345`` URL.

        Returns ``None`` if the URL does not contain a master path segment.
        """
        match = _MASTER_ID_RE.search(discogs_url)
        if not match:
            return None
        return int(match.group(1))
