"""Route providers. Estimates by default; Google Maps Routes when a key is configured.
Either way the output is an ETA estimate, never a quote or proof of bookability."""
from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Protocol

import httpx

from app.domain.geo import LegEstimate, estimate_leg
from app.domain.money import Money
from app.domain.types import Place

log = logging.getLogger(__name__)


class RoutesProvider(Protocol):
    name: str

    def leg(self, a: Place, b: Place, *, mode: str | None = None, at: datetime | None = None,
            fare_scale: float = 1.0) -> LegEstimate: ...


class EstimatorRoutes:
    name = "estimate"

    def leg(self, a: Place, b: Place, *, mode: str | None = None, at: datetime | None = None,
            fare_scale: float = 1.0) -> LegEstimate:
        return estimate_leg(a, b, mode=mode, at=at, fare_scale=fare_scale)


class GoogleRoutes:
    """Google Maps Routes API (computeRoutes) for drive legs; falls back to the estimator."""

    name = "google_routes"
    URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.Client(timeout=6.0)
        self._fallback = EstimatorRoutes()
        self._cached = lru_cache(maxsize=2048)(self._compute)

    def _compute(self, a: tuple[float, float], b: tuple[float, float], travel_mode: str) -> tuple[float, int] | None:
        body = {
            "origin": {"location": {"latLng": {"latitude": a[0], "longitude": a[1]}}},
            "destination": {"location": {"latLng": {"latitude": b[0], "longitude": b[1]}}},
            "travelMode": travel_mode,
        }
        if travel_mode == "DRIVE":
            body["routingPreference"] = "TRAFFIC_UNAWARE"
        try:
            r = self._client.post(self.URL, json=body, headers={
                "X-Goog-Api-Key": self._key, "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"})
            r.raise_for_status()
            route = r.json()["routes"][0]
            return route["distanceMeters"] / 1000, max(1, round(int(str(route["duration"]).rstrip("s")) / 60))
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            log.warning("google routes failed, using estimate: %s", e)
            return None

    def leg(self, a: Place, b: Place, *, mode: str | None = None, at: datetime | None = None,
            fare_scale: float = 1.0) -> LegEstimate:
        est = self._fallback.leg(a, b, mode=mode, at=at, fare_scale=fare_scale)
        travel_mode = "WALK" if est.mode == "walk" else "TWO_WHEELER" if est.mode == "auto" else "DRIVE"
        got = self._cached((round(a.lat, 4), round(a.lng, 4)), (round(b.lat, 4), round(b.lng, 4)), travel_mode)
        if not got:
            return est
        km, minutes = got
        fare = 0.0 if est.mode == "walk" else (30 + 16 * km if est.mode == "auto" else 150 + 22 * km)
        return LegEstimate(est.mode, round(km, 1), minutes, Money.of(round(fare * fare_scale / 10) * 10), "google_routes")
