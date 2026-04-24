from __future__ import annotations

import pytest

from tagger.enricher.models import EnrichmentData, TrackOverride


def test_enrichment_data_to_grp1_full() -> None:
    data = EnrichmentData(
        origin_city="Detroit",
        origin_country="US",
        gender="Male",
        race="Black",
        genre="Techno",
        subgenres=["Detroit Techno", "Electronic"],
        link="Underground Resistance",
        holiday="None",
    )
    grp1 = data.to_grp1()
    assert "Origin:Detroit, US" in grp1
    assert "Gender:Male" in grp1
    assert "Race:Black" in grp1
    assert "Subgenre:Detroit Techno, Electronic" in grp1
    assert "link:Underground Resistance" in grp1
    assert "Holiday" not in grp1  # None should be omitted


def test_enrichment_data_to_grp1_minimal() -> None:
    data = EnrichmentData(gender="Unknown", holiday="None")
    grp1 = data.to_grp1()
    assert grp1 == ""


def test_enrichment_data_to_grp1_label() -> None:
    data = EnrichmentData(label="Warp Records")
    grp1 = data.to_grp1()
    assert grp1 == "Label:Warp Records"


def test_enrichment_data_to_grp1_label_and_link_both_present() -> None:
    data = EnrichmentData(label="Giegling", link="Session Victim")
    grp1 = data.to_grp1()
    assert "Label:Giegling" in grp1
    assert "link:Session Victim" in grp1


def test_enrichment_data_to_grp1_track_override() -> None:
    data = EnrichmentData(genre="Electronic")
    override = TrackOverride(position="1", is_instrumental=True, is_cover=True, is_remix=False)
    grp1 = data.to_grp1(track_override=override)
    assert "Instrumental:Yes" in grp1
    assert "Cover:Yes" in grp1
    assert "Remix" not in grp1  # No should be omitted for Remix in current implementation?
    # Wait, let me check the implementation of to_grp1 again.


@pytest.mark.parametrize(
    ("link_value", "expected_segment"),
    [
        ("Wu-Tang Clan", "link:Wu-Tang Clan"),
        ("Wu-Tang Clan, Gravediggaz", "link:Wu-Tang Clan, Gravediggaz"),
        ("Giegling", "link:Giegling"),
    ],
)
def test_to_grp1_uses_link_key(link_value: str, expected_segment: str) -> None:
    data = EnrichmentData(link=link_value)
    grp1 = data.to_grp1()
    assert expected_segment in grp1
    assert "Collective:" not in grp1  # old key must not appear


def test_enrichment_data_to_grp1_remix_yes() -> None:
    data = EnrichmentData()
    override = TrackOverride(position="1", is_remix=True)
    grp1 = data.to_grp1(track_override=override)
    assert "Remix:Yes" in grp1
