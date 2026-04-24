from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from tagger.enricher.discogs.art_downloader import ArtDownloader


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "art_cache"


@pytest.fixture
def downloader(cache_dir: Path) -> ArtDownloader:
    return ArtDownloader(cache_dir=cache_dir)


def test_download_art_success(
    downloader: ArtDownloader, cache_dir: Path, httpx_mock: HTTPXMock
) -> None:
    url = "http://example.com/art.jpg"
    image_content = b"fake-image-data"
    httpx_mock.add_response(url=url, content=image_content)

    path = downloader.download(url, album_id=123)

    assert path is not None
    assert path.exists()
    assert path.read_bytes() == image_content
    assert path.suffix == ".jpg"
    assert "123" in path.name


def test_download_art_cached(
    downloader: ArtDownloader, cache_dir: Path, httpx_mock: HTTPXMock
) -> None:
    url = "http://example.com/art.jpg"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_file = cache_dir / "123.jpg"
    cached_file.write_bytes(b"cached-data")

    path = downloader.download(url, album_id=123)

    assert path == cached_file
    assert path.read_bytes() == b"cached-data"
    # httpx_mock should not have been called
    assert len(httpx_mock.get_requests()) == 0


def test_download_art_failure(downloader: ArtDownloader, httpx_mock: HTTPXMock) -> None:
    url = "http://example.com/art.jpg"
    httpx_mock.add_response(url=url, status_code=404)

    path = downloader.download(url, album_id=123)
    assert path is None
