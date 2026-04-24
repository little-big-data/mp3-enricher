"""End-to-end integration test: Scan → Enrich → Write pipeline.

Exercises the full data flow using a real SQLite database, real MP3 files
on disk, and mocked HTTP responses for all external APIs (Discogs, Wikipedia).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from mutagen.id3 import ID3
from pytest_httpx import HTTPXMock

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.enricher.discogs.client import DiscogsClient
from tagger.enricher.heuristic_enricher import HeuristicEnricher
from tagger.enricher.pipeline import EnrichmentPipeline
from tagger.enricher.web.scraper import WebScraper
from tagger.scanner.folder_parser import parse_folder_names
from tagger.scanner.id3_reader import read_id3_tags
from tagger.scanner.walker import find_mp3_files
from tagger.writer.id3_writer import ID3Writer


def _make_mp3(path: Path, title: str, track_num: int) -> Path:
    """Write a minimal valid MP3 with title and track-number tags."""
    from mutagen.id3 import TIT2, TRCK

    # Four 144-byte MPEG1 Layer3 frames (32kbps, 32000Hz, mono)
    frame = b"\xff\xfb\x18\xc0" + b"\x00" * 140
    path.write_bytes(frame * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TRCK(encoding=3, text=str(track_num)))
    tags.save(str(path))
    return path


def _register_discogs_mocks(httpx_mock: HTTPXMock, artist: str, album: str) -> None:
    """Register all Discogs API mock responses for a two-track release."""
    artist_q = artist.replace(" ", "+")
    album_q = album.replace(" ", "+")

    # Search
    httpx_mock.add_response(
        url=(
            f"https://api.discogs.com/database/search"
            f"?artist={artist_q}&release_title={album_q}&type=release"
        ),
        json={
            "results": [
                {
                    "id": 75544,
                    "type": "release",
                    "master_id": 207276,
                    "title": f"{artist} - {album}",
                    "year": "1989",
                    "resource_url": "https://api.discogs.com/releases/75544",
                }
            ]
        },
    )

    # Master versions
    httpx_mock.add_response(
        url="https://api.discogs.com/masters/207276/versions",
        json={
            "versions": [
                {
                    "id": 75544,
                    "title": album,
                    "released": "1989",
                    "resource_url": "https://api.discogs.com/releases/75544",
                }
            ]
        },
    )

    # Full release details (two tracks so both MP3s get enriched).
    # Registered twice: selector calls it once for track-count verification,
    # then the pipeline calls it again to build enrichment data.
    release_payload = {
        "id": 75544,
        "title": album,
        "year": 1989,
        "artists": [{"name": artist, "id": 1}],
        "images": [],
        "tracklist": [
            {"position": "1", "title": "Head Like A Hole", "duration": "4:59"},
            {"position": "2", "title": "Terrible Lie", "duration": "4:37"},
        ],
        "resource_url": "https://api.discogs.com/releases/75544",
    }
    httpx_mock.add_response(url="https://api.discogs.com/releases/75544", json=release_payload)
    httpx_mock.add_response(url="https://api.discogs.com/releases/75544", json=release_payload)

    # Artist profile
    httpx_mock.add_response(
        url="https://api.discogs.com/artists/1",
        json={
            "id": 1,
            "name": artist,
            "profile": (
                "Nine Inch Nails is an American industrial rock band formed in 1988 in "
                "Cleveland, Ohio by Trent Reznor."
            ),
            "resource_url": "https://api.discogs.com/artists/1",
        },
    )


def _register_wikipedia_mock(httpx_mock: HTTPXMock, artist: str) -> None:
    """Register a Wikipedia mock response for the given artist."""
    wiki_slug = artist.replace(" ", "%20")
    httpx_mock.add_response(
        url=f"https://en.wikipedia.org/wiki/{wiki_slug}",
        text=(
            '<div id="mw-content-text">'
            "<p>Nine Inch Nails is an American industrial rock band formed in 1988 in "
            "Cleveland, Ohio. The group is fronted by Trent Reznor, who has been the "
            "primary creative force and only constant member throughout its history. "
            "Their music blends electronic, industrial, and metal influences.</p>"
            "</div>"
        ),
    )


@pytest.mark.integration
def test_e2e_scan_enrich_write(
    tmp_path: Path, db_conn: sqlite3.Connection, httpx_mock: HTTPXMock
) -> None:
    """Full pipeline: scan real MP3s, enrich via mocked HTTP, write tags to disk."""
    run_migrations(db_conn)

    # --- SCAN PHASE ---
    album_dir = tmp_path / "Nine Inch Nails - Pretty Hate Machine"
    album_dir.mkdir()
    mp3_1 = _make_mp3(album_dir / "01.mp3", title="Original Title 1", track_num=1)
    mp3_2 = _make_mp3(album_dir / "02.mp3", title="Original Title 2", track_num=2)

    mp3_files = find_mp3_files(tmp_path)
    assert len(mp3_files) == 2

    guesses = parse_folder_names(album_dir)
    assert guesses["artist_guess"] == "Nine Inch Nails"
    assert guesses["album_guess"] == "Pretty Hate Machine"

    album_repo = AlbumRepository(db_conn)
    track_repo = TrackRepository(db_conn)

    album = AlbumRecord(
        folder_path=str(album_dir),
        artist_guess=guesses.get("artist_guess"),
        album_guess=guesses.get("album_guess"),
    )
    with db_conn:
        album_repo.upsert(album)
    saved_album = album_repo.get_by_folder_path(str(album_dir))
    assert saved_album is not None
    album_id = saved_album.id
    assert album_id is not None

    for mp3 in mp3_files:
        id3 = read_id3_tags(mp3)
        record = TrackRecord(
            album_id=album_id,
            file_path=str(mp3),
            filename=mp3.name,
            track_number=id3.get("track_number"),
            existing_title=id3.get("title"),
            existing_artist=id3.get("artist"),
        )
        with db_conn:
            track_repo.upsert(record)

    assert len(track_repo.get_by_album(album_id)) == 2

    # --- ENRICH PHASE ---
    _register_discogs_mocks(httpx_mock, "Nine Inch Nails", "Pretty Hate Machine")
    _register_wikipedia_mock(httpx_mock, "Nine Inch Nails")

    discogs_client = DiscogsClient(token="test_token")
    scraper = WebScraper()
    enricher = HeuristicEnricher()
    pipeline = EnrichmentPipeline(
        album_repo=album_repo,
        track_repo=track_repo,
        discogs_client=discogs_client,
        scraper=scraper,
        enricher=enricher,
        mb_client=None,  # skip MusicBrainz for this test
    )

    pipeline.enrich_album(saved_album)

    # Verify DB was updated
    updated_album = album_repo.get_by_folder_path(str(album_dir))
    assert updated_album is not None
    assert updated_album.enrichment_status == "found"
    assert updated_album.discogs_release_id == 75544

    enriched_tracks = track_repo.get_by_album(album_id)
    assert all(t.enrichment_status == "found" for t in enriched_tracks)
    assert all(t.album_title == "Pretty Hate Machine" for t in enriched_tracks)
    assert all(t.year == 1989 for t in enriched_tracks)
    assert all(t.genre is not None for t in enriched_tracks)

    track_titles = {t.title for t in enriched_tracks}
    assert track_titles == {"Head Like A Hole", "Terrible Lie"}

    # --- WRITE PHASE ---
    writer = ID3Writer(track_repo)
    success, errors = writer.write_pending()

    assert success == 2
    assert errors == 0

    # Verify final ID3 tags written to disk
    tags_1 = ID3(str(mp3_1))
    tags_2 = ID3(str(mp3_2))

    assert str(tags_1["TIT2"]) == "Head Like A Hole"
    assert str(tags_2["TIT2"]) == "Terrible Lie"

    for tags in (tags_1, tags_2):
        assert str(tags["TALB"]) == "Pretty Hate Machine"
        assert str(tags["TDRC"]) == "1989" or str(tags.get("TYER", "")) == "1989"

    # Verify all tracks marked done in DB
    remaining_pending = track_repo.get_pending_write()
    assert remaining_pending == []


@pytest.mark.integration
def test_e2e_enrich_no_discogs_match(
    tmp_path: Path, db_conn: sqlite3.Connection, httpx_mock: HTTPXMock
) -> None:
    """When Discogs returns no results, album is marked not_found and write phase is a no-op."""
    run_migrations(db_conn)

    album_dir = tmp_path / "Unknown Artist - Obscure Album"
    album_dir.mkdir()
    mp3 = album_dir / "01.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    tags.save(str(mp3))

    album_repo = AlbumRepository(db_conn)
    track_repo = TrackRepository(db_conn)

    album = AlbumRecord(
        folder_path=str(album_dir),
        artist_guess="Unknown Artist",
        album_guess="Obscure Album",
    )
    with db_conn:
        album_repo.upsert(album)
        track_repo.upsert(
            TrackRecord(
                album_id=album_repo.get_by_folder_path(str(album_dir)).id,  # type: ignore[union-attr]
                file_path=str(mp3),
                filename=mp3.name,
            )
        )
    saved_album = album_repo.get_by_folder_path(str(album_dir))
    assert saved_album is not None

    # Mock Discogs: no results for primary search and fallback (empty artist)
    httpx_mock.add_response(
        url=(
            "https://api.discogs.com/database/search"
            "?artist=Unknown+Artist&release_title=Obscure+Album&type=release"
        ),
        json={"results": []},
    )
    httpx_mock.add_response(
        url=(
            "https://api.discogs.com/database/search"
            "?artist=&release_title=Obscure+Album&type=release"
        ),
        json={"results": []},
    )

    pipeline = EnrichmentPipeline(
        album_repo=album_repo,
        track_repo=track_repo,
        discogs_client=DiscogsClient(token="test_token"),
        scraper=WebScraper(),
        enricher=HeuristicEnricher(),
        mb_client=None,
    )
    pipeline.enrich_album(saved_album)

    updated = album_repo.get_by_folder_path(str(album_dir))
    assert updated is not None
    assert updated.enrichment_status == "not_found"

    # Write phase: nothing enriched, so nothing written
    writer = ID3Writer(track_repo)
    success, errors = writer.write_pending()
    assert success == 0
    assert errors == 0


@pytest.mark.integration
def test_e2e_write_phase_idempotent(
    tmp_path: Path, db_conn: sqlite3.Connection, httpx_mock: HTTPXMock
) -> None:
    """Running write_pending twice only writes once — second pass is a no-op."""
    run_migrations(db_conn)

    album_dir = tmp_path / "Nine Inch Nails - Pretty Hate Machine"
    album_dir.mkdir()
    mp3 = _make_mp3(album_dir / "01.mp3", title="Original", track_num=1)

    album_repo = AlbumRepository(db_conn)
    track_repo = TrackRepository(db_conn)

    with db_conn:
        album_repo.upsert(AlbumRecord(folder_path=str(album_dir)))
    saved_album = album_repo.get_by_folder_path(str(album_dir))
    assert saved_album is not None
    assert saved_album.id is not None

    record = TrackRecord(
        album_id=saved_album.id,
        file_path=str(mp3),
        filename=mp3.name,
        track_number=1,
        title="Head Like A Hole",
        artist="Nine Inch Nails",
        album_artist="Nine Inch Nails",
        album_title="Pretty Hate Machine",
        year=1989,
        track_num="01/10",
        genre="Industrial",
        grouping="Origin:Cleveland, US | Gender:Male",
        enrichment_status="found",
        written_status="pending",
    )
    with db_conn:
        track_repo.upsert(record)

    writer = ID3Writer(track_repo)

    s1, e1 = writer.write_pending()
    assert s1 == 1
    assert e1 == 0

    s2, e2 = writer.write_pending()  # second pass — nothing pending
    assert s2 == 0
    assert e2 == 0

    assert str(ID3(str(mp3))["TIT2"]) == "Head Like A Hole"
