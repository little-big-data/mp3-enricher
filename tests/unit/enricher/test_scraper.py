from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock

from tagger.enricher.web.scraper import WebScraper


@pytest.fixture
def scraper() -> WebScraper:
    return WebScraper()


def test_fetch_wikipedia_summary_success(scraper: WebScraper, httpx_mock: HTTPXMock) -> None:
    artist = "Nine Inch Nails"
    url = "https://en.wikipedia.org/wiki/Nine%20Inch%20Nails"
    html_content = (
        '<div id="mw-content-text">'
        "<p>Nine Inch Nails is an American industrial rock band formed in 1988 in "
        "Cleveland, Ohio. It has released many influential albums and is known for "
        "its intense live performances and the creative vision of Trent Reznor.</p>"
        "<p>The band has won multiple Grammy Awards and has been inducted into the "
        "Rock and Roll Hall of Fame. Their music often explores themes of angst, "
        "industrialization, and personal struggle, blending electronic elements "
        "with heavy guitars.</p></div>"
    )
    httpx_mock.add_response(url=url, text=html_content)

    summary = scraper.fetch_wikipedia_summary(artist)
    assert "industrial rock band" in summary
    assert "Cleveland, Ohio" in summary


def test_fetch_wikipedia_summary_retry_suffix(scraper: WebScraper, httpx_mock: HTTPXMock) -> None:
    artist = "Trent Reznor"
    base_url = "https://en.wikipedia.org/wiki/Trent%20Reznor"
    musician_url = f"{base_url}_(musician)"

    # First attempt 404
    httpx_mock.add_response(url=base_url, status_code=404)
    # Second attempt success
    html_content = (
        '<div id="mw-content-text">'
        "<p>Michael Trent Reznor is an American musician, singer, songwriter, "
        "record producer, and composer. He is best known as the founder, primary "
        "songwriter, and only permanent member of the industrial rock project "
        "Nine Inch Nails.</p></div>"
    )
    httpx_mock.add_response(url=musician_url, text=html_content)

    summary = scraper.fetch_wikipedia_summary(artist)
    assert "industrial rock project" in summary


def test_fetch_wikipedia_summary_not_found(scraper: WebScraper, httpx_mock: HTTPXMock) -> None:
    artist = "NonExistentArtist"
    url = "https://en.wikipedia.org/wiki/NonExistentArtist"
    httpx_mock.add_response(url=url, status_code=404)
    # Also add responses for the retries
    httpx_mock.add_response(url=f"{url}_(musician)", status_code=404)
    httpx_mock.add_response(url=f"{url}_(band)", status_code=404)
    httpx_mock.add_response(url=f"{url}_(artist)", status_code=404)

    summary = scraper.fetch_wikipedia_summary(artist)
    assert summary == ""


def test_fetch_wikipedia_summary_error(scraper: WebScraper, httpx_mock: HTTPXMock) -> None:
    artist = "ErrorArtist"
    httpx_mock.add_exception(Exception("Connection error"))

    summary = scraper.fetch_wikipedia_summary(artist)
    assert summary == ""


def test_fetch_wikipedia_calls_rate_limiter(httpx_mock: HTTPXMock) -> None:
    rate_limiter = MagicMock()
    scraper = WebScraper(rate_limiter=rate_limiter)
    httpx_mock.add_response(
        url="https://en.wikipedia.org/wiki/Artist",
        text=(
            '<div id="mw-content-text"><p>Bio text that is definitely long enough'
            " to pass the 100 character minimum filter imposed by the scraper.</p></div>"
        ),
    )
    scraper.fetch_wikipedia_summary("Artist")
    rate_limiter.wait_and_consume.assert_called_once()


def test_no_rate_limiter_does_not_fail(httpx_mock: HTTPXMock) -> None:
    scraper = WebScraper()
    httpx_mock.add_response(
        url="https://en.wikipedia.org/wiki/Artist",
        text=(
            '<div id="mw-content-text"><p>Bio text that is definitely long enough'
            " to pass the 100 character minimum filter imposed by the scraper.</p></div>"
        ),
    )
    result = scraper.fetch_wikipedia_summary("Artist")
    assert "Bio text" in result
