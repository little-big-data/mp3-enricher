from __future__ import annotations

from pathlib import Path

from pytest_httpx import HTTPXMock

from tagger.enricher.discogs.art_downloader import ArtDownloader
from tagger.enricher.discogs.client import DiscogsClient
from tagger.enricher.discogs.release_selector import ReleaseSelector


def test_full_discogs_workflow(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    # Setup
    token = "test_token"
    client = DiscogsClient(token=token)
    selector = ReleaseSelector(client=client)
    cache_dir = tmp_path / "art_cache"
    downloader = ArtDownloader(cache_dir=cache_dir)

    artist = "Nine Inch Nails"
    album = "Pretty Hate Machine"

    # 1. Search for album
    artist_q = artist.replace(" ", "+")
    album_q = album.replace(" ", "+")
    search_url = (
        f"https://api.discogs.com/database/search?artist={artist_q}&"
        f"release_title={album_q}&type=release"
    )
    httpx_mock.add_response(
        url=search_url,
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

    # 2. Get master versions
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

    # 3. Get release details
    httpx_mock.add_response(
        url="https://api.discogs.com/releases/75544",
        json={
            "id": 75544,
            "title": album,
            "year": 1989,
            "artists": [{"name": artist, "id": 1}],
            "images": [{"type": "primary", "resource_url": "http://example.com/art.jpg"}],
            "tracklist": [{"position": "1", "title": "Head Like A Hole", "duration": "4:59"}],
        },
    )

    # 4. Download art
    httpx_mock.add_response(url="http://example.com/art.jpg", content=b"image-data")

    # Execution
    # Find best release
    best_match = selector.find_best_release(artist, album)
    assert best_match is not None
    assert best_match.id == 75544

    # Fetch full details
    release = client.get_release(best_match.id)
    assert release.title == album
    assert len(release.images) > 0

    # Download art
    art_path = downloader.download(str(release.images[0].resource_url), album_id=best_match.id)
    assert art_path is not None
    assert art_path.exists()
    assert art_path.read_bytes() == b"image-data"
