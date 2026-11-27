"""Inflation Forge: history + inflation + seasonal demand + regional funding inflow → a forecast
range and a book-now-or-wait signal. Forecasts are ranges, not quotes."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from app.domain.money import Money
from app.suppliers.simulator.adapter import lead_factor
from app.suppliers.simulator.catalog import PRICE_INDEX, Destination

FARE_INFLATION = 0.045
STAY_INFLATION = 0.045
OTHER_INFLATION = 0.04
FUNDING_PREMIUM = 0.014  # extra hotel growth per year at funding_index = 1
SIGMA_YEAR = 0.06
Z90 = 1.2816


def forecast(*, base_total: Money, shares: dict[str, float], dest: Destination, travel_date: date, today: date,
             days_to_departure: int) -> dict[str, Any]:
    fs, hs = shares.get("flights", 0.4), shares.get("hotel", 0.4)
    os_ = max(0.0, 1 - fs - hs)
    funding = FUNDING_PREMIUM * dest.funding_index
    growth = fs * FARE_INFLATION + hs * (STAY_INFLATION + funding) + os_ * OTHER_INFLATION
    year = today.year
    ref = PRICE_INDEX.get(year, PRICE_INDEX[max(PRICE_INDEX)])
    base = base_total.major
    series = []
    for y, idx in sorted(PRICE_INDEX.items()):
        if y > year:
            break
        series.append({"year": y, "p50": round(base * idx / ref), "kind": "history"})
    for h in range(1, 5):
        p50 = base * (1 + growth) ** h
        band = math.exp(Z90 * SIGMA_YEAR * math.sqrt(h))
        series.append({"year": year + h, "p50": round(p50), "p10": round(p50 / band), "p90": round(p50 * band),
                       "kind": "forecast"})

    month = travel_date.month
    seasonal = dest.demand[month - 1] / (sum(dest.demand) / 12) - 1
    now_f = lead_factor(days_to_departure)
    wait_days = 14
    later_f = lead_factor(max(0, days_to_departure - wait_days))
    wait_change = (later_f / now_f - 1) * fs  # only fares follow the lead-time curve
    sigma_short = 0.03 + 0.04 * max(0.0, seasonal)
    buffer = Money.of(round(base * (math.exp(Z90 * sigma_short) - 1) / 100) * 100)
    if days_to_departure <= 21 or wait_change > 0.01:
        signal, headline = "book_now", "Book now"
        reason = (f"Fares typically rise {wait_change * 100:.1f}% of the trip cost if you wait {wait_days} days"
                  if wait_change > 0 else "Departure is close; fares rarely fall from here")
    else:
        signal, headline = "wait", "Wait and watch"
        reason = f"Waiting {wait_days} days is expected to change the total by {wait_change * 100:+.1f}%"
    curve = []
    for d in range(min(120, max(days_to_departure + 14, 45)), -1, -5):
        curve.append({"days_before": d, "index": round(lead_factor(d) / now_f * 100, 1)})
    return {
        "base_total": base_total,
        "series": series,
        "growth_pct": round(growth * 100, 2),
        "booking_curve": curve,
        "days_to_departure": days_to_departure,
        "signal": {"code": signal, "headline": headline, "reason": reason, "buffer": buffer},
        "drivers": [
            {"key": "inflation", "label": "Inflation", "value": f"{FARE_INFLATION * 100:.1f}% / yr on fares and stays",
             "effect_pct": round((fs * FARE_INFLATION + hs * STAY_INFLATION) * 100, 2)},
            {"key": "seasonal", "label": "Seasonal demand",
             "value": f"{travel_date:%B} is {abs(seasonal) * 100:.0f}% {'above' if seasonal >= 0 else 'below'} the yearly average in {dest.city}",
             "effect_pct": round(seasonal * 100, 1)},
            {"key": "funding", "label": "Regional funding inflow",
             "value": f"{dest.city} business-travel index {dest.funding_index:.2f} → hotel rates +{funding * 100:.2f}% / yr",
             "effect_pct": round(hs * funding * 100, 2)},
        ],
        "disclaimer": "Forecasts are ranges, not quotes. Illustrative data. Prices at booking always come from the supplier.",
    }
