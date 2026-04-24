from __future__ import annotations

import threading
import time


class TokenBucket:
    """A token bucket rate limiter."""

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_fill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, amount: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            # Refill the bucket
            self.tokens += (now - self.last_fill) * self.rate
            if self.tokens > self.capacity:
                self.tokens = self.capacity
            self.last_fill = now

            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False

    def wait_and_consume(self, amount: float = 1.0) -> None:
        while True:
            if self.consume(amount):
                return
            # Sleep a bit before trying again
            time.sleep(1.0 / self.rate)
