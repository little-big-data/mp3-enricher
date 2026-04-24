"""LinkScanner — identifies and caches artist link (affiliation) mappings."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tagger.db.artist_links_repo import ArtistLinksRepository
    from tagger.db.track_repo import TrackRepository
    from tagger.enricher.llm.base import LLMClient

log = structlog.get_logger(__name__)

# Reuse the same featured-artist regex as formatter.py
_FEAT_RE = re.compile(r"\(feat\.\s+([^)]+)\)", re.IGNORECASE)
# Segment separator used in GRP1 strings
_LINK_SEGMENT_RE = re.compile(r"\s*\|\s*link:[^|]*", re.IGNORECASE)


class LinkScanner:
    """Detects which links (collectives, supergroups, label families) an artist belongs to.

    Uses the ``artist_links`` table as a cache so each artist is only
    sent to the LLM once.  The ``scan_artist`` method should be called with
    all album IDs for the artist so that featured-artist signals from track
    titles can be aggregated across the full discography.
    """

    def __init__(
        self,
        links_repo: ArtistLinksRepository,
        track_repo: TrackRepository,
        llm_client: LLMClient,
    ) -> None:
        self._links_repo = links_repo
        self._track_repo = track_repo
        self._llm = llm_client

    def scan_artist(
        self,
        *,
        artist: str,
        album_ids: list[int],
        label: str | None = None,
        genres: list[str] | None = None,
        album: str | None = None,
    ) -> list[str]:
        """Detect link affiliations for *artist* and cache the result.

        Returns the list of link names (may be empty).  Skips the LLM
        call when the cache already contains entries for this artist.
        """
        cached = self._links_repo.get_links(artist)
        if cached:
            log.debug("link_scanner.cache_hit", artist=artist, links=cached)
            return cached

        featured_artists = self._extract_featured_artists(album_ids)

        log.info(
            "link_scanner.llm_call",
            artist=artist,
            featured_count=len(featured_artists),
        )
        links = self._llm.detect_links(
            artist=artist,
            album=album or "",
            label=label,
            genres=genres or [],
            featured_artists=featured_artists,
        )

        for link in links:
            self._links_repo.upsert(artist, link, source="llm", confidence=1.0)

        return links

    def _extract_featured_artists(self, album_ids: list[int]) -> list[str]:
        """Extract unique featured artist names from track titles."""
        titles = self._track_repo.get_titles_for_albums(album_ids)
        seen: set[str] = set()
        result: list[str] = []
        for title in titles:
            for match in _FEAT_RE.finditer(title):
                name = match.group(1).strip()
                # Split on " & " or ", " in case of "feat. A & B"
                for part in re.split(r"\s*[,&]\s*", name):
                    part = part.strip()
                    if part and part not in seen:
                        seen.add(part)
                        result.append(part)
        return result

    @staticmethod
    def filter_to_library(links: list[str], library_artists: frozenset[str]) -> list[str]:
        """Return only the links whose name appears in *library_artists*.

        Comparison is case-insensitive.  The original casing of each link
        string is preserved in the output.

        Use this to avoid writing link tags for artists the user doesn't own —
        e.g. if Kandodo4 isn't in the library, don't include it in any tags
        even if MusicBrainz says the artist is related.
        """
        lower_library = frozenset(a.lower() for a in library_artists)
        return [lnk for lnk in links if lnk.lower() in lower_library]

    @staticmethod
    def update_grouping_tag(existing: str, link: str | None) -> str:
        """Return a new GRP1 string with the ``link:`` segment updated.

        - If *link* is ``None``, any existing ``link:`` segment is removed.
        - Otherwise, the existing ``link:`` segment is replaced (or appended).
        """
        # Strip any existing link: segment
        cleaned = _LINK_SEGMENT_RE.sub("", existing).strip().rstrip("|").strip()

        if not link:
            return cleaned

        if cleaned:
            return f"{cleaned} | link:{link}"
        return f"link:{link}"
