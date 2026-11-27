from __future__ import annotations

import asyncio
import json
import re
import time as _time
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.errors import DomainError, InvalidInput, NotFound, Unauthorized
from app.core.ids import new_id
from app.core.security import create_access_token, hash_password, verify_password
from app.domain import forge
from app.domain.money import Money
from app.models import Trip, User
from app.services import copilot, ledger, trips, views
from app.services.runtime import Ctx
from app.suppliers.simulator.catalog import AIRPORTS, DESTINATIONS
from app.api.deps import current_user, get_ctx, owned_trip

router = APIRouter(prefix="/api")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,}$", re.I)


def _auth(user: User, ctx: Ctx) -> dict:
    return {"token": create_access_token(user.id, ctx.settings),
            "user": {"id": user.id, "name": user.name, "email": user.email, "is_guest": user.is_guest}}


class GuestIn(BaseModel):
    name: str = Field(default="Traveller", min_length=1, max_length=60)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


@router.post("/auth/guest")
def guest(body: GuestIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    u = User(id=new_id("usr"), name=body.name.strip(), is_guest=True, created_at=ctx.clock.now())
    ctx.db.add(u)
    ctx.commit()
    return _auth(u, ctx)


@router.post("/auth/register")
def register(body: RegisterIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise InvalidInput("auth.email", "Enter a valid email address")
    if ctx.db.query(User).filter(User.email == email).first():
        raise DomainError("auth.taken", "An account with this email already exists")
    u = User(id=new_id("usr"), name=body.name.strip(), email=email, password_hash=hash_password(body.password),
             is_guest=False, created_at=ctx.clock.now())
    ctx.db.add(u)
    ctx.commit()
    return _auth(u, ctx)


@router.post("/auth/login")
def login(body: LoginIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    u = ctx.db.query(User).filter(User.email == body.email.strip().lower()).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise Unauthorized("auth.failed", "Email or password is incorrect")
    return _auth(u, ctx)


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "is_guest": user.is_guest}


@router.get("/meta")
def meta(ctx: Ctx = Depends(get_ctx)) -> dict:
    s = ctx.settings
    return {"ai": {"provider": ctx.rt.ai.name, "model": ctx.rt.ai.model}, "routes": ctx.rt.routes.name,
            "supplier": "simulator", "lab_enabled": s.simulator_lab_enabled, "today": ctx.clock.today().isoformat(),
            "destinations": len(DESTINATIONS), "airports": len(AIRPORTS),
            "experiences": sum(len(d.activities) for d in DESTINATIONS.values()),
            "hotels": sum(len(d.hotels) for d in DESTINATIONS.values())}


@router.get("/catalog/destinations")
def destinations(ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    m = ctx.clock.today().month
    return [{"code": d.code, "city": d.city, "region": d.region, "country": d.country, "tagline": d.tagline,
             "tags": list(d.tags), "lat": AIRPORTS[d.code].lat, "lng": AIRPORTS[d.code].lng,
             "international": d.international, "climate_now": {"high_c": d.climate[m - 1][0], "rain_mm": d.climate[m - 1][1]},
             "best_months": [i + 1 for i, (hi, rain) in enumerate(d.climate) if rain < 80 and 12 <= hi <= 33]}
            for d in DESTINATIONS.values()]


@router.get("/catalog/airports")
def airports() -> list[dict]:
    return [{"code": a.code, "city": a.city, "name": a.name, "lat": a.lat, "lng": a.lng, "country": a.country}
            for a in AIRPORTS.values()]


# ---- trips -----------------------------------------------------------------------------
@router.get("/trips")
def list_trips(ctx: Ctx = Depends(get_ctx), user: User = Depends(current_user)) -> list[dict]:
    ts = ctx.db.query(Trip).filter(Trip.owner_id == user.id).order_by(Trip.updated_at.desc()).all()
    out = []
    for t in ts:
        rev = trips.current_revision(ctx, t)
        out.append({**views.trip_summary(t), "total": rev.costs.get("total") if rev else None})
    return out


@router.post("/trips", status_code=201)
def create_trip(ctx: Ctx = Depends(get_ctx), user: User = Depends(current_user)) -> dict:
    t = trips.create_trip(ctx, user.id)
    copilot.greet(ctx, t, user.name)
    ctx.commit()
    return views.trip_view(ctx, t)


@router.get("/trips/{trip_id}")
def get_trip(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    return views.trip_view(ctx, trip)


@router.get("/trips/{trip_id}/messages")
def get_messages(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    return views.messages_view(ctx, trip)


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=1000)
    action: dict | None = None


@router.post("/trips/{trip_id}/messages")
def post_message(body: MessageIn, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    if not ctx.rt.chat_limiter.allow(ctx.user_id or "", _time.monotonic()):
        raise DomainError("rate_limited", "Too many messages; wait a moment", status_code=429)
    msgs = copilot.handle(ctx, trip, body.text, body.action)
    ctx.commit()
    return {"messages": [{"id": m.id, "role": m.role, "parts": m.parts, "created_at": m.created_at.isoformat()}
                         for m in msgs], "trip": views.trip_view(ctx, trip)}


@router.post("/trips/{trip_id}/search")
def run_search(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    if views.trip_view(ctx, trip)["missing"]:
        raise DomainError("intent.incomplete", "Tell the copilot what's missing first")
    trips.search(ctx, trip)
    ctx.commit()
    return views.trip_view(ctx, trip)


@router.post("/trips/{trip_id}/plans/{plan_id}/select")
def select(plan_id: str, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    trips.select_plan(ctx, trip, plan_id)
    ctx.commit()
    return views.trip_view(ctx, trip)


@router.post("/trips/{trip_id}/plans/release")
def release(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    if trip.workflow not in ("reprice", "await_approval"):
        raise DomainError("workflow.invalid_transition", "Nothing selected to release")
    trips.release_selection(ctx, trip, "Kept comparing")
    trip.workflow, trip.stage = "search", "plan"
    ctx.commit()
    return views.trip_view(ctx, trip)


@router.post("/trips/{trip_id}/checkout/reprice")
def reprice(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    trips.reprice(ctx, trip)
    ctx.commit()
    return views.trip_view(ctx, trip)


class ApproveIn(BaseModel):
    scope_digest: str = Field(min_length=64, max_length=64)
    amount_minor: int
    currency: str = Field(default="INR", min_length=3, max_length=3)


@router.post("/trips/{trip_id}/checkout/approve")
def approve(body: ApproveIn, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    r = trips.approve(ctx, trip, body.scope_digest, body.amount_minor, body.currency)
    ctx.commit()
    return {"receipt_id": r.id, "scope_digest": r.scope_digest, "expires_at": r.expires_at.isoformat()}


class ExecuteIn(BaseModel):
    receipt_id: str = Field(max_length=40)


@router.post("/trips/{trip_id}/checkout/execute")
def execute(body: ExecuteIn, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx),
            idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100)) -> dict:
    res = trips.execute(ctx, trip, body.receipt_id, idempotency_key)
    ctx.commit()
    trips.schedule_reconcile(ctx.rt, trip.id)
    return {**res, "trip": views.trip_view(ctx, trip)}


@router.post("/trips/{trip_id}/checkout/reconcile")
def reconcile(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    trips.reconcile(ctx, trip)
    ctx.commit()
    return views.trip_view(ctx, trip)


class OptionApproveIn(BaseModel):
    scope_digest: str = Field(min_length=64, max_length=64)
    amount_minor: int


@router.post("/trips/{trip_id}/disruptions/{did}/options/{oid}/approve")
def approve_option(did: str, oid: str, body: OptionApproveIn, trip: Trip = Depends(owned_trip),
                   ctx: Ctx = Depends(get_ctx)) -> dict:
    r = trips.approve_option(ctx, trip, did, oid, body.scope_digest, body.amount_minor)
    ctx.commit()
    return {"receipt_id": r.id}


class ResolveIn(BaseModel):
    receipt_id: str | None = Field(default=None, max_length=40)


@router.post("/trips/{trip_id}/disruptions/{did}/options/{oid}/apply")
def apply_option(did: str, oid: str, body: ResolveIn, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    trips.resolve(ctx, trip, did, oid, body.receipt_id)
    ctx.commit()
    return views.trip_view(ctx, trip)


@router.get("/trips/{trip_id}/ledger")
def get_ledger(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    evs = ledger.events(ctx, trip.id)
    return {**ledger.verify(ctx, trip.id), "events": [
        {"seq": e.seq, "type": e.type, "actor": e.actor, "summary": e.summary, "ts": e.ts, "hash": e.hash,
         "prev_hash": e.prev_hash} for e in evs]}


@router.get("/trips/{trip_id}/forge")
def get_forge(trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    intent = trips.intent_of(trip)
    dest = DESTINATIONS.get(intent.destination or "")
    if not dest or not intent.origin or not intent.nights or not intent.travellers:
        raise DomainError("forge.needs_intent", "Tell the copilot where, from where, how long and who's going first")
    rev = trips.current_revision(ctx, trip)
    plans = (trip.search or {}).get("plans") or []
    costs = rev.costs if rev else (plans[0]["costs"] if plans else None)
    today = ctx.clock.today()
    start = intent.start_date or (today + timedelta(days=30))
    if costs:
        lines = {ln["key"]: ln["amount"]["amount_minor"] for ln in costs["lines"]}
        total = Money.model_validate(costs["total"])
    else:
        from app.domain.planner import quick_estimate
        total = quick_estimate(intent, ctx.supplier, intent.origin, dest.code, start, intent.nights) or Money.of(0)
        lines = {"flights": int(total.amount_minor * 0.4), "hotel": int(total.amount_minor * 0.4)}
    t = max(1, total.amount_minor)
    shares = {"flights": lines.get("flights", 0) / t, "hotel": lines.get("hotel", 0) / t}
    out = forge.forecast(base_total=total, shares=shares, dest=dest, travel_date=start, today=today,
                         days_to_departure=(start - today).days)
    return {**out, "basis": "selected plan" if rev else ("best plan" if plans else "quick estimate"),
            "booked": trip.workflow == "done", "travel_date": start.isoformat(), "route": trip.title}


# ---- simulator lab ---------------------------------------------------------------------
def _lab(ctx: Ctx) -> None:
    if not ctx.settings.simulator_lab_enabled:
        raise NotFound("lab.disabled", "Simulator lab is disabled")


class MarketIn(BaseModel):
    force_fare_move: bool | None = None
    mode: str | None = Field(default=None, pattern="^(auto|calm)$")


@router.post("/trips/{trip_id}/lab/market")
def lab_market(body: MarketIn, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    _lab(ctx)
    st = ctx.supplier.set_market(trip.id, force_fare_move=body.force_fare_move, mode=body.mode)
    ledger.append(ctx, trip.id, "simulator.market", "system",
                  "Simulator: next recheck moves the outbound fare" if st.get("force_next") else f"Simulator market: {st['mode']}")
    ctx.commit()
    return st


class DelayIn(BaseModel):
    component: str = Field(default="flight_out", pattern="^flight_(out|ret)$")
    minutes: int = Field(default=120, ge=15, le=720)


@router.post("/trips/{trip_id}/lab/delay")
def lab_delay(body: DelayIn, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> dict:
    _lab(ctx)
    trips.inject_delay(ctx, trip, body.component, body.minutes)
    ctx.commit()
    return views.trip_view(ctx, trip)


@router.get("/trips/{trip_id}/events")
async def events(request: Request, trip: Trip = Depends(owned_trip), ctx: Ctx = Depends(get_ctx)) -> StreamingResponse:
    bus = ctx.rt.bus
    trip_id = trip.id
    ctx.db.close()
    q = bus.subscribe(trip_id)

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {ev.get('type', 'trip')}\ndata: {json.dumps(ev)}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(trip_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
