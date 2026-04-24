"""Unit tests for tagger.integrity.scanner.IntegrityScanner.

Tests use a real temporary filesystem with minimal MP3 files created via mutagen
so we can control the exact ID3 tags read by the scanner.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2

from tagger.integrity.models import IssueKind
from tagger.integrity.scanner import IntegrityScanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mp3(
    path: Path,
    *,
    tpe2: str | None = None,
    tpe1: str | None = None,
    talb: str | None = None,
    tit2: str | None = None,
) -> None:
    """Write a minimal MP3 file with the specified ID3 tags."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    if tpe2 is not None:
        tags.add(TPE2(encoding=3, text=tpe2))
    if tpe1 is not None:
        tags.add(TPE1(encoding=3, text=tpe1))
    if talb is not None:
        tags.add(TALB(encoding=3, text=talb))
    if tit2 is not None:
        tags.add(TIT2(encoding=3, text=tit2))
    tags.save(str(path))


# ---------------------------------------------------------------------------
# Happy path — no issues
# ---------------------------------------------------------------------------


def test_no_issues_when_tags_match_folders(tmp_path: Path) -> None:
    """An album whose tags match folder names produces no issues."""
    album_dir = tmp_path / "Pink Floyd" / "The Dark Side of the Moon"
    _make_mp3(
        album_dir / "01 Speak to Me.mp3",
        tpe2="Pink Floyd",
        tpe1="Pink Floyd",
        talb="The Dark Side of the Moon",
        tit2="Speak to Me",
    )
    _make_mp3(
        album_dir / "02 Breathe.mp3",
        tpe2="Pink Floyd",
        tpe1="Pink Floyd",
        talb="The Dark Side of the Moon",
        tit2="Breathe",
    )

    issues = IntegrityScanner().scan_library(tmp_path)
    assert issues == []


def test_empty_library_produces_no_issues(tmp_path: Path) -> None:
    issues = IntegrityScanner().scan_library(tmp_path)
    assert issues == []


def test_album_dir_with_no_mp3s_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "Artist" / "Album").mkdir(parents=True)
    issues = IntegrityScanner().scan_library(tmp_path)
    assert issues == []


# ---------------------------------------------------------------------------
# AlbumArtist mismatch
# ---------------------------------------------------------------------------


def test_album_artist_mismatch_detected(tmp_path: Path) -> None:
    """TPE2 that poorly matches the artist folder name is flagged."""
    album_dir = tmp_path / "Radiohead" / "OK Computer"
    _make_mp3(
        album_dir / "01 Airbag.mp3",
        tpe2="Totally Different Band",
        talb="OK Computer",
        tit2="Airbag",
    )

    issues = IntegrityScanner(threshold=75).scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.ALBUM_ARTIST_MISMATCH in kinds


def test_album_artist_mismatch_not_detected_above_threshold(tmp_path: Path) -> None:
    """A near-match above the threshold is not flagged."""
    album_dir = tmp_path / "The Beatles" / "Abbey Road"
    _make_mp3(
        album_dir / "01 Come Together.mp3",
        tpe2="Beatles, The",  # different word order but high token_sort_ratio
        talb="Abbey Road",
        tit2="Come Together",
    )

    issues = IntegrityScanner(threshold=75).scan_library(tmp_path)
    mismatch = [i for i in issues if i.issue_kind == IssueKind.ALBUM_ARTIST_MISMATCH]
    assert mismatch == []


# ---------------------------------------------------------------------------
# Album tag mismatch
# ---------------------------------------------------------------------------


def test_album_tag_mismatch_detected(tmp_path: Path) -> None:
    """TALB that poorly matches the album folder name is flagged."""
    album_dir = tmp_path / "Miles Davis" / "Kind of Blue"
    _make_mp3(
        album_dir / "01 So What.mp3",
        tpe2="Miles Davis",
        talb="Completely Wrong Album Title",
        tit2="So What",
    )

    issues = IntegrityScanner(threshold=75).scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.ALBUM_MISMATCH in kinds


# ---------------------------------------------------------------------------
# Inconsistent tags across tracks
# ---------------------------------------------------------------------------


def test_inconsistent_album_artist_detected(tmp_path: Path) -> None:
    album_dir = tmp_path / "Artist" / "Album"
    _make_mp3(album_dir / "01.mp3", tpe2="Artist A", talb="Album", tit2="Track 1")
    _make_mp3(album_dir / "02.mp3", tpe2="Artist B", talb="Album", tit2="Track 2")

    issues = IntegrityScanner().scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.INCONSISTENT_ALBUM_ARTIST in kinds


def test_inconsistent_album_tag_detected(tmp_path: Path) -> None:
    album_dir = tmp_path / "Artist" / "Album"
    _make_mp3(album_dir / "01.mp3", tpe2="Artist", talb="Album Version 1", tit2="Track 1")
    _make_mp3(album_dir / "02.mp3", tpe2="Artist", talb="Album Version 2", tit2="Track 2")

    issues = IntegrityScanner().scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.INCONSISTENT_ALBUM in kinds


# ---------------------------------------------------------------------------
# All-untitled
# ---------------------------------------------------------------------------


def test_all_untitled_detected_when_most_titles_are_generic(tmp_path: Path) -> None:
    """Albums where ≥80% of tracks have useless titles are flagged."""
    album_dir = tmp_path / "Mystery Artist" / "Unknown Album"
    for i in range(1, 6):
        _make_mp3(
            album_dir / f"0{i}.mp3",
            tpe2="Mystery Artist",
            talb="Unknown Album",
            tit2=f"Track {i}",  # useless
        )

    issues = IntegrityScanner().scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.ALL_UNTITLED in kinds


def test_all_untitled_not_triggered_for_titled_tracks(tmp_path: Path) -> None:
    album_dir = tmp_path / "Artist" / "Album"
    for i in range(1, 5):
        _make_mp3(
            album_dir / f"0{i}.mp3",
            tpe2="Artist",
            talb="Album",
            tit2=f"Real Song Title {i}",
        )
    # Only 1 out of 4 is generic — below 80% threshold
    _make_mp3(album_dir / "05.mp3", tpe2="Artist", talb="Album", tit2="")

    issues = IntegrityScanner().scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.ALL_UNTITLED not in kinds


# ---------------------------------------------------------------------------
# Compilation artist (catchall folder)
# ---------------------------------------------------------------------------


def test_compilation_artist_detected_in_catchall_folder(tmp_path: Path) -> None:
    """Real AlbumArtist + Various Artist TPE1 in a 'Compilations' folder is flagged."""
    album_dir = tmp_path / "Compilations" / "80s Hits"
    _make_mp3(
        album_dir / "01.mp3",
        tpe2="Some Real Artist",  # non-various album artist
        tpe1="Various Artists",  # but track artist is Various
        talb="80s Hits",
        tit2="Track 1",
    )

    issues = IntegrityScanner().scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.COMPILATION_ARTIST in kinds


def test_catchall_folder_various_artist_is_not_flagged(tmp_path: Path) -> None:
    """A legitimate Various Artists comp in a catchall folder is fine."""
    album_dir = tmp_path / "Compilations" / "80s Hits"
    _make_mp3(
        album_dir / "01.mp3",
        tpe2="Various Artists",
        tpe1="Madonna",
        talb="80s Hits",
        tit2="Track 1",
    )

    issues = IntegrityScanner().scan_library(tmp_path)
    mismatch = [i for i in issues if i.issue_kind == IssueKind.COMPILATION_ARTIST]
    assert mismatch == []


# ---------------------------------------------------------------------------
# Track title mismatch — only surfaced when album has other issues
# ---------------------------------------------------------------------------


def test_track_title_mismatch_not_surfaced_alone(tmp_path: Path) -> None:
    """Track title mismatches are suppressed unless the album has other issues."""
    album_dir = tmp_path / "Artist" / "Album"
    # The filename says "Come Together" but the tag says something completely different.
    # With no other album-level issues this should be suppressed.
    _make_mp3(
        album_dir / "01 Come Together.mp3",
        tpe2="Artist",
        talb="Album",
        tit2="Completely Unrelated Title XXXYYY",
    )

    issues = IntegrityScanner(threshold=75).scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.TRACK_TITLE not in kinds


def test_track_title_mismatch_surfaced_with_other_issues(tmp_path: Path) -> None:
    """Track title mismatches ARE surfaced when the album already has issues."""
    album_dir = tmp_path / "Radiohead" / "Album"
    # Album artist tag mismatches the folder name → album has issues
    _make_mp3(
        album_dir / "01 Come Together.mp3",
        tpe2="Ennio Morricone",  # clearly not Radiohead
        talb="Album",
        tit2="Completely Unrelated Title XXXYYY",
    )

    issues = IntegrityScanner(threshold=75).scan_library(tmp_path)
    kinds = {i.issue_kind for i in issues}
    assert IssueKind.ALBUM_ARTIST_MISMATCH in kinds
    assert IssueKind.TRACK_TITLE in kinds


# ---------------------------------------------------------------------------
# Threshold parametrization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("threshold", "expect_issue"), [(40, False), (95, True)])
def test_threshold_controls_sensitivity(tmp_path: Path, threshold: int, expect_issue: bool) -> None:
    """A 'The Beatles' → 'Beatles, The' transform scores ~87; threshold 40 passes, 95 fails."""
    album_dir = tmp_path / "The Beatles" / "Abbey Road"
    _make_mp3(
        album_dir / "01 Come Together.mp3",
        tpe2="Beatles",
        talb="Abbey Road",
        tit2="Come Together",
    )

    issues = IntegrityScanner(threshold=threshold).scan_library(tmp_path)
    mismatch = [i for i in issues if i.issue_kind == IssueKind.ALBUM_ARTIST_MISMATCH]
    assert bool(mismatch) == expect_issue


# ---------------------------------------------------------------------------
# folder_path is populated
# ---------------------------------------------------------------------------


def test_folder_path_is_set_on_issues(tmp_path: Path) -> None:
    """TagIssue.folder_path should point to the album directory."""
    album_dir = tmp_path / "Artist" / "Broken Album"
    _make_mp3(
        album_dir / "01.mp3",
        tpe2="Completely Different",
        talb="Broken Album",
        tit2="Track",
    )

    issues = IntegrityScanner().scan_library(tmp_path)
    assert issues
    assert issues[0].folder_path == str(album_dir)
