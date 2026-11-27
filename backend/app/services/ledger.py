from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.ledger import GENESIS, event_hash, verify_chain
from app.models import AuditEvent
from app.services.runtime import Ctx


def append(ctx: Ctx, trip_id: str, type_: str, actor: str, summary: str, payload: dict[str, Any] | None = None) -> AuditEvent:
    last = ctx.db.scalar(select(AuditEvent).where(AuditEvent.trip_id == trip_id).order_by(AuditEvent.seq.desc()).limit(1))
    seq = last.seq + 1 if last else 1
    prev = last.hash if last else GENESIS
    ts = ctx.clock.now().isoformat(timespec="microseconds")
    payload = payload or {}
    h = event_hash(prev, seq=seq, type=type_, actor=actor, summary=summary, payload=payload, ts=ts)
    ev = AuditEvent(trip_id=trip_id, seq=seq, type=type_, actor=actor, summary=summary, payload=payload, ts=ts,
                    prev_hash=prev, hash=h)
    ctx.db.add(ev)
    ctx.db.flush()
    ctx.emit(trip_id, {"type": "ledger", "seq": seq, "event": type_, "actor": actor, "summary": summary})
    return ev


def events(ctx: Ctx, trip_id: str) -> list[AuditEvent]:
    return list(ctx.db.scalars(select(AuditEvent).where(AuditEvent.trip_id == trip_id).order_by(AuditEvent.seq)))


def verify(ctx: Ctx, trip_id: str) -> dict[str, Any]:
    evs = events(ctx, trip_id)
    ok, bad = verify_chain(evs)
    return {"verified": ok, "first_bad_seq": bad, "count": len(evs), "head": evs[-1].hash if evs else GENESIS}
