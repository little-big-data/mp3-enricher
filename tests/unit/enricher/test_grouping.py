"""Unit tests for tagger/enricher/grouping.py — ensure_grouping_segment().

Covers appending a new ``key:value`` segment to a pipe-separated GRP1/TIT1
grouping string, merging a value comma-separated into an existing segment of
the same key, case-insensitive idempotency, and segment-boundary correctness.
"""

from __future__ import annotations

import pytest

from tagger.enricher import grouping


@pytest.mark.unit
@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        # empty / missing grouping -> the segment alone
        ("", "Holiday:Halloween"),
        (None, "Holiday:Halloween"),
        ("   ", "Holiday:Halloween"),
        # append after existing segments, preserving them verbatim
        (
            "Origin:Detroit, US | Gender:Male",
            "Origin:Detroit, US | Gender:Male | Holiday:Halloween",
        ),
        ("link:Wu-Tang Clan", "link:Wu-Tang Clan | Holiday:Halloween"),
        # trailing separator debris is cleaned before appending
        ("Origin:Detroit, US | ", "Origin:Detroit, US | Holiday:Halloween"),
        # key match is on the segment key, not a substring of another segment
        ("Subgenre:Holiday Music", "Subgenre:Holiday Music | Holiday:Halloween"),
        ("link:Holiday Inn", "link:Holiday Inn | Holiday:Halloween"),
        # merge into an existing segment of the same key
        ("Holiday:Christmas", "Holiday:Christmas, Halloween"),
        (
            "Origin:X | Holiday:Christmas | Label:Y",
            "Origin:X | Holiday:Christmas, Halloween | Label:Y",
        ),
        # existing key casing is preserved on merge
        ("HOLIDAY:Christmas", "HOLIDAY:Christmas, Halloween"),
        # value match is per comma-separated item, not a substring
        ("Holiday:Halloween Party", "Holiday:Halloween Party, Halloween"),
        # a placeholder 'None' value is replaced, never merged into
        ("Gender:Male | Holiday:None", "Gender:Male | Holiday:Halloween"),
    ],
)
def test_ensure_grouping_segment_adds_holiday(existing: str | None, expected: str) -> None:
    assert grouping.ensure_grouping_segment(existing, "Holiday", "Halloween") == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "existing",
    [
        "Holiday:Halloween",
        "Origin:Detroit, US | Gender:Male | Holiday:Halloween",
        "holiday:halloween",
        "Gender:Male | HOLIDAY:HALLOWEEN | Label:Z",
        "Holiday:Christmas, Halloween",
        "Holiday:Christmas,Halloween",
    ],
)
def test_ensure_grouping_segment_already_present_is_unchanged(existing: str) -> None:
    """If the key segment already holds the value (case-insensitive) return input as-is."""
    assert grouping.ensure_grouping_segment(existing, "Holiday", "Halloween") == existing


@pytest.mark.unit
@pytest.mark.parametrize(
    "existing",
    [
        "",
        "Origin:Detroit, US | Gender:Male",
        "Holiday:Christmas",
        "Origin:X | Holiday:Christmas | Label:Y",
        "Gender:Male | Holiday:None",
    ],
)
def test_ensure_grouping_segment_is_idempotent(existing: str) -> None:
    once = grouping.ensure_grouping_segment(existing, "Holiday", "Halloween")
    twice = grouping.ensure_grouping_segment(once, "Holiday", "Halloween")
    assert twice == once


@pytest.mark.unit
def test_ensure_grouping_segment_generic_key() -> None:
    """The helper is key-agnostic: works for any key, not just Holiday."""
    assert (
        grouping.ensure_grouping_segment("Holiday:Halloween", "Mood", "Spooky")
        == "Holiday:Halloween | Mood:Spooky"
    )
    assert grouping.ensure_grouping_segment("Mood:Dark", "Mood", "Spooky") == "Mood:Dark, Spooky"


@pytest.mark.unit
def test_ensure_grouping_segment_multiple_holidays_accumulate() -> None:
    result = grouping.ensure_grouping_segment("Gender:Male", "Holiday", "Christmas")
    result = grouping.ensure_grouping_segment(result, "Holiday", "Halloween")
    result = grouping.ensure_grouping_segment(result, "Holiday", "Christmas")
    assert result == "Gender:Male | Holiday:Christmas, Halloween"


@pytest.mark.unit
def test_grouping_module_does_not_import_link_scanner() -> None:
    """grouping.py must stay free of the LLM-flavoured LinkScanner dependency."""
    import inspect

    source = inspect.getsource(grouping)
    assert "link_scanner" not in source
    assert "LinkScanner" not in source
