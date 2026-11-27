"""Simulation clock. Business time (quotes, approvals, lead time) reads from here so demos and
tests can freeze or advance it. Security time (JWT) always uses the real clock."""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class Clock:
    def __init__(self, fixed: datetime | None = None) -> None:
        self._lock = threading.Lock()
        self._fixed = fixed.astimezone(timezone.utc) if fixed else None
        self._offset = timedelta()

    def now(self) -> datetime:
        with self._lock:
            base = self._fixed or datetime.now(timezone.utc)
            return base + self._offset

    def today(self) -> date:
        return self.now().astimezone(IST).date()

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._offset += timedelta(seconds=seconds)
