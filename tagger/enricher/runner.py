"""Parallel enrichment runner — orchestrates album enrichment with ThreadPoolExecutor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

import structlog
from alive_progress import alive_bar

from tagger.exceptions import TaggerError

if TYPE_CHECKING:
    from tagger.db.album_repo import AlbumRepository
    from tagger.enricher.pipeline import EnrichmentPipeline

log = structlog.get_logger(__name__)


class EnrichmentRunner:
    """Runs per-album enrichment in parallel using a thread pool.

    Each album is submitted as an independent task.  Errors on individual
    albums are logged and counted; they never stop the remaining work.
    """

    def __init__(
        self,
        pipeline: EnrichmentPipeline,
        album_repo: AlbumRepository,
        workers: int = 4,
        show_progress: bool = False,
    ) -> None:
        self._pipeline = pipeline
        self._album_repo = album_repo
        self._workers = workers
        self._show_progress = show_progress

    def run_enrichment(self) -> tuple[int, int]:
        """Enrich all pending albums in parallel.

        Returns:
            (success_count, error_count)
        """
        pending = self._album_repo.get_pending()
        if not pending:
            log.info("runner.no_pending_albums")
            return 0, 0

        log.info("runner.start", album_count=len(pending), workers=self._workers)
        success = 0
        errors = 0

        try:
            with (
                alive_bar(
                    len(pending),
                    title="Enriching albums",
                    disable=not self._show_progress,
                ) as bar,
                ThreadPoolExecutor(max_workers=self._workers) as pool,
            ):
                futures = {
                    pool.submit(self._pipeline.enrich_album, album): album for album in pending
                }
                for future in as_completed(futures):
                    album = futures[future]
                    try:
                        future.result()
                        success += 1
                        log.debug("runner.album_done", album_id=album.id)
                    except TaggerError as exc:
                        errors += 1
                        log.error(
                            "runner.album_failed",
                            album_id=album.id,
                            error=str(exc),
                        )
                    except Exception as exc:  # catch-all so one album never kills the run
                        errors += 1
                        log.error(
                            "runner.album_unexpected_error",
                            album_id=album.id,
                            error=str(exc),
                            exc_info=True,
                        )
                    finally:
                        bar()
        except KeyboardInterrupt:
            log.warning("runner.interrupted", success=success, errors=errors)
            raise

        log.info("runner.complete", success=success, errors=errors)
        return success, errors
