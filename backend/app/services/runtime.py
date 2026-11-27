from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.providers import AIProvider, GeminiProvider, HeuristicProvider
from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import Database
from app.domain.routes import EstimatorRoutes, GoogleRoutes, RoutesProvider
from app.suppliers.simulator.adapter import SimulatorSupplier

log = logging.getLogger(__name__)


class EventBus:
    """In-process pub/sub for per-trip server-sent events. Safe to publish from worker threads."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, trip_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs[trip_id].add(q)
        return q

    def unsubscribe(self, trip_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subs[trip_id].discard(q)

    @staticmethod
    def _put(q: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        if q.full():
            q.get_nowait()
        q.put_nowait(event)

    def publish(self, trip_id: str, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subs.get(trip_id, ()))
        for q in subs:
            loop.call_soon_threadsafe(self._put, q, event)


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float) -> bool:
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.clock = Clock(settings.fixed_now)
        self.db = Database(settings.database_url)
        self.bus = EventBus()
        self.chat_limiter = RateLimiter(settings.chat_rate_per_minute)
        self.ai: AIProvider = (GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.gemini_timeout_seconds)
                               if settings.gemini_api_key else HeuristicProvider())
        self.routes: RoutesProvider = (GoogleRoutes(settings.google_maps_api_key) if settings.google_maps_api_key
                                       else EstimatorRoutes())

    def supplier(self, session: Session) -> SimulatorSupplier:
        return SimulatorSupplier(session, self.clock, secret=self.settings.secret_key, seed=self.settings.simulator_seed,
                                 ack_delay_s=self.settings.supplier_ack_delay_seconds)


@dataclass
class Ctx:
    rt: Runtime
    db: Session
    user_id: str | None = None
    _outbox: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def settings(self) -> Settings:
        return self.rt.settings

    @property
    def clock(self) -> Clock:
        return self.rt.clock

    @property
    def supplier(self) -> SimulatorSupplier:
        if not hasattr(self, "_supplier"):
            self._supplier = self.rt.supplier(self.db)
        return self._supplier

    def emit(self, trip_id: str, event: dict[str, Any]) -> None:
        self._outbox.append((trip_id, event))

    def commit(self) -> None:
        self.db.commit()
        out, self._outbox = self._outbox, []
        for trip_id, ev in out:
            self.rt.bus.publish(trip_id, ev)
