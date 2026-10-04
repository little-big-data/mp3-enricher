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


# ---------------------------------------------------------------------------
# grouping key (GRP1 preferred, TIT1 fallback)
# ---------------------------------------------------------------------------


def test_read_id3_tags_grouping_prefers_grp1(mocker: MockerFixture) -> None:
    """GRP1 (iTunes 12.9.1+) wins over TIT1 when both are present."""
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio
    mock_audio.tags = {
        "GRP1": MagicMock(text=["Gender:Male | Holiday:Halloween"]),
        "TIT1": MagicMock(text=["Gender:Male"]),
    }

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["grouping"] == "Gender:Male | Holiday:Halloween"


def test_read_id3_tags_grouping_falls_back_to_tit1(mocker: MockerFixture) -> None:
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio
    mock_audio.tags = {"TIT1": MagicMock(text=["Origin:Detroit, US"])}

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["grouping"] == "Origin:Detroit, US"


def test_read_id3_tags_grouping_empty_grp1_falls_back_to_tit1(mocker: MockerFixture) -> None:
    mock_mp3 = mocker.patch("tagger.scanner.id3_reader.MP3")
    mock_audio = MagicMock()
    mock_mp3.return_value = mock_audio
    mock_audio.tags = {
        "GRP1": MagicMock(text=[""]),
        "TIT1": MagicMock(text=["Label:Def Jam"]),
    }

    tags = read_id3_tags(Path("fake.mp3"))
    assert tags["grouping"] == "Label:Def Jam"


# Four 144-byte MPEG1 Layer3 frames (32kbps, 32kHz, mono) that mutagen's MP3() can parse
_REAL_FRAMES = (b"\xff\xfb\x18\xc0" + b"\x00" * 140) * 4


def test_read_id3_tags_grouping_from_real_file(tmp_path: Path) -> None:
    """Round-trip through a real MP3 written with mutagen GRP1 + TIT1 frames.

    A second file with neither GRP1 nor TIT1 yields no grouping value.
    """
    from mutagen.id3 import GRP1, ID3, TIT1, TIT2

    path = tmp_path / "real.mp3"
    path.write_bytes(_REAL_FRAMES)
    id3 = ID3()
    id3.add(TIT2(encoding=3, text="Real Song"))
    id3.add(GRP1(encoding=3, text="Subgenre:Horrorcore"))
    id3.add(TIT1(encoding=3, text="Subgenre:Old"))
    id3.save(str(path))

    tags = read_id3_tags(path)
    assert tags["title"] == "Real Song"
    assert tags["grouping"] == "Subgenre:Horrorcore"

    # A file with neither GRP1 nor TIT1 has no grouping value.
    bare = tmp_path / "bare.mp3"
    bare.write_bytes(_REAL_FRAMES)
    bare_id3 = ID3()
    bare_id3.add(TIT2(encoding=3, text="Bare Song"))
    bare_id3.save(str(bare))
    bare_tags = read_id3_tags(bare)
    assert bare_tags["title"] == "Bare Song"
    assert bare_tags.get("grouping") is None
