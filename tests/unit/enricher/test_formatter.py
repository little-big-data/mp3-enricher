from __future__ import annotations

import pytest

from tagger.enricher.formatter import (
    format_track_number,
    normalize_artist,
    normalize_title,
    parse_track_from_filename,
    strip_discogs_number,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Song Title [feat. Guest]", "Song Title (feat. Guest)"),
        ("Song Title {feat. Guest}", "Song Title (feat. Guest)"),
        ("Song Title [Remix]", "Song Title (Remix)"),
        ("Song Title (Remix)", "Song Title (Remix)"),  # already correct
        ("Song Title [feat. A] [Remix]", "Song Title (feat. A) (Remix)"),
        ("SONG TITLE", "Song Title"),  # title-case normalisation
        ("song title (feat. guest)", "Song Title (feat. Guest)"),
        ("Song Title (feat. Artist A & Artist B)", "Song Title (feat. Artist A & Artist B)"),
        # Contractions must not uppercase the letter after the apostrophe (issue #79)
        ("She's Gone", "She's Gone"),
        ("she's gone", "She's Gone"),
        ("SHE'S GONE", "She's Gone"),
        ("Don't Stop", "Don't Stop"),
        ("don't stop", "Don't Stop"),
        ("WE'LL ROCK YOU", "We'll Rock You"),
        ("We're Not Gonna Take It", "We're Not Gonna Take It"),
        ("I've Got You", "I've Got You"),
        ("I'm Yours", "I'm Yours"),
        ("it's now or never", "It's Now Or Never"),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


@pytest.mark.parametrize(
    ("artist", "title", "expected_artist", "expected_title"),
    [
        ("Artist feat. Guest", "Song Title", "Artist", "Song Title (feat. Guest)"),
        ("Artist ft. Guest", "Song Title", "Artist", "Song Title (feat. Guest)"),
        ("Artist featuring Guest", "Song Title", "Artist", "Song Title (feat. Guest)"),
        ("Artist", "Song Title", "Artist", "Song Title"),
        ("Artist A & Artist B", "Song Title", "Artist A & Artist B", "Song Title"),
    ],
)
def test_extract_featured_artist(
    artist: str, title: str, expected_artist: str, expected_title: str
) -> None:

    norm_artist, norm_title = normalize_artist(artist, title)
    assert norm_artist == expected_artist
    assert norm_title == expected_title


def test_normalize_artist_double_feat() -> None:
    # If artist has feat AND title already has same feat
    artist = "Artist feat. Guest"
    title = "Song Title (feat. Guest)"
    norm_artist, norm_title = normalize_artist(artist, title)
    assert norm_artist == "Artist"
    assert norm_title == "Song Title (feat. Guest)"


def test_normalize_artist_different_feat() -> None:
    # If artist has feat AND title has DIFFERENT feat
    artist = "Artist feat. Guest B"
    title = "Song Title (feat. Guest A)"
    norm_artist, norm_title = normalize_artist(artist, title)
    assert norm_artist == "Artist"
    # Current implementation just appends
    assert "feat. Guest A" in norm_title
    assert "feat. Guest B" in norm_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Loop (3)", "Loop"),  # typical Discogs disambiguation
        ("Loop (12)", "Loop"),  # double-digit
        ("The Bug (2)", "The Bug"),
        ("Ceremony (2)", "Ceremony"),
        ("Loop", "Loop"),  # no number — unchanged
        ("Echo & The Bunnymen", "Echo & The Bunnymen"),  # parens-free — unchanged
        ("Talk Talk (2)", "Talk Talk"),
        ("The Fall (2)", "The Fall"),
        ("Cat Power", "Cat Power"),  # no parens at all
        ("A (2) B", "A (2) B"),  # number not at end — leave alone
    ],
)
def test_strip_discogs_number(raw: str, expected: str) -> None:
    assert strip_discogs_number(raw) == expected


@pytest.mark.parametrize(
    ("pos", "total", "expected"),
    [
        (1, 10, "01/10"),
        (10, 10, "10/10"),
        ("1", 12, "01/12"),
        ("A1", 5, "A1/05"),  # Vinyl — no sequential_index supplied → raw fallback
        ("1", None, "01"),
    ],
)
def test_format_track_number(pos: str | int, total: int | None, expected: str) -> None:
    assert format_track_number(pos, total) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # Single-disc: plain leading number
        ("01 Song.mp3", (1, None)),
        ("1 Song.mp3", (1, None)),
        ("12 Song Title.mp3", (12, None)),
        # Single-disc: "NN - Title" format
        ("01 - Song.mp3", (1, None)),
        ("07 - Track Name.mp3", (7, None)),
        # Multi-disc: "D-TT" prefix
        ("2-01 Song.mp3", (1, 2)),
        ("1-03 Song.mp3", (3, 1)),
        ("10-03 Song.mp3", (3, 10)),
        ("2-12 Last Track.mp3", (12, 2)),
        # No prefix
        ("Song.mp3", (None, None)),
        ("No Number Here.mp3", (None, None)),
        # Vinyl-style — NOT parsed (letters precede digits)
        ("A1 Song.mp3", (None, None)),
        ("B2 Track.mp3", (None, None)),
    ],
)
def test_parse_track_from_filename(filename: str, expected: tuple[int | None, int | None]) -> None:
    assert parse_track_from_filename(filename) == expected


@pytest.mark.parametrize(
    ("pos", "total", "seq", "expected"),
    [
        # Multi-disc: within-disc track number extracted from "D-T" position
        ("2-1", 20, 11, "01/20"),  # disc 2 track 1, overall 11th — uses T=1
        ("1-3", 12, 3, "03/12"),  # disc 1 track 3
        ("2-10", 20, 20, "10/20"),  # disc 2 track 10
        # Vinyl: sequential_index used when position is non-numeric
        ("A1", 8, 1, "01/08"),
        ("A2", 8, 2, "02/08"),
        ("B1", 8, 3, "03/08"),
        ("B2", 8, 4, "04/08"),
        # Pure numeric with sequential_index — numeric position wins
        ("3", 12, 99, "03/12"),
        # No total
        ("A1", None, 2, "02"),
    ],
)
def test_format_track_number_with_sequential(
    pos: str, total: int | None, seq: int, expected: str
) -> None:
    """Non-numeric positions use sequential_index; multi-disc uses within-disc number."""
    assert format_track_number(pos, total, sequential_index=seq) == expected
