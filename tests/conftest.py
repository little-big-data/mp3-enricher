from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from tagger.db.connection import get_db_connection


@pytest.fixture
def db_conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Provides a file-based SQLite connection configured for parallel access."""
    conn = get_db_connection(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture
def sample_mp3(tmp_path: Path) -> Path:
    """Creates a minimal valid MP3 file with mutagen tags."""
    from mutagen.id3 import ID3, TIT2

    path = tmp_path / "track.mp3"
    # write minimal valid MP3 header bytes
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Original Title"))
    tags.save(str(path))
    return path
