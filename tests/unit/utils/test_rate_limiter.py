from __future__ import annotations

import time

import pytest

from tagger.utils.rate_limiter import TokenBucket


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
