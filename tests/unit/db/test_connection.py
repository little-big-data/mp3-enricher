from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tagger.db.connection import get_db_connection, run_migrations


@pytest.mark.unit
def test_run_migrations_applies_initial_schema(db_conn: sqlite3.Connection) -> None:
    """Test that run_migrations correctly applies all migration files."""
    # Act
    run_migrations(db_conn)

    # Assert - check if tables exist
    cursor = db_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='albums'")
    assert cursor.fetchone() is not None

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'")
    assert cursor.fetchone() is not None

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'")
    assert cursor.fetchone() is not None

    # Check if migration is recorded
    cursor.execute("SELECT name FROM _migrations WHERE name='001_initial.sql'")
    assert cursor.fetchone() is not None


@pytest.mark.unit
def test_run_migrations_is_idempotent(db_conn: sqlite3.Connection) -> None:
    """Test that run_migrations can be called multiple times safely."""
    # Act
    run_migrations(db_conn)
    run_migrations(db_conn)

    # Assert - should still have the table
    cursor = db_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='albums'")
    assert cursor.fetchone() is not None


@pytest.mark.unit
def test_get_db_connection_enables_wal_mode(tmp_path: Path) -> None:
    """get_db_connection configures WAL journal mode for parallel access."""
    conn = get_db_connection(tmp_path / "test.db")
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    conn.close()


@pytest.mark.unit
def test_get_db_connection_allows_cross_thread_use(tmp_path: Path) -> None:
    """Connection created by get_db_connection can be used from another thread."""
    import threading

    conn = get_db_connection(tmp_path / "test.db")
    run_migrations(conn)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    conn.close()
    assert errors == []
