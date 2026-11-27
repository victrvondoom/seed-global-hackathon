"""Booking workflow: Search → Reprice → Await approval → Execute → Reconcile.
Explicit transitions; anything else is refused."""
from __future__ import annotations

from app.core.errors import DomainError

STATES = ("idle", "search", "reprice", "await_approval", "execute", "reconcile", "done")
STAGE_OF = {"idle": "plan", "search": "plan", "reprice": "verify", "await_approval": "verify",
            "execute": "verify", "reconcile": "book", "done": "adapt"}

TRANSITIONS: dict[str, set[str]] = {
    "idle": {"search"},
    "search": {"search", "reprice"},
    "reprice": {"await_approval", "search", "reprice"},
    "await_approval": {"execute", "reprice", "search", "await_approval"},
    "execute": {"reconcile", "reprice"},
    "reconcile": {"done", "reprice", "reconcile"},
    "done": {"done"},
}


def can(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def require(current: str, target: str) -> str:
    if not can(current, target):
        raise DomainError("workflow.invalid_transition",
                          f"Cannot move from '{current}' to '{target}'",
                          details={"from": current, "to": target, "allowed": sorted(TRANSITIONS.get(current, set()))})
    return target
