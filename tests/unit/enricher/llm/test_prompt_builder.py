"""Unit tests for tagger.enricher.llm.prompt_builder."""

from __future__ import annotations

from tagger.enricher.llm.prompt_builder import build_link_prompt


def test_prompt_contains_artist_name() -> None:
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label=None,
        genres=[],
        featured_artists=[],
    )
    assert "GZA" in prompt


def test_prompt_contains_album_title() -> None:
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label=None,
        genres=[],
        featured_artists=[],
    )
    assert "Liquid Swords" in prompt


def test_prompt_includes_label_when_provided() -> None:
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label="Geffen Records",
        genres=[],
        featured_artists=[],
    )
    assert "Geffen Records" in prompt


def test_prompt_includes_genres() -> None:
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label=None,
        genres=["Hip-Hop", "East Coast"],
        featured_artists=[],
    )
    assert "Hip-Hop" in prompt


def test_prompt_includes_featured_artists() -> None:
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label=None,
        genres=[],
        featured_artists=["RZA", "Method Man", "Raekwon"],
    )
    assert "RZA" in prompt
    assert "Method Man" in prompt
    assert "Raekwon" in prompt


def test_prompt_omits_label_section_when_none() -> None:
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label=None,
        genres=[],
        featured_artists=[],
    )
    # The word "Label:" should not appear if label is None
    assert "Label:" not in prompt


def test_prompt_omits_featured_section_when_empty() -> None:
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label=None,
        genres=[],
        featured_artists=[],
    )
    assert "Featured" not in prompt


def test_prompt_requests_json_response() -> None:
    """The prompt must request a JSON object so the response is parseable."""
    prompt = build_link_prompt(
        artist="GZA",
        album="Liquid Swords",
        label=None,
        genres=[],
        featured_artists=[],
    )
    assert "JSON" in prompt or "json" in prompt
    assert "links" in prompt
