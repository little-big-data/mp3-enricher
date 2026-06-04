from __future__ import annotations

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
