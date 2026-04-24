"""Heuristic LLM client — implements LLMClient without any paid API.

Uses MusicBrainz (free) for group-membership data, then falls back to
a keyword scan across artist name, album title, label, genres, and
featured-artist names.  Covers well-known collectives without requiring
Anthropic or Google API credits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tagger.enricher.musicbrainz.client import MusicBrainzClient

log = structlog.get_logger(__name__)

# Known affiliation keywords: each tuple is (canonical_name, [match_tokens])
# Tokens are checked case-insensitively against artist/album/label/genre/featured text.
_KNOWN_LINKS: list[tuple[str, list[str]]] = [
    ("Wu-Tang Clan", ["wu-tang", "wutang", "wu tang"]),
    ("Soulquarians", ["soulquarians", "soulquarian"]),
    ("Underground Resistance", ["underground resistance"]),
    ("Native Tongues", ["native tongues"]),
]

# Direct artist-name → collective mapping.  Checked against the *artist*
# argument (case-insensitive) before the featured-artist scan.
_ARTIST_COLLECTIVE: dict[str, str] = {
    # Wu-Tang Clan
    "rza": "Wu-Tang Clan",
    "gza": "Wu-Tang Clan",
    "ghostface killah": "Wu-Tang Clan",
    "method man": "Wu-Tang Clan",
    "raekwon": "Wu-Tang Clan",
    "ol' dirty bastard": "Wu-Tang Clan",
    "odb": "Wu-Tang Clan",
    "inspectah deck": "Wu-Tang Clan",
    "u-god": "Wu-Tang Clan",
    "masta killa": "Wu-Tang Clan",
    "cappadonna": "Wu-Tang Clan",
    # Soulquarians
    "j dilla": "Soulquarians",
    "d'angelo": "Soulquarians",
    "erykah badu": "Soulquarians",
    "common": "Soulquarians",
    "mos def": "Soulquarians",
    "talib kweli": "Soulquarians",
    "questlove": "Soulquarians",
    "bilal": "Soulquarians",
    # Native Tongues
    "de la soul": "Native Tongues",
    "a tribe called quest": "Native Tongues",
    "jungle brothers": "Native Tongues",
    "queen latifah": "Native Tongues",
    "monie love": "Native Tongues",
    "black sheep": "Native Tongues",
    "leaders of the new school": "Native Tongues",
    # Underground Resistance
    "model 500": "Underground Resistance",
    "robert hood": "Underground Resistance",
    "mad mike": "Underground Resistance",
}

# Artists who are well-known members of a collective — their appearance as a
# featured artist is treated as a signal for that collective.
_FEATURED_SIGNALS: dict[str, str] = {
    # Wu-Tang Clan members
    "rza": "Wu-Tang Clan",
    "gza": "Wu-Tang Clan",
    "ol' dirty bastard": "Wu-Tang Clan",
    "odb": "Wu-Tang Clan",
    "method man": "Wu-Tang Clan",
    "raekwon": "Wu-Tang Clan",
    "ghostface killah": "Wu-Tang Clan",
    "inspectah deck": "Wu-Tang Clan",
    "u-god": "Wu-Tang Clan",
    "masta killa": "Wu-Tang Clan",
    "cappadonna": "Wu-Tang Clan",
    # Soulquarians
    "d'angelo": "Soulquarians",
    "erykah badu": "Soulquarians",
    "questlove": "Soulquarians",
    "common": "Soulquarians",
    "mos def": "Soulquarians",
    "talib kweli": "Soulquarians",
    "j dilla": "Soulquarians",
    "bilal": "Soulquarians",
    # Native Tongues
    "de la soul": "Native Tongues",
    "a tribe called quest": "Native Tongues",
    "jungle brothers": "Native Tongues",
    "queen latifah": "Native Tongues",
    "monie love": "Native Tongues",
    "black sheep": "Native Tongues",
    "leaders of the new school": "Native Tongues",
}


class HeuristicLinkClient:
    """LLMClient implementation that uses MusicBrainz + keyword heuristics.

    Satisfies the ``LLMClient`` Protocol so it can be passed to
    ``LinkScanner`` as a drop-in replacement for ``ClaudeLinkClient``
    when no Anthropic API key is available.

    Args:
        mb_client: Optional MusicBrainzClient.  When supplied, MB group-
            membership data is queried first and takes priority.  Pass
            ``None`` for pure keyword-only operation.
    """

    def __init__(self, mb_client: MusicBrainzClient | None = None) -> None:
        self._mb = mb_client

    def detect_links(
        self,
        *,
        artist: str,
        album: str,
        label: str | None,
        genres: list[str],
        featured_artists: list[str],
    ) -> list[str]:
        """Return link affiliations using MusicBrainz data and keyword heuristics.

        Resolution order:
        1. MusicBrainz group-membership relations (authoritative, free API).
        2. Keyword scan of artist, album, label, genres for known collective names.
        3. Featured-artist lookup — if a well-known member features heavily, that
           collective is inferred.

        Returns an empty list when no affiliation is found.
        """
        # 1. MusicBrainz
        if self._mb is not None:
            try:
                mb_links = self._mb.find_links(artist)
                if mb_links:
                    log.info("heuristic_client.mb_hit", artist=artist, links=mb_links)
                    return mb_links
            except Exception as exc:
                log.warning("heuristic_client.mb_error", artist=artist, error=str(exc))

        # 2a. Direct artist-name lookup
        artist_lower = artist.lower()
        if artist_lower in _ARTIST_COLLECTIVE:
            collective = _ARTIST_COLLECTIVE[artist_lower]
            log.info("heuristic_client.artist_lookup_hit", artist=artist, link=collective)
            return [collective]

        # 2b. Keyword scan of album/label/genre signals
        combined = " ".join(filter(None, [artist, album, label, *genres])).lower()

        for canonical, tokens in _KNOWN_LINKS:
            if any(token in combined for token in tokens):
                log.info("heuristic_client.keyword_hit", artist=artist, link=canonical)
                return [canonical]

        # 3. Featured-artist signals
        featured_lower = [fa.lower() for fa in featured_artists]
        for member_name, collective in _FEATURED_SIGNALS.items():
            if member_name in featured_lower:
                log.info(
                    "heuristic_client.featured_signal",
                    artist=artist,
                    member=member_name,
                    link=collective,
                )
                return [collective]

        log.debug("heuristic_client.no_match", artist=artist)
        return []
