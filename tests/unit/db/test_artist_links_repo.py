"""Unit tests for tagger.db.artist_links_repo.ArtistLinksRepository."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from tagger.db.artist_links_repo import ArtistLinksRepository
from tagger.db.connection import get_db_connection, run_migrations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """File-based SQLite connection with all migrations applied."""
    c = get_db_connection(tmp_path / "test.db")
    run_migrations(c)
    yield c
    c.close()


@pytest.fixture
def repo(conn: sqlite3.Connection) -> ArtistLinksRepository:
    return ArtistLinksRepository(conn)


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_stores_link(repo: ArtistLinksRepository) -> None:
    repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=0.95)
    assert repo.get_links("GZA") == ["Wu-Tang Clan"]


def test_upsert_multiple_links_for_same_artist(
    repo: ArtistLinksRepository,
) -> None:
    repo.upsert("RZA", "Wu-Tang Clan", source="llm", confidence=1.0)
    repo.upsert("RZA", "Gravediggaz", source="llm", confidence=0.9)
    links = repo.get_links("RZA")
    assert "Wu-Tang Clan" in links
    assert "Gravediggaz" in links
    assert len(links) == 2


def test_upsert_is_idempotent(repo: ArtistLinksRepository) -> None:
    repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=0.95)
    repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=0.95)  # duplicate
    assert repo.get_links("GZA") == ["Wu-Tang Clan"]


def test_upsert_duplicate_updates_source_and_confidence(
    repo: ArtistLinksRepository,
) -> None:
    repo.upsert("GZA", "Wu-Tang Clan", source="heuristic", confidence=0.5)
    repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=0.95)
    rows = repo.get_all()
    match = next(r for r in rows if r[0] == "GZA" and r[1] == "Wu-Tang Clan")
    assert match[2] == "llm"  # source updated


# ---------------------------------------------------------------------------
# get_links
# ---------------------------------------------------------------------------


def test_get_links_unknown_artist_returns_empty(
    repo: ArtistLinksRepository,
) -> None:
    assert repo.get_links("Unknown Artist") == []


def test_get_links_returns_only_matching_artist(
    repo: ArtistLinksRepository,
) -> None:
    repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=1.0)
    repo.upsert("Raekwon", "Wu-Tang Clan", source="llm", confidence=1.0)
    assert repo.get_links("GZA") == ["Wu-Tang Clan"]


# ---------------------------------------------------------------------------
# get_all
# ---------------------------------------------------------------------------


def test_get_all_returns_all_rows(repo: ArtistLinksRepository) -> None:
    repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=1.0)
    repo.upsert("Raekwon", "Wu-Tang Clan", source="llm", confidence=1.0)
    repo.upsert("Efdemin", "Giegling", source="heuristic", confidence=0.7)
    rows = repo.get_all()
    assert len(rows) == 3
    artists = [r[0] for r in rows]
    assert "GZA" in artists
    assert "Raekwon" in artists
    assert "Efdemin" in artists


def test_get_all_empty_db_returns_empty_list(repo: ArtistLinksRepository) -> None:
    assert repo.get_all() == []


# ---------------------------------------------------------------------------
# get_link_tag_value
# ---------------------------------------------------------------------------


def test_get_link_tag_value_single(repo: ArtistLinksRepository) -> None:
    repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=1.0)
    assert repo.get_link_tag_value("GZA") == "Wu-Tang Clan"


def test_get_link_tag_value_multiple(repo: ArtistLinksRepository) -> None:
    repo.upsert("RZA", "Wu-Tang Clan", source="llm", confidence=1.0)
    repo.upsert("RZA", "Gravediggaz", source="llm", confidence=0.9)
    value = repo.get_link_tag_value("RZA")
    # Both links present, comma-separated
    assert "Wu-Tang Clan" in value
    assert "Gravediggaz" in value
    assert ", " in value


def test_get_link_tag_value_none_returns_none(
    repo: ArtistLinksRepository,
) -> None:
    assert repo.get_link_tag_value("Unknown Artist") is None
