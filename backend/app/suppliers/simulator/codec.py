from __future__ import annotations

import base64
import json
from typing import Any

import hmac

from app.core.canonical import canonical_json, hmac_hex


def encode(payload: dict[str, Any], secret: str) -> str:
    body = base64.urlsafe_b64encode(canonical_json(payload).encode()).decode().rstrip("=")
    return f"{body}.{hmac_hex(secret, body)[:32]}"


def decode(token: str, secret: str) -> dict[str, Any]:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError as e:
        raise ValueError("malformed token") from e
    if not hmac.compare_digest(hmac_hex(secret, body)[:32], sig):
        raise ValueError("bad token signature")
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
