from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)


def find_album_dirs(root: Path) -> list[Path]:
    """Return immediate subdirectories of *root* that directly contain MP3 files.

    Intended for artist-root directories whose children are per-album folders.
    Hidden directories (names starting with ``'.'``) are always excluded.
    The result is sorted by name for deterministic processing order.
    """
    if not root.is_dir():
        return []
    result: list[Path] = []
    for subdir in sorted(root.iterdir()):
        if (
            subdir.is_dir()
            and not subdir.name.startswith(".")
            and any(f.is_file() and f.suffix.lower() == ".mp3" for f in subdir.iterdir())
        ):
            result.append(subdir)
    return result


def find_mp3_files(root_dir: Path) -> list[Path]:
    """
    Recursively finds all MP3 files within a given directory, ignoring hidden files and directories.

    Args:
        root_dir: The root directory to start the search from.

    Returns:
        A sorted list of Path objects representing the MP3 files found.
        Returns an empty list if no MP3 files are found or if the root_dir is invalid.
    """
    mp3_files: list[Path] = []
    if not root_dir.is_dir():
        log.warning("walker.invalid_directory", dir_path=str(root_dir))
        return mp3_files

    try:
        # Use rglob to recursively find all files.
        # We need to filter out hidden files and directories.
        # A common convention is names starting with '.'
        for item in root_dir.rglob("*"):
            # Check if the file extension is .mp3 (case-insensitive)
            if item.is_file() and item.suffix.lower() == ".mp3":
                # Check if the file or any of its parent directories are hidden.
                is_hidden = False
                for part in item.parts:
                    if part.startswith("."):
                        is_hidden = True
                        break
                if not is_hidden:
                    mp3_files.append(item)

        # Sort the found files for consistent output and testing
        mp3_files.sort()
        return mp3_files

    except Exception as e:
        log.error(
            "walker.failed_to_scan_directory", dir_path=str(root_dir), error=str(e), exc_info=True
        )
        return []
