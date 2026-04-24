from __future__ import annotations

from pathlib import Path

import httpx
import structlog

log = structlog.get_logger(__name__)


class ArtDownloader:
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            headers={"User-Agent": "MP3Enricher/0.1.0 +https://github.com/jschloman/mp3-enricher"}
        )

    def download(self, url: str, album_id: int) -> Path | None:
        # Determine file extension from URL or default to .jpg
        ext = Path(url.split("?")[0]).suffix or ".jpg"
        # Discogs sometimes uses .jpeg, .png etc.
        if ext.lower() not in [".jpg", ".jpeg", ".png"]:
            ext = ".jpg"

        dest_path = self._cache_dir / f"{album_id}{ext}"

        if dest_path.exists():
            log.debug("art.cache_hit", album_id=album_id, path=str(dest_path))
            return dest_path

        log.info("art.download_start", album_id=album_id, url=url)
        try:
            response = self._client.get(url)
            response.raise_for_status()
            dest_path.write_bytes(response.content)
            log.info("art.download_success", album_id=album_id, path=str(dest_path))
            return dest_path
        except httpx.HTTPError as exc:
            log.error("art.download_failed", album_id=album_id, url=url, error=str(exc))
            return None
