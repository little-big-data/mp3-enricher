"""Unit tests for tagger/integrity/playlist_holiday.py — apply_holiday_and_audit().

The service resolves an iTunes playlist, ensures every member MP3 carries a
``Holiday:<holiday>`` segment in GRP1 + TIT1 (mirrored into tracks.grouping),
and fuzzy-compares iTunes Name/Artist/Album against ID3 TIT2/TPE1/TALB.

Uses a real ItunesLibrary built from a tmp plist, real MP3 files written with
mutagen in tmp_path, and a real migrated SQLite database. Only the I/O seams
``read_id3_tags`` / ``save_id3`` (imported by name into the service module)
are patched, to inject failures or measure concurrency.
"""

from __future__ import annotations

import plistlib
import sqlite3
import threading
import time
import urllib.parse
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from mutagen import MutagenError
from mutagen.id3 import GRP1, ID3, TALB, TIT1, TIT2, TPE1
from rapidfuzz import fuzz

from tagger import exceptions
from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.integrity.itunes_comparator import ItunesLibrary
from tagger.integrity.playlist_holiday import PlaylistAuditResult, apply_holiday_and_audit
from tagger.scanner.id3_reader import read_id3_tags

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

_FRAME = b"\xff\xfb\x18\xc0" + b"\x00" * 140  # MPEG1 Layer3 32kbps 32kHz mono
_SVC = "tagger.integrity.playlist_holiday"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mp3(
    path: Path,
    *,
    title: str | None = "Ghost",
    artist: str | None = "Artist",
    album: str | None = "Album",
    grouping: str | None = None,
    tit1: str | None = None,
) -> Path:
    """Write a real parseable MP3; GRP1 = grouping, TIT1 = tit1 or grouping."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_FRAME * 4)
    tags = ID3()
    if title is not None:
        tags.add(TIT2(encoding=3, text=title))
    if artist is not None:
        tags.add(TPE1(encoding=3, text=artist))
    if album is not None:
        tags.add(TALB(encoding=3, text=album))
    if grouping is not None:
        tags.add(GRP1(encoding=3, text=grouping))
    tit1_value = tit1 if tit1 is not None else grouping
    if tit1_value is not None:
        tags.add(TIT1(encoding=3, text=tit1_value))
    tags.save(str(path), v2_version=3)
    return path


def _location(path: Path) -> str:
    return "file://localhost/" + urllib.parse.quote(path.as_posix())


def _build_library(
    tmp_path: Path,
    entries: list[tuple[Path, str | None, str | None, str | None]],
) -> ItunesLibrary:
    """Write a plist with folder 'Genre' > playlist 'Halloween' holding *entries*.

    Each entry is (file path, iTunes Name, iTunes Artist, iTunes Album).
    """
    tracks: dict[str, dict[str, Any]] = {}
    for idx, (path, name, artist, album) in enumerate(entries, start=1):
        entry: dict[str, Any] = {"Track ID": idx, "Location": _location(path)}
        for key, value in (("Name", name), ("Artist", artist), ("Album", album)):
            if value is not None:
                entry[key] = value
        tracks[str(idx)] = entry
    playlists = [
        {"Name": "Genre", "Playlist Persistent ID": "GEN0", "Folder": True},
        {
            "Name": "Halloween",
            "Playlist Persistent ID": "HAL1",
            "Parent Persistent ID": "GEN0",
            "Playlist Items": [{"Track ID": i} for i in range(1, len(entries) + 1)],
        },
    ]
    xml = tmp_path / "iTunes Music Library.xml"
    with xml.open("wb") as fh:
        plistlib.dump({"Tracks": tracks, "Playlists": playlists}, fh, fmt=plistlib.FMT_XML)
    return ItunesLibrary(xml)


def _frames(path: Path) -> tuple[str | None, str | None]:
    tags = ID3(str(path))
    grp1 = str(tags["GRP1"]) if "GRP1" in tags else None
    tit1 = str(tags["TIT1"]) if "TIT1" in tags else None
    return grp1, tit1


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    c = get_db_connection(tmp_path / "library.db")
    run_migrations(c)
    yield c
    c.close()


@pytest.fixture
def repo(conn: sqlite3.Connection) -> TrackRepository:
    return TrackRepository(conn)


def _add_db_track(
    conn: sqlite3.Connection, path: Path, grouping: str | None, title: str = "DB Title"
) -> None:
    album_repo = AlbumRepository(conn)
    with conn:
        album_repo.upsert(AlbumRecord(folder_path=str(path.parent)))
    album = album_repo.get_by_folder_path(str(path.parent))
    assert album is not None
    assert album.id is not None
    with conn:
        TrackRepository(conn).upsert(
            TrackRecord(
                album_id=album.id,
                file_path=str(path),
                filename=path.name,
                title=title,
                grouping=grouping,
                enrichment_status="found",
                written_status="done",
            )
        )


def _db_grouping(repo: TrackRepository, path: Path) -> str | None:
    rec = repo.get_by_file_path(str(path))
    assert rec is not None
    return rec.grouping


def _run(library: ItunesLibrary, repo: TrackRepository, **kwargs: object) -> PlaylistAuditResult:
    params: dict[str, object] = {"threshold": 90, "dry_run": False, "workers": 1}
    params.update(kwargs)
    return apply_holiday_and_audit(library, "Genre/Halloween", "Halloween", repo, **params)


# ---------------------------------------------------------------------------
# Tagging (GRP1 + TIT1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_appends_holiday_segment_to_grp1_and_tit1(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping="Origin:Detroit, US | Gender:Male")
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    result = _run(lib, repo)

    expected = "Origin:Detroit, US | Gender:Male | Holiday:Halloween"
    assert _frames(mp3) == (expected, expected)
    assert str(ID3(str(mp3))["TIT2"]) == "Ghost"  # other frames preserved
    assert result.tagged == 1
    assert result.already_tagged == 0
    assert result.errors == 0


@pytest.mark.unit
def test_file_without_grouping_gets_segment_alone(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping=None)
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    _run(lib, repo)

    assert _frames(mp3) == ("Holiday:Halloween", "Holiday:Halloween")


@pytest.mark.unit
def test_merges_into_existing_holiday_segment(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping="Gender:Male | Holiday:Christmas")
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    _run(lib, repo)

    expected = "Gender:Male | Holiday:Christmas, Halloween"
    assert _frames(mp3) == (expected, expected)


@pytest.mark.unit
def test_grp1_is_source_of_truth_and_tit1_is_brought_in_line(
    tmp_path: Path, repo: TrackRepository
) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping="Gender:Male", tit1="Stale")
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    _run(lib, repo)

    expected = "Gender:Male | Holiday:Halloween"
    assert _frames(mp3) == (expected, expected)


@pytest.mark.unit
def test_already_tagged_file_is_not_rewritten(
    tmp_path: Path, repo: TrackRepository, mocker: MockerFixture
) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping="Gender:Male | Holiday:Halloween")
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])
    mock_save = mocker.patch(f"{_SVC}.save_id3")

    result = _run(lib, repo)

    mock_save.assert_not_called()
    assert result.tagged == 0
    assert result.already_tagged == 1


@pytest.mark.unit
def test_rerun_is_idempotent(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Re-running with identical inputs after success yields the same end state."""
    repo = TrackRepository(conn)
    a = _make_mp3(tmp_path / "a.mp3", grouping="Gender:Male")
    b = _make_mp3(tmp_path / "b.mp3", grouping=None)
    _add_db_track(conn, a, "Gender:Male")
    _add_db_track(conn, b, None)
    lib = _build_library(
        tmp_path, [(a, "Ghost", "Artist", "Album"), (b, "Ghost", "Artist", "Album")]
    )

    first = _run(lib, repo)
    state_after_first = (_frames(a), _frames(b), _db_grouping(repo, a), _db_grouping(repo, b))
    second = _run(lib, repo)
    state_after_second = (_frames(a), _frames(b), _db_grouping(repo, a), _db_grouping(repo, b))

    assert first.tagged == 2
    assert second.tagged == 0
    assert second.already_tagged == 2
    assert state_after_second == state_after_first
    assert "Holiday:Halloween, Halloween" not in str(state_after_second)


# ---------------------------------------------------------------------------
# DB mirror (tracks.grouping)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_db_grouping_mirrors_new_file_grouping(tmp_path: Path, conn: sqlite3.Connection) -> None:
    repo = TrackRepository(conn)
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping="Gender:Male | Label:Def Jam")
    _add_db_track(conn, mp3, "Gender:Male", title="Keep Me")
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    _run(lib, repo)

    grp1, _ = _frames(mp3)
    rec = repo.get_by_file_path(str(mp3))
    assert rec is not None
    assert rec.grouping == grp1 == "Gender:Male | Label:Def Jam | Holiday:Halloween"
    assert rec.title == "Keep Me"
    assert rec.written_status == "done"


@pytest.mark.unit
def test_db_synced_when_file_was_already_tagged(tmp_path: Path, conn: sqlite3.Connection) -> None:
    repo = TrackRepository(conn)
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping="Holiday:Halloween")
    _add_db_track(conn, mp3, None)
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    _run(lib, repo)

    assert _db_grouping(repo, mp3) == "Holiday:Halloween"


@pytest.mark.unit
def test_track_absent_from_db_is_still_tagged(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping=None)
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    result = _run(lib, repo)

    assert _frames(mp3) == ("Holiday:Halloween", "Holiday:Halloween")
    assert result.errors == 0
    assert result.rows == []


@pytest.mark.unit
def test_dry_run_modifies_neither_file_nor_db(tmp_path: Path, conn: sqlite3.Connection) -> None:
    repo = TrackRepository(conn)
    mp3 = _make_mp3(tmp_path / "a.mp3", grouping="Gender:Male")
    _add_db_track(conn, mp3, "Gender:Male")
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])
    before = mp3.read_bytes()

    result = _run(lib, repo, dry_run=True)

    assert mp3.read_bytes() == before
    assert _db_grouping(repo, mp3) == "Gender:Male"
    assert result.tagged == 1  # counted as "would tag"


# ---------------------------------------------------------------------------
# Fuzzy Name / Artist / Album audit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_only_mismatched_tracks_produce_rows(tmp_path: Path, repo: TrackRepository) -> None:
    exact = _make_mp3(tmp_path / "exact.mp3", title="Thriller", artist="Michael Jackson")
    fuzzy = _make_mp3(tmp_path / "fuzzy.mp3", title="Thriller", artist="Michael Jackson")
    wrong = _make_mp3(tmp_path / "wrong.mp3", title="Billie Jean", artist="Michael Jackson")
    lib = _build_library(
        tmp_path,
        [
            (exact, "THRILLER", "michael jackson", "Album"),  # case-only difference
            (fuzzy, "Thriller (2008 Remaster)", "Michael Jackson", "Album"),  # token superset
            (wrong, "Thriller", "Michael Jackson", "Album"),
        ],
    )

    result = _run(lib, repo)

    assert [r.file_path for r in result.rows] == [str(wrong)]
    row = result.rows[0]
    assert row.issues == ["title_mismatch"]
    assert row.itunes_name == "Thriller"
    assert row.id3_title == "Billie Jean"
    assert row.title_score is not None
    assert row.title_score < 90
    assert row.artist_score == 100
    assert row.album_score == 100
    assert row.itunes_artist == "Michael Jackson"
    assert row.id3_artist == "Michael Jackson"
    assert row.itunes_album == "Album"
    assert row.id3_album == "Album"
    assert result.mismatches == 1


@pytest.mark.unit
def test_threshold_boundary_is_strictly_below(tmp_path: Path, conn: sqlite3.Connection) -> None:
    itunes_name, id3_title = "Ghostbusters Theme", "Ghost Busters Theme"
    score = int(fuzz.token_set_ratio(itunes_name.lower(), id3_title.lower()))
    assert 0 < score < 100, "fixture precondition: need a partial score"

    mp3 = _make_mp3(tmp_path / "a.mp3", title=id3_title)
    lib = _build_library(tmp_path, [(mp3, itunes_name, "Artist", "Album")])

    at = _run(lib, TrackRepository(conn), threshold=score, dry_run=True)
    above = _run(lib, TrackRepository(conn), threshold=score + 1, dry_run=True)

    assert at.rows == []
    assert [r.issues for r in above.rows] == [["title_mismatch"]]
    assert above.rows[0].title_score == score


@pytest.mark.unit
def test_all_three_mismatches_listed_in_fixed_order(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", title="Alpha", artist="Bravo", album="Charlie")
    lib = _build_library(tmp_path, [(mp3, "Xylophone", "Yankee", "Zulu")])

    result = _run(lib, repo)

    assert result.rows[0].issues == ["title_mismatch", "artist_mismatch", "album_mismatch"]
    assert result.mismatches == 1  # counted per track, not per field


@pytest.mark.unit
def test_both_blank_fields_are_a_match(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", album=None)
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", None)])

    result = _run(lib, repo)

    assert result.rows == []


@pytest.mark.unit
def test_missing_id3_field_against_itunes_value_is_a_mismatch(
    tmp_path: Path, repo: TrackRepository
) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", album=None)
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Some Album")])

    result = _run(lib, repo)

    (row,) = result.rows
    assert row.issues == ["album_mismatch"]
    assert row.id3_album is None
    assert row.album_score == 0


@pytest.mark.unit
def test_non_ascii_names_and_paths(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "Café del Mar" / "01 Señor.mp3", title="Señor", album="Café")
    lib = _build_library(tmp_path, [(mp3, "Señor", "Artist", "Café")])

    result = _run(lib, repo)

    assert result.rows == []
    assert result.tagged == 1
    assert _frames(mp3)[0] == "Holiday:Halloween"


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_file_is_reported_and_others_continue(
    tmp_path: Path, repo: TrackRepository
) -> None:
    missing = tmp_path / "gone.mp3"  # never created
    present = _make_mp3(tmp_path / "here.mp3")
    lib = _build_library(
        tmp_path, [(missing, "Gone", "Artist", "Album"), (present, "Ghost", "Artist", "Album")]
    )

    result = _run(lib, repo)

    (row,) = result.rows
    assert row.file_path == str(missing)
    assert row.issues == ["file_missing"]
    assert row.itunes_name == "Gone"
    assert row.id3_title is None
    assert row.title_score is None
    assert result.errors == 1
    assert result.tagged == 1
    assert _frames(present)[0] == "Holiday:Halloween"


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [PermissionError(13, "denied"), OSError(5, "I/O error"), MutagenError("corrupt")],
)
def test_write_error_is_reported_db_untouched_and_others_continue(
    tmp_path: Path, conn: sqlite3.Connection, mocker: MockerFixture, exc: Exception
) -> None:
    from tagger.writer import id3_writer

    repo = TrackRepository(conn)
    bad = _make_mp3(tmp_path / "bad.mp3", grouping="Gender:Male")
    good = _make_mp3(tmp_path / "good.mp3", grouping="Gender:Male")
    _add_db_track(conn, bad, "Gender:Male")
    _add_db_track(conn, good, "Gender:Male")
    lib = _build_library(
        tmp_path, [(bad, "Ghost", "Artist", "Album"), (good, "Ghost", "Artist", "Album")]
    )

    def flaky_save(tags: ID3, file_path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(file_path) == bad:
            raise exc
        id3_writer.save_id3(tags, file_path, *args, **kwargs)

    mocker.patch(f"{_SVC}.save_id3", side_effect=flaky_save)

    result = _run(lib, repo)

    (row,) = result.rows
    assert row.file_path == str(bad)
    assert row.issues == ["write_error"]
    assert result.errors == 1
    assert result.tagged == 1
    assert _db_grouping(repo, bad) == "Gender:Male"  # file failed -> no DB write
    assert _db_grouping(repo, good) == "Gender:Male | Holiday:Halloween"
    assert _frames(good)[0] == "Gender:Male | Holiday:Halloween"


@pytest.mark.unit
def test_write_error_is_appended_after_mismatch_issues(
    tmp_path: Path, repo: TrackRepository, mocker: MockerFixture
) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3", title="Billie Jean")
    lib = _build_library(tmp_path, [(mp3, "Thriller", "Artist", "Album")])
    mocker.patch(f"{_SVC}.save_id3", side_effect=PermissionError(13, "denied"))

    result = _run(lib, repo)

    assert result.rows[0].issues == ["title_mismatch", "write_error"]
    assert result.mismatches == 1
    assert result.errors == 1


@pytest.mark.unit
def test_unknown_playlist_raises(tmp_path: Path, repo: TrackRepository) -> None:
    mp3 = _make_mp3(tmp_path / "a.mp3")
    lib = _build_library(tmp_path, [(mp3, "Ghost", "Artist", "Album")])

    with pytest.raises(exceptions.PlaylistNotFoundError):
        apply_holiday_and_audit(
            lib, "Genre/Christmas", "Halloween", repo, threshold=90, dry_run=True, workers=1
        )


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def _measure_peak(mocker: MockerFixture) -> dict[str, int]:
    """Patch the service's read_id3_tags with a slow wrapper recording peak concurrency."""
    lock = threading.Lock()
    state = {"active": 0, "peak": 0, "calls": 0}

    def slow_read(path: str | Path) -> dict[str, Any]:
        with lock:
            state["active"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.1)
            return read_id3_tags(Path(path))
        finally:
            with lock:
                state["active"] -= 1

    mocker.patch(f"{_SVC}.read_id3_tags", side_effect=slow_read)
    return state


@pytest.mark.unit
def test_submitting_n_plus_one_items_never_exceeds_n_workers(
    tmp_path: Path, repo: TrackRepository, mocker: MockerFixture
) -> None:
    files = [_make_mp3(tmp_path / f"{i:02d}.mp3") for i in range(5)]
    lib = _build_library(tmp_path, [(f, "Ghost", "Artist", "Album") for f in files])
    state = _measure_peak(mocker)

    result = _run(lib, repo, workers=2)

    assert state["calls"] == 5
    assert state["peak"] == 2, f"expected exactly 2 concurrent workers, saw {state['peak']}"
    assert result.tagged == 5
    assert all(_frames(f)[0] == "Holiday:Halloween" for f in files)


@pytest.mark.unit
def test_single_worker_runs_serially(
    tmp_path: Path, repo: TrackRepository, mocker: MockerFixture
) -> None:
    files = [_make_mp3(tmp_path / f"{i:02d}.mp3") for i in range(3)]
    lib = _build_library(tmp_path, [(f, "Ghost", "Artist", "Album") for f in files])
    state = _measure_peak(mocker)

    _run(lib, repo, workers=1)

    assert state["peak"] == 1


@pytest.mark.unit
def test_worker_failure_does_not_stall_the_pool(
    tmp_path: Path, repo: TrackRepository, mocker: MockerFixture
) -> None:
    """With workers=1, an early write failure must not hold the only slot."""
    files = [_make_mp3(tmp_path / f"{i:02d}.mp3") for i in range(3)]
    lib = _build_library(tmp_path, [(f, "Ghost", "Artist", "Album") for f in files])
    mock_save = MagicMock(side_effect=[OSError(5, "I/O error"), None, None])
    mocker.patch(f"{_SVC}.save_id3", mock_save)

    result = _run(lib, repo, workers=1)

    assert mock_save.call_count == 3
    assert result.errors == 1
    assert result.tagged == 2
