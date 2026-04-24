"""Integration tests for LinkScanner — real SQLite, mocked LLM."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tagger.db.album_repo import AlbumRepository
from tagger.db.artist_links_repo import ArtistLinksRepository
from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.enricher.link_scanner import LinkScanner
from tagger.enricher.llm.base import LLMClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    c = get_db_connection(tmp_path / "test.db")
    run_migrations(c)
    yield c
    c.close()


@pytest.fixture
def album_repo(conn: sqlite3.Connection) -> AlbumRepository:
    return AlbumRepository(conn)


@pytest.fixture
def track_repo(conn: sqlite3.Connection) -> TrackRepository:
    return TrackRepository(conn)


@pytest.fixture
def links_repo(conn: sqlite3.Connection) -> ArtistLinksRepository:
    return ArtistLinksRepository(conn)


def _make_album(
    conn: sqlite3.Connection,
    artist: str,
    album: str,
    folder: str = "/music/artist/album",
    status: str = "found",
) -> AlbumRecord:
    repo = AlbumRepository(conn)
    record = AlbumRecord(
        folder_path=folder,
        artist_guess=artist,
        album_guess=album,
        enrichment_status=status,
    )
    repo.upsert(record)
    return repo.get_by_folder_path(folder)  # type: ignore[return-value]


def _make_track(
    conn: sqlite3.Connection,
    album_id: int,
    title: str,
    file_path: str,
) -> None:
    repo = TrackRepository(conn)
    track = TrackRecord(
        album_id=album_id,
        file_path=file_path,
        filename=file_path.split("/")[-1],
        existing_title=title,
        title=title,
        grouping="Origin:New York, US | Gender:Male",
    )
    repo.upsert(track)


def _mock_llm(links: list[str]) -> LLMClient:
    mock = MagicMock(spec=LLMClient)
    mock.detect_links.return_value = links
    return mock


# ---------------------------------------------------------------------------
# LinkScanner.scan_artist
# ---------------------------------------------------------------------------


def test_scan_artist_stores_link_in_cache(
    conn: sqlite3.Connection,
    links_repo: ArtistLinksRepository,
    track_repo: TrackRepository,
) -> None:
    album = _make_album(conn, "GZA", "Liquid Swords", "/music/GZA/Liquid Swords")
    assert album.id is not None
    _make_track(conn, album.id, "Shadowboxin' (feat. Method Man)", "/music/GZA/01.mp3")

    llm = _mock_llm(["Wu-Tang Clan"])
    scanner = LinkScanner(
        links_repo=links_repo,
        track_repo=track_repo,
        llm_client=llm,
    )
    scanner.scan_artist(artist="GZA", album_ids=[album.id])

    assert links_repo.get_links("GZA") == ["Wu-Tang Clan"]


def test_scan_artist_passes_featured_artists_to_llm(
    conn: sqlite3.Connection,
    links_repo: ArtistLinksRepository,
    track_repo: TrackRepository,
) -> None:
    album = _make_album(conn, "GZA", "Liquid Swords", "/music/GZA/Liquid Swords")
    assert album.id is not None
    _make_track(conn, album.id, "Shadowboxin' (feat. Method Man)", "/music/GZA/01.mp3")
    _make_track(conn, album.id, "Labels (feat. RZA)", "/music/GZA/02.mp3")

    llm = _mock_llm(["Wu-Tang Clan"])
    scanner = LinkScanner(
        links_repo=links_repo,
        track_repo=track_repo,
        llm_client=llm,
    )
    scanner.scan_artist(artist="GZA", album_ids=[album.id])

    call_kwargs = llm.detect_links.call_args.kwargs
    featured = call_kwargs["featured_artists"]
    assert "Method Man" in featured
    assert "RZA" in featured


def test_scan_artist_skips_if_already_cached(
    conn: sqlite3.Connection,
    links_repo: ArtistLinksRepository,
    track_repo: TrackRepository,
) -> None:
    links_repo.upsert("GZA", "Wu-Tang Clan", source="llm", confidence=1.0)

    llm = _mock_llm(["Wu-Tang Clan"])
    scanner = LinkScanner(
        links_repo=links_repo,
        track_repo=track_repo,
        llm_client=llm,
    )
    scanner.scan_artist(artist="GZA", album_ids=[1])

    llm.detect_links.assert_not_called()


def test_scan_artist_no_link_found(
    conn: sqlite3.Connection,
    links_repo: ArtistLinksRepository,
    track_repo: TrackRepository,
) -> None:
    album = _make_album(conn, "Unknown Solo", "Some Album", "/music/Unknown/Album")
    assert album.id is not None

    llm = _mock_llm([])
    scanner = LinkScanner(
        links_repo=links_repo,
        track_repo=track_repo,
        llm_client=llm,
    )
    scanner.scan_artist(artist="Unknown Solo", album_ids=[album.id])

    assert links_repo.get_links("Unknown Solo") == []


# ---------------------------------------------------------------------------
# LinkScanner.update_grouping_tag
# ---------------------------------------------------------------------------


def test_update_grouping_tag_appends_link_segment() -> None:
    existing = "Origin:New York, US | Gender:Male"
    result = LinkScanner.update_grouping_tag(existing, "Wu-Tang Clan")
    assert "link:Wu-Tang Clan" in result
    assert "Origin:New York, US" in result


def test_update_grouping_tag_replaces_existing_link_segment() -> None:
    existing = "Origin:New York, US | link:Old Value"
    result = LinkScanner.update_grouping_tag(existing, "Wu-Tang Clan")
    assert "link:Wu-Tang Clan" in result
    assert "Old Value" not in result


def test_update_grouping_tag_empty_existing() -> None:
    result = LinkScanner.update_grouping_tag("", "Wu-Tang Clan")
    assert result == "link:Wu-Tang Clan"


def test_update_grouping_tag_none_link_removes_segment() -> None:
    existing = "Origin:New York, US | link:Old Value"
    result = LinkScanner.update_grouping_tag(existing, None)
    assert "link:" not in result
    assert "Origin:New York, US" in result


# ---------------------------------------------------------------------------
# LinkScanner.filter_to_library
# ---------------------------------------------------------------------------


def test_filter_to_library_keeps_only_present_artists() -> None:
    """Links are filtered to those whose artist name is in library_artists."""
    library = frozenset(["Godflesh", "Greymachine", "Jesu"])
    links = ["Greymachine", "Jesu", "Techno Animal", "Scorn"]

    result = LinkScanner.filter_to_library(links, library)

    assert result == ["Greymachine", "Jesu"]
    assert "Techno Animal" not in result
    assert "Scorn" not in result


def test_filter_to_library_case_insensitive() -> None:
    """Matching is case-insensitive: 'GODFLESH' matches library entry 'Godflesh'."""
    library = frozenset(["Godflesh", "Greymachine"])
    links = ["GREYMACHINE", "godflesh", "Techno Animal"]

    result = LinkScanner.filter_to_library(links, library)

    assert len(result) == 2
    assert "Techno Animal" not in result


def test_filter_to_library_empty_links() -> None:
    result = LinkScanner.filter_to_library([], frozenset(["Godflesh"]))
    assert result == []


def test_filter_to_library_empty_library() -> None:
    result = LinkScanner.filter_to_library(["Godflesh", "Greymachine"], frozenset())
    assert result == []


def test_filter_to_library_all_match() -> None:
    library = frozenset(["Godflesh", "Greymachine"])
    links = ["Godflesh", "Greymachine"]
    result = LinkScanner.filter_to_library(links, library)
    assert result == ["Godflesh", "Greymachine"]


def test_filter_to_library_preserves_original_case_of_link() -> None:
    """The returned list preserves the link name's original casing."""
    library = frozenset(["greymachine"])
    links = ["Greymachine"]

    result = LinkScanner.filter_to_library(links, library)

    assert result == ["Greymachine"]  # original casing preserved, not lowercased
