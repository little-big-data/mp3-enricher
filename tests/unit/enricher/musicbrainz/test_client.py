"""Tests for tagger.enricher.musicbrainz.client.MusicBrainzClient."""

from __future__ import annotations

import re
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
import tenacity
from pytest_httpx import HTTPXMock

from tagger.enricher.musicbrainz.client import MusicBrainzClient

# Regex patterns to match URLs regardless of query-string ordering
SEARCH_URL = re.compile(r"https://musicbrainz\.org/ws/2/artist\?")
ARTIST_DETAIL_URL = re.compile(r"https://musicbrainz\.org/ws/2/artist/[^?]+\?")

MBID = "11a2b3c4-d5e6-7890-abcd-ef1234567890"

SEARCH_RESPONSE = {
    "count": 1,
    "offset": 0,
    "artists": [
        {"id": MBID, "name": "Jeff Mills", "score": 100},
    ],
}

RELATIONS_RESPONSE = {
    "id": MBID,
    "name": "Jeff Mills",
    "type": "Person",
    "relations": [
        {
            "type": "member of band",
            "direction": "forward",
            "artist": {"id": "aaa", "name": "Underground Resistance"},
        },
        {
            "type": "member of band",
            "direction": "forward",
            "artist": {"id": "bbb", "name": "The Belleville Three"},
        },
        {
            # unrelated relation type — should be ignored
            "type": "collaboration",
            "direction": "forward",
            "artist": {"id": "ddd", "name": "Collab Artist"},
        },
    ],
}

# Artist whose only relations are backward/non-member — used to test that those
# do trigger traversal but return nothing when the traversed entity has no links.
_EMPTY_PERSON_RESPONSE = {
    "id": "empty-person-id",
    "name": "Side Project Member",
    "type": "Person",
    "relations": [],
}


@pytest.fixture
def client() -> MusicBrainzClient:
    # retry_attempts=1 → no retries in unit tests; keeps tests fast and mocks simple
    return MusicBrainzClient(retry_attempts=1)


def test_find_links_returns_group_names(client: MusicBrainzClient, httpx_mock: HTTPXMock) -> None:
    """find_links returns names of groups the artist is a backward member of."""
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
    httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=RELATIONS_RESPONSE)

    result = client.find_links("Jeff Mills")

    assert result == ["Underground Resistance", "The Belleville Three"]


def test_find_links_backward_member_traverses_but_returns_empty_when_no_links(
    client: MusicBrainzClient, httpx_mock: HTTPXMock
) -> None:
    """backward 'member of band' triggers traversal; returns [] when the member has no links."""
    relations_response = {
        "id": MBID,
        "name": "Jeff Mills",
        "type": "Person",
        "relations": [
            {
                # backward = someone is a member of Jeff Mills' project
                # we now traverse to them; if they have no forward links, result is []
                "type": "member of band",
                "direction": "backward",
                "artist": {"id": "empty-person-id", "name": "Side Project Member"},
            },
            {
                "type": "collaboration",
                "direction": "forward",
                "artist": {"id": "y", "name": "Should Not Appear"},
            },
        ],
    }
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
    httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=relations_response)
    # Traversal call for Side Project Member — has no forward links
    httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=_EMPTY_PERSON_RESPONSE)

    result = client.find_links("Jeff Mills")

    assert result == []
    assert "Should Not Appear" not in result


def test_find_links_empty_when_no_artist_found(
    client: MusicBrainzClient, httpx_mock: HTTPXMock
) -> None:
    """Returns empty list when search returns no results."""
    httpx_mock.add_response(url=SEARCH_URL, json={"count": 0, "offset": 0, "artists": []})

    result = client.find_links("Nonexistent Artist")

    assert result == []


def test_find_links_empty_on_search_http_error(
    client: MusicBrainzClient, httpx_mock: HTTPXMock
) -> None:
    """Returns empty list gracefully when search request fails."""
    httpx_mock.add_response(url=SEARCH_URL, status_code=503)

    result = client.find_links("Jeff Mills")

    assert result == []


def test_find_links_empty_on_relations_http_error(
    client: MusicBrainzClient, httpx_mock: HTTPXMock
) -> None:
    """Returns empty list gracefully when artist-relations request fails."""
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
    httpx_mock.add_response(url=ARTIST_DETAIL_URL, status_code=404)

    result = client.find_links("Jeff Mills")

    assert result == []


def test_find_links_no_relations_field(client: MusicBrainzClient, httpx_mock: HTTPXMock) -> None:
    """Returns empty list when artist detail has no relations key."""
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
    httpx_mock.add_response(
        url=ARTIST_DETAIL_URL,
        json={"id": MBID, "name": "Jeff Mills", "type": "Person"},
    )

    result = client.find_links("Jeff Mills")

    assert result == []


def test_search_artist_returns_parsed_results(
    client: MusicBrainzClient, httpx_mock: HTTPXMock
) -> None:
    """search_artist returns a list of MusicBrainzSearchArtist."""
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)

    results = client.search_artist("Jeff Mills")

    assert len(results) == 1
    assert results[0].id == MBID
    assert results[0].name == "Jeff Mills"
    assert results[0].score == 100


def test_get_artist_relations_returns_parsed_detail(
    client: MusicBrainzClient, httpx_mock: HTTPXMock
) -> None:
    """get_artist_relations returns a parsed MusicBrainzArtistDetail."""
    httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=RELATIONS_RESPONSE)

    detail = client.get_artist_relations(MBID)

    assert detail.id == MBID
    assert detail.name == "Jeff Mills"
    assert len(detail.relations) == 3


def test_get_artist_relations_raises_transient_on_5xx(
    client: MusicBrainzClient, httpx_mock: HTTPXMock
) -> None:
    """get_artist_relations raises TransientAPIError on 5xx responses."""
    from tagger.exceptions import TransientAPIError

    httpx_mock.add_response(url=ARTIST_DETAIL_URL, status_code=500)

    with pytest.raises(TransientAPIError):
        client.get_artist_relations(MBID)


def test_search_artist_calls_rate_limiter(httpx_mock: HTTPXMock) -> None:
    rate_limiter = MagicMock()
    client = MusicBrainzClient(rate_limiter=rate_limiter)
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)

    client.search_artist("Jeff Mills")

    rate_limiter.wait_and_consume.assert_called_once()


def test_get_artist_relations_calls_rate_limiter(httpx_mock: HTTPXMock) -> None:
    rate_limiter = MagicMock()
    client = MusicBrainzClient(rate_limiter=rate_limiter)
    httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=RELATIONS_RESPONSE)

    client.get_artist_relations(MBID)

    rate_limiter.wait_and_consume.assert_called_once()


def test_no_rate_limiter_does_not_fail(httpx_mock: HTTPXMock) -> None:
    client = MusicBrainzClient()
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)

    results = client.search_artist("Jeff Mills")

    assert len(results) == 1


_AREA_DETAIL_RESPONSE = {
    "id": MBID,
    "name": "Scientist",
    "type": "Person",
    "area": {"name": "Jamaica"},
    "begin-area": {"name": "Kingston"},
    "relations": [],
}


def test_find_area_happy_path(client: MusicBrainzClient, httpx_mock: HTTPXMock) -> None:
    """find_area() returns (begin_area.name, area.name) for the top MB match."""
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
    httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=_AREA_DETAIL_RESPONSE)

    city, country = client.find_area("Scientist")

    assert city == "Kingston"
    assert country == "Jamaica"


def test_find_area_no_results(client: MusicBrainzClient, httpx_mock: HTTPXMock) -> None:
    """find_area() returns (None, None) when search returns no results."""
    httpx_mock.add_response(url=SEARCH_URL, json={"count": 0, "offset": 0, "artists": []})

    city, country = client.find_area("Unknown Artist")

    assert city is None
    assert country is None


def test_find_area_http_error(client: MusicBrainzClient, httpx_mock: HTTPXMock) -> None:
    """find_area() returns (None, None) on HTTP error."""
    httpx_mock.add_response(url=SEARCH_URL, status_code=503)

    city, country = client.find_area("Scientist")

    assert city is None
    assert country is None


def test_find_area_missing_area_fields(client: MusicBrainzClient, httpx_mock: HTTPXMock) -> None:
    """find_area() returns (None, None) when artist detail has no area fields."""
    httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
    httpx_mock.add_response(
        url=ARTIST_DETAIL_URL,
        json={"id": MBID, "name": "Scientist", "type": "Person", "relations": []},
    )

    city, country = client.find_area("Scientist")

    assert city is None
    assert country is None


class TestIsPersonTraversal:
    """Glass Domain pattern: alias has 'is person' backward → real person → their aliases/bands."""

    ALIAS_MBID: ClassVar = "alias-0001-0000-0000-000000000000"
    PERSON_MBID: ClassVar = "person-0001-0000-0000-000000000000"

    ALIAS_SEARCH_RESPONSE: ClassVar = {
        "count": 1,
        "offset": 0,
        "artists": [{"id": ALIAS_MBID, "name": "Glass Domain", "score": 100}],
    }

    # Glass Domain has an "is person" BACKWARD relation → Gerald Donald
    ALIAS_RELATIONS_RESPONSE: ClassVar = {
        "id": ALIAS_MBID,
        "name": "Glass Domain",
        "type": "Person",
        "relations": [
            {
                "type": "is person",
                "direction": "backward",
                "artist": {"id": PERSON_MBID, "name": "Gerald Donald"},
            }
        ],
    }

    # Gerald Donald has "is person" FORWARD (aliases) and "member of band" FORWARD (groups)
    PERSON_RELATIONS_RESPONSE: ClassVar = {
        "id": PERSON_MBID,
        "name": "Gerald Donald",
        "type": "Person",
        "relations": [
            {
                "type": "is person",
                "direction": "forward",
                "artist": {"id": "g001", "name": "Dopplereffekt"},
            },
            {
                "type": "is person",
                "direction": "forward",
                "artist": {"id": "g002", "name": "Arpanet"},
            },
            {
                # Glass Domain itself — must be excluded from results
                "type": "is person",
                "direction": "forward",
                "artist": {"id": ALIAS_MBID, "name": "Glass Domain"},
            },
            {
                "type": "member of band",
                "direction": "forward",
                "artist": {"id": "g003", "name": "Drexciya"},
            },
        ],
    }

    def test_is_person_backward_traverses_to_real_person(
        self, client: MusicBrainzClient, httpx_mock: HTTPXMock
    ) -> None:
        """find_links traverses 'is person' backward to the person, collects their aliases."""
        httpx_mock.add_response(url=SEARCH_URL, json=self.ALIAS_SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.ALIAS_RELATIONS_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.PERSON_RELATIONS_RESPONSE)

        result = client.find_links("Glass Domain")

        assert "Dopplereffekt" in result
        assert "Arpanet" in result
        assert "Drexciya" in result
        assert "Glass Domain" not in result  # self excluded

    def test_is_person_backward_excludes_self(
        self, client: MusicBrainzClient, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=SEARCH_URL, json=self.ALIAS_SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.ALIAS_RELATIONS_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.PERSON_RELATIONS_RESPONSE)

        result = client.find_links("Glass Domain")

        assert "Glass Domain" not in result


class TestMemberOfBandBackwardTraversal:
    """G36 pattern: project has 'member of band' backward → person → their aliases/bands."""

    PROJECT_MBID: ClassVar = "proj-0001-0000-0000-000000000000"
    PERSON_MBID: ClassVar = "pers-0001-0000-0000-000000000000"

    PROJECT_SEARCH_RESPONSE: ClassVar = {
        "count": 1,
        "offset": 0,
        "artists": [{"id": PROJECT_MBID, "name": "G36", "score": 100}],
    }

    # G36 has "member of band" BACKWARD → Kevin Richard Martin (Kevin is the sole member)
    PROJECT_RELATIONS_RESPONSE: ClassVar = {
        "id": PROJECT_MBID,
        "name": "G36",
        "type": "Group",
        "relations": [
            {
                "type": "member of band",
                "direction": "backward",
                "artist": {"id": PERSON_MBID, "name": "Kevin Richard Martin"},
            }
        ],
    }

    # Kevin has "is person" forward (aliases) and "member of band" forward (other projects)
    PERSON_RELATIONS_RESPONSE: ClassVar = {
        "id": PERSON_MBID,
        "name": "Kevin Richard Martin",
        "type": "Person",
        "relations": [
            {
                "type": "is person",
                "direction": "forward",
                "artist": {"id": "k001", "name": "The Bug"},
            },
            {
                "type": "member of band",
                "direction": "forward",
                "artist": {"id": "k002", "name": "Ice"},
            },
            {
                "type": "member of band",
                "direction": "forward",
                "artist": {"id": "k003", "name": "Techno Animal"},
            },
            {
                # G36 appears in Kevin's forward bands too — must be excluded
                "type": "member of band",
                "direction": "forward",
                "artist": {"id": PROJECT_MBID, "name": "G36"},
            },
            {
                "type": "member of band",
                "direction": "forward",
                "artist": {"id": "k004", "name": "King Midas Sound"},
            },
        ],
    }

    def test_member_backward_traverses_to_person_aliases_and_bands(
        self, client: MusicBrainzClient, httpx_mock: HTTPXMock
    ) -> None:
        """find_links follows 'member of band' backward to the person then collects their projects."""  # noqa: E501
        httpx_mock.add_response(url=SEARCH_URL, json=self.PROJECT_SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.PROJECT_RELATIONS_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.PERSON_RELATIONS_RESPONSE)

        result = client.find_links("G36")

        assert "The Bug" in result  # via Kevin's "is person" forward
        assert "Ice" in result  # via Kevin's "member of band" forward
        assert "Techno Animal" in result  # via Kevin's "member of band" forward
        assert "King Midas Sound" in result
        assert "G36" not in result  # self excluded

    def test_member_backward_self_excluded(
        self, client: MusicBrainzClient, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=SEARCH_URL, json=self.PROJECT_SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.PROJECT_RELATIONS_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=self.PERSON_RELATIONS_RESPONSE)

        result = client.find_links("G36")

        assert "G36" not in result

    def test_direct_band_membership_unaffected(
        self, client: MusicBrainzClient, httpx_mock: HTTPXMock
    ) -> None:
        """Artists with direct forward memberships (GZA → Wu-Tang) still work unchanged."""
        httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=RELATIONS_RESPONSE)

        result = client.find_links("Jeff Mills")

        assert result == ["Underground Resistance", "The Belleville Three"]

    def test_no_traversal_relation_skips_extra_api_call(
        self, client: MusicBrainzClient, httpx_mock: HTTPXMock
    ) -> None:
        """Artists with only forward memberships make exactly one detail API call."""
        httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=RELATIONS_RESPONSE)
        # A second ARTIST_DETAIL_URL call would cause pytest-httpx to raise.

        result = client.find_links("Jeff Mills")

        assert result == ["Underground Resistance", "The Belleville Three"]


class TestRetryBehaviour:
    """Verify that transient 503s are retried and eventually succeed or gracefully exhaust."""

    def test_find_links_succeeds_after_transient_503(self, httpx_mock: HTTPXMock) -> None:
        """find_links retries a 503 and returns results when the retry succeeds."""
        retry_client = MusicBrainzClient(retry_attempts=2, _retry_wait=tenacity.wait_none())
        # First search attempt → 503, second → success
        httpx_mock.add_response(url=SEARCH_URL, status_code=503)
        httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=RELATIONS_RESPONSE)

        result = retry_client.find_links("Jeff Mills")

        assert result == ["Underground Resistance", "The Belleville Three"]

    def test_find_links_exhausts_retries_and_returns_empty(self, httpx_mock: HTTPXMock) -> None:
        """find_links returns [] when all retry attempts receive 503."""
        retry_client = MusicBrainzClient(retry_attempts=2, _retry_wait=tenacity.wait_none())
        httpx_mock.add_response(url=SEARCH_URL, status_code=503)
        httpx_mock.add_response(url=SEARCH_URL, status_code=503)

        result = retry_client.find_links("Jeff Mills")

        assert result == []

    def test_find_area_succeeds_after_transient_503(self, httpx_mock: HTTPXMock) -> None:
        """find_area retries a 503 and returns area data when the retry succeeds."""
        retry_client = MusicBrainzClient(retry_attempts=2, _retry_wait=tenacity.wait_none())
        httpx_mock.add_response(url=SEARCH_URL, status_code=503)
        httpx_mock.add_response(url=SEARCH_URL, json=SEARCH_RESPONSE)
        httpx_mock.add_response(url=ARTIST_DETAIL_URL, json=_AREA_DETAIL_RESPONSE)

        city, country = retry_client.find_area("Scientist")

        assert city == "Kingston"
        assert country == "Jamaica"
