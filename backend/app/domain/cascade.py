"""Disruption cascade: a schedule change on one booking propagates through the itinerary's
time dependencies (landing → pickup wait window → hotel → day-one plans)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.domain.types import ItineraryItem


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def ceil5(dt: datetime) -> datetime:
    return dt + timedelta(minutes=(-dt.minute) % 5)


def analyse_delay(items: list[ItineraryItem], component: str, delay_min: int, *, wait_min: int,
                  exit_buffer: int, airport_before: int) -> dict[str, Any]:
    by = {i.component: i for i in items if i.component}
    flight = by[component]
    old_dep, old_arr = _dt(flight.start), _dt(flight.end or flight.start)
    new_dep, new_arr = old_dep + timedelta(minutes=delay_min), old_arr + timedelta(minutes=delay_min)
    impacts: list[dict[str, Any]] = [{
        "item_id": flight.id, "severity": "info", "title": f"{flight.meta.get('number', 'Flight')} delayed {delay_min} min",
        "detail": f"Departs {_hm(new_dep)} · lands {_hm(new_arr)} (was {_hm(old_arr)})"}]
    result: dict[str, Any] = {"component": component, "delay_min": delay_min, "new_depart": new_dep.isoformat(),
                              "new_arrive": new_arr.isoformat(), "old_arrive": old_arr.isoformat(),
                              "impacts": impacts, "pickup": None, "at_risk": []}
    if component == "flight_out" and "transfer_in" in by:
        pick = by["transfer_in"]
        p_start, p_end = _dt(pick.start), _dt(pick.end or pick.start)
        ready = new_arr + timedelta(minutes=exit_buffer)
        wait_until = p_start + timedelta(minutes=wait_min)
        status = "missed" if ready > wait_until else "tight" if ready > p_start else "ok"
        new_pickup = ceil5(ready)
        result["pickup"] = {"item_id": pick.id, "status": status, "scheduled": p_start.isoformat(),
                            "wait_until": wait_until.isoformat(), "ready_at": ready.isoformat(),
                            "suggested": new_pickup.isoformat(), "duration_min": int((p_end - p_start).total_seconds() // 60)}
        if status != "ok":
            impacts.append({
                "item_id": pick.id, "severity": "high" if status == "missed" else "medium",
                "title": f"Airport pickup {_hm(p_start)} " + ("will be missed" if status == "missed" else "is tight"),
                "detail": f"Driver waits until {_hm(wait_until)}; you'll be out by {_hm(ready)}"})
        arrival_at_hotel = max(new_pickup, p_start) + (p_end - p_start)
        day = flight.day
        for it in items:
            if it.kind == "activity" and it.day == day and _dt(it.start) > old_arr:
                legs = [x for x in items if x.kind == "leg" and x.day == day and x.to_place and it.place
                        and x.to_place.name == it.place.name]
                travel = int(legs[0].meta.get("duration_min", 20)) if legs else 20
                latest_ok = _dt(it.start) - timedelta(minutes=travel + 15)
                if arrival_at_hotel > latest_ok:
                    result["at_risk"].append(it.id)
                    impacts.append({"item_id": it.id, "severity": "medium", "title": f"{it.title} at {_hm(_dt(it.start))} at risk",
                                    "detail": f"Earliest at hotel {_hm(arrival_at_hotel)}, plus ~{travel} min to get there"})
    if component == "flight_ret" and "transfer_out" in by:
        drop = by["transfer_out"]
        spare = int((new_dep - _dt(drop.end or drop.start)).total_seconds() // 60)
        impacts.append({"item_id": drop.id, "severity": "info", "title": "Airport drop still works",
                        "detail": f"You'll be at the airport {spare} min before the new departure (needs {airport_before})"})
    return result
