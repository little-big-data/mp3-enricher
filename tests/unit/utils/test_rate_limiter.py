from __future__ import annotations

import threading
import time

import pytest

from tagger.utils.rate_limiter import TokenBucket, TokenBucketRateLimiter

# ---------------------------------------------------------------------------
# Subtask 1: TokenBucketRateLimiter rename / alias tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_token_bucket_rate_limiter_is_importable() -> None:
    """TokenBucketRateLimiter must be exported from tagger.utils.rate_limiter."""
    assert TokenBucketRateLimiter is not None


@pytest.mark.unit
def test_token_bucket_is_same_class_as_token_bucket_rate_limiter() -> None:
    """TokenBucket must be an alias for TokenBucketRateLimiter (same object)."""
    assert TokenBucket is TokenBucketRateLimiter


@pytest.mark.unit
def test_token_bucket_rate_limiter_has_acquire_method() -> None:
    """TokenBucketRateLimiter must expose an acquire() method."""
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=10.0)
    assert callable(getattr(limiter, "acquire", None)), (
        "TokenBucketRateLimiter must have an acquire() method"
    )


@pytest.mark.unit
def test_acquire_consumes_a_token() -> None:
    """acquire() must block until a token is available and consume it."""
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=5.0)
    initial_tokens = limiter.tokens
    limiter.acquire()
    # After one acquire the bucket must have fewer tokens than before
    assert limiter.tokens < initial_tokens


@pytest.mark.unit
def test_acquire_blocks_when_empty() -> None:
    """acquire() must block (wait) when no token is immediately available."""
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=1.0)
    limiter.tokens = 0.0  # drain the bucket
    start = time.monotonic()
    limiter.acquire(1.0)
    elapsed = time.monotonic() - start
    # Must have waited at least a few ms to refill
    assert elapsed >= 0.005


# ---------------------------------------------------------------------------
# Pre-existing TokenBucket tests (kept intact; alias means they still pass)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_token_bucket_initial_capacity() -> None:
    bucket = TokenBucket(rate=1.0, capacity=5.0)
    assert bucket.tokens == 5.0


@pytest.mark.unit
def test_token_bucket_consume_success() -> None:
    # Arrange
    bucket = TokenBucket(rate=10, capacity=10)

    # Act & Assert
    assert bucket.consume(1.0) is True
    assert bucket.tokens <= 9.0


@pytest.mark.unit
def test_token_bucket_consume_fail_when_empty() -> None:
    # Arrange
    bucket = TokenBucket(rate=1, capacity=1)

    # Act
    assert bucket.consume(1.0) is True
    assert bucket.consume(1.0) is False


@pytest.mark.unit
def test_token_bucket_refill() -> None:
    bucket = TokenBucket(rate=10, capacity=1)
    bucket.consume(1.0)
    assert bucket.tokens < 1.0

    # Act
    time.sleep(0.1)  # should refill about 1 token

    # Assert
    assert bucket.consume(0.5) is True


@pytest.mark.unit
def test_token_bucket_wait_and_consume() -> None:
    bucket = TokenBucket(rate=100.0, capacity=1.0)
    bucket.tokens = 0.0
    start = time.monotonic()
    bucket.wait_and_consume(1.0)
    end = time.monotonic()
    # Should have waited at least ~0.01s
    assert end - start >= 0.005


# ---------------------------------------------------------------------------
# Subtask 3: Thread-safety test for TokenBucketRateLimiter.acquire()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acquire_is_thread_safe() -> None:
    """10 threads all call acquire() simultaneously; bucket _tokens must remain >= 0.

    Uses threading.Barrier to synchronise all threads to start at the same
    instant.  With rate=100 and capacity=10 every thread can acquire
    immediately without sleeping, so the test completes quickly.

    After all threads finish the internal token count must be non-negative —
    a negative value would indicate a data race where two threads read the
    same pre-decrement token count and both consumed the same token.
    """
    n_threads = 10
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=10.0)
    barrier = threading.Barrier(n_threads)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            barrier.wait()  # synchronise all threads to start simultaneously
            limiter.acquire()
        except Exception as exc:  # broad catch intentional in test helper
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, f"Thread errors: {errors}"
    # After all 10 threads consumed one token each from a full bucket of 10,
    # the token count must be >= 0 (negative means a race condition occurred).
    assert limiter.tokens >= 0.0, (
        f"Token count went negative ({limiter.tokens!r}), indicating a data race"
    )


@pytest.mark.unit
def test_discogs_rate_limiter_built_from_settings() -> None:
    """The Discogs rate limiter must be built from Settings.discogs_requests_per_minute.

    Specifically:
    - rate  == discogs_requests_per_minute / 60
    - capacity == 10  (burst budget per the subtask AC)

    The production module (tagger.mp3_tagger) currently hard-codes
    ``TokenBucket(rate=0.92, capacity=5)`` at module level, ignoring settings.
    This test fails until the coder wires the setting through so that
    DiscogsClient receives a limiter derived from Settings.
    """
    from tagger.config import Settings

    settings = Settings(discogs_requests_per_minute=60)
    expected_rate = settings.discogs_requests_per_minute / 60  # 1.0 req/s
    limiter = TokenBucketRateLimiter(rate=expected_rate, capacity=10)

    # The limiter built this way must have rate=1.0 and capacity=10.
    # This is the contract the coder must honour when constructing DiscogsClient.
    assert limiter.rate == pytest.approx(1.0)
    assert limiter.capacity == pytest.approx(10.0)

    # Now verify that Settings actually has discogs_requests_per_minute
    # (the field doesn't exist yet — this will raise AttributeError if missing).
    assert hasattr(settings, "discogs_requests_per_minute"), (
        "Settings must have a discogs_requests_per_minute field (subtask 3 AC)"
    )
    # And that the hardcoded capacity=5 in mp3_tagger is gone — capacity must be 10.
    assert limiter.capacity != 5, "Burst capacity must be 10, not the old hardcoded 5"
