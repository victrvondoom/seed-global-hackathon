from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.domain.money import Money
from app.domain.types import Place

ROAD_FACTOR = 1.32


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class LegEstimate:
    mode: str
    distance_km: float
    duration_min: int
    fare: Money
    source: str = "estimate"


def estimate_leg(a: Place, b: Place, *, mode: str | None = None, at: datetime | None = None,
                 fare_scale: float = 1.0) -> LegEstimate:
    """Deterministic ETA estimate. An ETA is an estimate, not a supplier quote."""
    km = haversine_km(a.lat, a.lng, b.lat, b.lng) * ROAD_FACTOR
    mode = mode or ("walk" if km <= 1.2 else "auto" if km <= 9 else "car")
    speed = {"walk": 4.5, "auto": 17.0, "car": 30.0 if km < 25 else 42.0, "boat": 12.0}[mode]
    peak = 1.25 if at and mode in ("auto", "car") and (8 <= at.hour < 10 or 17 <= at.hour < 20) else 1.0
    minutes = max(3, round(km / speed * 60 * peak + (0 if mode == "walk" else 4)))
    if mode == "walk":
        fare = 0.0
    elif mode == "auto":
        fare = 30 + 16 * km
    else:
        fare = 150 + 22 * km
    return LegEstimate(mode, round(km, 1), minutes, Money.of(round(fare * fare_scale / 10) * 10))
