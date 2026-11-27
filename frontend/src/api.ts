import { useCallback, useEffect, useRef, useState } from "react";

export type Money = { amount_minor: number; currency: string };
export type Place = { name: string; lat: number; lng: number };
export type Check = { code: string; status: "pass" | "warn" | "block"; title: string; detail: string };
export type Item = {
  id: string; day: number; date: string; kind: string; title: string; subtitle?: string; start: string; end?: string;
  place?: Place; to_place?: Place; component?: string; cost?: Money; source: string; status: string; meta: Record<string, any>;
};
export type CostLine = { key: string; label: string; amount: Money; source: string; detail?: string };
export type Costs = { lines: CostLine[]; bookable_total: Money; extras_total: Money; total: Money };
export type Plan = {
  id: string; strategy: string; name: string; headline: string; itinerary: Item[]; costs: Costs; highlights: string[];
  checks: Check[]; within_budget: boolean | null; budget_delta: Money | null; score: number;
  components: { key: string; kind: string; title: string; subtitle: string; supplier: string; price: Money; terms: any }[];
};
export type Part = { type: string; [k: string]: any };
export type Message = { id: string; role: "user" | "assistant"; parts: Part[]; created_at: string };
export type Trip = {
  id: string; title: string; stage: "plan" | "verify" | "book" | "adapt"; workflow: string; intent: any; missing: string[];
  search: { plans: Plan[]; trace: any[]; notes: string[]; blocked: string[] } | null; selected_plan_id: string | null;
  checkout: any; revision: { number: number; reason: string; itinerary: Item[]; costs: Costs } | null;
  revisions: { number: number; reason: string; total: Money; created_at: string }[];
  orders: any[]; disruptions: any[]; receipts: any[]; ledger: { verified: boolean; count: number; head: string };
  market: { mode: string; force_next: boolean }; updated_at: string;
};

const TOKEN = "waypoint.token";
export const store = {
  get: () => { try { return localStorage.getItem(TOKEN); } catch { return null; } },
  set: (t: string) => { try { localStorage.setItem(TOKEN, t); } catch { /* private mode */ } },
  clear: () => { try { localStorage.removeItem(TOKEN); } catch { /* ignore */ } },
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
  const token = store.get();
  const res = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...opts.headers },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    if (res.status === 401) store.clear();
    throw new ApiError(res.status, data?.error?.code ?? "error", data?.error?.message ?? data?.detail?.[0]?.msg ?? `Request failed (${res.status})`);
  }
  return data as T;
}

export async function ensureSession(name = "Traveller") {
  if (store.get()) {
    try { return await api("/auth/me"); } catch { /* fall through */ }
  }
  const r = await api<{ token: string; user: any }>("/auth/guest", { body: { name } });
  store.set(r.token);
  return r.user;
}

export function fmt(m?: Money | null, signed = false): string {
  if (!m) return "—";
  const v = Math.round(m.amount_minor / 100);
  const s = new Intl.NumberFormat("en-IN", { style: "currency", currency: m.currency || "INR", maximumFractionDigits: 0 }).format(Math.abs(v));
  return (v < 0 ? "−" : signed && v > 0 ? "+" : "") + s;
}
export const hm = (iso?: string) => (iso ? iso.slice(11, 16) : "");
export const dayLabel = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => fn().then((d) => { setData(d); setError(null); }, (e) => setError(e.message)), deps); // eslint-disable-line
  useEffect(() => { reload(); }, [reload]);
  return { data, error, reload, setData };
}

/** Live trip events over a fetch stream (Authorization header, no token in the URL). */
export function useTripEvents(tripId: string | undefined, onEvent: (type: string, data: any) => void) {
  const cb = useRef(onEvent);
  cb.current = onEvent;
  useEffect(() => {
    if (!tripId) return;
    const ctrl = new AbortController();
    let retry = 0;
    const run = async () => {
      while (!ctrl.signal.aborted) {
        try {
          const res = await fetch(`/api/trips/${tripId}/events`, {
            headers: { Authorization: `Bearer ${store.get()}` }, signal: ctrl.signal,
          });
          if (!res.ok || !res.body) throw new Error("stream");
          retry = 0;
          const reader = res.body.getReader();
          const dec = new TextDecoder();
          let buf = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            let i;
            while ((i = buf.indexOf("\n\n")) >= 0) {
              const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
              const ev = /^event: (.*)$/m.exec(chunk)?.[1];
              const data = /^data: (.*)$/m.exec(chunk)?.[1];
              if (ev && data && ev !== "hello") { try { cb.current(ev, JSON.parse(data)); } catch { /* ignore */ } }
            }
          }
        } catch { if (ctrl.signal.aborted) return; }
        await new Promise((r) => setTimeout(r, Math.min(8000, 800 * 2 ** retry++)));
      }
    };
    run();
    return () => ctrl.abort();
  }, [tripId]);
}
