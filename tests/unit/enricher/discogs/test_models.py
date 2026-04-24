from __future__ import annotations

from tagger.enricher.discogs.models import DiscogsRelease, DiscogsSearchResult


def test_discogs_release_parsing() -> None:
    data = {
        "id": 75544,
        "title": "Pretty Hate Machine",
        "year": 1989,
        "released": "1989-10-20",
        "master_id": 207276,
        "master_url": "https://api.discogs.com/masters/207276",
        "uri": "https://www.discogs.com/release/75544",
        "artists": [{"name": "Nine Inch Nails", "id": 789}],
        "images": [{"type": "primary", "resource_url": "http://example.com/art.jpg"}],
        "tracklist": [{"position": "1", "title": "Head Like A Hole", "duration": "4:59"}],
    }
    release = DiscogsRelease.model_validate(data)
    assert release.id == 75544
    assert release.title == "Pretty Hate Machine"
    assert release.year == 1989
    assert len(release.artists) == 1
    assert release.artists[0].name == "Nine Inch Nails"


def test_discogs_search_result_parsing() -> None:
    data = {
        "id": 75544,
        "type": "release",
        "master_id": 207276,
        "title": "Nine Inch Nails - Pretty Hate Machine",
        "year": "1989",
        "resource_url": "https://api.discogs.com/releases/75544",
    }
    result = DiscogsSearchResult.model_validate(data)
    assert result.id == 75544
    assert result.master_id == 207276
    assert result.title == "Nine Inch Nails - Pretty Hate Machine"
    assert result.year == 1989


def test_discogs_search_result_parsing_edge_cases() -> None:
    # Empty year
    data = {
        "id": 1,
        "type": "release",
        "title": "T",
        "resource_url": "https://api.discogs.com/r/1",
        "year": "",
    }
    assert DiscogsSearchResult.model_validate(data).year is None

    # Invalid year
    data["year"] = "not-a-year"
    assert DiscogsSearchResult.model_validate(data).year is None

    # Full date year
    data["year"] = "1994-10-20"
    assert DiscogsSearchResult.model_validate(data).year == 1994

    # None year
    data["year"] = None
    assert DiscogsSearchResult.model_validate(data).year is None


def test_discogs_release_null_artist_id() -> None:
    """Discogs API sometimes returns null for artists[0].id — must not crash."""
    data = {
        "id": 99999,
        "title": "Some Album",
        "artists": [{"name": "Some Artist", "id": None}],
        "tracklist": [],
    }
    release = DiscogsRelease.model_validate(data)
    assert release.artists[0].name == "Some Artist"
    assert release.artists[0].id is None


def test_discogs_release_null_label_id() -> None:
    """Discogs API sometimes returns null for labels[0].id — must not crash."""
    data = {
        "id": 99999,
        "title": "Some Album",
        "artists": [{"name": "Some Artist", "id": 1}],
        "labels": [{"name": "Some Label", "id": None}],
        "tracklist": [],
    }
    release = DiscogsRelease.model_validate(data)
    assert release.labels[0].name == "Some Label"
    assert release.labels[0].id is None


def test_discogs_release_null_artist_and_label_ids() -> None:
    """Both artist and label ids null — the M-letter crash scenario."""
    data = {
        "id": 99999,
        "title": "Some Album",
        "artists": [{"name": "Some Artist", "id": None}],
        "labels": [{"name": "Some Label", "id": None}],
        "tracklist": [],
    }
    release = DiscogsRelease.model_validate(data)
    assert release.artists[0].id is None
    assert release.labels[0].id is None
