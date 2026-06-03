"""Unit tests for tagger/integrity/itunes_comparator.py.

Covers ItunesLibrary (plist parsing + lookup) and compare_library
(discrepancy detection logic). No real filesystem I/O — all fixtures
use tmp_path or in-memory mocks.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import patch

from tagger.integrity.itunes_comparator import AuditDiscrepancy, ItunesLibrary, compare_library

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_plist(path: Path, tracks: dict[str, dict]) -> None:
    """Write a minimal iTunes-style plist XML to *path*."""
    data: dict = {"Tracks": tracks}
    with path.open("wb") as fh:
        plistlib.dump(data, fh, fmt=plistlib.FMT_XML)


def _make_track(
    *,
    location: str,
    artist: str = "Test Artist",
    album_artist: str = "Test Album Artist",
    album: str = "Test Album",
    track_number: int = 1,
) -> dict:
    return {
        "Track ID": 1,
        "Name": "Test Track",
        "Artist": artist,
        "Album Artist": album_artist,
        "Album": album,
        "Track Number": track_number,
        "Location": location,
    }


# ---------------------------------------------------------------------------
# ItunesLibrary tests
# ---------------------------------------------------------------------------


class TestItunesLibraryLookup:
    def test_lookup_found(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_plist(
            xml,
            {
                "1": _make_track(
                    location="file://localhost/M:/Shared%20Music/Artist/Album/01%20Song.mp3"
                )
            },
        )
        lib = ItunesLibrary(xml)
        result = lib.lookup(Path("M:/Shared Music/Artist/Album/01 Song.mp3"))
        assert result is not None
        assert result["Artist"] == "Test Artist"

    def test_lookup_not_found(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_plist(
            xml,
            {
                "1": _make_track(
                    location="file://localhost/M:/Shared%20Music/Artist/Album/01%20Song.mp3"
                )
            },
        )
        lib = ItunesLibrary(xml)
        result = lib.lookup(Path("M:/Shared Music/OtherArtist/Album/02 Other.mp3"))
        assert result is None

    def test_lookup_case_insensitive(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_plist(
            xml,
            {
                "1": _make_track(
                    location="file://localhost/M:/Shared%20Music/ARTIST/Album/01%20Song.mp3"
                )
            },
        )
        lib = ItunesLibrary(xml)
        # Lookup with different casing
        result = lib.lookup(Path("M:/Shared Music/artist/album/01 song.mp3"))
        assert result is not None

    def test_tracks_with_no_location_are_skipped(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        track = _make_track(
            location="file://localhost/M:/Shared%20Music/Artist/Album/01%20Song.mp3"
        )
        no_loc = {k: v for k, v in track.items() if k != "Location"}
        _write_plist(xml, {"1": no_loc, "2": track})
        lib = ItunesLibrary(xml)
        # Index should only have the track with a Location
        assert lib.lookup(Path("M:/Shared Music/Artist/Album/01 Song.mp3")) is not None

    def test_url_encoded_spaces_and_special_chars(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_plist(
            xml,
            {
                "1": _make_track(
                    location="file://localhost/M:/Shared%20Music/Jay-Z/The%20Blueprint/01%20The%20Ruler%27s%20Back.mp3"
                )
            },
        )
        lib = ItunesLibrary(xml)
        result = lib.lookup(Path("M:/Shared Music/Jay-Z/The Blueprint/01 The Ruler's Back.mp3"))
        assert result is not None


# ---------------------------------------------------------------------------
# compare_library tests
# ---------------------------------------------------------------------------

MP3_PATH = Path("M:/Shared Music/Some Artist/Some Album/01 Track.mp3")
LOCATION = "file://localhost/M:/Shared%20Music/Some%20Artist/Some%20Album/01%20Track.mp3"


def _run_compare(
    tmp_path: Path,
    *,
    itunes_artist: str = "Some Artist",
    itunes_album_artist: str = "Some Artist",
    itunes_album: str = "Some Album",
    itunes_track_number: int = 1,
    mp3_tags: dict,
    threshold: int = 75,
) -> list[AuditDiscrepancy]:
    """Build a single-track plist and run compare_library with mocked I/O."""
    xml = tmp_path / "library.xml"
    _write_plist(
        xml,
        {
            "1": _make_track(
                location=LOCATION,
                artist=itunes_artist,
                album_artist=itunes_album_artist,
                album=itunes_album,
                track_number=itunes_track_number,
            )
        },
    )

    with (
        patch("tagger.integrity.itunes_comparator.find_mp3_files", return_value=[MP3_PATH]),
        patch("tagger.integrity.itunes_comparator.read_id3_tags", return_value=mp3_tags),
    ):
        return compare_library(
            library_path=Path("M:/Shared Music"),
            itunes_xml=xml,
            threshold=threshold,
            workers=1,
        )


class TestCompareLibrary:
    def test_no_discrepancy_on_perfect_match(self, tmp_path: Path) -> None:
        results = _run_compare(
            tmp_path,
            mp3_tags={
                "artist": "Some Artist",
                "album_artist": "Some Artist",
                "album": "Some Album",
                "track_number": 1,
            },
        )
        assert results == []

    def test_artist_mismatch_flagged(self, tmp_path: Path) -> None:
        results = _run_compare(
            tmp_path,
            itunes_artist="Correct Artist",
            mp3_tags={
                "artist": "Completely Wrong",
                "album_artist": "Some Artist",
                "album": "Some Album",
                "track_number": 1,
            },
        )
        assert len(results) == 1
        assert "artist_mismatch" in results[0].issues
        assert "album_artist_mismatch" not in results[0].issues
        assert "album_mismatch" not in results[0].issues

    def test_album_artist_mismatch_flagged(self, tmp_path: Path) -> None:
        results = _run_compare(
            tmp_path,
            itunes_album_artist="Beethoven Ludwig Van",
            mp3_tags={
                "artist": "Some Artist",
                "album_artist": "Radiohead",
                "album": "Some Album",
                "track_number": 1,
            },
        )
        assert any("album_artist_mismatch" in r.issues for r in results)

    def test_album_mismatch_flagged(self, tmp_path: Path) -> None:
        results = _run_compare(
            tmp_path,
            itunes_album="Correct Album Title",
            mp3_tags={
                "artist": "Some Artist",
                "album_artist": "Some Artist",
                "album": "Completely Different Album",
                "track_number": 1,
            },
        )
        assert any("album_mismatch" in r.issues for r in results)

    def test_missing_track_number_flagged(self, tmp_path: Path) -> None:
        results = _run_compare(
            tmp_path,
            mp3_tags={
                "artist": "Some Artist",
                "album_artist": "Some Artist",
                "album": "Some Album",
                # track_number intentionally absent
            },
        )
        assert len(results) == 1
        assert results[0].issues == ["missing_track_number"]

    def test_itunes_not_found(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        # Plist has a track at a different path
        _write_plist(
            xml,
            {
                "1": _make_track(
                    location="file://localhost/M:/Shared%20Music/Other/Album/01%20Other.mp3"
                )
            },
        )

        with (
            patch("tagger.integrity.itunes_comparator.find_mp3_files", return_value=[MP3_PATH]),
            patch(
                "tagger.integrity.itunes_comparator.read_id3_tags",
                return_value={
                    "artist": "Some Artist",
                    "album_artist": "Some Artist",
                    "album": "Some Album",
                    "track_number": 1,
                },
            ),
        ):
            results = compare_library(
                library_path=Path("M:/Shared Music"),
                itunes_xml=xml,
                threshold=75,
                workers=1,
            )

        assert len(results) == 1
        assert "itunes_not_found" in results[0].issues

    def test_threshold_boundary_below_flags(self, tmp_path: Path) -> None:
        """Score strictly below threshold triggers a mismatch flag."""
        results = _run_compare(
            tmp_path,
            itunes_artist="Alpha",
            mp3_tags={
                "artist": "Completely Unrelated Name",
                "album_artist": "Some Artist",
                "album": "Some Album",
                "track_number": 1,
            },
            threshold=75,
        )
        assert any("artist_mismatch" in r.issues for r in results)

    def test_threshold_boundary_at_or_above_does_not_flag(self, tmp_path: Path) -> None:
        """Score at or above threshold does not trigger mismatch."""
        results = _run_compare(
            tmp_path,
            itunes_artist="Some Artist",
            mp3_tags={
                "artist": "Some Artist",
                "album_artist": "Some Artist",
                "album": "Some Album",
                "track_number": 1,
            },
            threshold=75,
        )
        assert results == []

    def test_multiple_issues_in_single_row(self, tmp_path: Path) -> None:
        """A track can have several issues at once."""
        results = _run_compare(
            tmp_path,
            itunes_artist="Original Artist",
            itunes_album="Original Album",
            mp3_tags={
                "artist": "Wrong Artist XYZ",
                "album_artist": "Some Artist",
                "album": "Wrong Album XYZ",
                # track_number absent
            },
        )
        assert len(results) == 1
        issues = results[0].issues
        assert "artist_mismatch" in issues
        assert "album_mismatch" in issues
        assert "missing_track_number" in issues

    def test_discrepancy_fields_populated(self, tmp_path: Path) -> None:
        """AuditDiscrepancy carries both sides of the comparison."""
        results = _run_compare(
            tmp_path,
            itunes_artist="Beethoven",
            mp3_tags={
                "artist": "Radiohead",
                "album_artist": "Some Artist",
                "album": "Some Album",
                "track_number": 1,
            },
        )
        assert len(results) == 1
        d = results[0]
        assert d.mp3_artist == "Radiohead"
        assert d.itunes_artist == "Beethoven"
        assert d.artist_folder == "Some Artist"
        assert d.album_folder == "Some Album"
        assert d.artist_score is not None
        assert 0 <= d.artist_score <= 100

    def test_empty_library_returns_empty(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_plist(xml, {})

        with patch("tagger.integrity.itunes_comparator.find_mp3_files", return_value=[]):
            results = compare_library(
                library_path=Path("M:/Shared Music"),
                itunes_xml=xml,
                threshold=75,
                workers=1,
            )
        assert results == []
