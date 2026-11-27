"""Shared value objects of the trip record."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.money import Money

Source = Literal["supplier", "estimate", "fee"]
CheckStatus = Literal["pass", "warn", "block"]


class Place(BaseModel):
    name: str
    lat: float
    lng: float


class ItineraryItem(BaseModel):
    id: str
    day: int
    date: str
    kind: Literal["flight", "transfer", "hotel", "activity", "leg", "note"]
    title: str
    subtitle: str | None = None
    start: str  # local wall time "YYYY-MM-DDTHH:MM"
    end: str | None = None
    place: Place | None = None
    to_place: Place | None = None
    component: str | None = None
    cost: Money | None = None
    source: Source = "supplier"
    status: Literal["planned", "quoted", "booked", "changed", "at_risk", "missed", "cancelled"] = "planned"
    meta: dict[str, Any] = Field(default_factory=dict)


class CostLine(BaseModel):
    key: str
    label: str
    amount: Money
    source: Source
    detail: str | None = None


class CostBreakdown(BaseModel):
    lines: list[CostLine]
    bookable_total: Money
    extras_total: Money
    total: Money


class RuleCheck(BaseModel):
    code: str
    status: CheckStatus
    title: str
    detail: str


class PlanComponent(BaseModel):
    key: str
    kind: Literal["flight", "hotel", "transfer", "activity"]
    title: str
    subtitle: str
    supplier: str
    price: Money
    offer_token: str
    terms: dict[str, Any]
    details: dict[str, Any]


class Plan(BaseModel):
    id: str
    strategy: Literal["value", "comfort", "experience"]
    name: str
    headline: str
    components: list[PlanComponent]
    itinerary: list[ItineraryItem]
    costs: CostBreakdown
    highlights: list[str]
    checks: list[RuleCheck]
    within_budget: bool | None
    budget_delta: Money | None
    score: float


class ToolCall(BaseModel):
    tool: str
    args: str
    result: str
