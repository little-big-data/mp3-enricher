"""Claude API client for link (affiliation) detection."""

from __future__ import annotations

import json
import re

import anthropic
import structlog
from anthropic.types import TextBlock

from tagger.enricher.llm.prompt_builder import build_link_prompt

log = structlog.get_logger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 256


class ClaudeLinkClient:
    """Uses Claude to identify which links (collectives/affiliations) an artist belongs to.

    Results should be cached in ``artist_links`` via
    ``ArtistLinksRepository`` to avoid repeated API calls.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        self._anthropic = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def detect_links(
        self,
        *,
        artist: str,
        album: str,
        label: str | None,
        genres: list[str],
        featured_artists: list[str],
    ) -> list[str]:
        """Return a list of link names the artist belongs to.

        Calls the Claude API with all available context signals.  Returns an
        empty list on parse failure or when no affiliations are identified.
        """
        prompt = build_link_prompt(
            artist=artist,
            album=album,
            label=label,
            genres=genres,
            featured_artists=featured_artists,
        )

        log.info(
            "llm.link.request",
            artist=artist,
            album=album,
            model=self._model,
        )

        response = self._anthropic.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )

        first_block = response.content[0]
        if not isinstance(first_block, TextBlock):
            log.warning("llm.link.unexpected_block_type", artist=artist)
            return []
        raw_text = first_block.text.strip()
        return self._parse_response(raw_text, artist=artist)

    def _parse_response(self, text: str, *, artist: str) -> list[str]:
        """Parse the JSON response from Claude.

        Returns an empty list when the response cannot be parsed.
        """
        try:
            # Strip markdown code fences if present (handles ```json, ```JSON, ``` json, etc.)
            text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
            text = re.sub(r"\n?```\s*$", "", text.strip())

            data = json.loads(text)
            links: list[str] = data.get("links", [])
            if not isinstance(links, list):
                log.warning("llm.link.parse_error", artist=artist, raw=text)
                return []
            result = [str(lnk) for lnk in links if lnk]
            log.info("llm.link.result", artist=artist, links=result)
            return result
        except (json.JSONDecodeError, KeyError, TypeError):
            log.warning("llm.link.parse_error", artist=artist, raw=text)
            return []
