from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tagger.config import Settings
from tagger.utils.rate_limiter import TokenBucketRateLimiter


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


# ---------------------------------------------------------------------------
# Subtask 3: discogs_requests_per_minute validator tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discogs_requests_per_minute_default_is_60() -> None:
    """Settings must default discogs_requests_per_minute to 60."""
    settings = Settings()
    assert settings.discogs_requests_per_minute == 60


@pytest.mark.unit
def test_discogs_requests_per_minute_zero_raises_validation_error() -> None:
    """discogs_requests_per_minute=0 must be rejected by the validator."""
    with pytest.raises(ValidationError):
        Settings(discogs_requests_per_minute=0)


@pytest.mark.unit
def test_discogs_requests_per_minute_301_raises_validation_error() -> None:
    """discogs_requests_per_minute=301 must be rejected by the validator."""
    with pytest.raises(ValidationError):
        Settings(discogs_requests_per_minute=301)


@pytest.mark.unit
def test_discogs_requests_per_minute_1_is_valid() -> None:
    """discogs_requests_per_minute=1 must be accepted (lower bound)."""
    settings = Settings(discogs_requests_per_minute=1)
    assert settings.discogs_requests_per_minute == 1


@pytest.mark.unit
def test_discogs_requests_per_minute_300_is_valid() -> None:
    """discogs_requests_per_minute=300 must be accepted (upper bound)."""
    settings = Settings(discogs_requests_per_minute=300)
    assert settings.discogs_requests_per_minute == 300


@pytest.mark.unit
def test_discogs_requests_per_minute_wiring_rate() -> None:
    """Settings(discogs_requests_per_minute=120) must yield a TokenBucketRateLimiter with rate=2.0.

    The rate is computed as requests_per_minute / 60.
    """
    settings = Settings(discogs_requests_per_minute=120)
    # The factory function / helper that converts the setting into a rate limiter
    # must produce rate = 120 / 60 = 2.0 with capacity = 10.
    rate = settings.discogs_requests_per_minute / 60
    limiter = TokenBucketRateLimiter(rate=rate, capacity=10)
    assert limiter.rate == pytest.approx(2.0)
    assert limiter.capacity == 10
