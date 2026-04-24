"""Unit tests for tagger.enricher.prefill — master URL pre-filling logic."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from tagger.enricher.discogs.models import DiscogsSearchResult
from tagger.enricher.prefill import best_master_url, get_track_artist, prefill_pass1, prefill_pass2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    title: str,
    release_id: int = 1,
    master_id: int | None = None,
) -> DiscogsSearchResult:
    return DiscogsSearchResult(
        id=release_id,
        type="release",
        master_id=master_id,
        title=title,
        resource_url=f"https://api.discogs.com/releases/{release_id}",  # type: ignore[arg-type]
    )


def _mock_client(results: list[DiscogsSearchResult]) -> MagicMock:
    client = MagicMock()
    client.search_album.return_value = results
    return client


# ---------------------------------------------------------------------------
# best_master_url
# ---------------------------------------------------------------------------


def test_best_master_url_returns_master_url_when_master_id_present() -> None:
    client = _mock_client([_make_result("Kind of Blue", release_id=42, master_id=99)])
    url = best_master_url("Miles Davis", "Kind of Blue", client)
    assert url == "https://www.discogs.com/master/99"


def test_best_master_url_falls_back_to_release_url_when_no_master() -> None:
    client = _mock_client([_make_result("Kind of Blue", release_id=42, master_id=None)])
    url = best_master_url("Miles Davis", "Kind of Blue", client)
    assert url == "https://www.discogs.com/release/42"


def test_best_master_url_returns_none_when_no_results() -> None:
    client = _mock_client([])
    url = best_master_url("Miles Davis", "Kind of Blue", client)
    assert url is None


def test_best_master_url_returns_none_when_fuzzy_score_below_40() -> None:
    client = _mock_client([_make_result("Completely Unrelated Title ZZZQQQ", release_id=1)])
    url = best_master_url("Miles Davis", "Kind of Blue", client)
    assert url is None


def test_best_master_url_picks_best_match_by_fuzzy_score() -> None:
    """When multiple results exist, the best fuzzy match is chosen."""
    client = _mock_client(
        [
            _make_result("Something Else", release_id=1, master_id=10),
            _make_result("Kind of Blue", release_id=2, master_id=20),
            _make_result("Kind of Green", release_id=3, master_id=30),
        ]
    )
    url = best_master_url("Miles Davis", "Kind of Blue", client)
    assert url == "https://www.discogs.com/master/20"


def test_best_master_url_returns_none_on_api_exception() -> None:
    client = MagicMock()
    client.search_album.side_effect = RuntimeError("network error")
    url = best_master_url("Miles Davis", "Kind of Blue", client)
    assert url is None


# ---------------------------------------------------------------------------
# get_track_artist
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            folder_path TEXT UNIQUE NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            album_id INTEGER NOT NULL,
            existing_artist TEXT
        )
    """)
    conn.commit()
    return conn


def test_get_track_artist_returns_most_common(mem_db: sqlite3.Connection) -> None:
    mem_db.execute("INSERT INTO albums (id, folder_path) VALUES (1, '/music/artist/album')")
    for artist in ["Ulaan Kohl", "Ulaan Kohl", "Steven R. Smith"]:
        mem_db.execute("INSERT INTO tracks (album_id, existing_artist) VALUES (1, ?)", (artist,))
    mem_db.commit()
    result = get_track_artist("/music/artist/album", mem_db)
    assert result == "Ulaan Kohl"


def test_get_track_artist_returns_none_for_unknown_path(mem_db: sqlite3.Connection) -> None:
    result = get_track_artist("/does/not/exist", mem_db)
    assert result is None


def test_get_track_artist_ignores_empty_artist(mem_db: sqlite3.Connection) -> None:
    mem_db.execute("INSERT INTO albums (id, folder_path) VALUES (1, '/music/artist/album')")
    for artist in ["", None, ""]:
        mem_db.execute("INSERT INTO tracks (album_id, existing_artist) VALUES (1, ?)", (artist,))
    mem_db.commit()
    result = get_track_artist("/music/artist/album", mem_db)
    assert result is None


# ---------------------------------------------------------------------------
# prefill_pass1 — track-count mismatches
# ---------------------------------------------------------------------------


def test_pass1_fills_rows_with_track_count_reason() -> None:
    rows = [
        {
            "reason": "No Discogs version with 12 tracks",
            "artist_guess": "Miles Davis",
            "album_guess": "Kind of Blue",
            "user_discogs_url": "",
        },
        {
            "reason": "No Discogs match",
            "artist_guess": "Other",
            "album_guess": "Other",
            "user_discogs_url": "",
        },
    ]
    client = _mock_client([_make_result("Kind of Blue", release_id=5, master_id=99)])
    filled = prefill_pass1(rows, client)
    assert filled == 1
    assert rows[0]["user_discogs_url"] == "https://www.discogs.com/master/99"
    assert rows[1]["user_discogs_url"] == ""


def test_pass1_skips_rows_that_already_have_url() -> None:
    rows = [
        {
            "reason": "No Discogs version with 12 tracks",
            "artist_guess": "Miles Davis",
            "album_guess": "Kind of Blue",
            "user_discogs_url": "https://www.discogs.com/master/99",
        }
    ]
    client = _mock_client([_make_result("Kind of Blue", release_id=5, master_id=99)])
    filled = prefill_pass1(rows, client)
    assert filled == 0
    client.search_album.assert_not_called()


def test_pass1_handles_no_match_gracefully() -> None:
    rows = [
        {
            "reason": "No Discogs version with 12 tracks",
            "artist_guess": "Unknown",
            "album_guess": "Unknown",
            "user_discogs_url": "",
        }
    ]
    client = _mock_client([])
    filled = prefill_pass1(rows, client)
    assert filled == 0
    assert rows[0]["user_discogs_url"] == ""


# ---------------------------------------------------------------------------
# prefill_pass2 — alias search
# ---------------------------------------------------------------------------


def test_pass2_uses_track_artist_when_different(mem_db: sqlite3.Connection) -> None:
    mem_db.execute("INSERT INTO albums (id, folder_path) VALUES (1, '/music/artist/album')")
    mem_db.execute("INSERT INTO tracks (album_id, existing_artist) VALUES (1, 'Ulaan Kohl')")
    mem_db.commit()

    rows = [
        {
            "reason": "No Discogs match",
            "artist_guess": "Steven R. Smith",
            "album_guess": "Threnody",
            "folder_path": "/music/artist/album",
            "user_discogs_url": "",
        }
    ]
    client = _mock_client([_make_result("Threnody", release_id=7, master_id=77)])
    filled = prefill_pass2(rows, client, mem_db)
    assert filled == 1
    assert "77" in rows[0]["user_discogs_url"]


def test_pass2_skips_when_track_artist_similar_to_album_artist(
    mem_db: sqlite3.Connection,
) -> None:
    mem_db.execute("INSERT INTO albums (id, folder_path) VALUES (1, '/music/artist/album')")
    # Track artist is essentially the same as album artist
    mem_db.execute("INSERT INTO tracks (album_id, existing_artist) VALUES (1, 'Miles Davis')")
    mem_db.commit()

    rows = [
        {
            "reason": "No Discogs match",
            "artist_guess": "Miles Davis",
            "album_guess": "Kind of Blue",
            "folder_path": "/music/artist/album",
            "user_discogs_url": "",
        }
    ]
    client = _mock_client([_make_result("Kind of Blue", release_id=5, master_id=99)])
    filled = prefill_pass2(rows, client, mem_db)
    assert filled == 0
    client.search_album.assert_not_called()


def test_pass2_skips_rows_that_already_have_url(mem_db: sqlite3.Connection) -> None:
    rows = [
        {
            "reason": "No Discogs match",
            "artist_guess": "Artist",
            "album_guess": "Album",
            "folder_path": "/music/artist/album",
            "user_discogs_url": "https://www.discogs.com/master/1",
        }
    ]
    client = _mock_client([])
    filled = prefill_pass2(rows, client, mem_db)
    assert filled == 0
