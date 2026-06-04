"""Unit tests for tagger/integrity/itunes_restorer.py.

Covers restore_from_itunes logic (which fields get written, dry-run,
fallback to filename, itunes_not_found skipping). All mutagen I/O and
compare_library calls are mocked — no real filesystem or iTunes XML needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tagger.integrity.itunes_comparator import (
    ALBUM_ARTIST_MISMATCH,
    ALBUM_MISMATCH,
    ARTIST_MISMATCH,
    ITUNES_NOT_FOUND,
    MISSING_TRACK_NUMBER,
    AuditDiscrepancy,
)
from tagger.integrity.itunes_restorer import RestoreResult, restore_from_itunes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LIBRARY = Path("M:/Shared Music")
_ITUNES_XML = Path("C:/iTunes/iTunes Music Library.xml")
_MP3 = Path("M:/Shared Music/Some Artist/Some Album/01 Track.mp3")


def _discrepancy(
    *,
    file_path: str = str(_MP3),
    artist_folder: str = "Some Artist",
    album_folder: str = "Some Album",
    issues: list[str],
    mp3_artist: str | None = "Wrong Artist",
    mp3_album_artist: str | None = "Wrong Album Artist",
    mp3_album: str | None = "Wrong Album",
    mp3_track_number: int | None = None,
    itunes_artist: str | None = "Correct Artist",
    itunes_album_artist: str | None = "Correct Album Artist",
    itunes_album: str | None = "Correct Album",
    itunes_track_number: int | None = 1,
    artist_score: int | None = 10,
    album_artist_score: int | None = 10,
    album_score: int | None = 10,
) -> AuditDiscrepancy:
    return AuditDiscrepancy(
        file_path=file_path,
        artist_folder=artist_folder,
        album_folder=album_folder,
        mp3_artist=mp3_artist,
        mp3_album_artist=mp3_album_artist,
        mp3_album=mp3_album,
        mp3_track_number=mp3_track_number,
        itunes_artist=itunes_artist,
        itunes_album_artist=itunes_album_artist,
        itunes_album=itunes_album,
        itunes_track_number=itunes_track_number,
        issues=issues,
        artist_score=artist_score,
        album_artist_score=album_artist_score,
        album_score=album_score,
    )


def _run_restore(
    discrepancies: list[AuditDiscrepancy],
    *,
    dry_run: bool = False,
) -> list[RestoreResult]:
    with (
        patch(
            "tagger.integrity.itunes_restorer.compare_library",
            return_value=discrepancies,
        ),
        patch("tagger.integrity.itunes_restorer._write_frames") as mock_write,
    ):
        results = restore_from_itunes(
            itunes_xml=_ITUNES_XML,
            library_path=_LIBRARY,
            threshold=75,
            workers=1,
            dry_run=dry_run,
        )
    return results, mock_write  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRestoreFromItunes:
    def test_artist_mismatch_restores_tpe1(self) -> None:
        results, mock_write = _run_restore(
            [_discrepancy(issues=[ARTIST_MISMATCH], itunes_artist="Correct Artist")]
        )
        assert len(results) == 1
        assert "artist" in results[0].fields_restored
        assert results[0].new_artist == "Correct Artist"
        assert results[0].old_artist == "Wrong Artist"
        mock_write.assert_called_once()
        frames = mock_write.call_args[0][1]
        assert "TPE1" in frames
        assert frames["TPE1"] == "Correct Artist"

    def test_album_mismatch_restores_talb(self) -> None:
        results, mock_write = _run_restore(
            [_discrepancy(issues=[ALBUM_MISMATCH], itunes_album="Correct Album")]
        )
        assert len(results) == 1
        assert "album" in results[0].fields_restored
        assert results[0].new_album == "Correct Album"
        frames = mock_write.call_args[0][1]
        assert "TALB" in frames

    def test_album_artist_mismatch_restores_tpe2(self) -> None:
        results, mock_write = _run_restore(
            [
                _discrepancy(
                    issues=[ALBUM_ARTIST_MISMATCH],
                    itunes_album_artist="Correct Album Artist",
                )
            ]
        )
        assert len(results) == 1
        assert "album_artist" in results[0].fields_restored
        frames = mock_write.call_args[0][1]
        assert "TPE2" in frames
        assert frames["TPE2"] == "Correct Album Artist"

    def test_missing_track_number_uses_itunes_value(self) -> None:
        results, mock_write = _run_restore(
            [
                _discrepancy(
                    issues=[MISSING_TRACK_NUMBER],
                    mp3_track_number=None,
                    itunes_track_number=5,
                )
            ]
        )
        assert len(results) == 1
        assert "track_number" in results[0].fields_restored
        assert results[0].new_track_number == 5
        frames = mock_write.call_args[0][1]
        assert frames["TRCK"] == "5"

    def test_missing_track_number_falls_back_to_filename(self) -> None:
        d = _discrepancy(
            file_path="M:/Shared Music/Some Artist/Some Album/03 Song.mp3",
            issues=[MISSING_TRACK_NUMBER],
            mp3_track_number=None,
            itunes_track_number=None,
        )
        results, mock_write = _run_restore([d])
        assert len(results) == 1
        assert "track_number" in results[0].fields_restored
        assert results[0].new_track_number == 3
        frames = mock_write.call_args[0][1]
        assert frames["TRCK"] == "3"

    def test_missing_track_number_no_itunes_no_filename_skips_field(self) -> None:
        d = _discrepancy(
            file_path="M:/Shared Music/Some Artist/Some Album/Track With No Number.mp3",
            issues=[MISSING_TRACK_NUMBER],
            mp3_track_number=None,
            itunes_track_number=None,
        )
        results, _mock_write = _run_restore([d])
        # No track number resolvable — row should not be emitted at all
        # (nothing to restore on this file)
        assert all("track_number" not in r.fields_restored for r in results)

    def test_itunes_not_found_skipped(self) -> None:
        results, mock_write = _run_restore([_discrepancy(issues=[ITUNES_NOT_FOUND])])
        assert results == []
        mock_write.assert_not_called()

    def test_itunes_not_found_with_missing_track_skipped(self) -> None:
        results, mock_write = _run_restore(
            [_discrepancy(issues=[ITUNES_NOT_FOUND, MISSING_TRACK_NUMBER])]
        )
        assert results == []
        mock_write.assert_not_called()

    def test_dry_run_emits_result_but_no_write(self) -> None:
        results, mock_write = _run_restore(
            [_discrepancy(issues=[ARTIST_MISMATCH])],
            dry_run=True,
        )
        assert len(results) == 1
        assert results[0].dry_run is True
        mock_write.assert_not_called()

    def test_multiple_fields_in_one_file(self) -> None:
        d = _discrepancy(
            issues=[ARTIST_MISMATCH, ALBUM_MISMATCH, MISSING_TRACK_NUMBER],
            itunes_track_number=7,
        )
        results, mock_write = _run_restore([d])
        assert len(results) == 1
        fields = results[0].fields_restored
        assert "artist" in fields
        assert "album" in fields
        assert "track_number" in fields
        frames = mock_write.call_args[0][1]
        assert "TPE1" in frames
        assert "TALB" in frames
        assert "TRCK" in frames

    def test_empty_discrepancies_returns_empty(self) -> None:
        results, mock_write = _run_restore([])
        assert results == []
        mock_write.assert_not_called()

    def test_disc_number_written_when_greater_than_one(self) -> None:
        d = _discrepancy(
            file_path="M:/Shared Music/Some Artist/Some Album/2-03 Song.mp3",
            issues=[MISSING_TRACK_NUMBER],
            mp3_track_number=None,
            itunes_track_number=None,
        )
        results, mock_write = _run_restore([d])
        assert len(results) == 1
        frames = mock_write.call_args[0][1]
        assert frames["TRCK"] == "3"
        assert frames.get("TPOS") == "2"

    def test_result_fields_populated(self) -> None:
        results, _ = _run_restore(
            [
                _discrepancy(
                    issues=[ARTIST_MISMATCH],
                    mp3_artist="Old Artist",
                    itunes_artist="New Artist",
                )
            ]
        )
        r = results[0]
        assert r.old_artist == "Old Artist"
        assert r.new_artist == "New Artist"
        assert r.artist_folder == "Some Artist"
        assert r.album_folder == "Some Album"
        assert r.dry_run is False
