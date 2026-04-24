"""Tests for HeuristicLinkClient — LLMClient implementation using MusicBrainz + keyword scan."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tagger.enricher.llm.heuristic_client import HeuristicLinkClient


@pytest.fixture
def mb_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(mb_client: MagicMock) -> HeuristicLinkClient:
    return HeuristicLinkClient(mb_client=mb_client)


class TestHeuristicLinkClientProtocol:
    """Verify the class satisfies the LLMClient Protocol."""

    def test_implements_llm_client_protocol(self, client: HeuristicLinkClient) -> None:
        from tagger.enricher.llm.base import LLMClient

        assert isinstance(client, LLMClient)


class TestMusicBrainzPriority:
    """MusicBrainz group memberships take priority over keyword scan."""

    def test_returns_mb_links_when_found(
        self, client: HeuristicLinkClient, mb_client: MagicMock
    ) -> None:
        mb_client.find_links.return_value = ["Wu-Tang Clan"]
        links = client.detect_links(
            artist="GZA",
            album="Liquid Swords",
            label=None,
            genres=[],
            featured_artists=[],
        )
        assert links == ["Wu-Tang Clan"]
        mb_client.find_links.assert_called_once_with("GZA")

    def test_returns_multiple_mb_links(
        self, client: HeuristicLinkClient, mb_client: MagicMock
    ) -> None:
        mb_client.find_links.return_value = ["Wu-Tang Clan", "Gravediggaz"]
        links = client.detect_links(
            artist="RZA",
            album="Bobby Digital in Stereo",
            label=None,
            genres=[],
            featured_artists=[],
        )
        assert links == ["Wu-Tang Clan", "Gravediggaz"]


class TestKeywordFallback:
    """When MusicBrainz returns nothing, fall back to keyword scan of genre/label signals."""

    def test_no_mb_no_keyword_returns_empty(
        self, client: HeuristicLinkClient, mb_client: MagicMock
    ) -> None:
        mb_client.find_links.return_value = []
        links = client.detect_links(
            artist="Radiohead",
            album="OK Computer",
            label="Parlophone",
            genres=["Alternative Rock"],
            featured_artists=[],
        )
        assert links == []

    @pytest.mark.parametrize(
        ("artist", "album", "label", "genres", "expected"),
        [
            (
                "J Dilla",
                "Donuts",
                None,
                ["Hip Hop"],
                ["Soulquarians"],
            ),
            (
                "Robert Hood",
                "Minimal Nation",
                "Underground Resistance",
                ["Techno"],
                ["Underground Resistance"],
            ),
            (
                "GZA",
                "Words from the Genius",
                None,
                ["Hip Hop"],
                ["Wu-Tang Clan"],
            ),
            (
                "De La Soul",
                "3 Feet High and Rising",
                "Tommy Boy",
                ["Hip Hop"],
                ["Native Tongues"],
            ),
        ],
    )
    def test_keyword_scan_signals(
        self,
        client: HeuristicLinkClient,
        mb_client: MagicMock,
        artist: str,
        album: str,
        label: str | None,
        genres: list[str],
        expected: list[str],
    ) -> None:
        mb_client.find_links.return_value = []
        links = client.detect_links(
            artist=artist,
            album=album,
            label=label,
            genres=genres,
            featured_artists=[],
        )
        assert links == expected

    def test_featured_artists_trigger_wu_tang(
        self, client: HeuristicLinkClient, mb_client: MagicMock
    ) -> None:
        """Artists known to be in Wu-Tang appearing as featured artists is a signal."""
        mb_client.find_links.return_value = []
        # A Ghostface solo album where RZA features heavily
        links = client.detect_links(
            artist="Ghostface Killah",
            album="Ironman",
            label=None,
            genres=["Hip Hop"],
            featured_artists=["RZA", "Method Man", "Raekwon"],
        )
        assert links == ["Wu-Tang Clan"]

    def test_mb_errors_are_swallowed(
        self, client: HeuristicLinkClient, mb_client: MagicMock
    ) -> None:
        """If MusicBrainz raises, the exception does not propagate."""
        mb_client.find_links.side_effect = Exception("network error")
        # Radiohead has no collective in any lookup table, so result is []
        links = client.detect_links(
            artist="Radiohead",
            album="The Bends",
            label="Parlophone",
            genres=["Alternative Rock"],
            featured_artists=[],
        )
        assert links == []


class TestNoMBClient:
    """HeuristicLinkClient works without a MusicBrainz client (keyword-only mode)."""

    def test_keyword_only_mode(self) -> None:
        client = HeuristicLinkClient(mb_client=None)
        links = client.detect_links(
            artist="Q-Tip",
            album="Amplified",
            label=None,
            genres=["Hip Hop"],
            featured_artists=["De La Soul", "Busta Rhymes"],
        )
        assert links == ["Native Tongues"]

    def test_keyword_only_no_match(self) -> None:
        client = HeuristicLinkClient(mb_client=None)
        links = client.detect_links(
            artist="Adele",
            album="21",
            label="XL Recordings",
            genres=["Soul"],
            featured_artists=[],
        )
        assert links == []
