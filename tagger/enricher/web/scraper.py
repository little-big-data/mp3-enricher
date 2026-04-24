from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
import structlog
from bs4 import BeautifulSoup, Tag

if TYPE_CHECKING:
    from tagger.utils.rate_limiter import TokenBucket

log = structlog.get_logger(__name__)


class WebScraper:
    def __init__(self, rate_limiter: TokenBucket | None = None) -> None:
        self._rate_limiter = rate_limiter
        self._client = httpx.Client(
            headers={"User-Agent": "MP3Enricher/0.1.0 +https://github.com/jschloman/mp3-enricher"},
            follow_redirects=True,
        )

    def fetch_wikipedia_summary(self, artist: str) -> str:
        """
        Fetches the summary text from Wikipedia for a given artist.
        Attempts to find the most likely page.
        """
        if self._rate_limiter is not None:
            self._rate_limiter.wait_and_consume()
        # Try direct search first
        search_query = quote(artist)
        url = f"https://en.wikipedia.org/wiki/{search_query}"

        try:
            log.info("scraper.wikipedia.fetch", artist=artist, url=url)
            response = self._client.get(url)

            if response.status_code == 404:
                # Try with (musician) or (band) suffix
                for suffix in ["_(musician)", "_(band)", "_(artist)"]:
                    alt_url = f"{url}{suffix}"
                    log.debug("scraper.wikipedia.retry", url=alt_url)
                    alt_resp = self._client.get(alt_url)
                    if alt_resp.status_code == 200:
                        response = alt_resp
                        break

            if response.status_code != 200:
                log.warning("scraper.wikipedia.not_found", artist=artist)
                return ""

            soup = BeautifulSoup(response.text, "html.parser")

            # Extract paragraphs from the content body
            content = soup.find(id="mw-content-text")
            if not content or not isinstance(content, Tag):
                return ""

            paragraphs = content.find_all("p")
            # Take first few meaningful paragraphs
            text_parts = []
            for p in paragraphs:
                text = p.get_text().strip()
                if len(text) > 100:
                    text_parts.append(text)
                if len(text_parts) >= 5:
                    break

            return "\n\n".join(text_parts)

        except Exception as e:
            log.error("scraper.wikipedia.error", artist=artist, error=str(e))
            return ""

    def fetch_discogs_artist_bio(self, artist_id: int) -> str:
        """
        Fetches the profile bio for an artist from the Discogs API (already handled by client).
        This scraper focuses on HTML scraping fallbacks.
        """
        # In this project, Discogs bio usually comes from the API client.
        return ""
