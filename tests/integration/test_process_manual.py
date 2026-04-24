"""Integration test: process-manual CLI command enriches and writes tags to MP3 files.

Verifies the full manual-correction flow: pre-populate DB with a not_found album,
create a manual_review CSV, mock Discogs API, invoke process-manual via CLI,
and assert enriched ID3 tags are written to disk.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from click.testing import CliRunner
from mutagen.id3 import ID3
from pytest_httpx import HTTPXMock

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.mp3_tagger import cli


def _make_mp3(path: Path, title: str, track_num: int) -> Path:
    """Write a minimal valid MP3 with ID3 title and track-number tags."""
    from mutagen.id3 import TIT2, TRCK

    frame = b"\xff\xfb\x18\xc0" + b"\x00" * 140
    path.write_bytes(frame * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TRCK(encoding=3, text=str(track_num)))
    tags.save(str(path))
    return path


@pytest.mark.integration
def test_process_manual_writes_tags_to_disk(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """process-manual should enrich DB records AND write enriched tags to MP3 files on disk."""
    db_path = tmp_path / "test.db"
    conn = get_db_connection(db_path)
    run_migrations(conn)

    # --- Filesystem: two real MP3 files ---
    album_dir = tmp_path / "Nine Inch Nails - Pretty Hate Machine"
    album_dir.mkdir()
    mp3_1 = _make_mp3(album_dir / "01.mp3", title="Old Title 1", track_num=1)
    mp3_2 = _make_mp3(album_dir / "02.mp3", title="Old Title 2", track_num=2)

    # --- DB: album in not_found state + raw (unenriched) tracks ---
    album_repo = AlbumRepository(conn)
    track_repo = TrackRepository(conn)

    album = AlbumRecord(
        folder_path=str(album_dir),
        artist_guess="Nine Inch Nails",
        album_guess="Pretty Hate Machine",
        enrichment_status="not_found",
    )
    with conn:
        album_repo.upsert(album)

    saved_album = album_repo.get_by_folder_path(str(album_dir))
    assert saved_album is not None
    album_id = saved_album.id
    assert album_id is not None

    for mp3, num in [(mp3_1, 1), (mp3_2, 2)]:
        with conn:
            track_repo.upsert(
                TrackRecord(
                    album_id=album_id,
                    file_path=str(mp3),
                    filename=mp3.name,
                    track_number=num,
                    existing_title=f"Old Title {num}",
                )
            )

    conn.close()  # CLI opens its own connection to the same file

    # --- CSV: manual correction with a Discogs release URL ---
    csv_path = tmp_path / "manual_review.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer_csv = csv.DictWriter(
            fh,
            fieldnames=[
                "album_id",
                "folder_path",
                "artist_guess",
                "album_guess",
                "reason",
                "user_discogs_url",
            ],
        )
        writer_csv.writeheader()
        writer_csv.writerow(
            {
                "album_id": album_id,
                "folder_path": str(album_dir),
                "artist_guess": "Nine Inch Nails",
                "album_guess": "Pretty Hate Machine",
                "reason": "No Discogs match",
                "user_discogs_url": "https://www.discogs.com/release/75544",
            }
        )

    # --- HTTP mocks: Discogs release (fetched twice: enrich + art-check) ---
    release_payload = {
        "id": 75544,
        "title": "Pretty Hate Machine",
        "year": 1989,
        "artists": [{"name": "Nine Inch Nails", "id": 1}],
        "images": [],  # No art — keeps test focused on tag writing
        "tracklist": [
            {"position": "1", "title": "Head Like A Hole", "duration": "4:59"},
            {"position": "2", "title": "Terrible Lie", "duration": "4:37"},
        ],
        "resource_url": "https://api.discogs.com/releases/75544",
    }
    httpx_mock.add_response(url="https://api.discogs.com/releases/75544", json=release_payload)
    httpx_mock.add_response(url="https://api.discogs.com/releases/75544", json=release_payload)

    # Discogs artist detail (best-effort — pipeline catches failures)
    httpx_mock.add_response(
        url="https://api.discogs.com/artists/1",
        json={
            "id": 1,
            "name": "Nine Inch Nails",
            "profile": "American industrial rock band.",
            "resource_url": "https://api.discogs.com/artists/1",
        },
    )

    # Wikipedia: 200 with minimal content so scraper doesn't try suffix variants
    httpx_mock.add_response(
        url="https://en.wikipedia.org/wiki/Nine%20Inch%20Nails",
        text=(
            '<div id="mw-content-text">'
            "<p>Nine Inch Nails is an American industrial rock band formed in 1988.</p>"
            "</div>"
        ),
    )

    # MusicBrainz search — pipeline calls find_links + find_area (2 searches)
    httpx_mock.add_response(
        url="https://musicbrainz.org/ws/2/artist?query=Nine+Inch+Nails&fmt=json&limit=5",
        json={"artists": []},
    )
    httpx_mock.add_response(
        url="https://musicbrainz.org/ws/2/artist?query=Nine+Inch+Nails&fmt=json&limit=5",
        json={"artists": []},
    )

    # --- Invoke the CLI ---
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "process-manual",
            "--db-path",
            str(db_path),
            "--token",
            "test_token",
            "--csv-path",
            str(csv_path),
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    assert "[SUCCESS]" in result.output

    # --- Assert: tags were written to the MP3 files on disk ---
    tags_1 = ID3(str(mp3_1))
    tags_2 = ID3(str(mp3_2))

    assert str(tags_1["TIT2"]) == "Head Like A Hole"
    assert str(tags_2["TIT2"]) == "Terrible Lie"

    for tags in (tags_1, tags_2):
        assert str(tags["TALB"]) == "Pretty Hate Machine"
        assert str(tags.get("TDRC", tags.get("TYER", ""))) == "1989"
