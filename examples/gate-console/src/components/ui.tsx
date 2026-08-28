import type { ReactNode } from "react";
import { Chevron } from "./Icons";

export function Tile({
  title,
  value,
  unit,
  sub,
  spark,
  tone,
}: {
  title: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  spark?: number[];
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="tile">
      <h4>
        {title}
        {tone && <span className={`dot ${tone}`} />}
      </h4>
      <div>
        <div className="big">
          {value}
          {unit && <small>{unit}</small>}
        </div>
        {spark && spark.length > 1 && <Sparkline data={spark} />}
        {sub && <div className="sub">{sub}</div>}
      </div>
    </div>
  );
}

export function Sparkline({ data }: { data: number[] }) {
  const w = 200;
  const h = 28;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const pts = data
    .map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / (max - min || 1)) * (h - 2) - 1}`)
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke="#e6e6e6" strokeWidth="1.2" />
    </svg>
  );
}

export function Row({
  icon,
  title,
  sub,
  right,
  onClick,
}: {
  icon?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="ico">{icon}</span>
      <span>
        <div className="t">{title}</div>
        {sub && <div className="s">{sub}</div>}
      </span>
      <span className="r">
        {right}
        {onClick && <Chevron className="chev" />}
      </span>
    </>
  );
  return onClick ? (
    <button className="row" onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="row">{body}</div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? "active" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
    </div>
  );
}

export function toneFor(state: string | undefined, ready?: boolean): "ok" | "warn" | "bad" {
  if (ready) return "ok";
  const s = (state || "").toUpperCase();
  if (s.includes("READY") || s === "ONLINE" || s === "IDLE") return "ok";
  if (s.includes("ERROR") || s.includes("FAULT") || s.includes("DISCONNECT")) return "bad";
  return "warn";
}

export function fmtTime(v: unknown): string {
  if (typeof v !== "string" || !v) return "-";
  const d = new Date(v);
  return isNaN(d.getTime()) ? v : d.toLocaleString();
}

export function titleCase(v: string | undefined): string {
  if (!v) return "Unknown";
  return v.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
