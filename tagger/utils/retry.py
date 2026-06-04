from __future__ import annotations

import random
from collections.abc import Callable  # noqa: TC003
from typing import Any

import structlog
import tenacity
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tagger.exceptions import RateLimitError, TransientAPIError

log = structlog.get_logger(__name__)

_fallback_wait = wait_exponential(multiplier=1, min=5, max=120)


def _discogs_wait(retry_state: tenacity.RetryCallState) -> float:
    """Return seconds to wait before the next attempt.

    Prefers the ``retry_after`` value carried by :class:`RateLimitError`
    (populated from the ``Retry-After`` response header).  Falls back to
    exponential back-off with full jitter when the header is absent.

    The ``Retry-After`` path is NOT jittered — the server's instruction is
    honoured exactly.  The exponential fallback applies full jitter
    (``random.uniform(0, computed_wait)``) to spread parallel retries.
    """
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome is not None else None
    if isinstance(exc, RateLimitError) and exc.retry_after is not None:
        return float(exc.retry_after)
    computed = _fallback_wait(retry_state)
    return random.uniform(0, computed)


def _log_before_sleep(retry_state: tenacity.RetryCallState) -> None:
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome is not None else None
    next_action = retry_state.next_action
    wait_secs = next_action.sleep if next_action is not None else 0.0
    log.warning(
        "discogs.retrying",
        attempt=retry_state.attempt_number,
        wait_seconds=wait_secs,
        error=str(exc),
    )


def retry_on_rate_limit(max_attempts: int = 8) -> Callable[[Any], Any]:
    """Decorator that retries on :class:`RateLimitError` and transient 5xx errors.

    Waits for the number of seconds specified in ``RateLimitError.retry_after``
    (from the ``Retry-After`` response header) when available; otherwise uses
    exponential back-off starting at 5 s and capped at 120 s.
    """
    return retry(
        retry=retry_if_exception_type((RateLimitError, TransientAPIError)),
        wait=_discogs_wait,
        stop=stop_after_attempt(max_attempts),
        before_sleep=_log_before_sleep,
        reraise=True,
    )
