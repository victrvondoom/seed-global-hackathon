"""The deck's demo sequence, end to end through the public API."""
from tests.conftest import auth


def say(client, h, tid, text=None, action=None):
    r = client.post(f"/api/trips/{tid}/messages", headers=h, json={"text": text or "", "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def plan_trip(client, h):
    tid = client.post("/api/trips", headers=h).json()["id"]
    r = say(client, h, tid, "4 days in Kochi from Chennai, under ₹30,000, no early flights")
    assert r["trip"]["intent"]["destination"] == "COK" and r["trip"]["missing"] == ["travellers", "start_date"]
    r = say(client, h, tid, action={"slot": "travellers", "value": {"adults": 2, "children": 0}, "label": "2 adults"})
    choices = next(p for p in r["messages"][1]["parts"] if p["type"] == "choices")
    assert choices["slot"] == "start_date" and choices["options"]
    r = say(client, h, tid, action={"slot": "start_date", "value": {"start_date": "2026-12-11", "nights": 3}})
    plans = r["trip"]["search"]["plans"]
    assert len(plans) >= 2 and all("offer_token" not in str(p) for p in plans)
    return tid, plans


def test_full_demo_sequence(client):
    h = auth(client)
    tid, plans = plan_trip(client, h)
    best = next(p for p in plans if p["within_budget"])
    t = client.post(f"/api/trips/{tid}/plans/{best['id']}/select", headers=h).json()
    assert t["stage"] == "verify"

    # 2. reprice with a forced fare move → fresh approval at the new price
    client.post(f"/api/trips/{tid}/lab/market", headers=h, json={"force_fare_move": True})
    t = client.post(f"/api/trips/{tid}/checkout/reprice", headers=h).json()
    co = t["checkout"]
    assert t["workflow"] == "await_approval" and co["changes"], co
    assert co["total"]["amount_minor"] > co["previous_total"]["amount_minor"]

    stale = client.post(f"/api/trips/{tid}/checkout/approve", headers=h,
                        json={"scope_digest": "0" * 64, "amount_minor": co["total"]["amount_minor"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "consent.stale"
    rc = client.post(f"/api/trips/{tid}/checkout/approve", headers=h,
                     json={"scope_digest": co["digest"], "amount_minor": co["total"]["amount_minor"]}).json()

    # 3. execute (idempotent), supplier acks, vouchers retrievable
    hk = {**h, "Idempotency-Key": "demo-key-0001"}
    r1 = client.post(f"/api/trips/{tid}/checkout/execute", headers=hk, json={"receipt_id": rc["receipt_id"]})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/api/trips/{tid}/checkout/execute", headers=hk, json={"receipt_id": rc["receipt_id"]})
    assert r2.json()["orders"] == r1.json()["orders"]
    assert all(o["status"] == "pending" for o in r1.json()["trip"]["orders"])  # no confirmation without ack
    t = client.post(f"/api/trips/{tid}/checkout/reconcile", headers=h).json()
    assert t["stage"] == "adapt" and all(o["voucher"] for o in t["orders"])

    # 4-6. inject 2h delay → keep (₹0) or move pickup (+fee, separate approval) → revised total
    t = client.post(f"/api/trips/{tid}/lab/delay", headers=h, json={"minutes": 120}).json()
    d = t["disruptions"][-1]
    move = next(o for o in d["options"] if o["id"] == "move_pickup")
    assert move["requires_approval"] and move["delta"]["amount_minor"] == 30000
    no_consent = client.post(f"/api/trips/{tid}/disruptions/{d['id']}/options/move_pickup/apply", headers=h, json={})
    assert no_consent.status_code == 403
    r = client.post(f"/api/trips/{tid}/disruptions/{d['id']}/options/move_pickup/approve", headers=h,
                    json={"scope_digest": move["scope_digest"], "amount_minor": 30000}).json()
    before = t["revision"]["costs"]["total"]["amount_minor"]
    t = client.post(f"/api/trips/{tid}/disruptions/{d['id']}/options/move_pickup/apply", headers=h,
                    json={"receipt_id": r["receipt_id"]}).json()
    assert t["revision"]["costs"]["total"]["amount_minor"] == before + 30000
    assert t["ledger"]["verified"] and t["ledger"]["count"] > 10


def test_trip_ownership_is_enforced(client):
    h1, h2 = auth(client, "A"), auth(client, "B")
    tid = client.post("/api/trips", headers=h1).json()["id"]
    assert client.get(f"/api/trips/{tid}", headers=h2).status_code == 404
    assert client.get(f"/api/trips/{tid}", headers={}).status_code == 401


def test_cannot_execute_without_approval(client):
    h = auth(client)
    tid, plans = plan_trip(client, h)
    client.post(f"/api/trips/{tid}/plans/{plans[0]['id']}/select", headers=h)
    r = client.post(f"/api/trips/{tid}/checkout/execute", headers={**h, "Idempotency-Key": "k-12345678"},
                    json={"receipt_id": "rcp_fake"})
    assert r.status_code == 409


def test_ledger_detects_tampering(client, app):
    h = auth(client)
    tid, _ = plan_trip(client, h)
    from app.models import AuditEvent
    with app.state.rt.db.session() as s:
        ev = s.query(AuditEvent).filter_by(trip_id=tid, seq=2).one()
        ev.summary = "edited"
        s.commit()
    assert client.get(f"/api/trips/{tid}/ledger", headers=h).json()["verified"] is False


def test_forge_signal(client):
    h = auth(client)
    tid, _ = plan_trip(client, h)
    f = client.get(f"/api/trips/{tid}/forge", headers=h).json()
    assert f["signal"]["code"] in ("book_now", "wait")
    fc = [p for p in f["series"] if p["kind"] == "forecast"]
    assert all(a["p50"] < b["p50"] for a, b in zip(fc, fc[1:])) and all(p["p10"] < p["p50"] < p["p90"] for p in fc)
