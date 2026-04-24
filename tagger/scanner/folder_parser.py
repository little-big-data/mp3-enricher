from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# This regex looks for a common separator (`-`, `/`, ``, or `|`) surrounded
# by optional whitespace.
# It captures the non-whitespace characters on either side.
# The groups are named 'artist' and 'album'.
ARTIST_ALBUM_RE = re.compile(r"^(?P<artist>.*?)\s*[-/_\|]\s*(?P<album>.*)$")

COMPILATION_KEYWORDS = {"compilations", "various", "various artists", "va"}


def parse_folder_names(path: Path) -> dict[str, str | None]:
    """
    Parses artist and album names from a given directory path's immediate parent name.
    """
    result: dict[str, str | None] = {"artist_guess": None, "album_guess": None}
    folder_name = path.name.strip()

    if not folder_name:
        return result

    match = ARTIST_ALBUM_RE.match(folder_name)
    if match:
        artist = match.group("artist").strip()
        album = match.group("album").strip()
        if artist:
            result["artist_guess"] = artist
        if album:
            result["album_guess"] = album
        elif artist and not album:  # if album is empty but artist is not, return the artist
            result["album_guess"] = ""  # Explicitly set to empty string
        return result

    # Fallback if no match or if parts are empty after stripping
    result["album_guess"] = folder_name

    # Try parent if we have a parent and don't have an artist guess yet
    if not result["artist_guess"] and path.parent and path.parent.name and path.parent.name != ".":
        parent_name = path.parent.name.strip()
        if parent_name.lower() in COMPILATION_KEYWORDS:
            result["artist_guess"] = "Various"
        else:
            result["artist_guess"] = parent_name

    return result
