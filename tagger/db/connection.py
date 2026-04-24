from __future__ import annotations

import sqlite3
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def get_db_connection(db_path: Path) -> sqlite3.Connection:
    """Creates a SQLite connection configured for concurrent parallel access.

    - WAL journal mode: allows concurrent reads alongside a single writer.
    - check_same_thread=False: permits the connection to be shared across threads.
    - timeout=30: threads wait up to 30 s for a write lock before raising.
    """
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    """Discovers and applies SQL migration files from the migrations directory."""
    migrations_dir = Path(__file__).parent / "migrations"

    # Ensure _migrations meta table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Get all .sql migration files sorted
    migration_files = sorted(migrations_dir.glob("*.sql"))

    for migration_file in migration_files:
        name = migration_file.name

        # Check if already applied
        cursor = conn.execute("SELECT name FROM _migrations WHERE name = ?", (name,))
        if cursor.fetchone():
            continue

        log.info("db.migration.applying", name=name)

        sql = migration_file.read_text()
        try:
            with conn:  # Transactional context
                conn.executescript(sql)
                conn.execute("INSERT INTO _migrations (name) VALUES (?)", (name,))
            log.info("db.migration.success", name=name)
        except sqlite3.Error as exc:
            log.error("db.migration.failed", name=name, error=str(exc))
            raise
