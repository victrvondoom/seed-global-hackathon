# Waypoint — task plan

Source of truth: `Waypoint_Travel_Copilot_Pitch.pdf` (VoyageHack 4.0, Team Autominds).
Repo state at start: empty (no commits). Mode: B+C (greenfield product build).

## Phases
- A. Investigation — deck read (9 slides), toolchain checked (Py 3.13 + uv, Node 22 + pnpm). Done.
- B. Synthesis — architecture + API contract in `decisions.md`.
- C. Implementation
  1. Backend (FastAPI): core, domain (intent/NLU, planner, itinerary, rules, workflow,
     receipts, ledger, cascade, forge, routes), supplier simulator, AI lane, services, API, SSE.
  2. Frontend (React + Vite + TS): landing, trips, workspace (chat / compare / itinerary /
     verify / my trip / forge), simulator lab.
- D. Verification — pytest, tsc, build, live API smoke run, Playwright e2e of the deck's
  6-step demo sequence, screenshots (mobile + desktop), independent review.

## Deck demo sequence (acceptance)
1. Compare two plans · pick one (estimate shown)
2. Recheck → fare moves (+1 bucket) → fresh approval required
3. Approve → execute → supplier ack → voucher retrievable in My Trip
4. Inject a 2h flight delay
5. Keep pickup (₹0, risk) or move it (+fee)
6. Revised total · separate approval
