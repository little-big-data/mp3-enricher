from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

import structlog

from tagger.enricher.models import EnrichmentData

if TYPE_CHECKING:
    from tagger.enricher.discogs.models import DiscogsArtistDetail, DiscogsRelease

log = structlog.get_logger(__name__)


class HeuristicEnricher:
    def __init__(self) -> None:
        pass

    def enrich_album(
        self,
        artist: str,
        album: str,
        context: str,
        discogs_release: DiscogsRelease | None = None,
        discogs_artist: DiscogsArtistDetail | None = None,
        mb_links: list[str] | None = None,
        release_artist: str | None = None,
        mb_origin: tuple[str | None, str | None] | None = None,
    ) -> EnrichmentData:
        """
        Enriches album metadata using heuristics instead of LLMs.
        Analyzes the context text (e.g. Wikipedia summary) and Discogs data.
        """
        log.info("enrich.heuristic.start", artist=artist, album=album)

        # Combine contexts
        full_context = context
        if discogs_artist and hasattr(discogs_artist, "profile"):
            full_context += "\n\n" + (discogs_artist.profile or "")

        gender = self._guess_gender(full_context)
        origin_city, origin_country = self._guess_origin(full_context)
        if mb_origin:
            mb_city, mb_country = mb_origin
            if mb_country:
                origin_country = mb_country
            if mb_city:
                origin_city = mb_city
        genre, subgenres = self._guess_genres(full_context, discogs_release)
        album_artist = self._guess_album_artist(
            artist, full_context, discogs_artist, release_artist
        )
        holiday = self._guess_holiday(full_context, album)
        link = self._guess_link(full_context, mb_links)
        label = self._extract_label(discogs_release)

        return EnrichmentData(
            album_artist_canonical=album_artist,
            origin_city=origin_city,
            origin_country=origin_country,
            gender=gender,
            race="Unknown",  # Hard to guess without LLM/Vision
            label=label,
            link=link,
            holiday=holiday,
            genre=genre,
            subgenres=subgenres,
            track_overrides=[],
        )

    def _guess_gender(
        self, text: str
    ) -> Literal["Male", "Female", "Non-binary", "Mixed", "Unknown"]:
        text_lower = text.lower()
        # Simple pronoun counting
        he_count = len(re.findall(r"\b(he|him|his)\b", text_lower))
        she_count = len(re.findall(r"\b(she|her|hers)\b", text_lower))

        if he_count > she_count * 2 and he_count > 5:
            return "Male"
        if she_count > he_count * 2 and she_count > 5:
            return "Female"
        if he_count > 2 and she_count > 2:
            return "Mixed"

        return "Unknown"

    def _guess_origin(self, text: str) -> tuple[str | None, str | None]:
        # Look for "from [City], [Country]" or "formed in [City]"
        # Patterns now handle optional years and different part counts
        # We use a non-greedy skip to handle intermediate words
        patterns = [
            # formed/born/from ... in [Year] in [City], [State/Country]
            (
                r"(?:formed|born|from).*?\s+in\s+(?:\d{4}\s+in\s+)?"
                r"([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*)"
                r"(?:,\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*))?"
            ),
            # from [City], [State/Country]
            (
                r"from\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*)"
                r"(?:,\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*))?"
            ),
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                # groups are (city, part2, part3)
                if len(groups) >= 3 and groups[2]:  # 3 parts found
                    return f"{groups[0]}, {groups[1]}", groups[2]
                return groups[0], groups[1]

        return None, None

    def _guess_genres(
        self, text: str, release: DiscogsRelease | None = None
    ) -> tuple[str | None, list[str]]:
        # Common genre keywords, including hyphenated ones
        genres_list = [
            "Electronic",
            "Rock",
            "Hip Hop",
            "Jazz",
            "Pop",
            "Metal",
            "Techno",
            "House",
            "Synth-pop",
            "Post-punk",
            "Industrial-rock",
            "New-age",
            "Trip-hop",
        ]
        found_genres: list[str] = []

        # 1. Check Discogs release/result if available (highest confidence)
        if release:
            # Full Release uses 'genres' and 'styles'
            if hasattr(release, "genres") and release.genres:
                found_genres.extend(release.genres)
            if hasattr(release, "styles") and release.styles:
                found_genres.extend(release.styles)
            # Search Result uses 'genre' and 'style'
            if hasattr(release, "genre") and release.genre:
                found_genres.extend(release.genre)
            if hasattr(release, "style") and release.style:
                found_genres.extend(release.style)

        # 2. Check text keywords
        text_lower = text.lower()
        # Create a normalized version of the text for easier hyphen matching
        text_normalized = text_lower.replace("-", " ").replace("  ", " ")

        for g in genres_list:
            g_lower = g.lower()
            g_norm = g_lower.replace("-", " ")

            # Match if exact keyword is in text or normalized keyword is in normalized text
            if (g_lower in text_lower or g_norm in text_normalized) and g not in found_genres:
                # Also check if any existing found genre already "covers" this (e.g. Synth-pop
                # vs Pop) but user wants Synth-pop as a subgenre specifically
                found_genres.append(g)

        if not found_genres:
            return None, []

        # First one as primary, rest as subgenres
        primary = found_genres[0]
        # De-duplicate while preserving order and case
        seen = {primary.lower()}
        subgenres = []
        for g in found_genres[1:]:
            if g.lower() not in seen:
                subgenres.append(g)
                seen.add(g.lower())

        return primary, subgenres

    def _guess_album_artist(
        self,
        artist: str,
        text: str,
        discogs_artist: DiscogsArtistDetail | None = None,
        release_artist: str | None = None,
    ) -> str:
        # 0. If artist is "Various", keep it
        if artist.lower() in ["various", "various artists"]:
            return "Various"

        # 1. Credited name on the Discogs release (highest priority)
        if release_artist:
            return release_artist

        # 2. Check for "born [Name]" pattern in text
        match = re.search(r"born\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*)", text)
        if match:
            return match.group(1)
        return artist

    def _guess_holiday(
        self, text: str, album: str
    ) -> Literal["Halloween", "Christmas", "Thanksgiving", "Easter", "None"]:
        content = (text + " " + album).lower()
        if any(w in content for w in ["christmas", "xmas", "carol", "noel"]):
            return "Christmas"
        if any(w in content for w in ["halloween", "spooky", "horror", "gothic"]):
            return "Halloween"
        return "None"

    def _guess_link(self, text: str, mb_links: list[str] | None = None) -> str | None:
        """Return the first link affiliation the artist belongs to.

        MusicBrainz data takes priority when available; otherwise a
        keyword scan of the context text is used as a fallback.
        """
        if mb_links:
            return mb_links[0]

        # Predefined list of famous collectives/affiliations
        known_links = ["Soulquarians", "Underground Resistance", "Wu-Tang Clan", "Native Tongues"]
        text_lower = text.lower()
        for link in known_links:
            if link.lower() in text_lower:
                return link
        return None

    def _extract_label(self, release: DiscogsRelease | None) -> str | None:
        if not release or not hasattr(release, "labels") or not release.labels:
            return None
        # Return first label name
        return release.labels[0].name
