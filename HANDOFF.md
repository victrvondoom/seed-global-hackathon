# Waypoint — handoff status

Spec: Waypoint pitch deck (VoyageHack 4.0). Plan/decisions: `.agent/`.

## DONE (backend, tested: `cd backend && uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest` → 9 passed)
- FastAPI app `backend/app/main.py` (run: `.venv/bin/uvicorn app.main:app --reload`), SQLite default, Postgres via `WAYPOINT_DATABASE_URL`.
- Auth (guest/register/login, JWT), trip ownership (404 on foreign trips).
- Copilot chat (`services/copilot.py`): deterministic NLU (`domain/nlu.py`), optional Gemini (`ai/providers.py`, set GEMINI_API_KEY), fact guard, one follow-up at a time, priced date chips, destination discovery.
- Supplier simulator (`suppliers/simulator/`): 10 destinations, seeded flights/fare buckets, hotels, transfers, activities, async acks, delays, reschedule fees.
- Planner (`domain/planner.py`): Value/Comfort/Experience plans, timed itinerary, route legs, cost breakdown, rules checks.
- Workflow: select → reprice → approve (signed receipt bound to scope digest) → execute (saga + Idempotency-Key) → reconcile → vouchers.
- Disruption cascade (2h delay → keep / move pickup +₹300 with separate approval), hash-chained ledger, Inflation Forge, SSE events, simulator lab endpoints.
- API: see `backend/app/api/routes.py`. Tests: `backend/tests/`.

## PARTIAL (frontend `frontend/`, React+Vite+TS)
- Done: package.json/deps, vite proxy to :8000, `src/api.ts` (client, types, SSE hook), `src/styles.css` (full design system), `src/components.tsx` (SplitFlap, StageRail, Receipt, Checks, QR).

## PENDING
1. `src/main.tsx` (import fonts @fontsource/oswald, instrument-serif, instrument-sans, jetbrains-mono + styles.css; routes `/` and `/trips/:id`).
2. Landing page (hero WAYPOINT., composer → ensureSession + POST /trips + POST message, globe SVG from /api/catalog/destinations, recent trips).
3. Workspace: chat column (render parts: text/understood/choices/trace/plans/notice/action), tabs Compare / Itinerary / Verify / My Trip / Forge, side receipt+checks+ledger, mobile bottom nav (CSS classes already exist in styles.css).
4. `pnpm build`, Playwright e2e of demo flow, screenshots.
5. README, Dockerfile/docker-compose (Postgres), CI.
