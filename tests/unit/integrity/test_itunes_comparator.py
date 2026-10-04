"""Unit tests for tagger/integrity/itunes_comparator.py.

Covers ItunesLibrary (plist parsing + lookup) and compare_library
(discrepancy detection logic). No real filesystem I/O — all fixtures
use tmp_path or in-memory mocks.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from tagger import exceptions
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


# ---------------------------------------------------------------------------
# ItunesLibrary.playlist_tracks tests (playlist folder-path resolution)
# ---------------------------------------------------------------------------

_LOC_A = "file://localhost/M:/Shared%20Music/ARTIST%20A/Album%20A/01%20Ghost.mp3"
_LOC_B = "file://localhost/M:/Shared%20Music/Artist%20B/Album%20B/02%20Thriller.mp3"
_LOC_C = "file://localhost/M:/Shared%20Music/Artist%20C/Album%20C/03%20Monster.mp3"


def _write_library(path: Path, tracks: dict[str, dict], playlists: list[dict] | None) -> None:
    """Write an iTunes-style plist with both a Tracks dict and a Playlists array."""
    data: dict[str, Any] = {"Tracks": tracks}
    if playlists is not None:
        data["Playlists"] = playlists
    with path.open("wb") as fh:
        plistlib.dump(data, fh, fmt=plistlib.FMT_XML)


def _track(
    track_id: int,
    *,
    location: str | None,
    name: str | None = "Name",
    artist: str | None = "Artist",
    album: str | None = "Album",
    grouping: str | None = None,
) -> dict:
    entry: dict[str, Any] = {"Track ID": track_id}
    for key, value in (
        ("Name", name),
        ("Artist", artist),
        ("Album", album),
        ("Grouping", grouping),
        ("Location", location),
    ):
        if value is not None:  # plistlib cannot serialise None
            entry[key] = value
    return entry


def _playlist(
    name: str,
    persistent_id: str,
    *,
    parent: str | None = None,
    items: list[int] | None = None,
    folder: bool = False,
) -> dict:
    pl: dict[str, Any] = {"Name": name, "Playlist Persistent ID": persistent_id}
    if parent is not None:
        pl["Parent Persistent ID"] = parent
    if folder:
        pl["Folder"] = True
    if items is not None:
        pl["Playlist Items"] = [{"Track ID": tid} for tid in items]
    return pl


def _standard_library(tmp_path: Path) -> ItunesLibrary:
    """Genre/Halloween (tracks 2, 1), Mood/Halloween (track 3), top-level Library."""
    xml = tmp_path / "library.xml"
    tracks = {
        "1": _track(
            1,
            location=_LOC_A,
            name="Ghost",
            artist="Artist A",
            album="Album A",
            grouping="Gender:Male",
        ),
        "2": _track(2, location=_LOC_B, name="Thriller", artist="Artist B", album="Album B"),
        "3": _track(3, location=_LOC_C, name="Monster", artist="Artist C", album="Album C"),
    }
    playlists = [
        _playlist("Library", "LIB0", items=[1, 2, 3]),
        _playlist("Genre", "GEN0", folder=True, items=[1, 2]),
        _playlist("Halloween", "HAL1", parent="GEN0", items=[2, 1]),
        _playlist("Mood", "MOO0", folder=True, items=[3]),
        _playlist("Halloween", "HAL2", parent="MOO0", items=[3]),
    ]
    _write_library(xml, tracks, playlists)
    return ItunesLibrary(xml)


class TestPlaylistTracks:
    def test_resolves_folder_path_and_returns_tracks_in_playlist_order(
        self, tmp_path: Path
    ) -> None:
        lib = _standard_library(tmp_path)
        result = lib.playlist_tracks("Genre/Halloween")
        assert [t.track_id for t in result] == [2, 1]

    def test_returned_tracks_are_pydantic_itunes_track_models(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        result = lib.playlist_tracks("Genre/Halloween")
        assert all(isinstance(t, BaseModel) for t in result)
        assert {type(t).__name__ for t in result} == {"ItunesTrack"}

    def test_track_fields_are_populated(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        by_id = {t.track_id: t for t in lib.playlist_tracks("Genre/Halloween")}
        ghost = by_id[1]
        assert ghost.name == "Ghost"
        assert ghost.artist == "Artist A"
        assert ghost.album == "Album A"
        assert ghost.grouping == "Gender:Male"
        assert by_id[2].grouping is None

    def test_file_path_is_decoded_and_case_preserved(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        by_id = {t.track_id: t for t in lib.playlist_tracks("Genre/Halloween")}
        expected = str(Path("M:/Shared Music/ARTIST A/Album A/01 Ghost.mp3"))
        assert by_id[1].file_path == expected

    def test_same_leaf_name_under_different_folder_is_disambiguated_by_parent(
        self, tmp_path: Path
    ) -> None:
        lib = _standard_library(tmp_path)
        assert [t.track_id for t in lib.playlist_tracks("Mood/Halloween")] == [3]

    def test_top_level_playlist_resolves(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        assert [t.track_id for t in lib.playlist_tracks("Library")] == [1, 2, 3]

    def test_three_level_path_resolves(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_library(
            xml,
            {"1": _track(1, location=_LOC_A)},
            [
                _playlist("A", "P_A", folder=True),
                _playlist("B", "P_B", parent="P_A", folder=True),
                _playlist("C", "P_C", parent="P_B", items=[1]),
                _playlist("C", "P_C2", items=[]),  # top-level decoy with the same leaf name
            ],
        )
        lib = ItunesLibrary(xml)
        assert [t.track_id for t in lib.playlist_tracks("A/B/C")] == [1]

    def test_name_match_is_case_insensitive(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        assert [t.track_id for t in lib.playlist_tracks("genre/HALLOWEEN")] == [2, 1]

    def test_leading_and_trailing_slashes_are_ignored(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        assert [t.track_id for t in lib.playlist_tracks("/Genre/Halloween/")] == [2, 1]

    def test_nested_leaf_is_not_resolved_from_the_root(self, tmp_path: Path) -> None:
        """Paths are rooted: a bare leaf name must not match a nested playlist."""
        lib = _standard_library(tmp_path)
        with pytest.raises(exceptions.PlaylistNotFoundError):
            lib.playlist_tracks("Halloween")

    def test_unknown_path_raises_playlist_not_found(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        with pytest.raises(exceptions.PlaylistNotFoundError) as excinfo:
            lib.playlist_tracks("Genre/Christmas")
        assert excinfo.value.playlist == "Genre/Christmas"
        assert "Genre/Christmas" in str(excinfo.value)

    def test_playlist_not_found_is_a_tagger_error(self) -> None:
        assert issubclass(exceptions.PlaylistNotFoundError, exceptions.TaggerError)

    def test_empty_path_raises_playlist_not_found(self, tmp_path: Path) -> None:
        lib = _standard_library(tmp_path)
        with pytest.raises(exceptions.PlaylistNotFoundError):
            lib.playlist_tracks("")

    def test_ambiguous_path_raises_playlist_not_found(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_library(
            xml,
            {"1": _track(1, location=_LOC_A), "2": _track(2, location=_LOC_B)},
            [
                _playlist("Genre", "G1", folder=True),
                _playlist("Halloween", "H1", parent="G1", items=[1]),
                _playlist("Genre", "G2", folder=True),
                _playlist("Halloween", "H2", parent="G2", items=[2]),
            ],
        )
        lib = ItunesLibrary(xml)
        with pytest.raises(exceptions.PlaylistNotFoundError, match=r"(?i)ambiguous"):
            lib.playlist_tracks("Genre/Halloween")

    def test_library_without_playlists_key_raises_playlist_not_found(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_library(xml, {"1": _track(1, location=_LOC_A)}, None)
        lib = ItunesLibrary(xml)
        with pytest.raises(exceptions.PlaylistNotFoundError):
            lib.playlist_tracks("Genre/Halloween")

    def test_playlist_without_items_returns_empty_list(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_library(xml, {}, [_playlist("Empty", "E1")])
        lib = ItunesLibrary(xml)
        assert lib.playlist_tracks("Empty") == []

    def test_items_missing_from_tracks_or_without_location_are_skipped(
        self, tmp_path: Path
    ) -> None:
        xml = tmp_path / "library.xml"
        _write_library(
            xml,
            {
                "1": _track(1, location=_LOC_A),
                "2": _track(2, location=None),  # e.g. a stream or cloud-only track
            },
            [_playlist("Mixed", "M1", items=[1, 2, 99])],
        )
        lib = ItunesLibrary(xml)
        assert [t.track_id for t in lib.playlist_tracks("Mixed")] == [1]

    def test_duplicate_items_are_returned_once(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_library(
            xml,
            {"1": _track(1, location=_LOC_A), "2": _track(2, location=_LOC_B)},
            [_playlist("Dupes", "D1", items=[1, 2, 1])],
        )
        lib = ItunesLibrary(xml)
        assert [t.track_id for t in lib.playlist_tracks("Dupes")] == [1, 2]

    def test_missing_optional_fields_are_none(self, tmp_path: Path) -> None:
        xml = tmp_path / "library.xml"
        _write_library(
            xml,
            {"1": _track(1, location=_LOC_A, name=None, artist=None, album=None)},
            [_playlist("Sparse", "S1", items=[1])],
        )
        lib = ItunesLibrary(xml)
        (track,) = lib.playlist_tracks("Sparse")
        assert track.name is None
        assert track.artist is None
        assert track.album is None
        assert track.grouping is None
