"""Tests for tagger.writer.id3_writer.ID3Writer."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.id3 import ID3, TIT2

from tagger.db.album_repo import AlbumRepository
from tagger.db.connection import get_db_connection, run_migrations
from tagger.db.models import AlbumRecord, TrackRecord
from tagger.db.track_repo import TrackRepository
from tagger.exceptions import FileProcessError
from tagger.writer.id3_writer import ID3Writer


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = get_db_connection(tmp_path / "test.db")
    run_migrations(conn)
    return conn


@pytest.fixture
def track_repo(db_conn: sqlite3.Connection) -> TrackRepository:
    return TrackRepository(db_conn)


@pytest.fixture
def album_id(db_conn: sqlite3.Connection) -> int:
    repo = AlbumRepository(db_conn)
    with db_conn:
        repo.upsert(AlbumRecord(folder_path="/music/Artist/Album"))
    album = repo.get_by_folder_path("/music/Artist/Album")
    assert album is not None
    assert album.id
    return album.id


@pytest.fixture
def enriched_mp3(tmp_path: Path, track_repo: TrackRepository, album_id: int) -> TrackRecord:
    """A minimal MP3 with a fully enriched TrackRecord in the DB."""
    path = tmp_path / "track.mp3"
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Original"))
    tags.save(str(path))

    record = TrackRecord(
        album_id=album_id,
        file_path=str(path),
        filename=path.name,
        track_number=1,
        title="Remastered Title",
        artist="Artist Name",
        album_artist="Album Artist",
        album_title="Great Album",
        year=1991,
        track_num="01/10",
        genre="Industrial",
        grouping="Origin:Cleveland, US | Gender:Male",
        enrichment_status="found",
        written_status="pending",
    )
    with track_repo._conn:
        track_repo.upsert(record)
    return track_repo.get_by_file_path(str(path))  # type: ignore[return-value]


def test_write_track_writes_tags_to_file(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    writer = ID3Writer(track_repo)
    result = writer.write_track(enriched_mp3)

    assert result is True
    tags = ID3(enriched_mp3.file_path)
    assert str(tags["TIT2"]) == "Remastered Title"
    assert str(tags["TPE1"]) == "Artist Name"
    assert str(tags["TALB"]) == "Great Album"
    assert str(tags["TCON"]) == "Industrial"
    assert str(tags["TIT1"]) == "Origin:Cleveland, US | Gender:Male"


def test_write_track_marks_db_done(enriched_mp3: TrackRecord, track_repo: TrackRepository) -> None:
    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    updated = track_repo.get_by_file_path(enriched_mp3.file_path)
    assert updated is not None
    assert updated.written_status == "done"


def test_write_track_skips_not_enriched(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    enriched_mp3 = enriched_mp3.model_copy(update={"enrichment_status": "pending"})
    writer = ID3Writer(track_repo)
    result = writer.write_track(enriched_mp3)

    assert result is False


def test_write_track_skips_already_written(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    enriched_mp3 = enriched_mp3.model_copy(update={"written_status": "done"})
    writer = ID3Writer(track_repo)
    result = writer.write_track(enriched_mp3)

    assert result is False


def test_write_track_force_rewrites_done_track(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    # First write
    ID3Writer(track_repo).write_track(enriched_mp3)
    done_record = track_repo.get_by_file_path(enriched_mp3.file_path)
    assert done_record is not None
    assert done_record.written_status == "done"

    # Force re-write with updated title
    updated = done_record.model_copy(update={"title": "Force Updated"})
    with track_repo._conn:
        track_repo.upsert(updated)

    result = ID3Writer(track_repo, force=True).write_track(updated)

    assert result is True
    assert str(ID3(enriched_mp3.file_path)["TIT2"]) == "Force Updated"


def test_write_track_dry_run_does_not_modify_file(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    original_mtime = Path(enriched_mp3.file_path).stat().st_mtime
    writer = ID3Writer(track_repo, dry_run=True)
    result = writer.write_track(enriched_mp3)

    assert result is True
    assert Path(enriched_mp3.file_path).stat().st_mtime == original_mtime


def test_write_track_dry_run_does_not_update_db(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    ID3Writer(track_repo, dry_run=True).write_track(enriched_mp3)
    record = track_repo.get_by_file_path(enriched_mp3.file_path)
    assert record is not None
    assert record.written_status == "pending"


def test_write_track_permission_error_marks_db_error(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    with patch("tagger.writer.id3_writer.ID3", side_effect=PermissionError("denied")):
        writer = ID3Writer(track_repo)
        with pytest.raises(FileProcessError):
            writer.write_track(enriched_mp3)

    record = track_repo.get_by_file_path(enriched_mp3.file_path)
    assert record is not None
    assert record.written_status == "error"


def test_write_track_os_error_marks_db_error(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    with patch("tagger.writer.id3_writer.ID3", side_effect=OSError("disk full")):
        writer = ID3Writer(track_repo)
        with pytest.raises(FileProcessError):
            writer.write_track(enriched_mp3)

    record = track_repo.get_by_file_path(enriched_mp3.file_path)
    assert record is not None
    assert record.written_status == "error"


def test_write_track_mutagen_error_marks_db_error_and_continues(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """MutagenError (e.g. permission denied on network share) is caught, recorded,
    and does not crash the whole write run."""
    from mutagen import MutagenError

    with patch("tagger.writer.id3_writer.ID3", side_effect=MutagenError("Permission denied")):
        writer = ID3Writer(track_repo)
        with pytest.raises(FileProcessError):
            writer.write_track(enriched_mp3)

    record = track_repo.get_by_file_path(enriched_mp3.file_path)
    assert record is not None
    assert record.written_status == "error"


def test_write_pending_continues_after_mutagen_error(
    enriched_mp3: TrackRecord, track_repo: TrackRepository, tmp_path: Path
) -> None:
    """A MutagenError on one track is counted as an error but the remaining tracks still run."""
    from mutagen import MutagenError

    # Add a second good track
    good_path = tmp_path / "good.mp3"
    good_path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 192)
    good_tags = ID3()
    good_tags.add(TIT2(encoding=3, text="Good"))
    good_tags.save(str(good_path))

    good_record = TrackRecord(
        album_id=enriched_mp3.album_id,
        file_path=str(good_path),
        filename=good_path.name,
        title="Good Track",
        artist="Artist",
        album_title="Album",
        enrichment_status="found",
        written_status="pending",
    )
    with track_repo._conn:
        track_repo.upsert(good_record)

    def id3_side_effect(path: str) -> ID3:
        if "track.mp3" in path:
            raise MutagenError("Permission denied")
        return ID3(path)

    with patch("tagger.writer.id3_writer.ID3", side_effect=id3_side_effect):
        writer = ID3Writer(track_repo)
        success, errors = writer.write_pending()

    assert errors == 1
    assert success == 1


def test_write_pending_returns_counts(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    writer = ID3Writer(track_repo)
    success, errors = writer.write_pending()

    assert success == 1
    assert errors == 0


def test_write_pending_counts_errors(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    with patch("tagger.writer.id3_writer.ID3", side_effect=PermissionError("denied")):
        writer = ID3Writer(track_repo)
        success, errors = writer.write_pending()

    assert success == 0
    assert errors == 1


def test_write_track_v24_saves_with_tdrc(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    writer = ID3Writer(track_repo, id3_version="2.4")
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert "TDRC" in tags
    assert str(tags["TDRC"]) == "1991"


def test_write_pending_parallel_returns_counts(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """write_pending with workers>1 uses a thread pool and returns correct counts."""
    writer = ID3Writer(track_repo)
    success, errors = writer.write_pending(workers=2)

    assert success == 1
    assert errors == 0


def test_write_pending_parallel_counts_errors(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    with patch("tagger.writer.id3_writer.ID3", side_effect=PermissionError("denied")):
        writer = ID3Writer(track_repo)
        success, errors = writer.write_pending(workers=2)

    assert success == 0
    assert errors == 1


def test_write_track_embeds_album_art(
    enriched_mp3: TrackRecord, track_repo: TrackRepository, tmp_path: Path
) -> None:
    """When art_path is set on the track, APIC tag is embedded in the MP3."""
    art_file = tmp_path / "cover.jpg"
    art_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)  # minimal JPEG bytes

    enriched_mp3 = enriched_mp3.model_copy(update={"art_path": str(art_file)})
    with track_repo._conn:
        track_repo.upsert(enriched_mp3)

    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert "APIC:" in tags
    assert tags["APIC:"].data == art_file.read_bytes()


def test_write_track_missing_art_file_skips_apic(
    enriched_mp3: TrackRecord, track_repo: TrackRepository, tmp_path: Path
) -> None:
    """If art_path points to a missing file, the write still succeeds (APIC skipped)."""
    enriched_mp3 = enriched_mp3.model_copy(update={"art_path": str(tmp_path / "nonexistent.jpg")})
    with track_repo._conn:
        track_repo.upsert(enriched_mp3)

    writer = ID3Writer(track_repo)
    result = writer.write_track(enriched_mp3)

    assert result is True
    tags = ID3(enriched_mp3.file_path)
    assert "APIC:" not in tags


def test_write_track_writes_grp1_for_itunes(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """Grouping is written to both TIT1 (standard) and GRP1 (iTunes 12.9.1+)."""
    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert "TIT1" in tags
    assert "GRP1" in tags
    assert str(tags["TIT1"]) == str(tags["GRP1"])


def test_write_track_normalizes_brackets_in_title(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """Title with [Remix] or {Remix} is written as (Remix) in TIT2."""
    enriched_mp3 = enriched_mp3.model_copy(update={"title": "Song Title [Remix]"})
    with track_repo._conn:
        track_repo.upsert(enriched_mp3)

    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert str(tags["TIT2"]) == "Song Title (Remix)"


def test_write_track_normalizes_uppercase_title(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """ALL CAPS title is title-cased before being written to TIT2."""
    enriched_mp3 = enriched_mp3.model_copy(update={"title": "SONG TITLE"})
    with track_repo._conn:
        track_repo.upsert(enriched_mp3)

    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert str(tags["TIT2"]) == "Song Title"


def test_write_track_extracts_feat_from_artist(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """Featured artist in TPE1 is moved to TIT2; TPE1 gets the clean artist name."""
    enriched_mp3 = enriched_mp3.model_copy(
        update={"artist": "Artist feat. Guest", "title": "Song Title"}
    )
    with track_repo._conn:
        track_repo.upsert(enriched_mp3)

    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert str(tags["TPE1"]) == "Artist"
    assert str(tags["TIT2"]) == "Song Title (feat. Guest)"


def test_write_track_sets_tcmp_for_compilation(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """TCMP=1 is written and TPE2 is omitted when track.compilation is True."""
    enriched_mp3 = enriched_mp3.model_copy(update={"compilation": True, "album_artist": None})
    with track_repo._conn:
        track_repo.upsert(enriched_mp3)

    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert str(tags["TCMP"]) == "1"
    assert "TPE2" not in tags


def test_write_track_no_tcmp_for_regular_track(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """Non-compilation tracks have no TCMP frame."""
    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert "TCMP" not in tags
    assert str(tags["TPE2"]) == "Album Artist"


def test_write_track_writes_tpos_for_multidisc_track(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """disc_number is written as TPOS when set on the track record."""
    enriched_mp3 = enriched_mp3.model_copy(update={"disc_number": 2})
    with track_repo._conn:
        track_repo.upsert(enriched_mp3)

    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert "TPOS" in tags
    assert str(tags["TPOS"]) == "2"


def test_write_track_omits_tpos_when_disc_number_is_none(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """TPOS is not written when disc_number is None (single-disc album)."""
    assert enriched_mp3.disc_number is None

    writer = ID3Writer(track_repo)
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert "TPOS" not in tags


def test_write_track_falls_back_to_temp_copy_on_oserror(
    enriched_mp3: TrackRecord, track_repo: TrackRepository
) -> None:
    """When mutagen raises OSError (e.g. network-share EINVAL), the writer
    falls back to copy-locally-then-replace and still writes the tags."""
    original_save = None

    call_count = 0

    def save_side_effect(file_path: str, v2_version: int) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError(22, "Invalid argument")
        # Second call (to temp path) succeeds normally
        original_save(file_path, v2_version=v2_version)  # type: ignore[misc]

    writer = ID3Writer(track_repo)

    # Patch only the first save attempt on the original path
    real_save_tags = writer._save_tags

    def patched_save_tags(tags: ID3, file_path: str) -> None:
        nonlocal original_save
        # Wrap the real save so first call raises, second succeeds
        real_tags_save = tags.save

        def wrapped_save(path: str, v2_version: int = 3) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError(22, "Invalid argument")
            real_tags_save(path, v2_version=v2_version)

        tags.save = wrapped_save  # type: ignore[method-assign]
        real_save_tags(tags, file_path)

    writer._save_tags = patched_save_tags  # type: ignore[method-assign]
    writer.write_track(enriched_mp3)

    tags = ID3(enriched_mp3.file_path)
    assert str(tags["TIT2"]) == "Remastered Title"
