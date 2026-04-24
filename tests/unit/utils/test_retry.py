from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from tagger.exceptions import RateLimitError
from tagger.utils.retry import retry_on_rate_limit


@pytest.mark.unit
def test_retry_on_rate_limit_success() -> None:
    # Arrange
    mock_func = Mock(return_value="success")
    decorated = retry_on_rate_limit(max_attempts=3)(mock_func)

    # Act
    result = decorated()

    # Assert
    assert result == "success"
    assert mock_func.call_count == 1


@pytest.mark.unit
def test_retry_on_rate_limit_fails_after_max_attempts() -> None:
    # Arrange
    mock_func = Mock(side_effect=RateLimitError(service="test"))
    decorated = retry_on_rate_limit(max_attempts=2)(mock_func)

    # Act & Assert
    with pytest.raises(RateLimitError):
        decorated()

    assert mock_func.call_count == 2


@pytest.mark.unit
def test_retry_waits_retry_after_seconds_when_set() -> None:
    """When RateLimitError carries retry_after, the decorator waits that many seconds."""
    slept: list[float] = []

    with patch("time.sleep", side_effect=lambda s: slept.append(s)):
        mock_func = Mock(
            side_effect=[
                RateLimitError(service="discogs", retry_after=30),
                "ok",
            ]
        )
        decorated = retry_on_rate_limit(max_attempts=3)(mock_func)
        result = decorated()

    assert result == "ok"
    assert mock_func.call_count == 2
    # The wait between the first and second attempt must equal retry_after
    assert slept[0] == 30.0


@pytest.mark.unit
def test_retry_uses_exponential_backoff_when_no_retry_after() -> None:
    """When retry_after is None, the decorator falls back to exponential back-off."""
    slept: list[float] = []

    with patch("time.sleep", side_effect=lambda s: slept.append(s)):
        mock_func = Mock(
            side_effect=[
                RateLimitError(service="discogs"),  # no retry_after
                "ok",
            ]
        )
        decorated = retry_on_rate_limit(max_attempts=3)(mock_func)
        result = decorated()

    assert result == "ok"
    assert mock_func.call_count == 2
    # Exponential fallback — wait should be at least the configured min (5s)
    assert slept[0] >= 5.0
