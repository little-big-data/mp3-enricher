"""LLM client protocol for link (affiliation) enrichment."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM clients that detect artist link affiliations."""

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

        Returns an empty list when no affiliations are identified.
        """
        ...
