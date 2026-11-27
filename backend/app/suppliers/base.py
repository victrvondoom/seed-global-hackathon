"""Supplier adapter contract. Prices, availability, booking status and refund terms come only
from here. The simulator implements it today; sandbox/real adapters implement the same shape."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from app.domain.intent import Travellers
from app.domain.money import Money
from app.domain.types import Place


class FlightOffer(BaseModel):
    kind: Literal["flight"] = "flight"
    offer_token: str
    flight_key: str
    airline: str
    number: str
    origin: str
    destination: str
    depart: datetime  # local wall time at origin
    arrive: datetime  # local wall time at destination
    duration_min: int
    stops: int
    via: str | None = None
    fare_family: str
    seats_left: int | None
    price: Money
    terms: dict[str, Any]


class HotelOffer(BaseModel):
    kind: Literal["hotel"] = "hotel"
    offer_token: str
    hotel_id: str
    name: str
    area: str
    place: Place
    rating: float
    tier: str
    check_in: date
    check_out: date
    check_in_time: str
    check_out_time: str
    nights: int
    rooms: int
    rate_plan: Literal["flexible", "saver"]
    price: Money
    terms: dict[str, Any]


class TransferOffer(BaseModel):
    kind: Literal["transfer"] = "transfer"
    offer_token: str
    provider: str
    vehicle: str
    direction: Literal["arrival", "departure"]
    pickup_at: datetime
    from_place: Place
    to_place: Place
    distance_km: float
    duration_min: int
    price: Money
    terms: dict[str, Any]


class ActivityOffer(BaseModel):
    kind: Literal["activity"] = "activity"
    offer_token: str
    activity_id: str
    name: str
    area: str
    place: Place
    tags: list[str]
    starts_at: datetime
    duration_min: int
    full_day: bool
    strenuous: bool
    provider: str
    price: Money
    terms: dict[str, Any]


Offer = FlightOffer | HotelOffer | TransferOffer | ActivityOffer


class RecheckResult(BaseModel):
    available: bool
    price: Money | None
    previous_price: Money
    reason: str | None
    offer_token: str | None
    terms: dict[str, Any]
    details: dict[str, Any]


class SupplierBooking(BaseModel):
    ref: str
    kind: str
    status: Literal["pending", "confirmed", "cancelled", "failed"]
    confirmation_code: str | None
    amount: Money
    details: dict[str, Any]


class ServiceOption(BaseModel):
    action_token: str
    title: str
    fee: Money
    details: dict[str, Any]


class SupplierError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SupplierAdapter(Protocol):
    name: str

    def search_flights(self, origin: str, destination: str, day: date, pax: Travellers) -> list[FlightOffer]: ...
    def search_hotels(self, dest: str, check_in: date, nights: int, pax: Travellers) -> list[HotelOffer]: ...
    def quote_transfer(self, dest: str, direction: str, from_place: Place, to_place: Place,
                       pickup_at: datetime, pax: Travellers) -> TransferOffer: ...
    def search_activities(self, dest: str, day: date, pax: Travellers) -> list[ActivityOffer]: ...
    def recheck(self, offer_token: str, *, trip_id: str, trip_seed: int, attempt: int) -> RecheckResult: ...
    def create_booking(self, offer_token: str, expected: Money, idempotency_key: str) -> SupplierBooking: ...
    def get_booking(self, ref: str) -> SupplierBooking: ...
    def cancel_booking(self, ref: str) -> SupplierBooking: ...
    def reschedule_option(self, ref: str, new_time: datetime) -> ServiceOption: ...
    def apply_service_action(self, ref: str, action_token: str, expected_fee: Money) -> SupplierBooking: ...
