from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from tagger.enricher.discogs.models import DiscogsArtistDetail, DiscogsRelease, DiscogsSearchResult
from tagger.exceptions import RateLimitError, TransientAPIError
from tagger.utils.retry import retry_on_rate_limit

if TYPE_CHECKING:
    from tagger.utils.rate_limiter import TokenBucketRateLimiter


def _split_format(raw: list[str] | str) -> list[str]:
    """Normalise the Discogs 'format' field to a list.

    The search endpoint returns a list; the master-versions endpoint returns
    a comma-separated string.  Both are coerced to list[str] here.
    """
    if isinstance(raw, list):
        return raw
    return [f.strip() for f in raw.split(",") if f.strip()]


class DiscogsClient:
    BASE_URL = "https://api.discogs.com"

    def __init__(self, token: str, rate_limiter: TokenBucketRateLimiter | None = None) -> None:
        self._token = token
        self._rate_limiter = rate_limiter
        self._client = httpx.Client(
            headers={
                "Authorization": f"Discogs token={self._token}",
                "User-Agent": "MP3Enricher/0.1.0 +https://github.com/jschloman/mp3-enricher",
            }
        )

    def _throttle(self) -> None:
        """Block until the rate limiter grants a token."""
        if self._rate_limiter is not None:
            self._rate_limiter.acquire()

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise domain errors for retryable codes; re-raise others normally."""
        if response.status_code == 429:
            retry_after_raw = response.headers.get("Retry-After")
            retry_after = int(retry_after_raw) if retry_after_raw else None
            raise RateLimitError(service="discogs", retry_after=retry_after)
        if response.status_code >= 500:
            raise TransientAPIError(service="discogs", status_code=response.status_code)
        response.raise_for_status()

    @retry_on_rate_limit()
    def search_album(self, artist: str, album: str) -> list[DiscogsSearchResult]:
        self._throttle()
        params = {"artist": artist, "release_title": album, "type": "release"}
        response = self._client.get(f"{self.BASE_URL}/database/search", params=params)
        self._raise_for_status(response)
        data = response.json()
        return [DiscogsSearchResult.model_validate(r) for r in data.get("results", [])]

    @retry_on_rate_limit()
    def get_release(self, release_id: int) -> DiscogsRelease:
        self._throttle()
        response = self._client.get(f"{self.BASE_URL}/releases/{release_id}")
        self._raise_for_status(response)
        return DiscogsRelease.model_validate(response.json())

    @retry_on_rate_limit()
    def get_artist(self, artist_id: int) -> DiscogsArtistDetail:
        self._throttle()
        response = self._client.get(f"{self.BASE_URL}/artists/{artist_id}")
        self._raise_for_status(response)
        return DiscogsArtistDetail.model_validate(response.json())

    @retry_on_rate_limit()
    def get_master_releases(self, master_id: int) -> list[DiscogsSearchResult]:
        self._throttle()
        response = self._client.get(f"{self.BASE_URL}/masters/{master_id}/versions")
        self._raise_for_status(response)
        data = response.json()

        results = []
        for v in data.get("versions", []):
            mapped_data = {
                "id": v["id"],
                "type": "release",
                "master_id": master_id,
                "title": v["title"],
                "year": v.get("released"),
                "resource_url": v["resource_url"],
                "genre": v.get("genre", []),
                "style": v.get("style", []),
                "format": list(v.get("major_formats", [])) + _split_format(v.get("format", [])),
            }
            results.append(DiscogsSearchResult.model_validate(mapped_data))
        return results
