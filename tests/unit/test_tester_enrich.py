"""Tests for the `enrich` command in tagger.mp3_tagger."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from mutagen.id3 import ID3, TIT2

from tagger.mp3_tagger import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_mp3(path: Path, title: str = "Track") -> Path:
    frame = b"\xff\xfb\x18\xc0" + b"\x00" * 140
    path.write_bytes(frame * 4)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.save(str(path))
    return path


def test_enrich_calls_pipeline_enrich_album(runner: CliRunner, tmp_path: Path) -> None:
    """enrich command calls EnrichmentPipeline.enrich_album so data is saved to DB."""
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()
    _make_mp3(album_dir / "01.mp3")

    mock_pipeline = MagicMock()

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.AlbumRepository"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ManualReviewRepository"),
        patch("tagger.mp3_tagger.EnrichmentPipeline", return_value=mock_pipeline),
        patch("tagger.mp3_tagger.DiscogsClient"),
        patch("tagger.mp3_tagger.WebScraper"),
        patch("tagger.mp3_tagger.HeuristicEnricher"),
        patch("tagger.mp3_tagger.MusicBrainzClient"),
        patch("tagger.mp3_tagger.find_mp3_files", return_value=[album_dir / "01.mp3"]),
    ):
        result = runner.invoke(cli, ["enrich", str(album_dir), "--token", "test_token"])

    assert result.exit_code == 0
    mock_pipeline.enrich_album.assert_called_once()


def test_enrich_no_mp3_files_exits_early(runner: CliRunner, tmp_path: Path) -> None:
    """enrich exits with a message when no MP3 files are found."""
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.AlbumRepository"),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ManualReviewRepository"),
        patch("tagger.mp3_tagger.find_mp3_files", return_value=[]),
    ):
        result = runner.invoke(cli, ["enrich", str(album_dir), "--token", "test_token"])

    assert result.exit_code == 0
    assert "No MP3 files or album subfolders found" in result.output


def test_enrich_no_token_exits_early(runner: CliRunner, tmp_path: Path) -> None:
    """enrich exits with an error when no Discogs token is available."""
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()

    with patch("tagger.mp3_tagger.Settings") as mock_settings_cls:
        mock_settings_cls.return_value.discogs_token = None
        result = runner.invoke(cli, ["enrich", str(album_dir)])

    assert result.exit_code == 0
    assert "token" in result.output.lower() or "Error" in result.output


def test_skip_enriched_skips_already_found_album(runner: CliRunner, tmp_path: Path) -> None:
    """--skip-enriched causes albums already in 'found' status to be skipped."""
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()
    _make_mp3(album_dir / "01.mp3")

    mock_pipeline = MagicMock()
    mock_album_repo = MagicMock()

    from tagger.db.models import AlbumRecord

    already_found = AlbumRecord(
        id=1,
        folder_path=str(album_dir.absolute()),
        enrichment_status="found",
        discogs_release_id=99,
    )
    mock_album_repo.get_by_folder_path.return_value = already_found

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.AlbumRepository", return_value=mock_album_repo),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ManualReviewRepository"),
        patch("tagger.mp3_tagger.EnrichmentPipeline", return_value=mock_pipeline),
        patch("tagger.mp3_tagger.DiscogsClient"),
        patch("tagger.mp3_tagger.WebScraper"),
        patch("tagger.mp3_tagger.HeuristicEnricher"),
        patch("tagger.mp3_tagger.MusicBrainzClient"),
        patch("tagger.mp3_tagger.find_mp3_files", return_value=[album_dir / "01.mp3"]),
    ):
        result = runner.invoke(
            cli, ["enrich", str(album_dir), "--token", "test_token", "--skip-enriched"]
        )

    assert result.exit_code == 0
    mock_pipeline.enrich_album.assert_not_called()


def test_skip_enriched_processes_pending_album(runner: CliRunner, tmp_path: Path) -> None:
    """--skip-enriched still processes albums with 'pending' enrichment status."""
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()
    _make_mp3(album_dir / "01.mp3")

    mock_pipeline = MagicMock()
    mock_album_repo = MagicMock()

    from tagger.db.models import AlbumRecord

    pending_album = AlbumRecord(
        id=1,
        folder_path=str(album_dir.absolute()),
        enrichment_status="pending",
    )
    # First call (skip check) → pending → don't skip.
    # Second call (after upsert) → still needed to get album_id.
    mock_album_repo.get_by_folder_path.return_value = pending_album

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.AlbumRepository", return_value=mock_album_repo),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ManualReviewRepository"),
        patch("tagger.mp3_tagger.EnrichmentPipeline", return_value=mock_pipeline),
        patch("tagger.mp3_tagger.DiscogsClient"),
        patch("tagger.mp3_tagger.WebScraper"),
        patch("tagger.mp3_tagger.HeuristicEnricher"),
        patch("tagger.mp3_tagger.MusicBrainzClient"),
        patch("tagger.mp3_tagger.find_mp3_files", return_value=[album_dir / "01.mp3"]),
    ):
        result = runner.invoke(
            cli, ["enrich", str(album_dir), "--token", "test_token", "--skip-enriched"]
        )

    assert result.exit_code == 0
    mock_pipeline.enrich_album.assert_called_once()


def test_without_skip_enriched_processes_found_album(runner: CliRunner, tmp_path: Path) -> None:
    """Without --skip-enriched, albums with 'found' status are re-enriched."""
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()
    _make_mp3(album_dir / "01.mp3")

    mock_pipeline = MagicMock()
    mock_album_repo = MagicMock()

    from tagger.db.models import AlbumRecord

    found_album = AlbumRecord(
        id=1,
        folder_path=str(album_dir.absolute()),
        enrichment_status="found",
        discogs_release_id=99,
    )
    mock_album_repo.get_by_folder_path.return_value = found_album

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.AlbumRepository", return_value=mock_album_repo),
        patch("tagger.mp3_tagger.TrackRepository"),
        patch("tagger.mp3_tagger.ManualReviewRepository"),
        patch("tagger.mp3_tagger.EnrichmentPipeline", return_value=mock_pipeline),
        patch("tagger.mp3_tagger.DiscogsClient"),
        patch("tagger.mp3_tagger.WebScraper"),
        patch("tagger.mp3_tagger.HeuristicEnricher"),
        patch("tagger.mp3_tagger.MusicBrainzClient"),
        patch("tagger.mp3_tagger.find_mp3_files", return_value=[album_dir / "01.mp3"]),
    ):
        # No --skip-enriched flag
        result = runner.invoke(cli, ["enrich", str(album_dir), "--token", "test_token"])

    assert result.exit_code == 0
    mock_pipeline.enrich_album.assert_called_once()


def test_enrich_downloads_art_after_pipeline(runner: CliRunner, tmp_path: Path) -> None:
    """After pipeline enrichment, art is downloaded and art_path saved to tracks."""
    album_dir = tmp_path / "Artist - Album"
    album_dir.mkdir()
    _make_mp3(album_dir / "01.mp3")

    mock_pipeline = MagicMock()
    mock_album_repo = MagicMock()
    mock_track_repo = MagicMock()

    # Simulate pipeline enriching the album (sets discogs_release_id)
    from tagger.db.models import AlbumRecord, TrackRecord

    enriched_album = AlbumRecord(
        id=1,
        folder_path=str(album_dir),
        enrichment_status="found",
        discogs_release_id=12345,
    )
    mock_album_repo.get_by_folder_path.return_value = enriched_album
    mock_track_repo.get_by_album.return_value = [
        TrackRecord(
            id=1,
            album_id=1,
            file_path=str(album_dir / "01.mp3"),
            filename="01.mp3",
            enrichment_status="found",
        )
    ]

    mock_client = MagicMock()
    from tagger.enricher.discogs.models import DiscogsImage, DiscogsRelease

    mock_client.get_release.return_value = DiscogsRelease(
        id=12345,
        title="Album",
        artists=[],
        tracklist=[],
        images=[DiscogsImage(type="primary", resource_url="http://img.example.com/art.jpg")],
        resource_url="https://api.discogs.com/releases/12345",
    )

    mock_downloader = MagicMock()
    art_file = tmp_path / "art" / "12345.jpg"
    art_file.parent.mkdir()
    art_file.write_bytes(b"image")
    mock_downloader.download.return_value = art_file

    with (
        patch("tagger.mp3_tagger.get_db_connection"),
        patch("tagger.mp3_tagger.run_migrations"),
        patch("tagger.mp3_tagger.AlbumRepository", return_value=mock_album_repo),
        patch("tagger.mp3_tagger.TrackRepository", return_value=mock_track_repo),
        patch("tagger.mp3_tagger.ManualReviewRepository"),
        patch("tagger.mp3_tagger.EnrichmentPipeline", return_value=mock_pipeline),
        patch("tagger.mp3_tagger.DiscogsClient", return_value=mock_client),
        patch("tagger.mp3_tagger.WebScraper"),
        patch("tagger.mp3_tagger.HeuristicEnricher"),
        patch("tagger.mp3_tagger.MusicBrainzClient"),
        patch("tagger.mp3_tagger.ArtDownloader", return_value=mock_downloader),
        patch("tagger.mp3_tagger.find_mp3_files", return_value=[album_dir / "01.mp3"]),
    ):
        result = runner.invoke(cli, ["enrich", str(album_dir), "--token", "test_token"])

    assert result.exit_code == 0
    mock_downloader.download.assert_called_once()
    # art_path should have been saved back to each track
    mock_track_repo.upsert.assert_called()
