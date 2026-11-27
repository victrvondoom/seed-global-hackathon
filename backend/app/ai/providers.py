"""AI lane (read-only). Extracts a TripIntent patch and optionally phrases replies.
It never sees offer tokens, never writes bookings, and its output is validated before use."""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from app.domain import nlu
from app.domain.intent import IntentPatch, Travellers, TripIntent
from app.domain.money import Money
from app.suppliers.simulator.catalog import AIRPORTS, TAGS

log = logging.getLogger(__name__)


class AIProvider(Protocol):
    name: str
    model: str | None

    def extract(self, text: str, current: TripIntent, today: date) -> tuple[IntentPatch, str]: ...
    def phrase(self, facts: dict[str, Any], draft: str) -> str | None: ...


class HeuristicProvider:
    name = "heuristic"
    model = None

    def extract(self, text: str, current: TripIntent, today: date) -> tuple[IntentPatch, str]:
        return nlu.extract(text, today), "deterministic parser"

    def phrase(self, facts: dict[str, Any], draft: str) -> str | None:
        return None


INTENT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "origin_iata": {"type": "STRING", "nullable": True},
        "destination_iata": {"type": "STRING", "nullable": True},
        "start_date": {"type": "STRING", "nullable": True, "description": "YYYY-MM-DD"},
        "nights": {"type": "INTEGER", "nullable": True},
        "month": {"type": "INTEGER", "nullable": True},
        "adults": {"type": "INTEGER", "nullable": True},
        "children": {"type": "INTEGER", "nullable": True},
        "budget_amount": {"type": "NUMBER", "nullable": True},
        "budget_currency": {"type": "STRING", "nullable": True},
        "interests": {"type": "ARRAY", "items": {"type": "STRING", "enum": list(TAGS)}},
        "pace": {"type": "STRING", "nullable": True, "enum": ["relaxed", "balanced", "packed"]},
        "depart_after": {"type": "STRING", "nullable": True, "description": "HH:MM"},
        "max_stops": {"type": "INTEGER", "nullable": True},
        "commands": {"type": "ARRAY", "items": {"type": "STRING", "enum": ["reset", "cheaper", "change_dates"]}},
    },
}

SYSTEM = (
    "You extract travel-planning intent from one traveller message for an Indian travel copilot. "
    "Return only fields the message states or clearly implies; leave others null. Map cities to IATA "
    "codes from this list only: {airports}. 'N days' means N-1 nights. Never invent prices, availability, "
    "bookings or dates. Today is {today}. Current known intent: {current}."
)


class GeminiProvider:
    """Gemini via REST with a response schema. Falls back to the deterministic parser on any error."""

    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str, timeout: float = 12.0, client: httpx.Client | None = None) -> None:
        self.model = model
        self._key = api_key
        self._client = client or httpx.Client(timeout=timeout)
        self._fallback = HeuristicProvider()

    def _call(self, system: str, user: str, schema: dict[str, Any] | None, temperature: float) -> str:
        cfg: dict[str, Any] = {"temperature": temperature}
        if schema:
            cfg |= {"responseMimeType": "application/json", "responseSchema": schema}
        r = self._client.post(self.URL.format(model=self.model), headers={"x-goog-api-key": self._key},
                              json={"systemInstruction": {"parts": [{"text": system}]},
                                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                                    "generationConfig": cfg})
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    def extract(self, text: str, current: TripIntent, today: date) -> tuple[IntentPatch, str]:
        airports = ", ".join(f"{a.city}={a.code}" for a in AIRPORTS.values())
        try:
            raw = self._call(SYSTEM.format(airports=airports, today=today.isoformat(),
                                           current=current.model_dump_json(exclude_defaults=True)),
                             text, INTENT_SCHEMA, 0.0)
            return to_patch(json.loads(raw), text, today), f"{self.model}"
        except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError) as e:
            log.warning("gemini extract failed, using deterministic parser: %s", e)
            patch, _ = self._fallback.extract(text, current, today)
            return patch, "deterministic parser (model unavailable)"

    def phrase(self, facts: dict[str, Any], draft: str) -> str | None:
        system = ("Rewrite the draft reply for a traveller in at most two short sentences, warm and plain. "
                  "Use only numbers, times and names that appear in FACTS. Do not claim anything is booked, "
                  "confirmed, available or refundable unless FACTS says so.")
        try:
            return self._call(system, f"FACTS: {json.dumps(facts, default=str)}\nDRAFT: {draft}", None, 0.3).strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            log.warning("gemini phrase failed: %s", e)
            return None


def to_patch(d: dict[str, Any], text: str, today: date) -> IntentPatch:
    """Validate model output: unknown codes are dropped (unknown stays unknown)."""
    heur = nlu.extract(text, today)
    p = IntentPatch()
    o, dst = (d.get("origin_iata") or "").upper(), (d.get("destination_iata") or "").upper()
    p.origin = o if o in AIRPORTS else heur.origin
    p.destination = dst if dst in AIRPORTS else heur.destination
    if d.get("start_date"):
        try:
            sd = date.fromisoformat(d["start_date"])
            p.start_date = sd if sd > today else None
        except ValueError:
            p.start_date = None
    p.nights = d.get("nights") if isinstance(d.get("nights"), int) and 1 <= d["nights"] <= 21 else heur.nights
    p.month = d.get("month") if isinstance(d.get("month"), int) and 1 <= d["month"] <= 12 else heur.month
    if d.get("adults"):
        p.travellers = Travellers(adults=max(1, min(9, int(d["adults"]))), children=max(0, min(8, int(d.get("children") or 0))))
    else:
        p.travellers = heur.travellers
    if d.get("budget_amount"):
        p.budget = Money.of(float(d["budget_amount"]), (d.get("budget_currency") or "INR").upper()[:3])
    else:
        p.budget = heur.budget
    p.interests = [i for i in d.get("interests") or [] if i in TAGS] or heur.interests
    p.pace = d.get("pace") if d.get("pace") in ("relaxed", "packed") else heur.pace
    c = dict(heur.constraints)
    if d.get("depart_after"):
        c["depart_after"] = d["depart_after"][:5]
    if d.get("max_stops") is not None:
        c["max_stops"] = int(d["max_stops"])
    p.constraints = c
    cmds = d.get("commands") or []
    p.commands = [x for x in cmds if x in ("reset", "cheaper")] + [x for x in heur.commands if x.startswith("select:")]
    if "change_dates" in cmds:
        p.clear.append("start_date")
    p.unknown_places = heur.unknown_places
    return p
