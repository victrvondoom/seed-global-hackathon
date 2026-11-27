"""Approval receipts bind explicit consent to an exact scope: these quote ids, these amounts,
these terms. Any change to the scope changes its digest and invalidates the consent."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.canonical import digest, hmac_hex, hmac_ok


def scope_digest(scope: dict[str, Any]) -> str:
    return digest(scope)


def _message(receipt_id: str, user_id: str, trip_id: str, scope_dig: str, amount_minor: int, currency: str,
             expires_at: datetime) -> str:
    return "|".join([receipt_id, user_id, trip_id, scope_dig, str(amount_minor), currency, expires_at.isoformat()])


def sign(secret: str, **kw: Any) -> str:
    return hmac_hex(secret + ":receipts", _message(**kw))


def verify(secret: str, signature: str, **kw: Any) -> bool:
    return hmac_ok(secret + ":receipts", _message(**kw), signature)
