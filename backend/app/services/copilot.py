"""Copilot turn: Chat → Validated intent → Read-tool results → Rules → Reply.
Asks one focused follow-up at a time; every number in a reply comes from tool results."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.ai import factguard
from app.core.errors import InvalidInput
from app.core.ids import new_id
from app.domain import rules
from app.domain.intent import IntentPatch, Travellers, TripIntent, affects_search, merge, missing_slots
from app.domain.money import Money
from app.domain.planner import quick_estimate
from app.models import ChatMessage, Trip
from app.services import ledger, trips
from app.services.runtime import Ctx
from app.suppliers.simulator.catalog import AIRPORTS, DESTINATIONS, METROS

SLOT_PROMPT = {
    "destination": "Where would you like to go?",
    "origin": "Where are you starting from?",
    "nights": "How long is the trip?",
    "travellers": "Who's travelling?",
    "start_date": "Which dates work?",
}


def _climate_score(dest, month: int) -> float:
    hi, rain = dest.climate[month - 1]
    t = 1.0 if 20 <= hi <= 33 else max(0.0, 1 - min(abs(hi - 20), abs(hi - 33)) / 12)
    r = 1.0 if rain < 60 else 0.6 if rain < 150 else 0.3 if rain < 300 else 0.1
    return t * 0.5 + r * 0.5


def suggest_destinations(ctx: Ctx, intent: TripIntent, month: int, limit: int = 3) -> list[dict[str, Any]]:
    scored = []
    for d in DESTINATIONS.values():
        if d.code == intent.origin:
            continue
        s = _climate_score(d, month) * 3
        overlap = set(intent.interests) & set(d.tags)
        s += 1.2 * len(overlap)
        if intent.origin and d.international:
            s -= 0.4
        scored.append((s, d, overlap))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _s, d, overlap in scored[:limit]:
        hi, rain = d.climate[month - 1]
        weather = f"{hi}°C · {'dry' if rain < 60 else 'some rain' if rain < 150 else 'wet season'}"
        price = None
        if intent.origin and intent.travellers and intent.nights:
            start = intent.start_date or _first_window(ctx.clock.today(), month)
            price = quick_estimate(intent, ctx.supplier, intent.origin, d.code, start, intent.nights)
        reason = d.tagline + (f" · matches {', '.join(sorted(overlap))}" if overlap else "")
        out.append({"label": d.city, "detail": weather, "value": {"code": d.code}, "reason": reason,
                    "price_from": price.model_dump() if price else None, "region": d.region})
    return out


def _first_window(today: date, month: int) -> date:
    year = today.year if month >= today.month else today.year + 1
    first = date(year, month, 1)
    return max(first, today + timedelta(days=7))


def suggest_dates(ctx: Ctx, intent: TripIntent) -> list[dict[str, Any]]:
    assert intent.origin and intent.destination and intent.nights
    today = ctx.clock.today()
    if intent.month:
        start = _first_window(today, intent.month)
        candidates = [start + timedelta(days=k) for k in range(0, 28, 2)
                      if (start + timedelta(days=k)).month == intent.month]
    else:
        candidates = [today + timedelta(days=k) for k in range(7, 50, 3)]
    priced = []
    for c in candidates:
        p = quick_estimate(intent, ctx.supplier, intent.origin, intent.destination, c, intent.nights)
        if p:
            priced.append((c, p))
    if not priced:
        return []
    cheapest = min(priced, key=lambda x: x[1].amount_minor)
    soonest = priced[0]
    weekend = next((x for x in priced if x[0].weekday() == 4), None)
    picks: dict[date, str] = {cheapest[0]: "lowest", soonest[0]: "soonest"}
    if weekend:
        picks.setdefault(weekend[0], "weekend")
    for c, _ in sorted(priced, key=lambda x: x[1].amount_minor):
        if len(picks) >= 4:
            break
        if all(abs((c - k).days) >= 5 for k in picks):
            picks[c] = "value"
    out = []
    for c, p in sorted(priced):
        if c not in picks:
            continue
        end = c + timedelta(days=intent.nights)
        out.append({"label": f"{c:%a %d %b} – {end:%a %d %b}", "detail": f"from {p.format()}", "tag": picks[c],
                    "value": {"start_date": c.isoformat(), "nights": intent.nights}, "price_from": p.model_dump()})
    return out


def _choices(ctx: Ctx, intent: TripIntent, slot: str) -> list[dict[str, Any]]:
    if slot == "destination":
        return suggest_destinations(ctx, intent, intent.month or (ctx.clock.today().month % 12) + 1, limit=4)
    if slot == "origin":
        return [{"label": AIRPORTS[c].city, "detail": c, "value": {"code": c}} for c in METROS]
    if slot == "nights":
        return [{"label": f"{n + 1} days", "detail": f"{n} nights", "value": {"nights": n}} for n in (2, 3, 4, 6)]
    if slot == "travellers":
        return [{"label": lbl, "value": {"adults": a, "children": c}} for lbl, a, c in
                (("Just me", 1, 0), ("2 adults", 2, 0), ("2 adults + 1 child", 2, 1), ("Family of 4", 2, 2), ("4 friends", 4, 0))]
    if slot == "start_date":
        return suggest_dates(ctx, intent)
    return []


def _patch_from_action(action: dict[str, Any]) -> IntentPatch:
    slot, v = action.get("slot"), action.get("value") or {}
    p = IntentPatch()
    try:
        if slot in ("destination", "origin"):
            code = str(v.get("code", "")).upper()
            if code not in AIRPORTS:
                raise ValueError(code)
            setattr(p, slot, code)
        elif slot == "nights":
            p.nights = int(v["nights"])
        elif slot == "travellers":
            p.travellers = Travellers(adults=int(v["adults"]), children=int(v.get("children", 0)))
        elif slot == "start_date":
            p.start_date = date.fromisoformat(v["start_date"])
            if v.get("nights"):
                p.nights = int(v["nights"])
        else:
            raise ValueError(slot)
    except (KeyError, ValueError, TypeError) as e:
        raise InvalidInput("chat.bad_action", "That choice could not be applied") from e
    return p


def _understood(intent: TripIntent, changed: list[str]) -> list[dict[str, str]]:
    out = []
    if "origin" in changed and intent.origin:
        out.append({"label": "From", "value": f"{AIRPORTS[intent.origin].city} ({intent.origin})"})
    if "destination" in changed and intent.destination:
        out.append({"label": "To", "value": f"{AIRPORTS[intent.destination].city} ({intent.destination})"})
    if "nights" in changed and intent.nights:
        out.append({"label": "Length", "value": f"{intent.nights + 1} days · {intent.nights} nights"})
    if "start_date" in changed and intent.start_date and intent.nights:
        out.append({"label": "Dates", "value": f"{intent.start_date:%d %b} – {intent.start_date + timedelta(days=intent.nights):%d %b}"})
    if "travellers" in changed and intent.travellers:
        out.append({"label": "Travellers", "value": intent.travellers.label()})
    if "budget" in changed and intent.budget:
        out.append({"label": "Budget", "value": f"under {intent.budget.format()}"})
    if "interests" in changed:
        out.append({"label": "Into", "value": ", ".join(intent.interests)})
    if "pace" in changed:
        out.append({"label": "Pace", "value": intent.pace})
    for c in changed:
        if c == "constraints.depart_after" and intent.constraints.depart_after:
            out.append({"label": "Flights", "value": f"depart after {intent.constraints.depart_after:%H:%M}"})
        if c == "constraints.max_stops" and intent.constraints.max_stops == 0:
            out.append({"label": "Flights", "value": "direct only"})
        if c == "constraints.hotel_tier" and intent.constraints.hotel_tier:
            out.append({"label": "Stay", "value": f"{intent.constraints.hotel_tier} hotels"})
        if c == "constraints.hotel_min_rating" and intent.constraints.hotel_min_rating:
            out.append({"label": "Stay", "value": f"{intent.constraints.hotel_min_rating}★ and up"})
    return out


def _phrase(ctx: Ctx, draft: str, facts: dict[str, Any]) -> tuple[str, str]:
    candidate = ctx.rt.ai.phrase(facts, draft)
    if candidate and factguard.allowed(candidate, draft, str(facts)):
        return candidate, ctx.rt.ai.name
    return draft, "template"


def _store(ctx: Ctx, trip: Trip, role: str, parts: list[dict[str, Any]]) -> ChatMessage:
    m = ChatMessage(id=new_id("msg"), trip_id=trip.id, role=role, parts=parts, created_at=ctx.clock.now())
    ctx.db.add(m)
    return m


def greet(ctx: Ctx, trip: Trip, name: str) -> ChatMessage:
    month = (ctx.clock.today().month % 12) + 1
    opts = suggest_destinations(ctx, TripIntent(), month, limit=4)
    first = name.split()[0] if name else "there"
    return _store(ctx, trip, "assistant", [
        {"type": "text", "text": f"Hi {first}. Tell me the trip in your own words — dates, budget, who's going, "
                                 "anything you'd rather avoid. I'll ask only for what's missing."},
        {"type": "choices", "slot": "destination", "prompt": f"In season next month", "options": opts},
    ])


def handle(ctx: Ctx, trip: Trip, text: str, action: dict[str, Any] | None) -> list[ChatMessage]:
    text = (text or "").strip()
    if not text and not action:
        raise InvalidInput("chat.empty", "Type a message or pick an option")
    user_msg = _store(ctx, trip, "user", [{"type": "text", "text": text or action.get("label", "")}])
    before = trips.intent_of(trip)
    if action:
        patch, source = _patch_from_action(action), "your choice"
    else:
        patch, source = ctx.rt.ai.extract(text, before, ctx.clock.today())
    if "cheaper" in patch.commands:
        patch.constraints["hotel_tier"] = "budget"
    booked = trip.workflow in ("execute", "reconcile", "done")
    parts: list[dict[str, Any]] = []

    if booked:
        parts.append({"type": "text", "text": "This trip is booked, so I won't change it from chat. Open My Trip to "
                                              "see bookings, vouchers and any change options with their costs."})
        assistant = _store(ctx, trip, "assistant", parts)
        ledger.append(ctx, trip.id, "chat.turn", "copilot", "Declined chat change on a booked trip")
        return [user_msg, assistant]

    select_cmd = next((c for c in patch.commands if c.startswith("select:")), None)
    intent, changed = merge(before, patch)
    problems = rules.check_dates(intent, ctx.clock.today())
    if patch.unknown_places and not (patch.destination or intent.destination):
        names = ", ".join(sorted({d.city for d in DESTINATIONS.values()}))
        parts.append({"type": "text", "text": f"I can't plan {patch.unknown_places[0].title()} yet — the supplier "
                                              f"simulator covers {names}."})
    if problems:
        parts.append({"type": "notice", "tone": "warn", "text": problems[0].detail})
        intent, _ = merge(intent, IntentPatch(clear=["start_date"] if problems[0].code.startswith("dates") else ["origin"]))
        changed = [c for c in changed if c not in ("start_date", "origin")]
    understood = _understood(intent, changed)
    if understood:
        parts.append({"type": "understood", "items": understood, "source": source})
    trips.update_intent(ctx, trip, intent)

    if select_cmd and trip.search and trip.search.get("plans"):
        strategy = select_cmd.split(":")[1]
        plan = next((p for p in trip.search["plans"] if p["strategy"] == strategy), None)
        if plan:
            sel = trips.select_plan(ctx, trip, plan["id"])
            parts.append({"type": "text", "text": f"{sel.name} it is. Next I'll recheck every price with the suppliers "
                                                  "before you approve anything."})
            parts.append({"type": "action", "action": "recheck", "label": "Recheck selected offers"})
            assistant = _store(ctx, trip, "assistant", parts)
            return [user_msg, assistant]

    missing = missing_slots(intent)
    if missing:
        slot = missing[0]
        if slot == "destination" and (intent.interests or intent.month):
            month = intent.month or (ctx.clock.today().month % 12) + 1
            prompt = f"Here's where fits {', '.join(intent.interests) or 'the season'} in {date(2000, month, 1):%B}."
        elif slot == "start_date":
            prompt = "Which dates work? Prices are full-trip estimates for " + (intent.travellers.label() if intent.travellers else "you") + "."
        else:
            prompt = SLOT_PROMPT[slot]
        lead = "Got it. " if understood else ""
        parts.append({"type": "text", "text": lead + prompt})
        options = _choices(ctx, intent, slot)
        parts.append({"type": "choices", "slot": slot, "prompt": SLOT_PROMPT[slot], "options": options})
        assistant = _store(ctx, trip, "assistant", parts)
        ledger.append(ctx, trip.id, "chat.turn", "copilot", f"Asked for {slot}", {"changed": changed, "source": source})
        ctx.emit(trip.id, {"type": "trip"})
        return [user_msg, assistant]

    need_search = affects_search(changed) or not trip.search or trip.workflow == "idle"
    if need_search:
        res = trips.search(ctx, trip)
        parts.append({"type": "trace", "calls": [t.model_dump() for t in res.trace]})
        if res.blocked:
            parts.append({"type": "text", "text": f"{res.blocked[0]}. Try different dates or relax a constraint."})
            parts.append({"type": "choices", "slot": "start_date", "prompt": "Other dates",
                          "options": _choices(ctx, intent.model_copy(update={"start_date": None}), "start_date")})
        else:
            fitting = [p for p in res.plans if p.within_budget is not False]
            budget = intent.budget
            if budget and fitting:
                draft = (f"I found {len(fitting)} plan{'s' if len(fitting) > 1 else ''} that fit your {budget.format()} budget. "
                         + " ".join(f"{p.name}: {p.costs.total.format()}." for p in fitting))
            elif budget:
                closest = min(res.plans, key=lambda p: p.costs.total.amount_minor)
                draft = (f"Nothing fits {budget.format()} on these dates. The closest is {closest.name} at "
                         f"{closest.costs.total.format()}. Try other dates or a shorter trip.")
            else:
                draft = f"Here are {len(res.plans)} plans. " + " ".join(f"{p.name}: {p.costs.total.format()}." for p in res.plans)
            facts = {"plans": [{"name": p.name, "total": p.costs.total.format(), "headline": p.headline} for p in res.plans],
                     "budget": budget.format() if budget else None}
            reply, phrased_by = _phrase(ctx, draft, facts)
            parts.append({"type": "text", "text": reply, "phrased_by": phrased_by})
            parts.append({"type": "plans", "plan_ids": [p.id for p in res.plans]})
            for n in res.notes:
                parts.append({"type": "notice", "tone": "info", "text": n})
            if not budget:
                parts.append({"type": "notice", "tone": "info", "text": "Tell me a budget and I'll filter plans against it."})
    else:
        parts.append({"type": "text", "text": "Nothing in that changes the search. Pick a plan to compare, or tell me "
                                              "what to change — dates, budget, pace or flight times."})
    assistant = _store(ctx, trip, "assistant", parts)
    ledger.append(ctx, trip.id, "chat.turn", "copilot", "Updated intent" + (" and searched" if need_search else ""),
                  {"changed": changed, "source": source})
    ctx.emit(trip.id, {"type": "trip"})
    return [user_msg, assistant]


__all__ = ["handle", "greet", "suggest_destinations", "suggest_dates", "Money"]
