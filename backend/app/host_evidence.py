"""Phase 14: серверное хранилище отчётов движка о Windows-хосте.

Backend не имеет доступа к Docker/Windows-хосту (никакого docker.sock и
удалённого исполнения команд). Единственный источник host-фактов —
добровольный отчёт движка по loopback с машинным токеном:

* движок сам присылает уже отредактированный набор фактов
  (``POST /api/updates/engine-host-report``);
* хранилище держит последний отчёт в памяти процесса с временем получения и
  честно сообщает его возраст; устаревший отчёт не выдаётся за свежий;
* сохраняются ТОЛЬКО поля известной схемы (``extra="ignore"`` на входе) —
  случайный секрет из окружения движка в диагностику не попадёт.

Данные не персистентны: после перезапуска backend host-проверки readiness
честно деградируют до ``warning`` до следующего отчёта движка.
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime

from app.utils import ensure_aware, utc_now

HOST_EVIDENCE_MAX_AGE_SECONDS = 24 * 3600


class PilotHostEvidenceStore:
    """Потокобезопасное хранилище последнего отчёта движка о хосте."""

    def __init__(self, *, max_age_seconds: int = HOST_EVIDENCE_MAX_AGE_SECONDS) -> None:
        self._lock = threading.Lock()
        self._payload: dict | None = None
        self._received_at: datetime | None = None
        self.max_age_seconds = max_age_seconds

    def record(self, payload: dict, *, now: datetime | None = None) -> datetime:
        received = ensure_aware(now or utc_now())
        with self._lock:
            self._payload = copy.deepcopy(payload)
            self._received_at = received
        return received

    def latest(self, *, now: datetime | None = None) -> tuple[dict | None, float | None]:
        """``(payload, age_seconds)``; ``(None, None)`` — отчёта ещё не было."""
        current = ensure_aware(now or utc_now())
        with self._lock:
            if self._payload is None or self._received_at is None:
                return None, None
            age = (current - self._received_at).total_seconds()
            return copy.deepcopy(self._payload), max(age, 0.0)

    def clear(self) -> None:
        with self._lock:
            self._payload = None
            self._received_at = None
