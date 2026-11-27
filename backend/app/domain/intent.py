"""TripIntent: what the traveller wants, gathered over several turns."""
from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.money import Money

SLOT_ORDER = ("destination", "origin", "nights", "travellers", "start_date")


class Travellers(BaseModel):
    adults: int = Field(ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)

    @property
    def total(self) -> int:
        return self.adults + self.children

    def label(self) -> str:
        a = f"{self.adults} adult{'s' if self.adults != 1 else ''}"
        if self.children:
            a += f" + {self.children} child{'ren' if self.children != 1 else ''}"
        return a


class Constraints(BaseModel):
    depart_after: time | None = None
    depart_before: time | None = None
    max_stops: int | None = None
    hotel_min_rating: float | None = None
    hotel_tier: Literal["budget", "mid", "premium"] | None = None


class TripIntent(BaseModel):
    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    nights: int | None = Field(default=None, ge=1, le=21)
    month: int | None = Field(default=None, ge=1, le=12)
    travellers: Travellers | None = None
    budget: Money | None = None
    interests: list[str] = Field(default_factory=list)
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    constraints: Constraints = Field(default_factory=Constraints)

    def search_key(self) -> dict:
        return self.model_dump(mode="json", exclude={"month"})


class IntentPatch(BaseModel):
    """What one message changed. None = not mentioned."""

    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    nights: int | None = None
    month: int | None = None
    travellers: Travellers | None = None
    budget: Money | None = None
    interests: list[str] = Field(default_factory=list)
    pace: Literal["relaxed", "balanced", "packed"] | None = None
    constraints: dict = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    unknown_places: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.model_dump(exclude_defaults=True))


def merge(intent: TripIntent, patch: IntentPatch) -> tuple[TripIntent, list[str]]:
    if "reset" in patch.commands:
        intent = TripIntent()
    data = intent.model_dump()
    changed: list[str] = []
    for f in patch.clear:
        if f in data and data[f] is not None:
            data[f] = None
            changed.append(f)
    for f in ("origin", "destination", "start_date", "nights", "month", "travellers", "budget", "pace"):
        v = getattr(patch, f)
        if v is None:
            continue
        v = v.model_dump() if isinstance(v, BaseModel) else v
        if data.get(f) != v:
            data[f] = v
            changed.append(f)
    if patch.start_date is not None:
        data["month"] = None
    new_interests = [i for i in patch.interests if i not in data["interests"]]
    if new_interests:
        data["interests"] = data["interests"] + new_interests
        changed.append("interests")
    for k, v in patch.constraints.items():
        if data["constraints"].get(k) != v:
            data["constraints"][k] = v
            changed.append(f"constraints.{k}")
    return TripIntent.model_validate(data), changed


def missing_slots(intent: TripIntent) -> list[str]:
    return [s for s in SLOT_ORDER if getattr(intent, s) is None]


SEARCH_FIELDS = {"origin", "destination", "start_date", "nights", "travellers", "budget", "interests", "pace"}


def affects_search(changed: list[str]) -> bool:
    return any(c in SEARCH_FIELDS or c.startswith("constraints.") for c in changed)
