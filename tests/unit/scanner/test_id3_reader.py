from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from tagger.scanner.id3_reader import read_id3_tags

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_read_id3_tags_success(mocker: MockerFixture) -> None:
    """Tests reading ID3 tags successfully."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio

    # Mock tags
    mock_tags = {
        "TIT2": MagicMock(text=["Song Title"]),
        "TPE1": MagicMock(text=["Artist Name"]),
        "TALB": MagicMock(text=["Album Name"]),
        "TRCK": MagicMock(text=["1/10"]),
        "TDRC": MagicMock(text=["2023"]),
        "TCON": MagicMock(text=["Rock"]),
    }
    mock_audio.tags = mock_tags

    file_path = Path("fake_song.mp3")
    tags = read_id3_tags(file_path)

    assert tags["title"] == "Song Title"
    assert tags["artist"] == "Artist Name"
    assert tags["album"] == "Album Name"
    assert tags["track_number"] == 1
    assert tags["year"] == 2023
    assert tags["genre"] == "Rock"


def test_read_id3_tags_v23_year(mocker: MockerFixture) -> None:
    """Tests reading TYER as fallback for year (ID3v2.3)."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio

    mock_tags = {
        "TYER": MagicMock(text=["1995"]),
    }
    mock_audio.tags = mock_tags

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["year"] == 1995


def test_read_id3_tags_invalid_track(mocker: MockerFixture) -> None:
    """Tests behavior when TRCK is in an invalid format."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio

    mock_tags = {
        "TRCK": MagicMock(text=["Invalid"]),
    }
    mock_audio.tags = mock_tags

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["track_number"] is None


def test_read_id3_tags_no_tags(mocker: MockerFixture) -> None:
    """Tests behavior when file has no tags."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio
    mock_audio.tags = None

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags == {}


def test_read_id3_tags_exception(mocker: MockerFixture) -> None:
    """Tests behavior when MP3() raises an exception."""
    mocker.patch("tagger.scanner.id3_reader.MP3", side_effect=Exception("mutagen error"))

    tags = read_id3_tags(Path("corrupt.mp3"))
    assert tags == {}


def test_read_id3_tags_complex_fields(mocker: MockerFixture) -> None:
    """Tests parsing other fields like BPM, disc_number, album_artist."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio

    mock_tags = {
        "TPE2": MagicMock(text=["Album Artist"]),
        "TBPM": MagicMock(text=["120"]),
        "TPOS": MagicMock(text=["1/2"]),
        "TCOM": MagicMock(text=["Composer Name"]),
    }
    mock_audio.tags = mock_tags

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["album_artist"] == "Album Artist"
    assert tags["bpm"] == 120
    assert tags["disc_number"] == 1
    assert tags["composer"] == "Composer Name"


def test_read_id3_tags_invalid_year_bpm_disc(mocker: MockerFixture) -> None:
    """Tests behavior with invalid year, bpm, and disc number formats."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio

    mock_tags = {
        "TDRC": MagicMock(text=["Not a year"]),
        "TBPM": MagicMock(text=["Not a bpm"]),
        "TPOS": MagicMock(text=["Not a disc"]),
    }
    mock_audio.tags = mock_tags

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["year"] is None
    assert tags["bpm"] is None
    assert tags["disc_number"] is None


def test_read_id3_tags_v23_invalid_year(mocker: MockerFixture) -> None:
    """Tests behavior with invalid TYER (ID3v2.3) format."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio

    mock_tags = {
        "TYER": MagicMock(text=["ABC"]),
    }
    mock_audio.tags = mock_tags

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["year"] is None
