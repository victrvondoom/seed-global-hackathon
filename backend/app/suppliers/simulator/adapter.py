"""Seeded, stateful supplier simulator.

Repeatable offers (seeded by route/date), fare buckets that sell out, asynchronous booking
acknowledgement, schedule changes and paid service actions. Its state lives in `sim_*` tables,
separate from the trip record, as an external supplier's would.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical import digest
from app.core.clock import IST, Clock
from app.core.ids import short_code
from app.domain.geo import ROAD_FACTOR, estimate_leg, haversine_km
from app.domain.intent import Travellers
from app.domain.money import Money
from app.domain.types import Place
from app.models import SimBooking, SimState
from app.suppliers.base import (
    ActivityOffer,
    FlightOffer,
    HotelOffer,
    RecheckResult,
    ServiceOption,
    SupplierBooking,
    SupplierError,
    TransferOffer,
)
from app.suppliers.simulator import codec
from app.suppliers.simulator.catalog import (
    AIRLINES,
    AIRPORTS,
    DESTINATIONS,
    METROS,
    Destination,
    is_direct,
)

FARE_FAMILIES = ("saver", "value", "flex")
FARE_TERMS = {
    "saver": {"label": "Saver", "cabin_bag": "7 kg", "checked_bag": "15 kg", "change": "₹2,999 + fare difference",
              "cancel": "₹3,500 fee per traveller, rest refunded", "refundable": False},
    "value": {"label": "Value", "cabin_bag": "7 kg", "checked_bag": "20 kg", "change": "₹1,999 + fare difference",
              "cancel": "₹2,500 fee per traveller, rest refunded", "refundable": True},
    "flex": {"label": "Flex", "cabin_bag": "7 kg", "checked_bag": "25 kg", "change": "Free date change",
             "cancel": "₹999 fee per traveller, rest refunded", "refundable": True},
}
LEAD_POINTS = ((0, 1.55), (3, 1.40), (7, 1.25), (14, 1.12), (21, 1.05), (30, 1.0), (45, 0.97), (60, 0.96),
               (90, 0.97), (120, 0.99), (180, 1.02), (365, 1.04))


def stable_rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def lead_factor(days: int) -> float:
    days = max(0, days)
    for (d0, f0), (d1, f1) in zip(LEAD_POINTS, LEAD_POINTS[1:], strict=False):
        if d0 <= days <= d1:
            return f0 + (f1 - f0) * (days - d0) / (d1 - d0)
    return LEAD_POINTS[-1][1]


def tod_factor(hour: int) -> float:
    if hour < 7:
        return 0.88
    if hour < 10:
        return 1.10
    if hour < 16:
        return 0.95
    if hour < 21:
        return 1.07
    return 0.90


def season_factor(dest: Destination | None, month: int) -> float:
    if not dest:
        return 1.0
    return dest.demand[month - 1] / (sum(dest.demand) / 12)


def airport_place(code: str) -> Place:
    a = AIRPORTS[code]
    return Place(name=f"{a.city} airport ({a.code})", lat=a.lat, lng=a.lng)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _pax_multiplier(pax: Travellers, child_share: float) -> float:
    return pax.adults + child_share * pax.children


class FlightSpec:
    __slots__ = ("key", "airline", "number", "origin", "destination", "depart", "arrive", "duration", "stops",
                 "via", "base_pp", "seats", "segments")

    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class SimulatorSupplier:
    name = "Waypoint supplier simulator"

    def __init__(self, db: Session, clock: Clock, *, secret: str, seed: int, ack_delay_s: float) -> None:
        self.db = db
        self.clock = clock
        self._secret = secret + ":simulator"
        self.seed = seed
        self.ack_delay_s = ack_delay_s

    # ---- catalog -----------------------------------------------------------------------
    @staticmethod
    def destination(code: str) -> Destination | None:
        return DESTINATIONS.get(code)

    def _today(self) -> date:
        return self.clock.today()

    # ---- state -------------------------------------------------------------------------
    def _state(self, key: str) -> dict[str, Any]:
        row = self.db.get(SimState, key)
        return dict(row.value) if row else {}

    def _set_state(self, key: str, value: dict[str, Any]) -> None:
        row = self.db.get(SimState, key)
        if row:
            row.value = value
        else:
            self.db.add(SimState(key=key, value=value))

    def _token(self, payload: dict[str, Any]) -> str:
        return codec.encode(payload, self._secret)

    def _read(self, token: str) -> dict[str, Any]:
        try:
            return codec.decode(token, self._secret)
        except ValueError as e:
            raise SupplierError("invalid_offer", "Offer token was not issued by this supplier") from e

    # ---- flights -----------------------------------------------------------------------
    def _direct_specs(self, o: str, d: str, day: date) -> list[FlightSpec]:
        ao, ad = AIRPORTS[o], AIRPORTS[d]
        km = haversine_km(ao.lat, ao.lng, ad.lat, ad.lng)
        intl = ao.country != ad.country
        rng = stable_rng(self.seed, "sched", o, d, day.isoformat())
        busy = o in METROS and d in METROS
        n = 3 if intl else (8 if busy else 5 if (o in METROS or d in METROS) else 3)
        dest = DESTINATIONS.get(d) or DESTINATIONS.get(o)
        season = season_factor(dest, day.month)
        dow = {4: 1.08, 6: 1.08, 1: 0.95, 2: 0.94}.get(day.weekday(), 1.0)
        base = 600 + 1.6 * km
        if intl:
            base = base * 1.2 + 3500
        specs, used = [], set()
        span = 1035 / n
        for i in range(n):
            minute = int(330 + i * span + rng.randint(0, int(span * 0.7)))
            minute = minute - minute % 5
            code, airline, af = AIRLINES[rng.randrange(len(AIRLINES))]
            num = rng.randint(101, 989)
            while (code, num) in used:
                num += 7
            used.add((code, num))
            dur = int(30 + km / 13.0) + rng.randint(-4, 6)
            dep = datetime.combine(day, time(minute // 60, minute % 60))
            arr = dep + timedelta(minutes=dur + ad.utc_offset_min - ao.utc_offset_min)
            base_pp = base * af * tod_factor(dep.hour) * dow * season * rng.uniform(0.95, 1.05)
            specs.append(FlightSpec(
                key=f"{code}{num}:{o}-{d}:{day.isoformat()}", airline=airline, number=f"{code} {num}",
                origin=o, destination=d, depart=dep, arrive=arr, duration=dur, stops=0, via=None,
                base_pp=base_pp, seats={"saver": rng.randint(1, 6), "value": rng.randint(4, 14), "flex": 30},
                segments=1))
        return specs

    def _schedule(self, o: str, d: str, day: date) -> list[FlightSpec]:
        if is_direct(o, d):
            return self._direct_specs(o, d, day)
        hubs = [h for h in ("DEL", "BOM", "BLR", "MAA", "CCU") if is_direct(o, h) and is_direct(h, d)]
        if not hubs:
            return []
        ao, ad = AIRPORTS[o], AIRPORTS[d]
        hub = min(hubs, key=lambda h: haversine_km(ao.lat, ao.lng, AIRPORTS[h].lat, AIRPORTS[h].lng)
                  + haversine_km(AIRPORTS[h].lat, AIRPORTS[h].lng, ad.lat, ad.lng))
        out = []
        for a in self._direct_specs(o, hub, day):
            legs = [b for b in self._direct_specs(hub, d, day)
                    if 60 <= (b.depart - a.arrive).total_seconds() / 60 <= 210]
            if not legs:
                continue
            b = legs[0]
            out.append(FlightSpec(
                key=f"{a.key}+{b.key}", airline=a.airline, number=f"{a.number} / {b.number}", origin=o,
                destination=d, depart=a.depart, arrive=b.arrive,
                duration=int((b.arrive - a.depart).total_seconds() / 60) - (ad.utc_offset_min - ao.utc_offset_min),
                stops=1, via=hub, base_pp=(a.base_pp + b.base_pp) * 0.82,
                seats={k: min(a.seats[k], b.seats[k]) for k in a.seats}, segments=2))
        return out[:4]

    def _fare_pp(self, spec: FlightSpec, family: str, today: date) -> int:
        saver = round_to(spec.base_pp * lead_factor((spec.depart.date() - today).days), 10)
        step = max(200, round_to(0.22 * saver, 50))
        fare = {"saver": saver, "value": saver + step, "flex": saver + round_to(2.4 * step, 50)}[family]
        taxes = round_to(0.12 * fare + 450 * spec.segments, 10)
        return fare + taxes

    def _available_family(self, spec: FlightSpec, wanted: str) -> tuple[str | None, int | None]:
        sold = self._state(f"sold:{spec.key}")
        for fam in FARE_FAMILIES[FARE_FAMILIES.index(wanted):]:
            left = spec.seats[fam] - int(sold.get(fam, 0))
            if left > 0:
                return fam, left
        return None, None

    def _flight_price(self, spec: FlightSpec, family: str, pax: Travellers) -> Money:
        pp = self._fare_pp(spec, family, self._today())
        return Money.of(pp * pax.adults + round_to(pp * 0.75, 10) * pax.children)

    def _flight_offer(self, spec: FlightSpec, family: str, left: int, pax: Travellers) -> FlightOffer:
        price = self._flight_price(spec, family, pax)
        token = self._token({"k": "flight", "o": spec.origin, "d": spec.destination,
                             "date": spec.depart.date().isoformat(), "key": spec.key, "fam": family,
                             "pax": pax.model_dump(), "price": price.amount_minor})
        return FlightOffer(offer_token=token, flight_key=spec.key, airline=spec.airline, number=spec.number,
                           origin=spec.origin, destination=spec.destination, depart=spec.depart,
                           arrive=spec.arrive, duration_min=spec.duration, stops=spec.stops, via=spec.via,
                           fare_family=family, seats_left=left if left <= 9 else None, price=price,
                           terms=FARE_TERMS[family])

    def search_flights(self, origin: str, destination: str, day: date, pax: Travellers) -> list[FlightOffer]:
        offers = []
        for spec in self._schedule(origin, destination, day):
            fam, left = self._available_family(spec, "saver")
            if fam:
                offers.append(self._flight_offer(spec, fam, left or 0, pax))
        return offers

    def _spec_for(self, p: dict[str, Any]) -> FlightSpec | None:
        for spec in self._schedule(p["o"], p["d"], date.fromisoformat(p["date"])):
            if spec.key == p["key"]:
                return spec
        return None

    # ---- hotels ------------------------------------------------------------------------
    def search_hotels(self, dest: str, check_in: date, nights: int, pax: Travellers) -> list[HotelOffer]:
        d = DESTINATIONS[dest]
        out = []
        for h in d.hotels:
            for plan in ("saver", "flexible"):
                out.append(self._hotel_offer(d, h.id, check_in, nights, pax, plan))
        return out

    def _hotel_offer(self, d: Destination, hotel_id: str, check_in: date, nights: int, pax: Travellers,
                     plan: str) -> HotelOffer:
        h = next(x for x in d.hotels if x.id == hotel_id)
        rooms = max(1, math.ceil(pax.adults / 2))
        lead = (check_in - self._today()).days
        lead_f = 1.08 if lead < 7 else 1.03 if lead < 21 else 1.0
        total = 0
        for i in range(nights):
            night = check_in + timedelta(days=i)
            rate = h.base_rate * season_factor(d, night.month) * lead_f
            rate *= 1.12 if night.weekday() in (4, 5) else (1 + 0.15 * d.funding_index)
            rate *= stable_rng(self.seed, h.id, night.isoformat()).uniform(0.96, 1.04)
            if plan == "saver":
                rate *= 0.9
            total += round_to(rate, 10)
        price = Money.of(round_to(total * rooms * 1.12, 10))
        terms = ({"label": "Free cancellation until 48h before check-in", "refundable": True}
                 if plan == "flexible" else {"label": "Non-refundable saver rate", "refundable": False})
        terms |= {"check_in": h.check_in, "check_out": h.check_out, "taxes": "Taxes & fees included (simulated 12%)"}
        token = self._token({"k": "hotel", "dest": d.code, "hotel": h.id, "in": check_in.isoformat(),
                             "nights": nights, "plan": plan, "pax": pax.model_dump(), "price": price.amount_minor})
        return HotelOffer(offer_token=token, hotel_id=h.id, name=h.name, area=h.area,
                          place=Place(name=h.name, lat=h.lat, lng=h.lng), rating=h.rating, tier=h.tier,
                          check_in=check_in, check_out=check_in + timedelta(days=nights), check_in_time=h.check_in,
                          check_out_time=h.check_out, nights=nights, rooms=rooms, rate_plan=plan, price=price,
                          terms=terms)

    # ---- transfers ---------------------------------------------------------------------
    def quote_transfer(self, dest: str, direction: str, from_place: Place, to_place: Place,
                       pickup_at: datetime, pax: Travellers) -> TransferOffer:
        d = DESTINATIONS[dest]
        tp = d.transfer
        leg = estimate_leg(from_place, to_place, mode="car", at=pickup_at)
        vehicle = "SUV" if pax.total > 3 else "Sedan"
        price = Money.of(round_to((tp.base + tp.per_km * leg.distance_km) * (1.35 if vehicle == "SUV" else 1), 10))
        terms = {"wait_min": tp.wait_min, "reschedule_fee": tp.reschedule_fee,
                 "free_reschedule_hours": tp.free_reschedule_hours,
                 "label": f"Driver waits {tp.wait_min} min · reschedule free until {tp.free_reschedule_hours}h before, "
                          f"then ₹{tp.reschedule_fee}",
                 "cancel": "Free until 6h before pickup"}
        token = self._token({"k": "transfer", "dest": dest, "dir": direction, "from": from_place.model_dump(),
                             "to": to_place.model_dump(), "at": pickup_at.isoformat(), "vehicle": vehicle,
                             "pax": pax.model_dump(), "price": price.amount_minor})
        return TransferOffer(offer_token=token, provider=tp.provider, vehicle=vehicle, direction=direction,
                             pickup_at=pickup_at, from_place=from_place, to_place=to_place,
                             distance_km=leg.distance_km, duration_min=leg.duration_min, price=price, terms=terms)

    # ---- activities --------------------------------------------------------------------
    def search_activities(self, dest: str, day: date, pax: Travellers) -> list[ActivityOffer]:
        d = DESTINATIONS[dest]
        out = []
        for a in d.activities:
            if day.weekday() not in a.weekdays:
                continue
            for slot in a.slots:
                out.append(self._activity_offer(d, a.id, datetime.combine(day, _hm(slot)), pax))
        return out

    def _activity_offer(self, d: Destination, act_id: str, at: datetime, pax: Travellers) -> ActivityOffer:
        a = next(x for x in d.activities if x.id == act_id)
        price = Money.of(round_to(a.price_pp * _pax_multiplier(pax, 0.6), 10))
        terms = {"label": "Free cancellation or reschedule until 2h before start", "free_until_hours": 2}
        token = self._token({"k": "activity", "dest": d.code, "act": a.id, "at": at.isoformat(),
                             "pax": pax.model_dump(), "price": price.amount_minor})
        return ActivityOffer(offer_token=token, activity_id=a.id, name=a.name, area=a.area,
                             place=Place(name=a.name, lat=a.lat, lng=a.lng), tags=list(a.tags), starts_at=at,
                             duration_min=a.duration_min, full_day=a.full_day, strenuous=a.strenuous,
                             provider=f"{d.city} Experiences", price=price, terms=terms)

    # ---- recheck (reprice) -------------------------------------------------------------
    def set_market(self, trip_id: str, *, force_fare_move: bool | None = None, mode: str | None = None) -> dict:
        st = self._state(f"market:{trip_id}") or {"mode": "auto", "force_next": False}
        if mode:
            st["mode"] = mode
        if force_fare_move is not None:
            st["force_next"] = force_fare_move
        self._set_state(f"market:{trip_id}", st)
        return st

    def market(self, trip_id: str) -> dict:
        return self._state(f"market:{trip_id}") or {"mode": "auto", "force_next": False}

    def _market_move(self, spec: FlightSpec, family: str, left: int, trip_id: str, seed: int, attempt: int) -> None:
        st = self.market(trip_id)
        sell_out = False
        if st.get("force_next"):
            sell_out = True
            st["force_next"] = False
            self._set_state(f"market:{trip_id}", st)
        elif st.get("mode", "auto") == "auto":
            p = {1: 0.7, 2: 0.55, 3: 0.35, 4: 0.2}.get(left, 0.05)
            sell_out = stable_rng(seed, spec.key, family, attempt).random() < p
        if sell_out:
            sold = self._state(f"sold:{spec.key}")
            sold[family] = spec.seats[family]
            self._set_state(f"sold:{spec.key}", sold)

    def recheck(self, offer_token: str, *, trip_id: str, trip_seed: int, attempt: int) -> RecheckResult:
        p = self._read(offer_token)
        prev = Money(amount_minor=p["price"])
        pax = Travellers.model_validate(p["pax"])
        if p["k"] == "flight":
            spec = self._spec_for(p)
            if not spec:
                return RecheckResult(available=False, price=None, previous_price=prev, reason="Flight no longer operates",
                                     offer_token=None, terms={}, details={})
            fam, left = self._available_family(spec, p["fam"])
            if fam == p["fam"] and left is not None:
                self._market_move(spec, fam, left, trip_id, trip_seed, attempt)
                fam, left = self._available_family(spec, p["fam"])
            if not fam:
                return RecheckResult(available=False, price=None, previous_price=prev, reason="Sold out",
                                     offer_token=None, terms={}, details={})
            offer = self._flight_offer(spec, fam, left or 0, pax)
            reason = None
            if fam != p["fam"]:
                reason = (f"{FARE_TERMS[p['fam']]['label']} fare sold out on {spec.number}; "
                          f"next available is {FARE_TERMS[fam]['label']}")
            elif offer.price != prev:
                reason = "Fare moved as departure approaches"
            return RecheckResult(available=True, price=offer.price, previous_price=prev, reason=reason,
                                 offer_token=offer.offer_token, terms=offer.terms,
                                 details={"fare_family": fam, "seats_left": offer.seats_left})
        d = DESTINATIONS[p["dest"]]
        if p["k"] == "hotel":
            offer = self._hotel_offer(d, p["hotel"], date.fromisoformat(p["in"]), p["nights"], pax, p["plan"])
        elif p["k"] == "transfer":
            offer = self.quote_transfer(d.code, p["dir"], Place(**p["from"]), Place(**p["to"]),
                                        datetime.fromisoformat(p["at"]), pax)
        else:
            offer = self._activity_offer(d, p["act"], datetime.fromisoformat(p["at"]), pax)
        reason = None if offer.price == prev else "Supplier rate changed"
        return RecheckResult(available=True, price=offer.price, previous_price=prev, reason=reason,
                             offer_token=offer.offer_token, terms=offer.terms, details={})

    # ---- bookings ----------------------------------------------------------------------
    def _current_price(self, p: dict[str, Any]) -> Money | None:
        pax = Travellers.model_validate(p["pax"])
        if p["k"] == "flight":
            spec = self._spec_for(p)
            if not spec:
                return None
            fam, _ = self._available_family(spec, p["fam"])
            return self._flight_price(spec, fam, pax) if fam == p["fam"] else None
        d = DESTINATIONS[p["dest"]]
        if p["k"] == "hotel":
            return self._hotel_offer(d, p["hotel"], date.fromisoformat(p["in"]), p["nights"], pax, p["plan"]).price
        if p["k"] == "transfer":
            return self.quote_transfer(d.code, p["dir"], Place(**p["from"]), Place(**p["to"]),
                                       datetime.fromisoformat(p["at"]), pax).price
        return self._activity_offer(d, p["act"], datetime.fromisoformat(p["at"]), pax).price

    def _view(self, b: SimBooking) -> SupplierBooking:
        return SupplierBooking(ref=b.ref, kind=b.kind, status=b.status, confirmation_code=b.confirmation_code,
                               amount=Money(amount_minor=b.amount_minor), details=dict(b.details))

    def create_booking(self, offer_token: str, expected: Money, idempotency_key: str) -> SupplierBooking:
        existing = self.db.scalar(select(SimBooking).where(SimBooking.idempotency_key == idempotency_key))
        if existing:
            return self._view(existing)
        p = self._read(offer_token)
        current = self._current_price(p)
        if current is None:
            raise SupplierError("unavailable", "No longer available at this fare")
        if current != expected:
            raise SupplierError("price_changed", f"Price is now {current.format()}, approved {expected.format()}")
        details: dict[str, Any] = {k: v for k, v in p.items() if k not in ("pax",)}
        if p["k"] == "flight":
            spec = self._spec_for(p)
            assert spec
            details |= {"number": spec.number, "airline": spec.airline, "depart": spec.depart.isoformat(),
                        "arrive": spec.arrive.isoformat(), "delay_min": 0, "schedule_events": []}
            sold = self._state(f"sold:{spec.key}")
            sold[p["fam"]] = int(sold.get(p["fam"], 0)) + p["pax"]["adults"] + p["pax"].get("children", 0)
            self._set_state(f"sold:{spec.key}", sold)
        if p["k"] == "transfer":
            details |= {"pickup_at": p["at"], "fees": []}
        ref = ("WP" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:8]).upper()
        now = self.clock.now()
        b = SimBooking(ref=ref, idempotency_key=idempotency_key, kind=p["k"], status="pending", offer=p,
                       details=details, amount_minor=current.amount_minor,
                       ack_at=now + timedelta(seconds=self.ack_delay_s), created_at=now)
        self.db.add(b)
        self.db.flush()
        return self._view(b)

    def _get(self, ref: str) -> SimBooking:
        b = self.db.get(SimBooking, ref)
        if not b:
            raise SupplierError("not_found", "Unknown supplier reference")
        return b

    def get_booking(self, ref: str) -> SupplierBooking:
        b = self._get(ref)
        if b.status == "pending" and self.clock.now() >= b.ack_at:
            b.status = "confirmed"
            b.confirmation_code = short_code(6)
        return self._view(b)

    def cancel_booking(self, ref: str) -> SupplierBooking:
        b = self._get(ref)
        b.status = "cancelled"
        return self._view(b)

    # ---- service actions & disruptions --------------------------------------------------
    def reschedule_option(self, ref: str, new_time: datetime) -> ServiceOption:
        b = self._get(ref)
        if b.kind != "transfer":
            raise SupplierError("unsupported", "Only transfers can be rescheduled in the simulator")
        tp = DESTINATIONS[b.offer["dest"]].transfer
        pickup = datetime.fromisoformat(b.details["pickup_at"])
        now_local = self.clock.now().astimezone(IST).replace(tzinfo=None)
        hours_before = (pickup - now_local).total_seconds() / 3600
        # A same-day disruption is always inside the free window for a demo trip in the future, so the
        # fee applies whenever the change is requested on the travel day or later than the free cut-off.
        fee_due = hours_before < tp.free_reschedule_hours or pickup.date() <= new_time.date()
        fee = Money.of(tp.reschedule_fee if fee_due else 0)
        action = {"ref": ref, "type": "reschedule", "to": new_time.isoformat(), "fee": fee.amount_minor,
                  "v": digest(b.details)[:12]}
        return ServiceOption(action_token=self._token(action),
                             title=f"Move pickup to {new_time:%H:%M}", fee=fee,
                             details={"from": b.details["pickup_at"], "to": new_time.isoformat(),
                                      "policy": f"Reschedule fee ₹{tp.reschedule_fee} inside {tp.free_reschedule_hours}h"})

    def apply_service_action(self, ref: str, action_token: str, expected_fee: Money) -> SupplierBooking:
        a = self._read(action_token)
        b = self._get(ref)
        if a["ref"] != ref:
            raise SupplierError("invalid_action", "Action does not belong to this booking")
        if a["fee"] != expected_fee.amount_minor:
            raise SupplierError("price_changed", "Service fee changed; approval needed again")
        if a["v"] != digest(b.details)[:12]:
            raise SupplierError("stale_action", "Booking changed since this option was offered")
        details = dict(b.details)
        details["fees"] = list(details.get("fees", [])) + [{"type": "reschedule", "amount_minor": a["fee"]}]
        details["pickup_at"] = a["to"]
        b.details = details
        return self._view(b)

    def inject_flight_delay(self, ref: str, minutes: int) -> SupplierBooking:
        b = self._get(ref)
        if b.kind != "flight":
            raise SupplierError("unsupported", "Delays apply to flights")
        details = dict(b.details)
        details["delay_min"] = int(details.get("delay_min", 0)) + minutes
        details["schedule_events"] = list(details.get("schedule_events", [])) + [
            {"type": "delay", "minutes": minutes, "at": self.clock.now().isoformat()}]
        b.details = details
        return self._view(b)


__all__ = ["SimulatorSupplier", "airport_place", "stable_rng", "lead_factor", "season_factor", "ROAD_FACTOR"]
