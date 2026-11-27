import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import type { Check, Costs, Money } from "./api";
import { fmt } from "./api";

/** Split-flap board: characters that changed flip, so a changed fact visibly announces itself. */
export function SplitFlap({ text, small }: { text: string; small?: boolean }) {
  const prev = useRef(text);
  const [flipped, setFlipped] = useState<boolean[]>([]);
  useEffect(() => {
    const old = prev.current.padStart(text.length);
    setFlipped([...text].map((c, i) => old[i] !== c));
    prev.current = text;
  }, [text]);
  return (
    <span className={`flap${small ? " small" : ""}`} aria-label={text} role="text">
      {[...text].map((c, i) => (
        <span key={`${i}-${c}-${flipped[i] ? "f" : ""}`} aria-hidden className={`${/[0-9₹A-Z]/.test(c) ? "" : "sep"}${flipped[i] ? " flip" : ""}`}
          style={flipped[i] ? { animationDelay: `${i * 45}ms` } : undefined}>{c}</span>
      ))}
    </span>
  );
}

export function StageRail({ stage }: { stage: string }) {
  const steps = ["plan", "verify", "book", "adapt"];
  const at = steps.indexOf(stage);
  return (
    <nav className="stagerail" aria-label="Trip stage">
      {steps.map((s, i) => (
        <span key={s} className={i < at ? "done" : i === at ? "now" : ""} aria-current={i === at ? "step" : undefined}>{s.toUpperCase()}</span>
      ))}
    </nav>
  );
}

export function Receipt({ costs, title = "Cost summary", note }: { costs: Costs; title?: string; note?: string }) {
  return (
    <div className="receipt">
      <h4>{title}</h4>
      {costs.lines.map((l) => (
        <div key={l.key} className={`rline${l.source === "estimate" ? " est" : ""}${l.source === "fee" ? " fee" : ""}`} title={l.detail}>
          <span>{l.label}</span><span className="num">{fmt(l.amount)}</span>
        </div>
      ))}
      <div className="rtotal"><span>Estimate</span><b className="num"><SplitFlap text={fmt(costs.total)} small /></b></div>
      <div className="rnote">{note ?? `Bookable ${fmt(costs.bookable_total)} · planned extras are paid locally`}</div>
    </div>
  );
}

export function Checks({ checks }: { checks: Check[] }) {
  const icon = { pass: "✓", warn: "!", block: "×" } as const;
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.code + c.detail} className={c.status}>
          <span className="ic" aria-label={c.status}>{icon[c.status]}</span>
          <span>{c.title}<small>{c.detail}</small></span>
        </li>
      ))}
    </ul>
  );
}

export function QR({ value, size = 84 }: { value: string; size?: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    QRCode.toDataURL(value, { margin: 0, width: size * 2, color: { dark: "#0b1650", light: "#ffffff" } }).then(setSrc, () => setSrc(""));
  }, [value, size]);
  return src ? <img className="qr" src={src} width={size} height={size} alt={`QR code for ${value}`} /> : null;
}

export const MoneyText = ({ m, signed }: { m?: Money | null; signed?: boolean }) => <span className="num">{fmt(m, signed)}</span>;
