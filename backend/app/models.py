"""Persistence: the shared trip record (preferences, itinerary versions, quotes, orders,
approval receipts, vouchers, disruptions, audit events) plus the simulator's own tables."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str | None] = mapped_column(String(200))
    is_guest: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="New trip")
    stage: Mapped[str] = mapped_column(String(16), default="plan")  # plan|verify|book|adapt
    workflow: Mapped[str] = mapped_column(String(24), default="idle")
    intent: Mapped[dict[str, Any]] = mapped_column(default=dict)
    search: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    selected_plan_id: Mapped[str | None] = mapped_column(String(40))
    checkout: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    current_revision: Mapped[int] = mapped_column(default=0)
    seed: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    parts: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TripRevision(Base):
    __tablename__ = "trip_revisions"
    __table_args__ = (UniqueConstraint("trip_id", "number"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    number: Mapped[int]
    reason: Mapped[str] = mapped_column(String(200))
    itinerary: Mapped[list[Any]] = mapped_column(default=list)
    costs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    component: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    supplier: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    offer_token: Mapped[str] = mapped_column(Text)
    amount_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    terms: Mapped[dict[str, Any]] = mapped_column(default=dict)
    terms_digest: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    firm: Mapped[bool] = mapped_column(default=False)  # indicative (search) vs rechecked
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|superseded|released|booked
    valid_until: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ApprovalReceipt(Base):
    __tablename__ = "approval_receipts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String(16))  # booking|change
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    scope_digest: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    signature: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None] = mapped_column(default=None)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("trip_id", "quote_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    quote_id: Mapped[str | None] = mapped_column(String(40))
    receipt_id: Mapped[str] = mapped_column(String(40))
    component: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))  # flight|hotel|transfer|activity|fee
    supplier: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    supplier_ref: Mapped[str | None] = mapped_column(String(40))
    confirmation_code: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|confirmed|failed|cancelled
    amount_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(default=None)


class Voucher(Base):
    __tablename__ = "vouchers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    code: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    signature: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(default=utcnow)


class Disruption(Base):
    __tablename__ = "disruptions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    order_id: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    impacts: Mapped[list[Any]] = mapped_column(default=list)
    options: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")
    resolution: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("trip_id", "seq"), Index("ix_audit_trip_seq", "trip_id", "seq"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    seq: Mapped[int]
    type: Mapped[str] = mapped_column(String(48))
    actor: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ts: Mapped[str] = mapped_column(String(40))  # exact string that was hashed
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope: Mapped[str] = mapped_column(String(60))
    request_digest: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# --- supplier simulator (conceptually an external system) ---------------------------------

class SimState(Base):
    __tablename__ = "sim_state"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)


class SimBooking(Base):
    __tablename__ = "sim_bookings"
    ref: Mapped[str] = mapped_column(String(40), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    offer: Mapped[dict[str, Any]] = mapped_column(default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    amount_minor: Mapped[int]
    confirmation_code: Mapped[str | None] = mapped_column(String(40))
    ack_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
