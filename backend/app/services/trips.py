"""Trip record workflow: Search → Reprice → Await approval → Execute → Reconcile, then Adapt.
The model never reaches this module's write paths; every write is gated by deterministic rules."""
from __future__ import annotations

import logging
import random
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.core.canonical import digest, hmac_hex
from app.core.errors import DomainError, NotFound
from app.core.ids import new_id
from app.domain import cascade, planner, receipts, rules, workflow
from app.domain.intent import TripIntent
from app.domain.money import Money, money_sum
from app.domain.types import ItineraryItem, Plan
from app.models import (
    ApprovalReceipt,
    Disruption,
    IdempotencyRecord,
    Order,
    Quote,
    Trip,
    TripRevision,
    Voucher,
)
from app.services import ledger
from app.services.runtime import Ctx, Runtime
from app.suppliers.base import SupplierError
from app.suppliers.simulator.adapter import airport_place
from app.suppliers.simulator.catalog import AIRPORTS, DESTINATIONS

log = logging.getLogger(__name__)
BOOK_ORDER = {"flight": 0, "hotel": 1, "transfer": 2, "activity": 3}


# ---- helpers ------------------------------------------------------------------------------
def intent_of(trip: Trip) -> TripIntent:
    return TripIntent.model_validate(trip.intent or {})


def set_workflow(trip: Trip, target: str) -> None:
    trip.workflow = workflow.require(trip.workflow, target)
    trip.stage = workflow.STAGE_OF[target]


def current_revision(ctx: Ctx, trip: Trip) -> TripRevision | None:
    if not trip.current_revision:
        return None
    return ctx.db.scalar(select(TripRevision).where(TripRevision.trip_id == trip.id,
                                                    TripRevision.number == trip.current_revision))


def add_revision(ctx: Ctx, trip: Trip, reason: str, items: list[ItineraryItem] | list[dict], costs: dict) -> TripRevision:
    trip.current_revision += 1
    rev = TripRevision(id=new_id("rev"), trip_id=trip.id, number=trip.current_revision, reason=reason,
                       itinerary=[i.model_dump(mode="json") if isinstance(i, ItineraryItem) else i for i in items],
                       costs=costs, created_at=ctx.clock.now())
    ctx.db.add(rev)
    ledger.append(ctx, trip.id, "itinerary.revised", "system", f"Itinerary v{rev.number}: {reason}",
                  {"revision": rev.number, "total_minor": costs["total"]["amount_minor"]})
    return rev


def selected_plan(trip: Trip) -> Plan | None:
    if not trip.search or not trip.selected_plan_id:
        return None
    for p in trip.search.get("plans", []):
        if p["id"] == trip.selected_plan_id:
            return Plan.model_validate(p)
    return None


def active_quotes(ctx: Ctx, trip: Trip) -> list[Quote]:
    q = ctx.db.scalars(select(Quote).where(Quote.trip_id == trip.id, Quote.status == "active"))
    return sorted(q, key=lambda x: (BOOK_ORDER.get(x.kind, 9), x.component))


def trip_orders(ctx: Ctx, trip: Trip) -> list[Order]:
    return list(ctx.db.scalars(select(Order).where(Order.trip_id == trip.id).order_by(Order.created_at)))


def _costs(kind_prices: list[tuple[str, Money]], extras: Money, nights: int, fees: Money | None = None) -> dict:
    comps = [SimpleNamespace(kind=k, price=p) for k, p in kind_prices]
    return planner.cost_breakdown(comps, extras, nights, fees).model_dump(mode="json")


def _title(intent: TripIntent) -> str:
    if intent.origin and intent.destination:
        return f"{AIRPORTS[intent.origin].city} → {AIRPORTS[intent.destination].city}"
    if intent.destination:
        return f"Trip to {AIRPORTS[intent.destination].city}"
    return "New trip"


# ---- create / search / select -------------------------------------------------------------
def create_trip(ctx: Ctx, owner_id: str) -> Trip:
    trip = Trip(id=new_id("trp"), owner_id=owner_id, title="New trip", stage="plan", workflow="idle",
                intent=TripIntent().model_dump(mode="json"), seed=random.SystemRandom().randint(1, 2**31 - 1),
                created_at=ctx.clock.now(), updated_at=ctx.clock.now())
    ctx.db.add(trip)
    ctx.db.flush()
    ledger.append(ctx, trip.id, "trip.created", "traveller", "Trip record opened")
    return trip


def update_intent(ctx: Ctx, trip: Trip, intent: TripIntent) -> None:
    trip.intent = intent.model_dump(mode="json")
    trip.title = _title(intent)


def release_selection(ctx: Ctx, trip: Trip, reason: str) -> None:
    for q in active_quotes(ctx, trip):
        q.status = "released"
    if trip.selected_plan_id:
        ledger.append(ctx, trip.id, "plan.released", "system", reason, {"plan_id": trip.selected_plan_id})
    trip.selected_plan_id = None
    trip.checkout = None


def search(ctx: Ctx, trip: Trip) -> planner.PlanSearch:
    if trip.workflow in ("execute", "reconcile", "done"):
        raise DomainError("trip.booked", "This trip is booked. Manage changes from My Trip.")
    intent = intent_of(trip)
    res = planner.build_plans(intent, ctx.supplier, ctx.rt.routes)
    if trip.selected_plan_id:
        release_selection(ctx, trip, "Preferences changed; earlier selection released")
    trip.search = {
        "searched_at": ctx.clock.now().isoformat(),
        "intent_digest": digest(intent.search_key()),
        "plans": [p.model_dump(mode="json") for p in res.plans],
        "trace": [t.model_dump() for t in res.trace],
        "notes": res.notes,
        "blocked": res.blocked,
    }
    trip.workflow = "search" if trip.workflow in ("idle", "search", "reprice", "await_approval") else trip.workflow
    trip.stage = "plan"
    ledger.append(ctx, trip.id, "search.completed", "copilot",
                  f"Read tools returned {len(res.plans)} plan(s)" + (f"; {res.blocked[0]}" if res.blocked else ""),
                  {"plans": [{"id": p.id, "name": p.name, "total_minor": p.costs.total.amount_minor} for p in res.plans],
                   "tools": [t.tool for t in res.trace]})
    ctx.emit(trip.id, {"type": "trip"})
    return res


def select_plan(ctx: Ctx, trip: Trip, plan_id: str) -> Plan:
    if trip.workflow not in ("search", "reprice", "await_approval"):
        raise DomainError("workflow.invalid_transition", "Search for plans before selecting one")
    plan = next((Plan.model_validate(p) for p in (trip.search or {}).get("plans", []) if p["id"] == plan_id), None)
    if not plan:
        raise NotFound("plan.not_found", "That plan is no longer in the latest search")
    release_selection(ctx, trip, "Switched plans")
    now = ctx.clock.now()
    for c in plan.components:
        ctx.db.add(Quote(id=new_id("quo"), trip_id=trip.id, plan_id=plan.id, component=c.key, kind=c.kind,
                         supplier=c.supplier, title=c.title, offer_token=c.offer_token, amount_minor=c.price.amount_minor,
                         currency=c.price.currency, terms=c.terms, terms_digest=digest(c.terms), details=c.details,
                         firm=False, status="active", created_at=now))
    trip.selected_plan_id = plan.id
    trip.workflow = "reprice"
    trip.stage = "verify"
    trip.checkout = None
    items = [i.model_copy(update={"status": "quoted"}) if i.component else i for i in plan.itinerary]
    add_revision(ctx, trip, f"{plan.name} plan selected · estimate {plan.costs.total.format()}", items,
                 plan.costs.model_dump(mode="json"))
    ledger.append(ctx, trip.id, "plan.selected", "traveller", f"Selected {plan.name} plan ({plan.costs.total.format()} estimate)",
                  {"plan_id": plan.id, "estimate_minor": plan.costs.total.amount_minor})
    ctx.emit(trip.id, {"type": "trip"})
    return plan


# ---- verify: reprice → approval ------------------------------------------------------------
def reprice(ctx: Ctx, trip: Trip) -> dict[str, Any]:
    if trip.workflow not in ("reprice", "await_approval"):
        raise DomainError("workflow.invalid_transition", "Select a plan before rechecking prices",
                          details={"workflow": trip.workflow})
    plan = selected_plan(trip)
    if not plan:
        raise DomainError("plan.missing", "Selected plan not found; search again")
    intent = intent_of(trip)
    attempt = int((trip.checkout or {}).get("attempt", 0)) + 1
    now = ctx.clock.now()
    valid_until = now + timedelta(seconds=ctx.settings.quote_ttl_seconds)
    quotes = active_quotes(ctx, trip)
    changes, unavailable, new_quotes = [], [], []
    previous = money_sum(Money(amount_minor=q.amount_minor, currency=q.currency) for q in quotes)
    for q in quotes:
        res = ctx.supplier.recheck(q.offer_token, trip_id=trip.id, trip_seed=trip.seed, attempt=attempt)
        q.status = "superseded"
        if not res.available or res.price is None or res.offer_token is None:
            unavailable.append({"component": q.component, "title": q.title, "reason": res.reason or "Unavailable"})
            continue
        nq = Quote(id=new_id("quo"), trip_id=trip.id, plan_id=q.plan_id, component=q.component, kind=q.kind,
                   supplier=q.supplier, title=q.title, offer_token=res.offer_token, amount_minor=res.price.amount_minor,
                   currency=res.price.currency, terms=res.terms, terms_digest=digest(res.terms),
                   details={**q.details, **res.details}, firm=True, status="active", valid_until=valid_until, created_at=now)
        ctx.db.add(nq)
        new_quotes.append(nq)
        if res.price != res.previous_price:
            changes.append({"component": q.component, "title": q.title, "previous": res.previous_price.model_dump(),
                            "current": res.price.model_dump(), "delta": (res.price - res.previous_price).model_dump(),
                            "reason": res.reason})
    ctx.db.flush()
    total = money_sum(Money(amount_minor=q.amount_minor, currency=q.currency) for q in new_quotes)
    estimate_total = total + plan.costs.extras_total
    rev = current_revision(ctx, trip)
    items = [ItineraryItem.model_validate(i) for i in (rev.itinerary if rev else [])]
    checks = [rules.check_quote_validity([q.valid_until for q in new_quotes], now),
              rules.check_currency(Money(amount_minor=q.amount_minor, currency=q.currency) for q in new_quotes),
              rules.check_budget(estimate_total, intent.budget)]
    dest = DESTINATIONS.get(intent.destination or "")
    checks += rules.check_itinerary_timing(items, international=bool(dest and dest.international))
    if unavailable:
        checks.insert(0, rules.RuleCheck(code="supplier.available", status="block", title="No longer available",
                                         detail="; ".join(f"{u['title']}: {u['reason']}" for u in unavailable)))
    scope = {
        "trip_id": trip.id, "purpose": "booking",
        "items": [{"component": q.component, "quote_id": q.id, "supplier": q.supplier, "title": q.title,
                   "amount_minor": q.amount_minor, "currency": q.currency, "terms_digest": q.terms_digest}
                  for q in sorted(new_quotes, key=lambda x: (BOOK_ORDER.get(x.kind, 9), x.component))],
        "total_minor": total.amount_minor, "currency": total.currency,
    }
    trip.checkout = {
        "attempt": attempt, "scope": scope, "digest": receipts.scope_digest(scope),
        "previous_total": previous.model_dump(), "total": total.model_dump(),
        "estimate_total": estimate_total.model_dump(), "extras": plan.costs.extras_total.model_dump(),
        "changes": changes, "unavailable": unavailable, "checks": [c.model_dump() for c in checks],
        "repriced_at": now.isoformat(), "valid_until": valid_until.isoformat(),
    }
    trip.workflow = "await_approval" if not unavailable else "reprice"
    trip.stage = "verify"
    if changes or unavailable:
        price_by = {q.component: Money(amount_minor=q.amount_minor, currency=q.currency) for q in new_quotes}
        new_items = [i.model_copy(update={"cost": price_by.get(i.component, i.cost)}) if i.component else i for i in items]
        delta = total - previous
        add_revision(ctx, trip, f"Supplier recheck: {'+' if delta.amount_minor >= 0 else ''}{delta.format()}",
                     new_items, _costs([(q.kind, Money(amount_minor=q.amount_minor)) for q in new_quotes],
                                       plan.costs.extras_total, intent.nights or 1))
    delta = total - previous
    ledger.append(ctx, trip.id, "reprice.completed", "supplier",
                  f"Rechecked {len(quotes)} offers: {previous.format()} → {total.format()}"
                  + (f" ({len(unavailable)} unavailable)" if unavailable else ""),
                  {"previous_minor": previous.amount_minor, "total_minor": total.amount_minor,
                   "delta_minor": delta.amount_minor, "changes": [c["component"] for c in changes],
                   "scope_digest": trip.checkout["digest"]})
    ctx.emit(trip.id, {"type": "trip"})
    return trip.checkout


def _issue_receipt(ctx: Ctx, trip: Trip, purpose: str, scope: dict, amount: Money) -> ApprovalReceipt:
    now = ctx.clock.now()
    rid = new_id("rcp")
    expires = now + timedelta(seconds=ctx.settings.approval_ttl_seconds)
    sdig = receipts.scope_digest(scope)
    sig = receipts.sign(ctx.settings.secret_key, receipt_id=rid, user_id=ctx.user_id or "", trip_id=trip.id,
                        scope_dig=sdig, amount_minor=amount.amount_minor, currency=amount.currency, expires_at=expires)
    r = ApprovalReceipt(id=rid, trip_id=trip.id, user_id=ctx.user_id or "", purpose=purpose, scope=scope,
                        scope_digest=sdig, amount_minor=amount.amount_minor, currency=amount.currency, signature=sig,
                        created_at=now, expires_at=expires)
    ctx.db.add(r)
    return r


def _check_receipt(ctx: Ctx, trip: Trip, receipt_id: str, purpose: str, expected_digest: str) -> ApprovalReceipt:
    r = ctx.db.get(ApprovalReceipt, receipt_id)
    if not r or r.trip_id != trip.id or r.user_id != ctx.user_id or r.purpose != purpose:
        raise DomainError("consent.missing", "No matching approval for this action", status_code=403)
    if not receipts.verify(ctx.settings.secret_key, r.signature, receipt_id=r.id, user_id=r.user_id, trip_id=r.trip_id,
                           scope_dig=r.scope_digest, amount_minor=r.amount_minor, currency=r.currency,
                           expires_at=r.expires_at):
        raise DomainError("consent.invalid", "Approval receipt failed verification", status_code=403)
    if r.consumed_at:
        raise DomainError("consent.used", "This approval was already used")
    if r.expires_at <= ctx.clock.now():
        raise DomainError("consent.expired", "Approval expired; review and approve again")
    if r.scope_digest != expected_digest:
        raise DomainError("consent.stale", "Prices or terms changed after approval; approve the new scope")
    return r


def approve(ctx: Ctx, trip: Trip, scope_digest: str, amount_minor: int, currency: str) -> ApprovalReceipt:
    if trip.workflow != "await_approval" or not trip.checkout:
        raise DomainError("workflow.invalid_transition", "Recheck prices before approving")
    co = trip.checkout
    if scope_digest != co["digest"]:
        raise DomainError("consent.stale", "What you approved is no longer current; review the updated price",
                          details={"current_digest": co["digest"]})
    if amount_minor != co["total"]["amount_minor"] or currency != co["total"]["currency"]:
        raise DomainError("consent.amount_mismatch", "Approved amount does not match the rechecked total")
    validity = rules.check_quote_validity([q.valid_until for q in active_quotes(ctx, trip)], ctx.clock.now())
    if validity.status == "block":
        raise DomainError("quote.expired", validity.detail)
    r = _issue_receipt(ctx, trip, "booking", co["scope"], Money(amount_minor=amount_minor, currency=currency))
    trip.checkout = {**co, "receipt_id": r.id}
    ledger.append(ctx, trip.id, "approval.granted", "traveller",
                  f"Approved {Money(amount_minor=amount_minor, currency=currency).format()} for {len(co['scope']['items'])} items",
                  {"receipt_id": r.id, "scope_digest": r.scope_digest, "amount_minor": amount_minor})
    ctx.emit(trip.id, {"type": "trip"})
    return r


# ---- execute (saga) & reconcile ----------------------------------------------------------
def execute(ctx: Ctx, trip: Trip, receipt_id: str, idem_key: str) -> dict[str, Any]:
    req_digest = digest({"trip": trip.id, "receipt": receipt_id})
    rec = ctx.db.get(IdempotencyRecord, (ctx.user_id, idem_key))
    if rec:
        if rec.request_digest != req_digest:
            raise DomainError("idempotency.conflict", "Idempotency key reused with a different request", status_code=422)
        return rec.response
    if trip.workflow != "await_approval" or not trip.checkout:
        raise DomainError("workflow.invalid_transition", "Nothing approved to book", details={"workflow": trip.workflow})
    co = trip.checkout
    r = _check_receipt(ctx, trip, receipt_id, "booking", co["digest"])
    quotes = {q.id: q for q in active_quotes(ctx, trip)}
    items = co["scope"]["items"]
    if any(i["quote_id"] not in quotes for i in items):
        raise DomainError("consent.stale", "Quotes changed after approval; recheck and approve again")
    validity = rules.check_quote_validity([quotes[i["quote_id"]].valid_until for i in items], ctx.clock.now())
    if validity.status == "block":
        raise DomainError("quote.expired", validity.detail)
    dupes = ctx.db.scalars(select(Order).where(Order.trip_id == trip.id, Order.status.in_(("pending", "confirmed")),
                                               Order.component.in_([i["component"] for i in items]))).all()
    if dupes:
        raise DomainError("booking.duplicate", "These items are already booked on this trip")
    set_workflow(trip, "execute")
    created: list[Order] = []
    now = ctx.clock.now()
    for it in items:
        q = quotes[it["quote_id"]]
        try:
            sb = ctx.supplier.create_booking(q.offer_token, Money(amount_minor=q.amount_minor, currency=q.currency),
                                             f"{trip.id}:{idem_key}:{q.component}")
        except SupplierError as e:
            for o in created:
                if o.supplier_ref:
                    ctx.supplier.cancel_booking(o.supplier_ref)
                o.status = "cancelled"
                o.details = {**o.details, "compensated": True}
            trip.workflow = "reprice"
            trip.stage = "verify"
            trip.checkout = {**co, "receipt_id": None, "failed": {"component": q.component, "reason": e.message}}
            ledger.append(ctx, trip.id, "booking.failed", "supplier",
                          f"{q.title}: {e.message}. {len(created)} earlier booking(s) cancelled; nothing charged",
                          {"component": q.component, "code": e.code, "compensated": [o.id for o in created]})
            ctx.emit(trip.id, {"type": "trip"})
            ctx.commit()
            raise DomainError("booking.failed", f"{q.title}: {e.message}. Nothing was booked; recheck and approve again.") from e
        o = Order(id=new_id("ord"), trip_id=trip.id, quote_id=q.id, receipt_id=r.id, component=q.component, kind=q.kind,
                  supplier=q.supplier, title=q.title, supplier_ref=sb.ref, status="pending", amount_minor=q.amount_minor,
                  currency=q.currency, details={**q.details, **sb.details, "terms": q.terms}, created_at=now, updated_at=now)
        ctx.db.add(o)
        created.append(o)
        q.status = "booked"
    r.consumed_at = now
    set_workflow(trip, "reconcile")
    ledger.append(ctx, trip.id, "booking.submitted", "system",
                  f"{len(created)} bookings sent to suppliers; awaiting acknowledgement",
                  {"orders": [o.id for o in created], "receipt_id": r.id})
    ctx.db.flush()
    response = {"orders": [o.id for o in created], "status": "pending"}
    ctx.db.add(IdempotencyRecord(user_id=ctx.user_id, key=idem_key, scope="execute", request_digest=req_digest,
                                 response=response, created_at=now))
    ctx.emit(trip.id, {"type": "trip"})
    return response


def _issue_voucher(ctx: Ctx, trip: Trip, o: Order) -> Voucher:
    payload = {"order_id": o.id, "trip_id": trip.id, "title": o.title, "supplier": o.supplier,
               "supplier_ref": o.supplier_ref, "confirmation_code": o.confirmation_code, "component": o.component,
               "amount_minor": o.amount_minor, "currency": o.currency, "issued_at": ctx.clock.now().isoformat()}
    sig = hmac_hex(ctx.settings.secret_key + ":vouchers", digest(payload))
    v = Voucher(id=new_id("vch"), trip_id=trip.id, order_id=o.id, code=f"WPV-{o.confirmation_code}", payload=payload,
                signature=sig, issued_at=ctx.clock.now())
    ctx.db.add(v)
    return v


def reconcile(ctx: Ctx, trip: Trip) -> dict[str, Any]:
    orders = trip_orders(ctx, trip)
    confirmed_now = 0
    for o in orders:
        if o.status != "pending" or not o.supplier_ref:
            continue
        sb = ctx.supplier.get_booking(o.supplier_ref)
        if sb.status == "confirmed":
            o.status = "confirmed"
            o.confirmation_code = sb.confirmation_code
            o.confirmed_at = ctx.clock.now()
            _issue_voucher(ctx, trip, o)
            confirmed_now += 1
            ledger.append(ctx, trip.id, "booking.confirmed", "supplier",
                          f"{o.title} confirmed by {o.supplier} · ref {o.supplier_ref}",
                          {"order_id": o.id, "supplier_ref": o.supplier_ref, "confirmation_code": o.confirmation_code})
        elif sb.status in ("failed", "cancelled"):
            o.status = sb.status
    active = [o for o in orders if o.kind != "fee" and o.status != "cancelled"]
    if trip.workflow == "reconcile" and active and all(o.status == "confirmed" for o in active):
        set_workflow(trip, "done")
        rev = current_revision(ctx, trip)
        by_comp = {o.component: o for o in active}
        items = []
        for i in rev.itinerary if rev else []:
            o = by_comp.get(i.get("component"))
            if o:
                i = {**i, "status": "booked", "meta": {**i.get("meta", {}), "ref": o.supplier_ref,
                                                       "confirmation": o.confirmation_code}}
            items.append(i)
        add_revision(ctx, trip, "All bookings confirmed by suppliers", items, rev.costs if rev else {})
    if trip.workflow == "done":
        _detect_schedule_changes(ctx, trip, orders)
    if confirmed_now:
        ctx.emit(trip.id, {"type": "trip"})
    return {"confirmed_now": confirmed_now, "workflow": trip.workflow}


def schedule_reconcile(rt: Runtime, trip_id: str) -> None:
    if not rt.settings.auto_reconcile:
        return

    def run() -> None:
        for _ in range(4):
            with rt.db.session() as s:
                ctx = Ctx(rt=rt, db=s)
                trip = s.get(Trip, trip_id)
                if not trip:
                    return
                ctx.user_id = trip.owner_id
                try:
                    reconcile(ctx, trip)
                    ctx.commit()
                except StaleDataError:
                    s.rollback()
                    continue
                if trip.workflow != "reconcile":
                    return
            threading.Event().wait(0.5)

    threading.Timer(rt.settings.supplier_ack_delay_seconds + 0.2, run).start()


# ---- adapt: disruptions --------------------------------------------------------------------
def _exit_buffers(intent: TripIntent) -> tuple[int, int]:
    dest = DESTINATIONS.get(intent.destination or "")
    intl = bool(dest and dest.international)
    return ((rules.EXIT_BUFFER_INTL, rules.AIRPORT_BEFORE_INTL) if intl
            else (rules.EXIT_BUFFER_DOMESTIC, rules.AIRPORT_BEFORE_DOMESTIC))


def _detect_schedule_changes(ctx: Ctx, trip: Trip, orders: list[Order]) -> None:
    for o in orders:
        if o.kind != "flight" or o.status != "confirmed" or not o.supplier_ref:
            continue
        sb = ctx.supplier.get_booking(o.supplier_ref)
        known = int(o.details.get("delay_min", 0))
        now_delay = int(sb.details.get("delay_min", 0))
        if now_delay <= known:
            continue
        o.details = {**o.details, "delay_min": now_delay}
        _open_disruption(ctx, trip, o, now_delay - known)


def _open_disruption(ctx: Ctx, trip: Trip, flight_order: Order, delay: int) -> Disruption:
    intent = intent_of(trip)
    rev = current_revision(ctx, trip)
    items = [ItineraryItem.model_validate(i) for i in (rev.itinerary if rev else [])]
    exit_buf, before = _exit_buffers(intent)
    dest = DESTINATIONS[intent.destination or "COK"]
    a = cascade.analyse_delay(items, flight_order.component, delay, wait_min=dest.transfer.wait_min,
                              exit_buffer=exit_buf, airport_before=before)
    base_total = Money.model_validate(rev.costs["total"]) if rev else Money.zero()
    options: list[dict[str, Any]] = [{
        "id": "keep", "title": "Keep the current plan", "delta": Money.zero().model_dump(),
        "new_total": base_total.model_dump(), "requires_approval": False, "recommended": False,
        "summary": ("Pickup may be missed; you'd arrange your own ride" if a["pickup"] and a["pickup"]["status"] == "missed"
                    else "No supplier changes"), "changes": []}]
    pick = a.get("pickup")
    if pick and pick["status"] != "ok":
        t_order = next((o for o in trip_orders(ctx, trip) if o.component == "transfer_in" and o.status == "confirmed"), None)
        if t_order and t_order.supplier_ref:
            opt = ctx.supplier.reschedule_option(t_order.supplier_ref, datetime.fromisoformat(pick["suggested"]))
            fee = opt.fee
            options.append({
                "id": "move_pickup", "title": opt.title, "delta": fee.model_dump(), "new_total": (base_total + fee).model_dump(),
                "requires_approval": fee.amount_minor > 0, "recommended": True,
                "summary": f"Driver meets you at {datetime.fromisoformat(pick['suggested']):%H:%M}; {opt.details['policy']}",
                "changes": [{"component": "transfer_in", "order_id": t_order.id, "action": "reschedule",
                             "from": pick["scheduled"], "to": pick["suggested"], "fee": fee.model_dump()}],
                "_action_token": opt.action_token})
    for opt in options:
        opt["scope"] = {"trip_id": trip.id, "purpose": "change", "option": opt["id"], "changes": opt["changes"],
                        "delta_minor": opt["delta"]["amount_minor"], "currency": "INR"}
        opt["scope_digest"] = receipts.scope_digest(opt["scope"])
    d = Disruption(id=new_id("dis"), trip_id=trip.id, kind="flight_delay", order_id=flight_order.id,
                   summary=f"{flight_order.details.get('number', 'Flight')} delayed {delay} min · lands "
                           f"{datetime.fromisoformat(a['new_arrive']):%H:%M}",
                   details=a, impacts=a["impacts"], options=options, status="open", created_at=ctx.clock.now())
    ctx.db.add(d)
    status_of = {i["item_id"]: ("missed" if i["severity"] == "high" else "at_risk")
                 for i in a["impacts"] if i["severity"] in ("high", "medium")}
    new_items = []
    for it in items:
        upd: dict[str, Any] = {}
        if it.component == flight_order.component:
            upd = {"start": a["new_depart"][:16], "end": a["new_arrive"][:16], "status": "changed",
                   "meta": {**it.meta, "delay_min": int(flight_order.details.get("delay_min", delay))}}
        elif it.id in status_of:
            upd = {"status": status_of[it.id]}
        new_items.append(it.model_copy(update=upd))
    add_revision(ctx, trip, d.summary, new_items, rev.costs if rev else {})
    ledger.append(ctx, trip.id, "disruption.detected", "supplier", d.summary,
                  {"disruption_id": d.id, "impacts": [i["title"] for i in a["impacts"]]})
    ctx.emit(trip.id, {"type": "disruption", "id": d.id, "summary": d.summary})
    return d


def approve_option(ctx: Ctx, trip: Trip, disruption_id: str, option_id: str, scope_digest: str,
                   amount_minor: int) -> ApprovalReceipt:
    d, opt = _option(ctx, trip, disruption_id, option_id)
    if scope_digest != opt["scope_digest"]:
        raise DomainError("consent.stale", "This option changed; review it again")
    if amount_minor != opt["delta"]["amount_minor"]:
        raise DomainError("consent.amount_mismatch", "Approved amount does not match the option")
    r = _issue_receipt(ctx, trip, "change", opt["scope"], Money(amount_minor=amount_minor))
    ledger.append(ctx, trip.id, "approval.granted", "traveller",
                  f"Approved change '{opt['title']}' for {Money(amount_minor=amount_minor).format()}",
                  {"receipt_id": r.id, "scope_digest": r.scope_digest, "disruption_id": d.id})
    return r


def _option(ctx: Ctx, trip: Trip, disruption_id: str, option_id: str) -> tuple[Disruption, dict[str, Any]]:
    d = ctx.db.get(Disruption, disruption_id)
    if not d or d.trip_id != trip.id:
        raise NotFound("disruption.not_found", "Disruption not found")
    if d.status != "open":
        raise DomainError("disruption.resolved", "This disruption is already resolved")
    opt = next((o for o in d.options if o["id"] == option_id), None)
    if not opt:
        raise NotFound("option.not_found", "Option not found")
    return d, opt


def resolve(ctx: Ctx, trip: Trip, disruption_id: str, option_id: str, receipt_id: str | None) -> Disruption:
    d, opt = _option(ctx, trip, disruption_id, option_id)
    if opt["requires_approval"]:
        if not receipt_id:
            raise DomainError("consent.missing", "This change costs extra and needs your approval", status_code=403)
        r = _check_receipt(ctx, trip, receipt_id, "change", opt["scope_digest"])
        r.consumed_at = ctx.clock.now()
    rev = current_revision(ctx, trip)
    items = [ItineraryItem.model_validate(i) for i in (rev.itinerary if rev else [])]
    costs = rev.costs if rev else {}
    for ch in opt["changes"]:
        order = ctx.db.get(Order, ch["order_id"])
        if not order or not order.supplier_ref:
            raise DomainError("order.missing", "Booking to change was not found")
        fee = Money.model_validate(ch["fee"])
        try:
            sb = ctx.supplier.apply_service_action(order.supplier_ref, opt["_action_token"], fee)
        except SupplierError as e:
            raise DomainError("supplier.rejected", f"{order.supplier}: {e.message}") from e
        order.details = {**order.details, "pickup_at": sb.details.get("pickup_at")}
        if fee.amount_minor:
            ctx.db.add(Order(id=new_id("ord"), trip_id=trip.id, quote_id=None, receipt_id=receipt_id or "",
                             component=f"{order.component}:change", kind="fee", supplier=order.supplier,
                             title=f"{order.title} · reschedule fee", supplier_ref=order.supplier_ref,
                             confirmation_code=order.confirmation_code, status="confirmed", amount_minor=fee.amount_minor,
                             currency=fee.currency, details={"disruption_id": d.id}, created_at=ctx.clock.now(),
                             updated_at=ctx.clock.now(), confirmed_at=ctx.clock.now()))
        new_start = datetime.fromisoformat(ch["to"])
        upd_items = []
        for it in items:
            if it.component == ch["component"]:
                dur = (datetime.fromisoformat(it.end) - datetime.fromisoformat(it.start)) if it.end else timedelta()
                it = it.model_copy(update={"start": planner.iso(new_start), "end": planner.iso(new_start + dur),
                                           "status": "changed", "meta": {**it.meta, "rescheduled_from": ch["from"][:16]}})
            upd_items.append(it)
        items = upd_items
        if fee.amount_minor:
            lines = [ln for ln in costs["lines"] if ln["key"] != "fees"]
            prev_fees = next((Money.model_validate(ln["amount"]) for ln in costs["lines"] if ln["key"] == "fees"), Money.zero())
            fees = prev_fees + fee
            lines.insert(len(lines) - 1, {"key": "fees", "label": "Change fees", "amount": fees.model_dump(), "source": "fee",
                                          "detail": None})
            bookable = Money.model_validate(costs["bookable_total"]) + fee
            costs = {**costs, "lines": lines, "bookable_total": bookable.model_dump(),
                     "total": (bookable + Money.model_validate(costs["extras_total"])).model_dump()}
    d.status = "resolved"
    d.resolved_at = ctx.clock.now()
    d.resolution = {"option_id": option_id, "receipt_id": receipt_id}
    if opt["changes"]:
        add_revision(ctx, trip, f"{opt['title']} · total {Money.model_validate(costs['total']).format()}", items, costs)
    ledger.append(ctx, trip.id, "disruption.resolved", "traveller",
                  f"Chose '{opt['title']}'" + ("" if opt["changes"] else " (risk accepted)"),
                  {"disruption_id": d.id, "option": option_id, "delta_minor": opt["delta"]["amount_minor"]})
    ctx.emit(trip.id, {"type": "trip"})
    return d


def inject_delay(ctx: Ctx, trip: Trip, component: str, minutes: int) -> Disruption | None:
    if trip.workflow != "done":
        raise DomainError("workflow.invalid_transition", "Delays can be simulated once the trip is booked")
    o = next((x for x in trip_orders(ctx, trip) if x.component == component and x.status == "confirmed"), None)
    if not o or not o.supplier_ref:
        raise NotFound("order.not_found", "No confirmed flight to delay")
    ctx.supplier.inject_flight_delay(o.supplier_ref, minutes)
    ledger.append(ctx, trip.id, "simulator.delay_injected", "system", f"Simulator: {o.title} delayed {minutes} min",
                  {"order_id": o.id, "minutes": minutes})
    before = {x.id for x in ctx.db.scalars(select(Disruption).where(Disruption.trip_id == trip.id))}
    ctx.db.flush()
    _detect_schedule_changes(ctx, trip, trip_orders(ctx, trip))
    ctx.db.flush()
    after = list(ctx.db.scalars(select(Disruption).where(Disruption.trip_id == trip.id)))
    return next((x for x in after if x.id not in before), None)


def airport_for(code: str):  # re-exported for views
    return airport_place(code)
