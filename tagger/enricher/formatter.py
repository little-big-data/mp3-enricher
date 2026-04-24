from __future__ import annotations

import os
import re

# Discogs appends a disambiguation number to non-unique artist names, e.g. "Loop (3)".
# This pattern matches only when the parenthetical is at the very end and contains
# solely digits — so it won't strip meaningful suffixes like "Talk Talk (Live)".
_DISCOGS_NUM_RE = re.compile(r"\s+\(\d+\)$")

# Multi-disc position like "2-3" or "1-12" — captures the within-disc track number.
_DISC_TRACK_RE = re.compile(r"^\d+-(\d+)$")

# Filename-prefix patterns for track/disc number extraction.
# Try multi-disc first: "2-01 Song.mp3" → disc=2, track=1
_FN_DISC_PREFIX_RE = re.compile(r"^(\d+)-(\d{1,2})\b")
# Single-disc fallback: "01 Song.mp3" → track=1
_FN_LEADING_NUMBER_RE = re.compile(r"^(\d+)")

# Featured artist patterns: feat., ft., featuring
FEAT_RE = re.compile(r"\s+\(?(feat\.|ft\.|featuring)\s+(.*?)\)?$", re.IGNORECASE)
FEAT_SUB_RE = re.compile(r"[\[\{]\s*(feat\.|ft\.|featuring)\s+(.*?)[\]\}]", re.IGNORECASE)


def parse_track_from_filename(filename: str) -> tuple[int | None, int | None]:
    """Return ``(track_number, disc_number)`` parsed from a filename prefix.

    Handles the two common MP3 library naming conventions:

    - ``"2-01 Song.mp3"``  → ``(1, 2)``   (disc 2, track 1)
    - ``"01 Song.mp3"``    → ``(1, None)`` (single-disc)
    - ``"01 - Song.mp3"``  → ``(1, None)``
    - ``"Song.mp3"``        → ``(None, None)`` (no prefix)
    - ``"A1 Song.mp3"``    → ``(None, None)`` (vinyl — ignored)

    Only the stem (extension-stripped) prefix is examined.  Vinyl-style
    prefixes (e.g. ``A1``, ``B2``) that start with a letter are not
    recognised and return ``(None, None)``.
    """
    stem = os.path.splitext(filename)[0]
    # Multi-disc: "D-TT" prefix must be tried first so "2-01 Song" isn't
    # misread as single-disc track 2.
    m = _FN_DISC_PREFIX_RE.match(stem)
    if m:
        return int(m.group(2)), int(m.group(1))
    # Single-disc: plain leading number
    m = _FN_LEADING_NUMBER_RE.match(stem)
    if m:
        return int(m.group(1)), None
    return None, None


def strip_discogs_number(name: str) -> str:
    """Remove Discogs disambiguation suffixes from artist/label names.

    Discogs appends ``(N)`` to disambiguate artists with identical names,
    e.g. ``"Loop (3)"`` or ``"Ceremony (2)"``.  These numbers are an
    artefact of the Discogs data model and should not appear in ID3 tags.

    Only strips a trailing ``(N)`` where N is purely numeric; parentheticals
    containing text (e.g. ``"Talk Talk (Live)"``) are left untouched.
    """
    return _DISCOGS_NUM_RE.sub("", name)


def normalize_title(title: str) -> str:
    """
    Normalizes title by:
    1. Converting [Remix] or {Remix} to (Remix)
    2. Converting [feat. X] or {feat. X} to (feat. X)
    3. Title-casing the entire string
    """
    # Normalize brackets and braces to parentheses
    # First, handle the feat. specifically to ensure it follows the (feat. X) format
    title = FEAT_SUB_RE.sub(r"(feat. \2)", title)
    # Then handle other bracketed content
    title = re.sub(r"[\[\{](.*?)[\]\}]", r"(\1)", title)

    # Title case normalization - we need to be careful with things already in parentheses
    # A simple .title() is too aggressive (it makes (feat. Guest) into (Feat. Guest))
    # Let's use a more surgical title case or just simple string logic if possible.
    # The requirement says "SONG TITLE" -> "Song Title"

    # We'll split by words and title case them, but preserve some acronyms or formats.
    # For now, a standard title() followed by some fixups for common lowercase things.
    title = title.title()

    # Fix 'feat.' which title() makes 'Feat.'
    title = re.sub(r"\(Feat\.", "(feat.", title)
    title = re.sub(r"\(Ft\.", "(feat.", title)  # also normalize ft. to feat.

    # title() capitalises the letter after every apostrophe, turning "She's" into
    # "She'S".  Lowercase any letters that immediately follow an apostrophe
    # preceded by a word character — this covers all English contractions
    # ('s, 't, 've, 're, 'll, 'd, 'm) without special-casing each one.
    title = re.sub(r"(?<=\w)'([A-Za-z]+)", lambda m: "'" + m.group(1).lower(), title)

    return title


def normalize_artist(artist: str, title: str) -> tuple[str, str]:
    """
    Extracts featured artists from artist name and appends them to the title.
    Returns (cleaned_artist, cleaned_title).
    """
    match = FEAT_RE.search(artist)
    if match:
        feat_marker = "feat."  # We normalize to feat.
        guest_artist = match.group(2).strip()
        cleaned_artist = artist[: match.start()].strip()

        # Avoid double feat if already in title
        if f"(feat. {guest_artist})" not in title:
            # If title already has something in parens at the end, append after or inside?
            # Brief says: Song Title (feat. Guest)
            cleaned_title = f"{title} ({feat_marker} {guest_artist})"
        else:
            cleaned_title = title

        return cleaned_artist, cleaned_title

    return artist, title


def format_track_number(
    pos: str | int,
    total: int | None,
    sequential_index: int | None = None,
) -> str:
    """Format a Discogs position into a TRCK tag value (``N`` or ``N/M``).

    Pads with a leading zero when total ≥ 10.

    Handles all common Discogs position formats:

    - **Pure numeric** (``"3"``, ``"12"``): used directly.
    - **Multi-disc** (``"2-3"``): the within-disc track number is extracted
      (``3`` in this example).  The disc number is written separately to TPOS.
    - **Non-numeric** (vinyl ``"A1"``/``"B2"``, roman numerals ``"II"``, etc.):
      uses ``sequential_index`` (1-based ordinal in the full tracklist) when
      provided; otherwise falls back to the raw position string.
    """
    pos_str = str(pos).strip()

    # Pure numeric
    if pos_str.isdigit():
        val = int(pos_str)
    # Multi-disc "D-T": extract within-disc track number; TPOS carries the disc
    elif m := _DISC_TRACK_RE.match(pos_str):
        val = int(m.group(1))
    # Non-numeric (vinyl A1/B2, roman numerals, etc.)
    elif sequential_index is not None:
        val = sequential_index
    else:
        # No numeric interpretation — return raw position string
        total_str = f"/{total:02d}" if total else ""
        return f"{pos_str}{total_str}"

    pad = total is not None and total >= 10
    formatted = f"{val:02d}" if (pad or val < 10) else str(val)
    return f"{formatted}/{total:02d}" if total else formatted
