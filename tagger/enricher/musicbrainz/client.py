"""MusicBrainz API client for artist link lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog
import tenacity

from tagger.enricher.musicbrainz.models import (
    MusicBrainzArtistDetail,
    MusicBrainzSearchArtist,
    MusicBrainzSearchResponse,
)
from tagger.exceptions import TransientAPIError

if TYPE_CHECKING:
    from tagger.utils.rate_limiter import TokenBucket

log = structlog.get_logger(__name__)

_MEMBER_OF_BAND = "member of band"
# MB uses "is person" for solo-alias relations (e.g. Glass Domain → Gerald Donald).
# "performance name" is an older/alternate type that may also appear.
_IS_PERSON_TYPES = frozenset({"is person", "performance name"})

# HTTP status codes that indicate a transient server-side failure worth retrying.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class MusicBrainzClient:
    """Thin wrapper around the MusicBrainz ws/2 REST API.

    Requires no API key.  MusicBrainz asks that all clients identify
    themselves via a descriptive User-Agent header and stay at or below
    1 request per second.

    Args:
        rate_limiter: Optional :class:`~tagger.utils.rate_limiter.TokenBucket`.
            When provided, each request blocks until a token is available.
        retry_attempts: Maximum number of attempts (including the first) for
            transient 5xx/429 errors.  Defaults to 3.  Pass ``1`` to disable
            retries (useful in unit tests).
    """

    BASE_URL = "https://musicbrainz.org/ws/2"

    def __init__(
        self,
        rate_limiter: TokenBucket | None = None,
        retry_attempts: int = 3,
        _retry_wait: tenacity.wait.wait_base | None = None,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._retry_attempts = retry_attempts
        # Allow tests to inject wait_none() so retries don't sleep.
        self._retry_wait: tenacity.wait.wait_base = (
            _retry_wait
            if _retry_wait is not None
            else tenacity.wait_exponential(multiplier=1, min=2, max=30)
        )
        self._client = httpx.Client(
            headers={
                "User-Agent": "MP3Enricher/0.1.0 +https://github.com/jschloman/mp3-enricher",
            }
        )

    def _throttle(self) -> None:
        """Block until the rate limiter grants a token."""
        if self._rate_limiter is not None:
            self._rate_limiter.wait_and_consume()

    def _retrying(self) -> tenacity.Retrying:
        """Return a configured :class:`tenacity.Retrying` context for transient MB errors."""
        return tenacity.Retrying(
            retry=tenacity.retry_if_exception_type(TransientAPIError),
            wait=self._retry_wait,
            stop=tenacity.stop_after_attempt(self._retry_attempts),
            before_sleep=lambda rs: log.warning(
                "mb.retrying",
                attempt=rs.attempt_number,
                error=str(rs.outcome.exception() if rs.outcome else None),
            ),
            reraise=True,
        )

    def _check_response(self, response: httpx.Response, context: str) -> None:
        """Raise :class:`TransientAPIError` for retryable status codes.

        Non-retryable error codes fall through to ``response.raise_for_status()``.
        """
        if response.status_code in _RETRYABLE_STATUSES:
            raise TransientAPIError("musicbrainz", response.status_code)
        response.raise_for_status()

    def search_artist(self, artist_name: str) -> list[MusicBrainzSearchArtist]:
        """Search for artists by name, returning up to 5 scored candidates."""
        self._throttle()
        response = self._client.get(
            f"{self.BASE_URL}/artist",
            params={"query": artist_name, "fmt": "json", "limit": 5},
        )
        self._check_response(response, f"search:{artist_name}")
        return MusicBrainzSearchResponse.model_validate(response.json()).artists

    def get_artist_relations(self, mbid: str) -> MusicBrainzArtistDetail:
        """Fetch an artist entity with all artist-to-artist relations included."""
        self._throttle()
        response = self._client.get(
            f"{self.BASE_URL}/artist/{mbid}",
            params={"inc": "artist-rels", "fmt": "json"},
        )
        self._check_response(response, f"relations:{mbid}")
        return MusicBrainzArtistDetail.model_validate(response.json())

    def find_links(self, artist_name: str) -> list[str]:
        """Return names of groups/affiliations the artist is a member of or performs as.

        Two-hop traversal:

        1. **Direct membership** — ``"member of band"`` forward relations on the
           searched artist (e.g. GZA → Wu-Tang Clan).
        2. **Alias traversal** — if the artist has a ``"performance name"`` *backward*
           relation, that means a real person "performs as" this alias.  We then fetch
           the person's entity and collect:
           - all their ``"performance name"`` *forward* relations (sibling aliases,
             e.g. Kevin Richard Martin performs as The Bug, Ice, G36 …), and
           - all their ``"member of band"`` *forward* relations (bands the person
             is in directly).
           The searched artist name itself is excluded from the results.

        Transient 5xx / 429 responses are retried up to ``retry_attempts`` times
        with exponential back-off.  Returns an empty list when all attempts fail
        or a non-retryable error occurs.
        """
        try:
            for attempt in self._retrying():
                with attempt:
                    results = self.search_artist(artist_name)
                    if not results:
                        log.info("mb.search.no_results", artist=artist_name)
                        return []

                    best = results[0]
                    log.info(
                        "mb.search.found",
                        artist=artist_name,
                        mbid=best.id,
                        score=best.score,
                    )

                    detail = self.get_artist_relations(best.id)
                    links = self._collect_links(artist_name, detail)

                    log.info("mb.links.found", artist=artist_name, links=links)
                    return links

        except TransientAPIError as exc:
            log.warning(
                "mb.transient_exhausted",
                artist=artist_name,
                status_code=exc.status_code,
                attempts=self._retry_attempts,
            )
        except httpx.HTTPStatusError as exc:
            log.warning(
                "mb.http_error",
                artist=artist_name,
                status_code=exc.response.status_code,
            )
        except httpx.RequestError as exc:
            log.warning("mb.request_error", artist=artist_name, error=str(exc))

        return []

    def _collect_links(self, artist_name: str, detail: MusicBrainzArtistDetail) -> list[str]:
        """Collect all link names from an artist detail, including two-hop traversal.

        Three sources are combined (order preserved, deduped):

        1. **Direct forward** ``"member of band"`` relations — the artist is a member
           of a named group (e.g. GZA → Wu-Tang Clan).

        2. **"is person" / "performance name" backward** — this entity is a solo alias;
           the backward-linked artist is the real person.  We fetch the person and
           collect all their forward ``"is person"`` aliases and ``"member of band"``
           group memberships (e.g. Glass Domain → Gerald Donald → Dopplereffekt,
           Arpanet, Drexciya …).

        3. **"member of band" backward** — the backward-linked artists are *members* of
           this entity (indicating it is a solo project owned by one person).  We fetch
           each member and collect their forward ``"is person"`` aliases and
           ``"member of band"`` memberships (e.g. G36 → Kevin Richard Martin → The Bug,
           Ice, Techno Animal, King Midas Sound …).

        The searched ``artist_name`` is always excluded from the results.

        Returns a deduplicated list preserving insertion order.
        """
        seen: dict[str, None] = {}  # ordered set via dict

        # 1. Direct forward band membership
        for r in detail.relations:
            if r.type == _MEMBER_OF_BAND and r.direction == "forward":
                seen.setdefault(r.artist.name, None)

        # 2. "is person" / "performance name" backward → fetch the real person
        for r in detail.relations:
            if r.type in _IS_PERSON_TYPES and r.direction == "backward":
                log.info("mb.is_person_traversal", alias=artist_name, person=r.artist.name)
                self._harvest_person_links(r.artist.id, artist_name, seen)

        # 3. "member of band" backward → fetch each member, collect their aliases/bands
        for r in detail.relations:
            if r.type == _MEMBER_OF_BAND and r.direction == "backward":
                log.info("mb.member_traversal", project=artist_name, member=r.artist.name)
                self._harvest_person_links(r.artist.id, artist_name, seen)

        return list(seen)

    def _harvest_person_links(
        self,
        person_mbid: str,
        exclude_name: str,
        seen: dict[str, None],
    ) -> None:
        """Fetch *person_mbid* and add their aliases and band memberships to *seen*.

        Skips any entry whose name matches *exclude_name* (the originally searched
        artist, to avoid a self-reference in the results).
        """
        person_detail = self.get_artist_relations(person_mbid)
        for pr in person_detail.relations:
            if pr.artist.name == exclude_name:
                continue
            if (pr.type in _IS_PERSON_TYPES and pr.direction == "forward") or (
                pr.type == _MEMBER_OF_BAND and pr.direction == "forward"
            ):
                seen.setdefault(pr.artist.name, None)

    def find_area(self, artist_name: str) -> tuple[str | None, str | None]:
        """Return (begin_area_name, area_name) for the top MB artist match.

        Uses the same search + detail lookup as find_links.
        Returns (None, None) on any network or API error so the enrichment
        pipeline can continue without MusicBrainz data.
        """
        try:
            for attempt in self._retrying():
                with attempt:
                    results = self.search_artist(artist_name)
                    if not results:
                        return None, None
                    detail = self.get_artist_relations(results[0].id)
                    city = detail.begin_area.name if detail.begin_area else None
                    country = detail.area.name if detail.area else None
                    return city, country

        except TransientAPIError as exc:
            log.warning(
                "mb.find_area.transient_exhausted",
                artist=artist_name,
                status_code=exc.status_code,
                attempts=self._retry_attempts,
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            log.warning("mb.find_area_error", artist=artist_name, error=str(exc))

        return None, None
