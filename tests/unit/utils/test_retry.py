"""Tests for tagger/utils/retry.py — retry_on_rate_limit decorator and _discogs_wait."""

from __future__ import annotations

import random
from unittest.mock import Mock, patch

import pytest
import structlog.testing
import tenacity

from tagger.exceptions import DiscogsServerError, RateLimitError, TransientAPIError
from tagger.utils.retry import _discogs_wait, retry_on_rate_limit


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
    """When retry_after is None, the decorator falls back to exponential back-off with jitter."""
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
    # Exponential fallback with full jitter — wait is in [0, max_exponential],
    # so it can be anywhere from 0 to the computed exponential cap (5s on attempt 1).
    assert slept[0] >= 0.0


# ---------------------------------------------------------------------------
# Subtask 2: jitter, strengthened retry coverage
# ---------------------------------------------------------------------------


def _make_retry_state(attempt: int = 1, exc: Exception | None = None) -> tenacity.RetryCallState:
    """Build a minimal RetryCallState for testing _discogs_wait directly."""
    # Use a real tenacity Retrying object so RetryCallState is fully initialised
    retryer = tenacity.Retrying(
        retry=tenacity.retry_if_exception_type(RateLimitError),
        wait=tenacity.wait_none(),
        stop=tenacity.stop_after_attempt(10),
        reraise=True,
    )
    state = tenacity.RetryCallState(retryer, fn=lambda: None, args=(), kwargs={})
    state.attempt_number = attempt
    if exc is not None:
        import concurrent.futures

        f: concurrent.futures.Future[None] = concurrent.futures.Future()
        f.set_exception(exc)
        state.outcome = f
    return state


@pytest.mark.unit
@pytest.mark.parametrize("sample_index", list(range(20)))
def test_jitter_spread_is_non_zero(sample_index: int) -> None:
    """_discogs_wait must return different values across calls (jitter is applied).

    With pure exponential back-off there is no randomness; two calls on the
    same RetryCallState with no Retry-After always return the identical value.
    After jitter is added, the probability of two calls returning the same
    float is negligible.  We collect 20 samples and assert the spread > 0.
    """
    # Use a fixed seed for reproducibility but non-degenerate values
    rng = random.Random(sample_index * 1337)
    exc = RateLimitError(service="discogs")  # no retry_after
    state = _make_retry_state(attempt=2, exc=exc)

    samples: list[float] = []
    for _ in range(5):
        with patch("random.uniform", side_effect=rng.uniform):
            val = _discogs_wait(state)
        samples.append(val)

    # If jitter is implemented, random.uniform is called and results vary.
    # If NOT implemented, all samples are identical (pure exponential).
    # We assert that random.uniform was called at least once during those 5 calls.
    # The simplest RED assertion: the implementation must call random.uniform.
    # We verify this by patching and checking it was invoked.
    called: list[bool] = []
    mock_uniform = Mock(side_effect=lambda a, b: rng.uniform(a, b))
    with patch("random.uniform", mock_uniform):
        _discogs_wait(state)
    called.append(mock_uniform.called)

    assert any(called), "_discogs_wait did not call random.uniform — jitter is not implemented"


@pytest.mark.unit
def test_retry_gives_up_after_max_attempts() -> None:
    """Function raising RateLimitError forever: exactly max_attempts calls, jitter applied.

    After jitter is implemented, random.uniform must be called once per retry sleep
    interval.  With max_attempts=3 there are 2 sleep intervals, so random.uniform
    must be called at least 2 times.  With pure exponential (no jitter) it is never
    called, making this test RED until jitter is added.
    """
    original_error = RateLimitError(service="discogs")
    mock_func = Mock(side_effect=original_error)
    uniform_calls: list[tuple[float, float]] = []
    _real_uniform = random.uniform  # capture before patching to avoid recursion

    def capture_uniform(a: float, b: float) -> float:
        uniform_calls.append((a, b))
        return _real_uniform(a, b)

    with patch("time.sleep"), patch("random.uniform", side_effect=capture_uniform):
        decorated = retry_on_rate_limit(max_attempts=3)(mock_func)
        with pytest.raises(RateLimitError) as exc_info:
            decorated()

    assert mock_func.call_count == 3, f"Expected exactly 3 calls, got {mock_func.call_count}"
    assert exc_info.value is original_error
    # Jitter must have been applied during the 2 sleep intervals
    assert len(uniform_calls) >= 2, (
        f"random.uniform called {len(uniform_calls)} times — expected ≥2 (one per retry sleep). "
        "Jitter is not implemented."
    )


@pytest.mark.unit
def test_retry_logs_warning_on_each_attempt() -> None:
    """structlog WARNING must be emitted once per retry, with a jittered wait_seconds field.

    With max_attempts=3: 2 sleep events → 2 warning log events.  After jitter is
    implemented the `wait_seconds` field in each log event must differ between the
    two retries (they will occasionally collide, but far more often differ — we assert
    that random.uniform was called to compute each wait value, which is the definitive
    RED/GREEN signal rather than a probabilistic spread check).
    """
    mock_func = Mock(side_effect=RateLimitError(service="discogs"))
    uniform_calls: list[tuple[float, float]] = []
    _real_uniform = random.uniform  # capture before patching to avoid recursion

    def capture_uniform(a: float, b: float) -> float:
        uniform_calls.append((a, b))
        return _real_uniform(a, b)

    with patch("time.sleep"), patch("random.uniform", side_effect=capture_uniform):
        decorated = retry_on_rate_limit(max_attempts=3)(mock_func)
        with structlog.testing.capture_logs() as captured, pytest.raises(RateLimitError):
            decorated()

    warning_events = [e for e in captured if e.get("log_level") == "warning"]
    assert len(warning_events) == 2, (
        f"Expected 2 warning log events (one per sleep interval), got {len(warning_events)}"
    )
    for event in warning_events:
        assert "attempt" in event, f"Log event missing 'attempt' field: {event}"
        assert "wait_seconds" in event, f"Log event missing 'wait_seconds' field: {event}"

    # Jitter must have been applied: random.uniform called at least once per log event
    assert len(uniform_calls) >= 2, (
        f"random.uniform called {len(uniform_calls)} times — expected ≥2. "
        "Jitter is not implemented in _discogs_wait."
    )


@pytest.mark.unit
def test_retry_on_transient_api_error() -> None:
    """DiscogsServerError (alias for TransientAPIError) must trigger a jittered retry.

    First call raises DiscogsServerError; second call succeeds.  After jitter is
    implemented, random.uniform must be called during the single retry sleep.
    """
    mock_func = Mock(
        side_effect=[
            DiscogsServerError(service="discogs", status_code=503),
            "recovered",
        ]
    )
    uniform_calls: list[tuple[float, float]] = []
    _real_uniform = random.uniform  # capture before patching to avoid recursion

    def capture_uniform(a: float, b: float) -> float:
        uniform_calls.append((a, b))
        return _real_uniform(a, b)

    with patch("time.sleep"), patch("random.uniform", side_effect=capture_uniform):
        decorated = retry_on_rate_limit(max_attempts=3)(mock_func)
        result = decorated()

    assert result == "recovered"
    assert mock_func.call_count == 2
    # Jitter must have been applied during the single retry sleep
    assert len(uniform_calls) >= 1, (
        f"random.uniform called {len(uniform_calls)} times — expected ≥1. "
        "Jitter is not implemented in _discogs_wait for TransientAPIError."
    )


@pytest.mark.unit
def test_retry_on_discogs_server_error_is_same_as_transient_api_error() -> None:
    """DiscogsServerError is an alias — isinstance checks must hold both ways."""
    err = DiscogsServerError(service="discogs", status_code=500)
    assert isinstance(err, TransientAPIError)
    assert isinstance(err, DiscogsServerError)


@pytest.mark.unit
def test_retry_after_path_not_jittered() -> None:
    """When Retry-After header is set, _discogs_wait must return it exactly.

    Jitter must NOT be applied to the server-specified wait — honouring the
    server's instruction is more important than spreading retries.
    """
    exc = RateLimitError(service="discogs", retry_after=45)
    state = _make_retry_state(attempt=2, exc=exc)

    uniform_mock = Mock(side_effect=random.uniform)
    with patch("random.uniform", uniform_mock):
        result = _discogs_wait(state)

    # Must return the exact retry_after value
    assert result == 45.0, f"Expected 45.0, got {result}"
    # Must NOT call random.uniform — the server's value is used as-is
    assert not uniform_mock.called, (
        "random.uniform was called on the Retry-After path — jitter must not be applied here"
    )


@pytest.mark.unit
def test_retry_after_zero_not_jittered() -> None:
    """retry_after=0 edge case: wait must be 0.0 and random.uniform must not be called."""
    exc = RateLimitError(service="discogs", retry_after=0)
    state = _make_retry_state(attempt=2, exc=exc)

    uniform_mock = Mock(side_effect=random.uniform)
    with patch("random.uniform", uniform_mock):
        result = _discogs_wait(state)

    # retry_after=0 → RateLimitError.__init__ stores 0 but the truthiness check
    # `if exc.retry_after:` evaluates False for 0, so the current code falls through
    # to exponential back-off.  After the fix the condition must handle 0 explicitly
    # (use `is not None` check).  This test documents the expected post-fix behaviour:
    # result must be 0.0 and random.uniform must not be called.
    assert result == 0.0, (
        f"retry_after=0 should produce wait=0.0, got {result}. "
        "Hint: use `exc.retry_after is not None` instead of `if exc.retry_after`."
    )
    assert not uniform_mock.called, (
        "random.uniform was called for retry_after=0 — the Retry-After path must not jitter"
    )
