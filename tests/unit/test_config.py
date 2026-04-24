from __future__ import annotations

from pathlib import Path

import pytest

from tagger.config import Settings


def test_settings_default() -> None:
    # Set dummy env for required fields
    # library_root is Path and doesn't have a default.

    settings = Settings(library_root="/tmp/music")
    assert settings.library_root == Path("/tmp/music")
    assert settings.workers == 4
    assert settings.discogs_fuzzy_threshold == 85


def test_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAGGER_WORKERS", "8")
    monkeypatch.setenv("TAGGER_LIBRARY_ROOT", "/env/music")

    settings = Settings(library_root="/env/music", workers=8)
    assert settings.workers == 8
    assert settings.library_root == Path("/env/music")
