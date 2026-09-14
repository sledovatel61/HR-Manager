"""Server-owned host facts for the pilot readiness check (Phase 14).

The Windows engine is the ONLY component allowed to observe the host (Docker
daemon, published ports, disk space, state-directory ACL). The backend never
runs host commands and never exposes a command surface: the engine pushes a
strictly validated, redacted fact report over the existing loopback + machine
token contract (``POST /api/updates/engine-facts``).

The report schema is closed (unknown fields are rejected with 422 before any
storage happens) and every field is a bounded enum/bool/int — there is no free
text, so no secret, path or PII can be smuggled through it. The store keeps
only the latest report plus its timestamp, mirroring the in-memory
``update_state`` approach (no database migration; after a restart the facts
are honestly "unknown" until the engine watcher reports again).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from app.schemas import UpdateEngineFactsRequest
from app.utils import utc_now

# A fact report older than this is "stale": the engine watcher refreshes the
# report every few minutes, so a stale report means the watcher is not running
# (or the engine cannot observe the host). Stale facts degrade readiness
# checks to honest warnings instead of fabricated passes.
FACTS_FRESH_AFTER = timedelta(minutes=15)


class HostFactsStore:
    """Thread-safe single-slot store for the latest engine fact report."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._facts: UpdateEngineFactsRequest | None = None
        self._received_at: datetime | None = None

    def apply(self, facts: UpdateEngineFactsRequest) -> None:
        with self._lock:
            self._facts = facts
            self._received_at = utc_now()

    def snapshot(self) -> tuple[UpdateEngineFactsRequest | None, datetime | None]:
        """Return the latest report and its server-side receive time."""
        with self._lock:
            return self._facts, self._received_at

    def fresh(self, now: datetime | None = None) -> bool:
        """True when a report exists and it is younger than FACTS_FRESH_AFTER."""
        with self._lock:
            if self._facts is None or self._received_at is None:
                return False
            reference = now or utc_now()
            return (reference - self._received_at) <= FACTS_FRESH_AFTER
