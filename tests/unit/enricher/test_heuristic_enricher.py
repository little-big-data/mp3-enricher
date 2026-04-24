from __future__ import annotations

import pytest

from tagger.enricher.heuristic_enricher import HeuristicEnricher


@pytest.fixture
def enricher() -> HeuristicEnricher:
    return HeuristicEnricher()


def test_guess_gender_male(enricher: HeuristicEnricher) -> None:
    # The current threshold is he_count > 5
    text = (
        "He is a musician. He was born in 1970. His first album was a hit. "
        "He toured the world. He won many awards. He lives in London."
    )
    assert enricher._guess_gender(text) == "Male"


def test_guess_gender_female(enricher: HeuristicEnricher) -> None:
    text = (
        "She is a singer. She was born in 1980. Her first song was a hit. "
        "She toured the world. She won many awards. She lives in Paris."
    )
    assert enricher._guess_gender(text) == "Female"


def test_guess_origin(enricher: HeuristicEnricher) -> None:
    text = "The band was formed in London, United Kingdom. They play rock music."
    city, country = enricher._guess_origin(text)
    assert city == "London"
    assert country == "United Kingdom"


def test_guess_origin_with_year(enricher: HeuristicEnricher) -> None:
    text = "The band was formed in 1988 in Cleveland, Ohio."
    city, country = enricher._guess_origin(text)
    assert city == "Cleveland"
    assert country == "Ohio"


def test_guess_genres_from_discogs(enricher: HeuristicEnricher) -> None:
    from unittest.mock import MagicMock

    mock_release = MagicMock()
    mock_release.genres = ["Electronic", "Rock"]
    mock_release.styles = ["Techno", "Industrial"]

    primary, subgenres = enricher._guess_genres("", mock_release)
    assert primary == "Electronic"
    assert subgenres == ["Rock", "Techno", "Industrial"]


def test_guess_holiday_halloween(enricher: HeuristicEnricher) -> None:
    text = "This is a spooky gothic album with horror themes."
    assert enricher._guess_holiday(text, "Ghost Stories") == "Halloween"


def test_guess_holiday_christmas(enricher: HeuristicEnricher) -> None:
    text = "A collection of classic Christmas carols."
    assert enricher._guess_holiday(text, "Holiday Special") == "Christmas"


def test_guess_gender_mixed(enricher: HeuristicEnricher) -> None:
    text = (
        "He and she are both musicians. He plays guitar and she sings. "
        "He lives in NY and she lives in LA. He is tall and she is short."
    )
    assert enricher._guess_gender(text) == "Mixed"


def test_guess_gender_unknown(enricher: HeuristicEnricher) -> None:
    text = "This band is from Detroit. They play techno music."
    assert enricher._guess_gender(text) == "Unknown"


def test_guess_origin_born_in(enricher: HeuristicEnricher) -> None:
    text = "Trent Reznor was born in 1965 in Mercer, Pennsylvania."
    city, country = enricher._guess_origin(text)
    assert city == "Mercer"
    assert country == "Pennsylvania"


def test_guess_origin_no_match(enricher: HeuristicEnricher) -> None:
    text = "No location info here."
    city, country = enricher._guess_origin(text)
    assert city is None
    assert country is None


def test_guess_genres_no_match(enricher: HeuristicEnricher) -> None:
    assert enricher._guess_genres("Unknown style") == (None, [])


def test_guess_album_artist_born(enricher: HeuristicEnricher) -> None:
    text = "He was born Michael Trent Reznor."
    assert enricher._guess_album_artist("Trent Reznor", text) == "Michael Trent Reznor"


def test_guess_album_artist_release_artist_takes_priority(enricher: HeuristicEnricher) -> None:
    """release_artist (credited name on release) wins over realname and text heuristics."""
    from unittest.mock import MagicMock

    mock_artist = MagicMock()
    mock_artist.realname = "Hopeton Overton Brown"
    assert (
        enricher._guess_album_artist("Scientist", "", mock_artist, release_artist="Scientist")
        == "Scientist"
    )


def test_guess_album_artist_release_artist_beats_born(enricher: HeuristicEnricher) -> None:
    """release_artist takes priority even when text contains a 'born [Name]' pattern."""
    text = "He was born Michael Trent Reznor."
    assert (
        enricher._guess_album_artist("Trent Reznor", text, release_artist="Trent Reznor")
        == "Trent Reznor"
    )


def test_guess_album_artist_various(enricher: HeuristicEnricher) -> None:
    assert enricher._guess_album_artist("Various", "") == "Various"


def test_guess_holiday_none(enricher: HeuristicEnricher) -> None:
    assert enricher._guess_holiday("Regular album", "Normal Title") == "None"


def test_guess_link(enricher: HeuristicEnricher) -> None:
    assert enricher._guess_link("Member of Soulquarians") == "Soulquarians"
    assert enricher._guess_link("No link") is None


def test_guess_link_mb_takes_priority(enricher: HeuristicEnricher) -> None:
    # Even if text mentions a known link, MB data wins
    result = enricher._guess_link("Member of Soulquarians", mb_links=["Underground Resistance"])
    assert result == "Underground Resistance"


def test_guess_link_mb_first_entry_used(enricher: HeuristicEnricher) -> None:
    result = enricher._guess_link("", mb_links=["Group A", "Group B"])
    assert result == "Group A"


def test_guess_link_empty_mb_falls_back_to_text(enricher: HeuristicEnricher) -> None:
    result = enricher._guess_link("Member of Wu-Tang Clan", mb_links=[])
    assert result == "Wu-Tang Clan"


def test_enrich_album_uses_mb_links(enricher: HeuristicEnricher) -> None:
    data = enricher.enrich_album(
        "Jeff Mills", "Waveform Transmission", "", mb_links=["Underground Resistance"]
    )
    assert data.link == "Underground Resistance"


def test_extract_label(enricher: HeuristicEnricher) -> None:
    from unittest.mock import MagicMock

    mock_release = MagicMock()
    mock_label = MagicMock()
    mock_label.name = "Warp"
    mock_release.labels = [mock_label]
    assert enricher._extract_label(mock_release) == "Warp"
    assert enricher._extract_label(None) is None


def test_enrich_album_full(enricher: HeuristicEnricher) -> None:
    context = (
        "He is a rock musician from Detroit, United States. "
        "He released this spooky album in October. He. He. He. He. He."
    )
    data = enricher.enrich_album("Artist", "Album", context)
    assert data.gender == "Male"
    assert data.origin_city == "Detroit"
    assert data.origin_country == "United States"
    assert data.genre == "Rock"
    assert data.holiday == "Halloween"


def test_enrich_album_uses_release_artist(enricher: HeuristicEnricher) -> None:
    """release_artist param is passed through enrich_album to _guess_album_artist."""
    data = enricher.enrich_album(
        "Scientist", "Scientist Rids the World", "", release_artist="Scientist"
    )
    assert data.album_artist_canonical == "Scientist"


def test_mb_origin_overrides_heuristic_county_as_country(enricher: HeuristicEnricher) -> None:
    """mb_origin takes priority over regex heuristics.

    "from Cullen, Moray" makes the regex capture "Moray" (a Scottish county) as
    the country.  When mb_origin is supplied it overrides the heuristic result.
    """
    text = "She was born from Cullen, Moray."
    data = enricher.enrich_album("Artist", "Album", text, mb_origin=("Cullen", "Scotland"))
    assert data.origin_country == "Scotland"


def test_origin_without_mb_uses_heuristic(enricher: HeuristicEnricher) -> None:
    """Without mb_origin, _guess_origin heuristic result is used."""
    text = "She was formed in London, United Kingdom."
    data = enricher.enrich_album("Artist", "Album", text)
    assert data.origin_country == "United Kingdom"
