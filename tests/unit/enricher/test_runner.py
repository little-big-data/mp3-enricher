"""Tests for tagger.enricher.runner.EnrichmentRunner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tagger.db.models import AlbumRecord
from tagger.enricher.runner import EnrichmentRunner
from tagger.exceptions import EnrichmentError


def _make_album(album_id: int) -> AlbumRecord:
    return AlbumRecord(id=album_id, folder_path=f"/music/album{album_id}")


@pytest.fixture
def album_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def pipeline() -> MagicMock:
    return MagicMock()


def test_run_enrichment_calls_enrich_for_each_pending_album(
    album_repo: MagicMock, pipeline: MagicMock
) -> None:
    albums = [_make_album(1), _make_album(2), _make_album(3)]
    album_repo.get_pending.return_value = albums

    runner = EnrichmentRunner(pipeline, album_repo, workers=1)
    success, errors = runner.run_enrichment()

    assert success == 3
    assert errors == 0
    assert pipeline.enrich_album.call_count == 3


def test_run_enrichment_empty_pending_returns_zeros(
    album_repo: MagicMock, pipeline: MagicMock
) -> None:
    album_repo.get_pending.return_value = []

    runner = EnrichmentRunner(pipeline, album_repo)
    success, errors = runner.run_enrichment()

    assert success == 0
    assert errors == 0
    pipeline.enrich_album.assert_not_called()


def test_run_enrichment_counts_enrichment_error(album_repo: MagicMock, pipeline: MagicMock) -> None:
    album_repo.get_pending.return_value = [_make_album(1)]
    pipeline.enrich_album.side_effect = EnrichmentError("discogs failed")

    runner = EnrichmentRunner(pipeline, album_repo)
    success, errors = runner.run_enrichment()

    assert success == 0
    assert errors == 1


def test_run_enrichment_error_in_one_does_not_stop_others(
    album_repo: MagicMock, pipeline: MagicMock
) -> None:
    """An error on one album is logged and counted; others still complete."""
    albums = [_make_album(i) for i in range(5)]
    album_repo.get_pending.return_value = albums

    def side_effect(album: AlbumRecord) -> None:
        if album.id == 2:
            raise EnrichmentError("failed for album 2")

    pipeline.enrich_album.side_effect = side_effect

    runner = EnrichmentRunner(pipeline, album_repo, workers=2)
    success, errors = runner.run_enrichment()

    assert success == 4
    assert errors == 1


def test_run_enrichment_unexpected_exception_counted_as_error(
    album_repo: MagicMock, pipeline: MagicMock
) -> None:
    """Unexpected (non-EnrichmentError) exceptions are also caught and counted."""
    album_repo.get_pending.return_value = [_make_album(1)]
    pipeline.enrich_album.side_effect = RuntimeError("unexpected")

    runner = EnrichmentRunner(pipeline, album_repo)
    success, errors = runner.run_enrichment()

    assert success == 0
    assert errors == 1


def test_run_enrichment_uses_thread_pool(album_repo: MagicMock, pipeline: MagicMock) -> None:
    """EnrichmentRunner submits work to a ThreadPoolExecutor."""
    albums = [_make_album(1), _make_album(2)]
    album_repo.get_pending.return_value = albums

    with patch("tagger.enricher.runner.ThreadPoolExecutor") as mock_executor_cls:
        mock_pool = MagicMock()
        mock_executor_cls.return_value.__enter__.return_value = mock_pool

        future1 = MagicMock()
        future1.result.return_value = None
        future2 = MagicMock()
        future2.result.return_value = None
        mock_pool.submit.side_effect = [future1, future2]

        with patch("tagger.enricher.runner.as_completed", return_value=[future1, future2]):
            runner = EnrichmentRunner(pipeline, album_repo, workers=3)
            runner.run_enrichment()

        mock_executor_cls.assert_called_once_with(max_workers=3)
