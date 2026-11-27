"""Discover → Compare. Builds complete, timed plans from supplier read tools and ranks them
against the traveller's hard constraints and budget. Nothing here writes to a supplier."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.core.canonical import digest
from app.domain import rules
from app.domain.intent import TripIntent
from app.domain.money import Money, money_sum
from app.domain.routes import RoutesProvider
from app.domain.types import CostBreakdown, CostLine, ItineraryItem, Place, Plan, PlanComponent, ToolCall
from app.suppliers.base import ActivityOffer, FlightOffer, HotelOffer, SupplierAdapter, TransferOffer
from app.suppliers.simulator.adapter import airport_place
from app.suppliers.simulator.catalog import AIRPORTS, DESTINATIONS, Destination

Strategy = Literal["value", "comfort", "experience"]
STRATEGIES: tuple[Strategy, ...] = ("value", "comfort", "experience")
NAMES = {"value": "Value", "comfort": "Comfort", "experience": "Experience"}
PACE_MAX = {"relaxed": 1, "balanced": 2, "packed": 3}


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def ceil5(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    return dt + timedelta(minutes=(-dt.minute) % 5)


def floor5(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0) - timedelta(minutes=dt.minute % 5)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class PlanSearch:
    plans: list[Plan] = field(default_factory=list)
    trace: list[ToolCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
def flight_ok(f: FlightOffer, intent: TripIntent, direction: str) -> bool:
    c = intent.constraints
    if c.max_stops is not None and f.stops > c.max_stops:
        return False
    if direction == "out" and c.depart_after and f.depart.time() < c.depart_after:
        return False
    if c.depart_before and f.depart.time() > c.depart_before:
        return False
    return True


def _flight_score(f: FlightOffer, strategy: Strategy, direction: str, cheapest: float) -> float:
    h = f.depart.hour + f.depart.minute / 60
    if direction == "out":
        ideal = {"comfort": (8.5, 11.5), "experience": (6.5, 10.5), "value": (7.0, 21.0)}[strategy]
    else:
        ideal = {"comfort": (16.0, 20.5), "experience": (17.0, 21.5), "value": (10.0, 21.5)}[strategy]
    dist = 0.0 if ideal[0] <= h <= ideal[1] else min(abs(h - ideal[0]), abs(h - ideal[1]))
    w_price = {"value": 1.0, "comfort": 0.35, "experience": 0.6}[strategy]
    return w_price * (f.price.major / cheapest) * 10 + dist + f.stops * 3 + f.duration_min / 120


def pick_flight(offers: list[FlightOffer], strategy: Strategy, direction: str) -> FlightOffer | None:
    if not offers:
        return None
    cheapest = min(o.price.major for o in offers)
    return min(offers, key=lambda o: (_flight_score(o, strategy, direction, cheapest), o.depart))


def pick_hotel(offers: list[HotelOffer], strategy: Strategy, intent: TripIntent) -> HotelOffer | None:
    c = intent.constraints
    pool = [o for o in offers if o.rating >= (c.hotel_min_rating or 0)]
    if c.hotel_tier:
        pool = [o for o in pool if o.tier == c.hotel_tier] or pool
    if not pool:
        return None
    if strategy == "value":
        cand = [o for o in pool if o.rate_plan == "saver" and o.rating >= 4.0 and (o.tier != "premium" or c.hotel_tier == "premium")]
        return min(cand or pool, key=lambda o: (o.price.amount_minor, -o.rating))
    flex = [o for o in pool if o.rate_plan == "flexible"] or pool
    if strategy == "comfort":
        tiers = [o for o in flex if o.tier in ("mid", "premium")] or flex
        return max(tiers, key=lambda o: (o.rating, -o.price.amount_minor))
    mid = [o for o in flex if o.tier == "mid"] or flex
    return max(mid, key=lambda o: (o.rating, -o.price.amount_minor))


def _act_score(a: ActivityOffer, interests: list[str], strategy: Strategy, dest: Destination) -> float:
    tags = set(a.tags)
    match = len(tags & set(interests)) if interests else 0.6 * len(tags & set(dest.tags))
    s = 3.0 * match
    if a.starts_at.hour < 7:
        s -= 1.5
    if strategy == "value":
        s -= a.price.major / 1500
    elif strategy == "experience":
        s += 0.8 if a.full_day else 0.0
        s -= a.price.major / 6000
    else:
        s -= a.price.major / 4000
    return s


def _end(a: ActivityOffer) -> datetime:
    return a.starts_at + timedelta(minutes=a.duration_min)


def fill_day(day: date, window: tuple[datetime, datetime], anchor: Place, offers: list[ActivityOffer], max_count: int,
             used: set[str], intent: TripIntent, strategy: Strategy, dest: Destination, routes: RoutesProvider,
             *, allow_full_day: bool, allow_strenuous: bool = True) -> list[ActivityOffer]:
    start_w, end_w = window
    if max_count <= 0 or end_w - start_w < timedelta(minutes=60):
        return []
    cands = [o for o in offers if o.starts_at.date() == day and o.activity_id not in used
             and (allow_full_day or not o.full_day) and (allow_strenuous or not o.strenuous)]
    cands.sort(key=lambda o: (-_act_score(o, intent.interests, strategy, dest), o.starts_at))
    chosen: list[ActivityOffer] = []
    for c in cands:
        if len(chosen) >= max_count or (chosen and chosen[0].full_day):
            break
        if c.full_day and chosen:
            continue
        if any(x.activity_id == c.activity_id for x in chosen):
            continue
        s, e = c.starts_at, _end(c)
        if s < start_w or e > end_w or any(s < _end(x) and x.starts_at < e for x in chosen):
            continue
        before = [x for x in chosen if _end(x) <= s]
        prev_end, prev_place = (max(((_end(x), x.place) for x in before), key=lambda t: t[0])
                                if before else (start_w, anchor))
        if prev_end + timedelta(minutes=routes.leg(prev_place, c.place, at=s).duration_min + 10) > s:
            continue
        after = [x for x in chosen if x.starts_at >= e]
        nxt_start, nxt_place = (min(((x.starts_at, x.place) for x in after), key=lambda t: t[0])
                                if after else (end_w, anchor))
        if e + timedelta(minutes=routes.leg(c.place, nxt_place, at=e).duration_min + 10) > nxt_start:
            continue
        chosen.append(c)
    chosen.sort(key=lambda o: o.starts_at)
    used.update(o.activity_id for o in chosen)
    return chosen


# ---------------------------------------------------------------------------------------
@dataclass
class Assembly:
    components: list[PlanComponent]
    items: list[ItineraryItem]
    extras: Money
    activities: list[ActivityOffer]
    transfers: tuple[TransferOffer, TransferOffer]


def _component(key: str, offer, title: str, subtitle: str, supplier: str) -> PlanComponent:
    details = offer.model_dump(mode="json", exclude={"offer_token", "price", "terms"})
    return PlanComponent(key=key, kind=offer.kind, title=title, subtitle=subtitle, supplier=supplier,
                         price=offer.price, offer_token=offer.offer_token, terms=offer.terms, details=details)


def assemble(strategy: Strategy, intent: TripIntent, dest: Destination, out: FlightOffer, ret: FlightOffer,
             hotel: HotelOffer, acts: list[ActivityOffer], supplier: SupplierAdapter,
             routes: RoutesProvider) -> Assembly:
    assert intent.travellers and intent.nights and intent.start_date
    pax, nights, start = intent.travellers, intent.nights, intent.start_date
    intl = dest.international or AIRPORTS[intent.origin or "DEL"].country != AIRPORTS[dest.code].country
    exit_buf = rules.EXIT_BUFFER_INTL if intl else rules.EXIT_BUFFER_DOMESTIC
    before = rules.AIRPORT_BEFORE_INTL if intl else rules.AIRPORT_BEFORE_DOMESTIC
    apt = airport_place(dest.code)
    hotel_place = hotel.place
    scale = dest.local_fare_scale

    pickup_at = ceil5(out.arrive + timedelta(minutes=exit_buf))
    t_in = supplier.quote_transfer(dest.code, "arrival", apt, hotel_place, pickup_at, pax)
    drop_est = routes.leg(hotel_place, apt, mode="car", at=datetime.combine(ret.depart.date(), ret.depart.time()))
    drop_at = floor5(ret.depart - timedelta(minutes=before + drop_est.duration_min))
    t_out = supplier.quote_transfer(dest.code, "departure", hotel_place, apt, drop_at, pax)

    items: list[ItineraryItem] = []
    last_day = nights + 1
    day_date = {d: start + timedelta(days=d - 1) for d in range(1, last_day + 1)}
    pace_max = PACE_MAX[intent.pace] + (1 if strategy == "experience" and intent.pace != "packed" else 0)
    used: set[str] = set()
    chosen_by_day: dict[int, list[ActivityOffer]] = {}

    hotel_ready = max(t_in.pickup_at + timedelta(minutes=t_in.duration_min),
                      datetime.combine(start, _hm(hotel.check_in_time)))
    day1_window = (hotel_ready + timedelta(minutes=30), datetime.combine(start, time(22, 0)))
    if not dest.rest_on_arrival:
        chosen_by_day[1] = fill_day(start, day1_window, hotel_place, acts, 1, used, intent, strategy, dest, routes,
                                    allow_full_day=False)
    for d in range(2, last_day):
        dd = day_date[d]
        chosen_by_day[d] = fill_day(dd, (datetime.combine(dd, time(6, 0)), datetime.combine(dd, time(22, 0))),
                                    hotel_place, acts, pace_max, used, intent, strategy, dest, routes,
                                    allow_full_day=strategy != "value" and d != 2 or (strategy != "value" and not dest.rest_on_arrival),
                                    allow_strenuous=not (dest.rest_on_arrival and d == 2))
    last = day_date[last_day]
    last_window = (datetime.combine(last, time(7, 0)), drop_at - timedelta(minutes=30))
    chosen_by_day[last_day] = fill_day(last, last_window, hotel_place, acts, 1, used, intent, strategy, dest, routes,
                                       allow_full_day=False)

    o_city, d_city = AIRPORTS[out.origin].city, AIRPORTS[out.destination].city
    items.append(ItineraryItem(
        id="d1-flight", day=1, date=start.isoformat(), kind="flight", title=f"Flight {out.origin} → {out.destination}",
        subtitle=f"{out.number} · {out.airline} · {out.fare_family.title()}" + (f" · via {out.via}" if out.via else ""),
        start=iso(out.depart), end=iso(out.arrive), place=airport_place(out.origin), to_place=apt, component="flight_out",
        cost=out.price, meta={"number": out.number, "from_city": o_city, "to_city": d_city, "seats_left": out.seats_left}))
    items.append(ItineraryItem(
        id="d1-pickup", day=1, date=start.isoformat(), kind="transfer", title="Airport pickup",
        subtitle=f"{t_in.provider} · {t_in.vehicle} · driver waits {t_in.terms['wait_min']} min",
        start=iso(t_in.pickup_at), end=iso(t_in.pickup_at + timedelta(minutes=t_in.duration_min)),
        place=apt, to_place=hotel_place, component="transfer_in", cost=t_in.price,
        meta={"distance_km": t_in.distance_km, "duration_min": t_in.duration_min}))
    early = hotel_ready > t_in.pickup_at + timedelta(minutes=t_in.duration_min)
    items.append(ItineraryItem(
        id="d1-hotel", day=1, date=start.isoformat(), kind="hotel", title=f"Check in · {hotel.name}",
        subtitle=f"{hotel.area} · {hotel.rating}★ · {nights} night{'s' if nights > 1 else ''} · "
                 f"{'refundable' if hotel.rate_plan == 'flexible' else 'non-refundable'}"
                 + (f" · room ready {hotel.check_in_time}" if early else ""),
        start=iso(hotel_ready), place=hotel_place, component="hotel", cost=hotel.price,
        meta={"rooms": hotel.rooms, "check_in_time": hotel.check_in_time}))

    components = [
        _component("flight_out", out, f"Flight {out.origin} → {out.destination}", f"{out.number} · {out.depart:%d %b %H:%M}", out.airline),
        _component("hotel", hotel, hotel.name, f"{nights} night{'s' if nights > 1 else ''} · {hotel.area}", hotel.name),
        _component("transfer_in", t_in, "Airport pickup", f"{t_in.pickup_at:%d %b %H:%M} · {t_in.vehicle}", t_in.provider),
    ]

    extras_legs: list[Money] = []
    for d in range(1, last_day + 1):
        acts_d = chosen_by_day.get(d, [])
        prev_place, prev_end = hotel_place, None
        for k, a in enumerate(acts_d):
            leg = routes.leg(prev_place, a.place, at=a.starts_at, fare_scale=scale)
            items.append(_leg_item(f"d{d}-leg{k}", d, day_date[d], a.starts_at - timedelta(minutes=leg.duration_min),
                                   prev_place, a.place, leg))
            extras_legs.append(leg.fare)
            comp_key = f"activity:{a.activity_id}"
            items.append(ItineraryItem(
                id=f"d{d}-{a.activity_id}", day=d, date=day_date[d].isoformat(), kind="activity", title=a.name,
                subtitle=f"{a.provider} · {a.duration_min // 60}h{a.duration_min % 60:02d}" if a.duration_min % 60 else f"{a.provider} · {a.duration_min // 60}h",
                start=iso(a.starts_at), end=iso(_end(a)), place=a.place, component=comp_key, cost=a.price,
                meta={"tags": a.tags}))
            components.append(_component(comp_key, a, a.name, f"{a.starts_at:%a %d %b %H:%M}", a.provider))
            prev_place, prev_end = a.place, _end(a)
        if acts_d and prev_end:
            leg = routes.leg(prev_place, hotel_place, at=prev_end, fare_scale=scale)
            items.append(_leg_item(f"d{d}-legback", d, day_date[d], prev_end, prev_place, hotel_place, leg))
            extras_legs.append(leg.fare)

    co_time = min(datetime.combine(last, _hm(hotel.check_out_time)), drop_at)
    items.append(ItineraryItem(id=f"d{last_day}-checkout", day=last_day, date=last.isoformat(), kind="hotel",
                               title=f"Check out · {hotel.name}", start=iso(co_time), place=hotel_place,
                               component=None, cost=None, source="supplier"))
    items.append(ItineraryItem(
        id=f"d{last_day}-drop", day=last_day, date=last.isoformat(), kind="transfer", title="Airport drop",
        subtitle=f"{t_out.provider} · {t_out.vehicle}", start=iso(t_out.pickup_at),
        end=iso(t_out.pickup_at + timedelta(minutes=t_out.duration_min)), place=hotel_place, to_place=apt,
        component="transfer_out", cost=t_out.price, meta={"distance_km": t_out.distance_km}))
    items.append(ItineraryItem(
        id=f"d{last_day}-flight", day=last_day, date=last.isoformat(), kind="flight",
        title=f"Flight {ret.origin} → {ret.destination}",
        subtitle=f"{ret.number} · {ret.airline} · {ret.fare_family.title()}" + (f" · via {ret.via}" if ret.via else ""),
        start=iso(ret.depart), end=iso(ret.arrive), place=apt, to_place=airport_place(ret.destination),
        component="flight_ret", cost=ret.price, meta={"number": ret.number, "seats_left": ret.seats_left}))
    components.insert(1, _component("flight_ret", ret, f"Flight {ret.origin} → {ret.destination}",
                                    f"{ret.number} · {ret.depart:%d %b %H:%M}", ret.airline))
    components.insert(4, _component("transfer_out", t_out, "Airport drop", f"{t_out.pickup_at:%d %b %H:%M} · {t_out.vehicle}", t_out.provider))

    items.sort(key=lambda i: (i.day, i.start, 0 if i.kind == "leg" else 1))
    days = last_day
    misc = Money.of(dest.extras_pp_day * pax.total * days)
    extras_raw = money_sum(extras_legs) + misc
    extras = Money.of(round(extras_raw.major / 50) * 50)
    all_acts = [a for d in sorted(chosen_by_day) for a in chosen_by_day[d]]
    return Assembly(components, items, extras, all_acts, (t_in, t_out))


def _leg_item(id_: str, day: int, d: date, start: datetime, a: Place, b: Place, leg) -> ItineraryItem:
    label = {"walk": "Walk", "auto": "Auto-rickshaw", "car": "Cab", "boat": "Boat"}[leg.mode]
    return ItineraryItem(id=id_, day=day, date=d.isoformat(), kind="leg", title=f"{label} to {b.name}",
                         subtitle=f"{leg.distance_km} km · ~{leg.duration_min} min · estimate",
                         start=iso(start), end=iso(start + timedelta(minutes=leg.duration_min)), place=a, to_place=b,
                         cost=leg.fare if leg.fare.amount_minor else None, source="estimate",
                         meta={"mode": leg.mode, "distance_km": leg.distance_km, "duration_min": leg.duration_min,
                               "provider": leg.source})


def cost_breakdown(components: list[PlanComponent] | list[dict], extras: Money, nights: int,
                   fees: Money | None = None) -> CostBreakdown:
    comps = [c if hasattr(c, "kind") else PlanComponent.model_validate(c) for c in components]
    def total(kind: str) -> Money:
        return money_sum(c.price for c in comps if c.kind == kind)
    lines = [
        CostLine(key="flights", label="Flights", amount=total("flight"), source="supplier"),
        CostLine(key="hotel", label=f"Hotel · {nights} night{'s' if nights != 1 else ''}", amount=total("hotel"), source="supplier"),
        CostLine(key="transfers", label="Transfers", amount=total("transfer"), source="supplier"),
        CostLine(key="activities", label="Activities", amount=total("activity"), source="supplier"),
    ]
    if fees and fees.amount_minor:
        lines.append(CostLine(key="fees", label="Change fees", amount=fees, source="fee"))
    lines.append(CostLine(key="extras", label="Planned extras", amount=extras, source="estimate",
                          detail="Local rides, tips and entry fees, paid as you go. An estimate, not a quote."))
    bookable = money_sum(line.amount for line in lines if line.source != "estimate")
    return CostBreakdown(lines=lines, bookable_total=bookable, extras_total=extras, total=bookable + extras)


# ---------------------------------------------------------------------------------------
def build_plans(intent: TripIntent, supplier: SupplierAdapter, routes: RoutesProvider) -> PlanSearch:
    assert intent.origin and intent.destination and intent.start_date and intent.nights and intent.travellers
    res = PlanSearch()
    dest = DESTINATIONS.get(intent.destination)
    if not dest:
        res.blocked.append(f"{intent.destination} is not in the supplier catalogue")
        return res
    pax, start, nights = intent.travellers, intent.start_date, intent.nights
    end = start + timedelta(days=nights)
    out_all = supplier.search_flights(intent.origin, dest.code, start, pax)
    ret_all = supplier.search_flights(dest.code, intent.origin, end, pax)
    out_ok = [f for f in out_all if flight_ok(f, intent, "out")]
    ret_ok = [f for f in ret_all if flight_ok(f, intent, "return")]
    res.trace.append(ToolCall(tool="search_flights", args=f"{intent.origin}→{dest.code} {start:%d %b} · {pax.label()}",
                              result=f"{len(out_all)} found, {len(out_ok)} meet your constraints"))
    res.trace.append(ToolCall(tool="search_flights", args=f"{dest.code}→{intent.origin} {end:%d %b}",
                              result=f"{len(ret_all)} found, {len(ret_ok)} meet your constraints"))
    if not out_ok or not ret_ok:
        which = "outbound" if not out_ok else "return"
        res.blocked.append(f"No {which} flight on {(start if not out_ok else end):%d %b} meets your constraints")
        return res
    hotels = supplier.search_hotels(dest.code, start, nights, pax)
    res.trace.append(ToolCall(tool="search_hotels", args=f"{dest.city} {start:%d %b} · {nights} nights",
                              result=f"{len({h.hotel_id for h in hotels})} hotels, {len(hotels)} rates"))
    acts: list[ActivityOffer] = []
    for i in range(nights + 1):
        acts.extend(supplier.search_activities(dest.code, start + timedelta(days=i), pax))
    res.trace.append(ToolCall(tool="search_activities", args=f"{dest.city} · {nights + 1} days",
                              result=f"{len({a.activity_id for a in acts})} experiences, {len(acts)} time slots"))

    seen: set[str] = set()
    for strategy in STRATEGIES:
        out, ret = pick_flight(out_ok, strategy, "out"), pick_flight(ret_ok, strategy, "return")
        hotel = pick_hotel(hotels, strategy, intent)
        if not (out and ret and hotel):
            continue
        asm = assemble(strategy, intent, dest, out, ret, hotel, acts, supplier, routes)
        plan_id = "pln_" + digest(sorted(c.offer_token.split(".")[0] for c in asm.components))[:12]
        if plan_id in seen:
            continue
        seen.add(plan_id)
        res.plans.append(_finish_plan(plan_id, strategy, intent, dest, out, ret, hotel, asm))
    res.trace.append(ToolCall(tool="quote_transfers", args=f"{dest.city} airport ⇄ hotel",
                              result=f"{len(res.plans) * 2} transfer quotes"))
    if dest.advisory:
        res.notes.append(dest.advisory)
    res.plans.sort(key=lambda p: (not (p.within_budget is not False), -p.score))
    fitting = [p for p in res.plans if p.within_budget is not False]
    if fitting:
        over = [p for p in res.plans if p.within_budget is False]
        res.plans = fitting[:3]
        if len(fitting) < 2 and over:
            closest = min(over, key=lambda p: p.costs.total.amount_minor)
            res.plans.append(closest)
            res.notes.append(f"{closest.name} is shown for comparison; it is over budget.")
    else:
        res.plans = sorted(res.plans, key=lambda p: p.costs.total.amount_minor)[:2]
    return res


def _finish_plan(plan_id: str, strategy: Strategy, intent: TripIntent, dest: Destination, out: FlightOffer,
                 ret: FlightOffer, hotel: HotelOffer, asm: Assembly) -> Plan:
    assert intent.nights
    costs = cost_breakdown(asm.components, asm.extras, intent.nights)
    checks = rules.check_flight_constraints(intent, out.depart, ret.depart, max(out.stops, ret.stops))
    checks += rules.check_itinerary_timing(asm.items, international=dest.international)
    checks.append(rules.check_currency([c.price for c in asm.components]))
    budget_check = rules.check_budget(costs.total, intent.budget)
    checks.insert(0, budget_check)
    conv, _ = rules.budget_in_inr(intent.budget)
    delta = (conv - costs.total) if conv else None
    within = None if conv is None else delta.amount_minor >= 0

    hl = []
    if intent.constraints.depart_after:
        hl.append(f"Departs {out.depart:%H:%M}, after your {intent.constraints.depart_after:%H:%M} cut-off")
    else:
        hl.append(f"{out.number} departs {out.depart:%H:%M}, lands {out.arrive:%H:%M}")
    hl.append(f"{hotel.rating}★ in {hotel.area}, " + ("free cancellation" if hotel.rate_plan == "flexible" else "non-refundable saver rate"))
    if asm.activities:
        matched = [a for a in asm.activities if set(a.tags) & set(intent.interests)] if intent.interests else asm.activities
        hl.append(f"{len(asm.activities)} experiences" + (f", {len(matched)} matching your interests" if intent.interests else ""))
    if out.seats_left and out.seats_left <= 3:
        hl.append(f"Only {out.seats_left} seat{'s' if out.seats_left > 1 else ''} left at this fare")

    n_match = sum(1 for a in asm.activities if set(a.tags) & set(intent.interests))
    score = (n_match * 2 + len(asm.activities) + hotel.rating * 2
             - costs.total.major / 4000 - sum(1 for c in checks if c.status == "block") * 10)
    return Plan(id=plan_id, strategy=strategy, name=NAMES[strategy],
                headline=f"{hotel.area} stay · {out.number} at {out.depart:%H:%M}",
                components=asm.components, itinerary=asm.items, costs=costs, highlights=hl, checks=checks,
                within_budget=within, budget_delta=delta, score=round(score, 2))


def quick_estimate(intent: TripIntent, supplier: SupplierAdapter, origin: str, dest_code: str, start: date,
                   nights: int) -> Money | None:
    """Cheapest sensible trip total for date-window chips and destination discovery."""
    pax = intent.travellers
    dest = DESTINATIONS.get(dest_code)
    if not pax or not dest:
        return None
    outs = [f for f in supplier.search_flights(origin, dest_code, start, pax) if flight_ok(f, intent, "out")]
    rets = [f for f in supplier.search_flights(dest_code, origin, start + timedelta(days=nights), pax)
            if flight_ok(f, intent, "return")]
    if not outs or not rets:
        return None
    hotels = [h for h in supplier.search_hotels(dest_code, start, nights, pax) if h.rating >= 4.0]
    if not hotels:
        return None
    hotel = min(hotels, key=lambda h: h.price.amount_minor)
    t = supplier.quote_transfer(dest_code, "arrival", airport_place(dest_code), hotel.place,
                                datetime.combine(start, time(12, 0)), pax)
    acts_pp = sorted(a.price_pp for a in dest.activities)[: max(1, nights)]
    acts = Money.of(sum(acts_pp) * (pax.adults + 0.6 * pax.children))
    extras = Money.of(dest.extras_pp_day * pax.total * (nights + 1))
    total = (min(f.price for f in outs) + min(f.price for f in rets) + hotel.price + t.price + t.price + acts + extras)
    return Money.of(round(total.major / 100) * 100)
