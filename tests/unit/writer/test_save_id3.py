"""Unit tests for tagger.writer.id3_writer.save_id3 — the public tag-save helper.

save_id3 is the module-level extraction of ID3Writer._save_tags: it saves an
ID3 tag object at a given ID3v2 minor version and, when the in-place save
raises OSError (e.g. EINVAL on a Windows SMB share), falls back to writing a
temp copy in the same directory and moving it over the original. The temp
file must never be left behind, on success or failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from mutagen import MutagenError
from mutagen.id3 import GRP1, ID3, TIT1, TIT2

from tagger.writer import id3_writer
from tagger.writer.id3_writer import ID3Writer

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.fixture
def mp3(tmp_path: Path) -> Path:
    """A real, parseable MP3 (four MPEG1 Layer3 frames) carrying a TIT2 frame."""
    path = tmp_path / "song.mp3"
    path.write_bytes((b"\xff\xfb\x18\xc0" + b"\x00" * 140) * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Original"))
    tags.save(str(path), v2_version=3)
    return path


def _fail_first_save(tags: ID3, *, fail_times: int, exc: Exception) -> list[str]:
    """Make the first *fail_times* calls to tags.save raise *exc*; record every path."""
    real_save = tags.save
    calls: list[str] = []

    def wrapped(path: str | Path, v2_version: int = 4, **kwargs: object) -> None:
        calls.append(str(path))
        if len(calls) <= fail_times:
            raise exc
        real_save(path, v2_version=v2_version, **kwargs)

    tags.save = wrapped  # type: ignore[method-assign]  # test double for SMB EINVAL
    return calls


@pytest.mark.unit
def test_save_id3_writes_frames_to_disk(mp3: Path) -> None:
    tags = ID3(str(mp3))
    tags["TIT1"] = TIT1(encoding=3, text="Holiday:Halloween")
    tags["GRP1"] = GRP1(encoding=3, text="Holiday:Halloween")

    id3_writer.save_id3(tags, str(mp3))

    reread = ID3(str(mp3))
    assert str(reread["TIT1"]) == "Holiday:Halloween"
    assert str(reread["GRP1"]) == "Holiday:Halloween"
    assert str(reread["TIT2"]) == "Original"


@pytest.mark.unit
def test_save_id3_accepts_path_object(mp3: Path) -> None:
    tags = ID3(str(mp3))
    tags["TIT1"] = TIT1(encoding=3, text="X")

    id3_writer.save_id3(tags, mp3)

    assert str(ID3(str(mp3))["TIT1"]) == "X"


@pytest.mark.unit
def test_save_id3_defaults_to_v23(mp3: Path) -> None:
    tags = ID3(str(mp3))
    id3_writer.save_id3(tags, str(mp3))
    assert ID3(str(mp3)).version[:2] == (2, 3)


@pytest.mark.unit
def test_save_id3_honours_v24(mp3: Path) -> None:
    tags = ID3(str(mp3))
    id3_writer.save_id3(tags, str(mp3), v2_version=4)
    assert ID3(str(mp3)).version[:2] == (2, 4)


@pytest.mark.unit
def test_save_id3_falls_back_to_temp_copy_on_oserror(mp3: Path) -> None:
    tags = ID3(str(mp3))
    tags["TIT1"] = TIT1(encoding=3, text="Holiday:Halloween")
    calls = _fail_first_save(tags, fail_times=1, exc=OSError(22, "Invalid argument"))

    id3_writer.save_id3(tags, str(mp3))

    assert len(calls) == 2, "expected one failed in-place save then one temp-file save"
    assert calls[1] != str(mp3)
    assert str(ID3(str(mp3))["TIT1"]) == "Holiday:Halloween"
    # temp file has been moved over the original: nothing else left in the dir
    assert sorted(p.name for p in mp3.parent.iterdir()) == ["song.mp3"]


@pytest.mark.unit
def test_save_id3_temp_file_is_created_beside_target(mp3: Path, mocker: MockerFixture) -> None:
    """Temp copy lives in the target's directory to avoid a cross-drive move on SMB."""
    spy = mocker.spy(id3_writer.tempfile, "mkstemp")
    tags = ID3(str(mp3))
    _fail_first_save(tags, fail_times=1, exc=OSError(22, "Invalid argument"))

    id3_writer.save_id3(tags, str(mp3))

    assert spy.call_count == 1
    call = spy.call_args
    temp_dir = call.kwargs.get("dir", call.args[2] if len(call.args) > 2 else None)
    assert temp_dir is not None
    assert Path(temp_dir) == mp3.parent


@pytest.mark.unit
@pytest.mark.parametrize(
    "second_exc",
    [OSError(5, "I/O error"), PermissionError(13, "denied"), MutagenError("corrupt")],
)
def test_save_id3_cleans_up_temp_file_when_fallback_fails(mp3: Path, second_exc: Exception) -> None:
    """If the temp-file save also fails, the error propagates and no temp file leaks."""
    original_bytes = mp3.read_bytes()
    tags = ID3(str(mp3))
    tags["TIT1"] = TIT1(encoding=3, text="never written")
    errors: list[Exception] = [OSError(22, "Invalid argument"), second_exc]

    def always_fails(path: str | Path, v2_version: int = 4, **kwargs: object) -> None:
        raise errors.pop(0)

    tags.save = always_fails  # type: ignore[method-assign]  # both save attempts fail

    with pytest.raises(type(second_exc)):
        id3_writer.save_id3(tags, str(mp3))

    assert errors == [], "expected the in-place save and the temp-file save to be attempted"
    assert sorted(p.name for p in mp3.parent.iterdir()) == ["song.mp3"]
    assert mp3.read_bytes() == original_bytes


@pytest.mark.unit
@pytest.mark.parametrize(("version", "expected"), [("2.3", 3), ("2.4", 4)])
def test_id3writer_save_tags_delegates_to_save_id3(
    mocker: MockerFixture, version: str, expected: int
) -> None:
    """ID3Writer keeps a single save implementation: _save_tags calls save_id3."""
    mock_save = mocker.patch("tagger.writer.id3_writer.save_id3")
    writer = ID3Writer(MagicMock(), id3_version=version)  # type: ignore[arg-type]
    tags = MagicMock(spec=ID3)

    writer._save_tags(tags, "/music/a.mp3")

    mock_save.assert_called_once()
    call = mock_save.call_args
    passed_version = call.kwargs.get("v2_version", call.args[2] if len(call.args) > 2 else None)
    assert call.args[0] is tags
    assert str(call.args[1]) == "/music/a.mp3"
    assert passed_version == expected
