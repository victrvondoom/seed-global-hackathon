# Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | FastAPI + SQLAlchemy 2 (SQLite default, PostgreSQL via `WAYPOINT_DATABASE_URL`) | Deck names FastAPI + PostgreSQL; SQLite keeps the demo zero-setup. |
| 2 | Gemini adapter (REST, structured JSON) + deterministic heuristic fallback | Deck pins Gemini; no key/network here, so the copilot must work offline. Model is read-only. |
| 3 | Fact guard on model-phrased replies | "The model cannot invent a price, a booking status, a refund or availability." Amounts/times in model text must exist in tool facts or the template reply is used. |
| 4 | Money as integer minor units + currency | No float drift in totals/approvals. |
| 5 | Approval receipts = HMAC-signed, bound to a scope digest (quote ids, amounts, terms digests) | Any price/term change invalidates consent → fresh approval. |
| 6 | Hash-chained audit ledger per trip | Tamper-evident trip record; verifiable in UI. |
| 7 | Supplier simulator is seeded + stateful (own `sim_*` tables) | Repeatable offers, fare buckets, async acks, delays, service actions. |
| 8 | Saga on execute: book sequentially, compensate (cancel) on failure | Never leave a half-booked trip; never charge more than approved. |
| 9 | Idempotency-Key on execute | Duplicate prevention for retries/double clicks. |
| 10 | SSE (fetch-stream, auth header) for live trip events | Pending→confirmed, disruptions arrive without refresh; no token in URLs. |
| 11 | Planned extras are estimates, never part of the approved charge | Approval amount = bookable total only; estimates rendered visibly different. |
| 12 | Simulator lab endpoints gated by `WAYPOINT_SIMULATOR_LAB_ENABLED` | Delay/fare-move injection is a demo harness, off in production. |
