"""Hash-chained audit events: each event commits to the previous hash, so any edit or deletion
breaks verification from that point on."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.core.canonical import canonical_json, sha256_hex

GENESIS = "0" * 64


class EventLike(Protocol):
    seq: int
    type: str
    actor: str
    summary: str
    payload: dict[str, Any]
    ts: str
    prev_hash: str
    hash: str


def event_hash(prev_hash: str, *, seq: int, type: str, actor: str, summary: str, payload: dict[str, Any], ts: str) -> str:
    body = canonical_json({"seq": seq, "type": type, "actor": actor, "summary": summary, "payload": payload, "ts": ts})
    return sha256_hex(prev_hash + body)


def verify_chain(events: Sequence[EventLike]) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_seq)."""
    prev = GENESIS
    for i, e in enumerate(events, start=1):
        expected = event_hash(prev, seq=e.seq, type=e.type, actor=e.actor, summary=e.summary, payload=e.payload, ts=e.ts)
        if e.seq != i or e.prev_hash != prev or e.hash != expected:
            return False, e.seq
        prev = e.hash
    return True, None
