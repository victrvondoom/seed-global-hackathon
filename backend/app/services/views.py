from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.intent import missing_slots
from app.models import ApprovalReceipt, ChatMessage, Disruption, Order, Trip, TripRevision, Voucher
from app.services import ledger, trips
from app.services.runtime import Ctx


def _strip(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _strip(v) for k, v in d.items() if not k.startswith("_") and k != "offer_token"}
    if isinstance(d, list):
        return [_strip(x) for x in d]
    return d


def trip_summary(t: Trip) -> dict[str, Any]:
    return {"id": t.id, "title": t.title, "stage": t.stage, "workflow": t.workflow, "intent": t.intent,
            "updated_at": t.updated_at.isoformat(), "created_at": t.created_at.isoformat()}


def trip_view(ctx: Ctx, t: Trip) -> dict[str, Any]:
    db = ctx.db
    rev = trips.current_revision(ctx, t)
    orders = trips.trip_orders(ctx, t)
    vouchers = {v.order_id: v for v in db.scalars(select(Voucher).where(Voucher.trip_id == t.id))}
    disruptions = list(db.scalars(select(Disruption).where(Disruption.trip_id == t.id).order_by(Disruption.created_at)))
    revs = list(db.scalars(select(TripRevision).where(TripRevision.trip_id == t.id).order_by(TripRevision.number)))
    rcps = list(db.scalars(select(ApprovalReceipt).where(ApprovalReceipt.trip_id == t.id).order_by(ApprovalReceipt.created_at)))
    return {
        **trip_summary(t),
        "missing": missing_slots(trips.intent_of(t)),
        "search": _strip(t.search),
        "selected_plan_id": t.selected_plan_id,
        "checkout": _strip(t.checkout),
        "revision": None if not rev else {"number": rev.number, "reason": rev.reason, "itinerary": rev.itinerary,
                                          "costs": rev.costs, "created_at": rev.created_at.isoformat()},
        "revisions": [{"number": r.number, "reason": r.reason, "total": r.costs.get("total"),
                       "created_at": r.created_at.isoformat()} for r in revs],
        "orders": [{"id": o.id, "component": o.component, "kind": o.kind, "title": o.title, "supplier": o.supplier,
                    "supplier_ref": o.supplier_ref, "confirmation_code": o.confirmation_code, "status": o.status,
                    "amount": {"amount_minor": o.amount_minor, "currency": o.currency},
                    "details": _strip(o.details), "voucher": None if o.id not in vouchers else {
                        "id": vouchers[o.id].id, "code": vouchers[o.id].code, "payload": vouchers[o.id].payload,
                        "signature": vouchers[o.id].signature[:16], "issued_at": vouchers[o.id].issued_at.isoformat()}}
                   for o in orders],
        "disruptions": [{"id": d.id, "kind": d.kind, "summary": d.summary, "status": d.status, "impacts": d.impacts,
                         "options": _strip(d.options), "resolution": d.resolution, "details": d.details,
                         "created_at": d.created_at.isoformat()} for d in disruptions],
        "receipts": [{"id": r.id, "purpose": r.purpose, "amount": {"amount_minor": r.amount_minor, "currency": r.currency},
                      "scope_digest": r.scope_digest, "created_at": r.created_at.isoformat(),
                      "expires_at": r.expires_at.isoformat(), "consumed": r.consumed_at is not None} for r in rcps],
        "ledger": ledger.verify(ctx, t.id),
        "market": ctx.supplier.market(t.id),
    }


def messages_view(ctx: Ctx, t: Trip) -> list[dict[str, Any]]:
    ms = ctx.db.scalars(select(ChatMessage).where(ChatMessage.trip_id == t.id).order_by(ChatMessage.created_at))
    return [{"id": m.id, "role": m.role, "parts": m.parts, "created_at": m.created_at.isoformat()} for m in ms]
