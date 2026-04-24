"""Unit tests for tagger.enricher.llm.claude_client.ClaudeLinkClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import TextBlock

from tagger.enricher.llm.claude_client import ClaudeLinkClient


@pytest.fixture
def client() -> ClaudeLinkClient:
    return ClaudeLinkClient(api_key="test-key", model="claude-haiku-4-5-20251001")


def _mock_response(text: str) -> MagicMock:
    """Build a minimal mock of an Anthropic messages API response."""
    content_block = TextBlock(type="text", text=text)
    response = MagicMock()
    response.content = [content_block]
    return response


def test_detect_links_returns_list(client: ClaudeLinkClient) -> None:
    mock_resp = _mock_response('{"links": ["Wu-Tang Clan"]}')
    with patch.object(client._anthropic.messages, "create", return_value=mock_resp):
        result = client.detect_links(
            artist="GZA",
            album="Liquid Swords",
            label=None,
            genres=["Hip-Hop"],
            featured_artists=["RZA"],
        )
    assert result == ["Wu-Tang Clan"]


def test_detect_links_multiple_affiliations(client: ClaudeLinkClient) -> None:
    mock_resp = _mock_response('{"links": ["Wu-Tang Clan", "Gravediggaz"]}')
    with patch.object(client._anthropic.messages, "create", return_value=mock_resp):
        result = client.detect_links(
            artist="RZA",
            album="Bobby Digital in Stereo",
            label=None,
            genres=["Hip-Hop"],
            featured_artists=[],
        )
    assert "Wu-Tang Clan" in result
    assert "Gravediggaz" in result


def test_detect_links_empty_array_returns_empty_list(
    client: ClaudeLinkClient,
) -> None:
    mock_resp = _mock_response('{"links": []}')
    with patch.object(client._anthropic.messages, "create", return_value=mock_resp):
        result = client.detect_links(
            artist="Unknown Solo Artist",
            album="Some Album",
            label=None,
            genres=[],
            featured_artists=[],
        )
    assert result == []


def test_detect_links_malformed_json_returns_empty_list(
    client: ClaudeLinkClient,
) -> None:
    mock_resp = _mock_response("I don't know any links for this artist.")
    with patch.object(client._anthropic.messages, "create", return_value=mock_resp):
        result = client.detect_links(
            artist="Unknown",
            album="Unknown Album",
            label=None,
            genres=[],
            featured_artists=[],
        )
    assert result == []


def test_detect_links_missing_key_returns_empty_list(
    client: ClaudeLinkClient,
) -> None:
    mock_resp = _mock_response('{"result": "none"}')
    with patch.object(client._anthropic.messages, "create", return_value=mock_resp):
        result = client.detect_links(
            artist="Unknown",
            album="Unknown Album",
            label=None,
            genres=[],
            featured_artists=[],
        )
    assert result == []
