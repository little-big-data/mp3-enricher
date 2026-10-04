"""Integration tests for the ``playlist-holiday`` CLI command (tagger/mp3_tagger.py).

Drives the command through click's CliRunner against a real tmp iTunes plist,
real MP3 files and a real SQLite database. One test patches the service to
verify option propagation; the rest run end to end. Also checks that the
README and CHANGELOG document the command.
"""

from __future__ import annotations

import csv
import inspect
import plistlib
import sqlite3
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner, Result
from mutagen.id3 import GRP1, ID3, TALB, TIT1, TIT2, TPE1

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.integrity.itunes_comparator import ItunesLibrary
from tagger.mp3_tagger import cli

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

_FRAME = b"\xff\xfb\x18\xc0" + b"\x00" * 140
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_COLUMNS = [
    "file_path",
    "itunes_name",
    "id3_title",
    "title_score",
    "itunes_artist",
    "id3_artist",
    "artist_score",
    "itunes_album",
    "id3_album",
    "album_score",
    "issues",
]


def _make_mp3(
    path: Path,
    *,
    title: str,
    grouping: str | None = None,
    artist: str = "Artist",
    album: str = "Album",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_FRAME * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text=album))
    if grouping is not None:
        tags.add(GRP1(encoding=3, text=grouping))
        tags.add(TIT1(encoding=3, text=grouping))
    tags.save(str(path), v2_version=3)
    return path


def _write_xml(tmp_path: Path, entries: list[tuple[Path, str]]) -> Path:
    """Folder 'Genre' > playlist 'Halloween' holding (path, iTunes Name) entries."""
    tracks: dict[str, dict[str, Any]] = {}
    for idx, (path, name) in enumerate(entries, start=1):
        tracks[str(idx)] = {
            "Track ID": idx,
            "Name": name,
            "Artist": "Artist",
            "Album": "Album",
            "Location": "file://localhost/" + urllib.parse.quote(path.as_posix()),
        }
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
    return xml


def _seed_db(db_path: Path, paths: list[Path]) -> None:
    conn = get_db_connection(db_path)
    try:
        run_migrations(conn)
        album_repo = AlbumRepository(conn)
        with conn:
            album_repo.upsert(AlbumRecord(folder_path=str(paths[0].parent)))
        album = album_repo.get_by_folder_path(str(paths[0].parent))
        assert album is not None
        assert album.id is not None
        with conn:
            for p in paths:
                TrackRepository(conn).upsert(
                    TrackRecord(
                        album_id=album.id,
                        file_path=str(p),
                        filename=p.name,
                        grouping="Gender:Male",
                    )
                )
    finally:
        conn.close()


def _db_grouping(db_path: Path, path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT grouping FROM tracks WHERE file_path = ?", (str(path),)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[0]


@pytest.fixture
def scenario(tmp_path: Path) -> dict[str, Path]:
    """Two tracks: 'good' matches iTunes, 'bad' has a different title. Both in the DB."""
    music = tmp_path / "music"
    good = _make_mp3(music / "01 Ghost.mp3", title="Ghost", grouping="Gender:Male")
    bad = _make_mp3(music / "02 Wrong.mp3", title="Billie Jean", grouping="Gender:Male")
    xml = _write_xml(tmp_path, [(good, "Ghost"), (bad, "Thriller")])
    db = tmp_path / "library.db"
    _seed_db(db, [good, bad])
    return {"good": good, "bad": bad, "xml": xml, "db": db, "out": tmp_path / "audit.csv"}


def _invoke(s: dict[str, Path], *extra: str, holiday: str = "Halloween") -> Result:
    args = [
        "playlist-holiday",
        "--itunes-xml",
        str(s["xml"]),
        "--playlist",
        "Genre/Halloween",
        "--holiday",
        holiday,
        "--db-path",
        str(s["db"]),
        "--out",
        str(s["out"]),
        *extra,
    ]
    return CliRunner().invoke(cli, args)


def _grp1(path: Path) -> str:
    return str(ID3(str(path))["GRP1"])


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_tags_files_mirrors_db_and_prints_summary(scenario: dict[str, Path]) -> None:
    result = _invoke(scenario)

    assert result.exit_code == 0, result.output
    assert "Tagged: 2 | Already tagged: 0 | Mismatches: 1 | Errors: 0" in result.output
    for key in ("good", "bad"):
        assert _grp1(scenario[key]) == "Gender:Male | Holiday:Halloween"
        assert str(ID3(str(scenario[key]))["TIT1"]) == "Gender:Male | Holiday:Halloween"
        assert _db_grouping(scenario["db"], scenario[key]) == "Gender:Male | Holiday:Halloween"


@pytest.mark.integration
def test_writes_csv_report_with_expected_columns(scenario: dict[str, Path]) -> None:
    result = _invoke(scenario)
    assert result.exit_code == 0, result.output

    with scenario["out"].open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == _EXPECTED_COLUMNS
        rows = list(reader)

    assert len(rows) == 1
    row = rows[0]
    assert row["file_path"] == str(scenario["bad"])
    assert row["itunes_name"] == "Thriller"
    assert row["id3_title"] == "Billie Jean"
    assert row["issues"] == "title_mismatch"
    assert int(row["title_score"]) < 90
    assert int(row["artist_score"]) == 100


@pytest.mark.integration
def test_multiple_issues_are_pipe_joined_in_csv(tmp_path: Path) -> None:
    present = _make_mp3(
        tmp_path / "music" / "01 A.mp3", title="Alpha", artist="Yankee", album="Xray"
    )
    missing = tmp_path / "music" / "02 Gone.mp3"
    xml = _write_xml(tmp_path, [(present, "Zulu"), (missing, "Gone")])
    out = tmp_path / "audit.csv"
    db = tmp_path / "library.db"
    _seed_db(db, [present])

    result = CliRunner().invoke(
        cli,
        [
            "playlist-holiday",
            "--itunes-xml",
            str(xml),
            "--playlist",
            "Genre/Halloween",
            "--holiday",
            "Halloween",
            "--db-path",
            str(db),
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    with out.open(encoding="utf-8", newline="") as fh:
        issues = {r["file_path"]: r["issues"] for r in csv.DictReader(fh)}
    assert issues[str(present)] == "title_mismatch|artist_mismatch|album_mismatch"
    assert issues[str(missing)] == "file_missing"
    assert "Errors: 1" in result.output


@pytest.mark.integration
def test_rerun_reports_nothing_newly_tagged(scenario: dict[str, Path]) -> None:
    assert _invoke(scenario).exit_code == 0
    second = _invoke(scenario)

    assert second.exit_code == 0, second.output
    assert "Tagged: 0 | Already tagged: 2" in second.output
    assert _grp1(scenario["good"]) == "Gender:Male | Holiday:Halloween"


@pytest.mark.integration
def test_non_mp3_files_are_skipped_and_reported(tmp_path: Path) -> None:
    music = tmp_path / "music"
    mp3 = _make_mp3(music / "01 Ghost.mp3", title="Ghost", grouping="Gender:Male")
    m4a = music / "02 Song.m4a"
    m4a.write_bytes(b"mp4 container bytes")
    before = m4a.read_bytes()
    xml = _write_xml(tmp_path, [(mp3, "Ghost"), (m4a, "Song")])
    db = tmp_path / "library.db"
    _seed_db(db, [mp3])
    s = {"xml": xml, "db": db, "out": tmp_path / "audit.csv"}

    result = _invoke(s)

    assert result.exit_code == 0, result.output
    assert m4a.read_bytes() == before
    assert "Skipped (not .mp3): 1" in result.output
    assert "Tagged: 1 | Already tagged: 0 | Mismatches: 0 | Errors: 0" in result.output
    assert "not_mp3" in s["out"].read_text(encoding="utf-8")


@pytest.mark.integration
def test_stale_tit1_is_synced_and_reported(tmp_path: Path) -> None:
    grouping = "Gender:Male | Holiday:Halloween"
    mp3 = _make_mp3(tmp_path / "music" / "01 Ghost.mp3", title="Ghost", grouping=grouping)
    tags = ID3(str(mp3))
    tags.setall("TIT1", [TIT1(encoding=3, text="Gender:Male")])
    tags.save(str(mp3), v2_version=3)
    xml = _write_xml(tmp_path, [(mp3, "Ghost")])
    db = tmp_path / "library.db"
    _seed_db(db, [mp3])
    s = {"xml": xml, "db": db, "out": tmp_path / "audit.csv"}

    result = _invoke(s)

    assert result.exit_code == 0, result.output
    assert "TIT1 synced: 1" in result.output
    assert "Tagged: 0 | Already tagged: 1 | Mismatches: 0 | Errors: 0" in result.output
    assert str(ID3(str(mp3))["TIT1"]) == grouping


@pytest.mark.integration
def test_dry_run_leaves_files_and_db_untouched(scenario: dict[str, Path]) -> None:
    before = scenario["good"].read_bytes()

    result = _invoke(scenario, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()
    assert scenario["good"].read_bytes() == before
    assert _db_grouping(scenario["db"], scenario["good"]) == "Gender:Male"
    assert scenario["out"].exists()  # the audit report is still written


@pytest.mark.integration
def test_other_holiday_values_are_accepted(scenario: dict[str, Path]) -> None:
    result = _invoke(scenario, holiday="Christmas")

    assert result.exit_code == 0, result.output
    assert _grp1(scenario["good"]) == "Gender:Male | Holiday:Christmas"


# ---------------------------------------------------------------------------
# Validation and errors
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("holiday", ["Arbor Day", "None", "halloween night"])
def test_holiday_outside_allowed_values_is_rejected(
    scenario: dict[str, Path], holiday: str
) -> None:
    before = scenario["good"].read_bytes()

    result = _invoke(scenario, holiday=holiday)

    assert result.exit_code == 2
    assert "Invalid value for '--holiday'" in result.output
    assert scenario["good"].read_bytes() == before


@pytest.mark.integration
def test_unknown_playlist_exits_1_with_clear_message(scenario: dict[str, Path]) -> None:
    args = [
        "playlist-holiday",
        "--itunes-xml",
        str(scenario["xml"]),
        "--playlist",
        "Genre/Nope",
        "--holiday",
        "Halloween",
        "--db-path",
        str(scenario["db"]),
        "--out",
        str(scenario["out"]),
    ]
    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 1
    assert "Genre/Nope" in result.output
    assert isinstance(result.exception, SystemExit)  # a ClickException, not a traceback


@pytest.mark.integration
def test_options_and_defaults_propagate_to_service(
    scenario: dict[str, Path], mocker: MockerFixture
) -> None:
    from tagger.integrity import playlist_holiday

    empty = playlist_holiday.PlaylistAuditResult(
        rows=[], tagged=0, already_tagged=0, mismatches=0, errors=0
    )
    mock_service = mocker.patch(
        "tagger.integrity.playlist_holiday.apply_holiday_and_audit",
        autospec=True,
        return_value=empty,
    )
    signature = inspect.signature(playlist_holiday.apply_holiday_and_audit)

    custom = _invoke(scenario, "--threshold", "75", "--workers", "2", "--dry-run")
    assert custom.exit_code == 0, custom.output
    bound = signature.bind(*mock_service.call_args.args, **mock_service.call_args.kwargs)
    bound.apply_defaults()
    assert isinstance(bound.arguments["library"], ItunesLibrary)
    assert isinstance(bound.arguments["track_repo"], TrackRepository)
    assert bound.arguments["playlist"] == "Genre/Halloween"
    assert bound.arguments["holiday"] == "Halloween"
    assert bound.arguments["threshold"] == 75
    assert bound.arguments["workers"] == 2
    assert bound.arguments["dry_run"] is True

    default = _invoke(scenario)
    assert default.exit_code == 0, default.output
    bound = signature.bind(*mock_service.call_args.args, **mock_service.call_args.kwargs)
    bound.apply_defaults()
    assert bound.arguments["threshold"] == 90
    assert bound.arguments["workers"] == 4
    assert bound.arguments["dry_run"] is False


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_readme_documents_playlist_holiday() -> None:
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "playlist-holiday" in readme
    assert "--holiday" in readme
    assert "Get Info" in readme  # the iTunes tag-cache refresh note


@pytest.mark.integration
def test_changelog_unreleased_mentions_playlist_holiday() -> None:
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert "playlist-holiday" in unreleased
