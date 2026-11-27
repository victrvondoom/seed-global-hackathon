"""Deterministic checks: budget, currency, timing, quote validity, consent and duplicates.
Pure functions over plain data so every rule is unit-testable."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from app.domain.intent import TripIntent
from app.domain.money import CurrencyMismatch, Money
from app.domain.types import ItineraryItem, RuleCheck

EXIT_BUFFER_DOMESTIC = 20
EXIT_BUFFER_INTL = 45
AIRPORT_BEFORE_DOMESTIC = 90
AIRPORT_BEFORE_INTL = 180


def budget_in_inr(budget: Money | None) -> tuple[Money | None, str | None]:
    if budget is None:
        return None, None
    if budget.currency == "INR":
        return budget, None
    try:
        conv = budget.to_inr_indicative()
    except CurrencyMismatch:
        return None, f"No indicative rate for {budget.currency}; budget not compared"
    return conv, f"Budget {budget.format()} compared at an indicative rate as {conv.format()}"


def check_budget(total: Money, budget: Money | None) -> RuleCheck:
    conv, note = budget_in_inr(budget)
    if conv is None:
        return RuleCheck(code="budget.fit", status="warn" if note else "pass",
                         title="No budget set" if not note else "Budget not compared", detail=note or "Add a budget to filter plans")
    delta = conv - total
    suffix = f" ({note})" if note else ""
    if delta.amount_minor >= 0:
        return RuleCheck(code="budget.fit", status="pass", title="Within budget",
                         detail=f"{delta.format()} below your {conv.format()} budget{suffix}")
    over = -delta
    status = "warn" if over.amount_minor <= conv.amount_minor * 0.05 else "block"
    return RuleCheck(code="budget.fit", status=status, title="Over budget",
                     detail=f"{over.format()} over your {conv.format()} budget{suffix}")


def check_currency(amounts: Iterable[Money], charge_currency: str = "INR") -> RuleCheck:
    bad = sorted({m.currency for m in amounts if m.currency != charge_currency})
    if bad:
        return RuleCheck(code="currency.consistent", status="block", title="Mixed currencies",
                         detail=f"Components priced in {', '.join(bad)}; charge currency is {charge_currency}")
    return RuleCheck(code="currency.consistent", status="pass", title="One charge currency",
                     detail=f"Every component is priced in {charge_currency}")


def check_dates(intent: TripIntent, today: date) -> list[RuleCheck]:
    out = []
    if intent.start_date and intent.start_date <= today:
        out.append(RuleCheck(code="dates.future", status="block", title="Start date has passed",
                             detail=f"{intent.start_date:%d %b %Y} is not after today ({today:%d %b})"))
    if intent.start_date and intent.start_date > today + timedelta(days=330):
        out.append(RuleCheck(code="dates.window", status="block", title="Too far ahead",
                             detail="Suppliers open inventory up to 330 days ahead"))
    if intent.origin and intent.origin == intent.destination:
        out.append(RuleCheck(code="route.distinct", status="block", title="Same origin and destination",
                             detail="Pick a different starting city or destination"))
    return out


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def check_itinerary_timing(items: list[ItineraryItem], *, international: bool) -> list[RuleCheck]:
    """Transfer buffers and airport arrival time, computed from the itinerary itself."""
    out: list[RuleCheck] = []
    by_comp = {i.component: i for i in items if i.component}
    f_out, pick = by_comp.get("flight_out"), by_comp.get("transfer_in")
    exit_buf = EXIT_BUFFER_INTL if international else EXIT_BUFFER_DOMESTIC
    if f_out and pick and f_out.end:
        gap = (_dt(pick.start) - _dt(f_out.end)).total_seconds() / 60
        ok = gap >= exit_buf
        out.append(RuleCheck(code="timing.pickup_buffer", status="pass" if ok else "block",
                             title="Pickup after landing" if ok else "Pickup too soon after landing",
                             detail=f"{int(gap)} min between landing and pickup (needs {exit_buf})"))
    f_ret, drop = by_comp.get("flight_ret"), by_comp.get("transfer_out")
    need = AIRPORT_BEFORE_INTL if international else AIRPORT_BEFORE_DOMESTIC
    if f_ret and drop and drop.end:
        gap = (_dt(f_ret.start) - _dt(drop.end)).total_seconds() / 60
        ok = gap >= need
        out.append(RuleCheck(code="timing.airport_buffer", status="pass" if ok else "block",
                             title="Airport arrival in time" if ok else "Airport arrival too late",
                             detail=f"At the airport {int(gap)} min before departure (needs {need})"))
    acts = sorted((i for i in items if i.kind == "activity"), key=lambda i: i.start)
    for a, b in zip(acts, acts[1:], strict=False):
        if a.end and _dt(a.end) > _dt(b.start):
            out.append(RuleCheck(code="timing.overlap", status="block", title="Overlapping experiences",
                                 detail=f"{a.title} overlaps {b.title}"))
    return out


def check_flight_constraints(intent: TripIntent, out_depart: datetime, ret_depart: datetime, stops: int) -> list[RuleCheck]:
    c = intent.constraints
    res = []
    if c.depart_after:
        ok = out_depart.time() >= c.depart_after
        res.append(RuleCheck(code="constraint.depart_after", status="pass" if ok else "block",
                             title="No early flights" if ok else "Departs too early",
                             detail=f"Departs {out_depart:%H:%M}; you asked for after {c.depart_after:%H:%M}"))
    if c.depart_before:
        ok = out_depart.time() <= c.depart_before and ret_depart.time() <= c.depart_before
        res.append(RuleCheck(code="constraint.depart_before", status="pass" if ok else "block",
                             title="No late flights" if ok else "Flight too late", detail=f"Latest departure {c.depart_before:%H:%M}"))
    if c.max_stops is not None:
        ok = stops <= c.max_stops
        res.append(RuleCheck(code="constraint.stops", status="pass" if ok else "block",
                             title="Direct flights" if ok else "Has stops", detail=f"{stops} stop(s)"))
    return res


def check_quote_validity(valid_until: Iterable[datetime | None], now: datetime) -> RuleCheck:
    vals = list(valid_until)
    if not vals or any(v is None for v in vals):
        return RuleCheck(code="quote.firm", status="block", title="Prices not rechecked",
                         detail="Recheck with suppliers before approving")
    soonest = min(v for v in vals if v)
    if soonest <= now:
        return RuleCheck(code="quote.valid", status="block", title="Quotes expired",
                         detail="Prices must be rechecked with suppliers")
    mins = int((soonest - now).total_seconds() // 60)
    return RuleCheck(code="quote.valid", status="pass", title="Quotes are current",
                     detail=f"Held by suppliers for {mins} more min")


def blocking(checks: Iterable[RuleCheck]) -> list[RuleCheck]:
    return [c for c in checks if c.status == "block"]
