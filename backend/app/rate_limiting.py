"""In-memory sliding-window rate limiting for the login endpoint.

This protects the *endpoint* from brute-force/DoS flooding (per client IP).
Account-level protection (consecutive failures -> temporary lock) is
persisted in the ``users`` table by the auth router and works across
processes/restarts.

The limiter is deliberately process-local: phase 2 runs a single API process
(see docs/ARCHITECTURE.md). It is thread-safe (TestClient/uvicorn workers use
threads) and its state can be reset in tests via :func:`reset`.
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a rate-limit check."""

    allowed: bool
    retry_after_seconds: int


class SlidingWindowRateLimiter:
    """Sliding-window counter keyed by an arbitrary string (e.g. client IP)."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> RateLimitResult:
        """Register an attempt at ``now`` and decide whether it is allowed.

        Returns ``allowed=False`` with ``retry_after_seconds`` advising when
        the oldest attempt will leave the window.
        """
        current = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._events[key]
            cutoff = current - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                oldest = bucket[0]
                retry_after = max(1, int(oldest + self._window - current) + 1)
                return RateLimitResult(allowed=False, retry_after_seconds=retry_after)
            bucket.append(current)
            return RateLimitResult(allowed=True, retry_after_seconds=0)

    def reset(self) -> None:
        """Clear all counters (used between tests)."""
        with self._lock:
            self._events.clear()
