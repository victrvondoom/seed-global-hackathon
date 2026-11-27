from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel


def _default(o: Any) -> Any:
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    if isinstance(o, (datetime, date, time)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not canonicalisable: {type(o)!r}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def digest(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def hmac_ok(secret: str, message: str, signature: str) -> bool:
    return hmac.compare_digest(hmac_hex(secret, message), signature)
