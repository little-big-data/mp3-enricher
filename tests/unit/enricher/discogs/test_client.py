from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock

from tagger.enricher.discogs.client import DiscogsClient
from tagger.exceptions import RateLimitError


@pytest.fixture
def discogs_client() -> DiscogsClient:
    return DiscogsClient(token="test_token")


def test_search_album(discogs_client: DiscogsClient, httpx_mock: HTTPXMock) -> None:
    search_response = {
        "results": [
            {
                "id": 1,
                "type": "release",
                "master_id": 10,
                "title": "Artist - Album",
                "year": "1994",
                "resource_url": "https://api.discogs.com/releases/1",
            }
        ]
    }
    httpx_mock.add_response(
        url="https://api.discogs.com/database/search?artist=Artist&release_title=Album&type=release",
        json=search_response,
        match_headers={"Authorization": "Discogs token=test_token"},
    )

    results = discogs_client.search_album("Artist", "Album")
    assert len(results) == 1
    assert results[0].id == 1
    assert results[0].title == "Artist - Album"


def test_get_release(discogs_client: DiscogsClient, httpx_mock: HTTPXMock) -> None:
    release_data = {
        "id": 1,
        "title": "Album",
        "year": 1994,
        "resource_url": "https://api.discogs.com/releases/1",
        "artists": [{"name": "Artist", "id": 100}],
        "images": [{"type": "primary", "resource_url": "http://example.com/art.jpg"}],
        "tracklist": [{"position": "1", "title": "Track 1", "duration": "4:00"}],
    }
    httpx_mock.add_response(
        url="https://api.discogs.com/releases/1",
        json=release_data,
        match_headers={"Authorization": "Discogs token=test_token"},
    )

    release = discogs_client.get_release(1)
    assert release.id == 1
    assert release.title == "Album"
    assert release.artists[0].name == "Artist"


def test_get_master_releases(discogs_client: DiscogsClient, httpx_mock: HTTPXMock) -> None:
    master_versions = {
        "versions": [
            {
                "id": 1,
                "title": "Album (Original)",
                "major_formats": ["Vinyl"],
                "released": "1994",
                "resource_url": "https://api.discogs.com/releases/1",
            },
            {
                "id": 2,
                "title": "Album (Reissue)",
                "major_formats": ["CD"],
                "released": "2000",
                "resource_url": "https://api.discogs.com/releases/2",
            },
        ]
    }
    httpx_mock.add_response(
        url="https://api.discogs.com/masters/10/versions",
        json=master_versions,
        match_headers={"Authorization": "Discogs token=test_token"},
    )

    releases = discogs_client.get_master_releases(10)
    assert len(releases) == 2
    assert releases[0].id == 1
    assert releases[1].id == 2


def test_get_master_releases_includes_major_formats(
    discogs_client: DiscogsClient, httpx_mock: HTTPXMock
) -> None:
    """major_formats (e.g. ['CD']) from master-versions endpoint is merged into format list."""
    httpx_mock.add_response(
        url="https://api.discogs.com/masters/10/versions",
        json={
            "versions": [
                {
                    "id": 1,
                    "title": "Album",
                    "released": "1998",
                    "resource_url": "https://api.discogs.com/releases/1",
                    "major_formats": ["CD"],
                    "format": "Compilation, Club Edition",
                }
            ]
        },
    )
    releases = discogs_client.get_master_releases(10)
    assert "CD" in releases[0].format
    assert "Compilation" in releases[0].format
    assert "Club Edition" in releases[0].format


def test_get_master_releases_parses_format_string(
    discogs_client: DiscogsClient, httpx_mock: HTTPXMock
) -> None:
    """The master-versions endpoint returns 'format' as a comma-separated string;
    it must be split into a list before Pydantic validation."""
    httpx_mock.add_response(
        url="https://api.discogs.com/masters/10/versions",
        json={
            "versions": [
                {
                    "id": 1,
                    "title": "Album",
                    "released": "1994",
                    "resource_url": "https://api.discogs.com/releases/1",
                    "format": "CD, Album",  # string, not list
                }
            ]
        },
    )
    releases = discogs_client.get_master_releases(10)
    assert releases[0].format == ["CD", "Album"]


def test_search_album_calls_rate_limiter(httpx_mock: HTTPXMock) -> None:
    rate_limiter = MagicMock()
    client = DiscogsClient(token="test", rate_limiter=rate_limiter)
    httpx_mock.add_response(
        url="https://api.discogs.com/database/search?artist=A&release_title=B&type=release",
        json={"results": []},
    )
    client.search_album("A", "B")
    rate_limiter.wait_and_consume.assert_called_once()


def test_get_release_calls_rate_limiter(httpx_mock: HTTPXMock) -> None:
    rate_limiter = MagicMock()
    client = DiscogsClient(token="test", rate_limiter=rate_limiter)
    httpx_mock.add_response(
        url="https://api.discogs.com/releases/1",
        json={
            "id": 1,
            "title": "Album",
            "artists": [],
            "tracklist": [],
            "resource_url": "https://api.discogs.com/releases/1",
        },
    )
    client.get_release(1)
    rate_limiter.wait_and_consume.assert_called_once()


def test_no_rate_limiter_does_not_fail(httpx_mock: HTTPXMock) -> None:
    """When no rate limiter is injected the client works normally."""
    client = DiscogsClient(token="test")
    httpx_mock.add_response(
        url="https://api.discogs.com/database/search?artist=A&release_title=B&type=release",
        json={"results": []},
    )
    results = client.search_album("A", "B")
    assert results == []


# ---------------------------------------------------------------------------
# 429 / rate-limit handling
# ---------------------------------------------------------------------------

_SEARCH_URL = (
    "https://api.discogs.com/database/search?artist=Artist&release_title=Album&type=release"
)


def test_search_album_429_raises_rate_limit_error(
    discogs_client: DiscogsClient, httpx_mock: HTTPXMock, mocker: MagicMock
) -> None:
    """A 429 response is converted to RateLimitError (not a raw httpx error)."""
    mocker.patch("time.sleep")  # prevent tenacity from actually waiting
    for _ in range(8):  # saturate all retry attempts
        httpx_mock.add_response(url=_SEARCH_URL, status_code=429)

    with pytest.raises(RateLimitError):
        discogs_client.search_album("Artist", "Album")


def test_search_album_429_reads_retry_after_header(
    discogs_client: DiscogsClient, httpx_mock: HTTPXMock, mocker: MagicMock
) -> None:
    """retry_after on RateLimitError is populated from the Retry-After response header."""
    mocker.patch("time.sleep")
    for _ in range(8):
        httpx_mock.add_response(url=_SEARCH_URL, status_code=429, headers={"Retry-After": "42"})

    with pytest.raises(RateLimitError) as exc_info:
        discogs_client.search_album("Artist", "Album")

    assert exc_info.value.retry_after == 42


def test_search_album_retries_on_429_then_succeeds(
    discogs_client: DiscogsClient, httpx_mock: HTTPXMock, mocker: MagicMock
) -> None:
    """Client retries after a 429 and returns the successful response."""
    mocker.patch("time.sleep")
    httpx_mock.add_response(url=_SEARCH_URL, status_code=429, headers={"Retry-After": "1"})
    httpx_mock.add_response(url=_SEARCH_URL, json={"results": []})

    results = discogs_client.search_album("Artist", "Album")

    assert results == []


def test_get_release_429_raises_rate_limit_error(
    discogs_client: DiscogsClient, httpx_mock: HTTPXMock, mocker: MagicMock
) -> None:
    """get_release also converts 429 to RateLimitError and retries."""
    mocker.patch("time.sleep")
    for _ in range(8):
        httpx_mock.add_response(url="https://api.discogs.com/releases/1", status_code=429)

    with pytest.raises(RateLimitError):
        discogs_client.get_release(1)
