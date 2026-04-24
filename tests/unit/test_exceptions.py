from __future__ import annotations

import pytest

from tagger.exceptions import RateLimitError


@pytest.mark.unit
def test_rate_limit_error_message() -> None:
    # Arrange & Act
    err = RateLimitError(service="discogs", retry_after=30)

    # Assert
    assert err.service == "discogs"
    assert err.retry_after == 30
    assert "discogs" in str(err)
    assert "30" in str(err)


@pytest.mark.unit
def test_rate_limit_error_without_retry_after() -> None:
    # Arrange & Act
    err = RateLimitError(service="llm")

    # Assert
    assert err.service == "llm"
    assert err.retry_after is None
    assert "llm" in str(err)
