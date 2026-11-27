"""Fact guard: a model may phrase a reply, but every amount, time and flight number in it must
already exist in the facts the backend computed. Otherwise the deterministic draft is used."""
from __future__ import annotations

import re

_AMOUNT = re.compile(r"(?:₹|rs\.?\s?|inr\s?)\s?(\d[\d,]*)", re.I)
_TIME = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
_FLIGHT = re.compile(r"\b[A-Z]{2}\s?\d{3}\b")
_STATUS = re.compile(r"\b(booked|confirmed|guaranteed|refund(?:ed|able)?|available)\b", re.I)


def _norm_amount(s: str) -> str:
    return s.replace(",", "")


def allowed(candidate: str, draft: str, facts_text: str) -> bool:
    source = f"{draft}\n{facts_text}"
    src_amounts = {_norm_amount(m) for m in _AMOUNT.findall(source)}
    if any(_norm_amount(m) not in src_amounts for m in _AMOUNT.findall(candidate)):
        return False
    src_times = {m.group(0).zfill(5) for m in _TIME.finditer(source)}
    if any(m.group(0).zfill(5) not in src_times for m in _TIME.finditer(candidate)):
        return False
    src_flights = {f.replace(" ", "") for f in _FLIGHT.findall(source)}
    if any(f.replace(" ", "") not in src_flights for f in _FLIGHT.findall(candidate)):
        return False
    src_status = {s.lower() for s in _STATUS.findall(source)}
    return all(s.lower() in src_status for s in _STATUS.findall(candidate))
