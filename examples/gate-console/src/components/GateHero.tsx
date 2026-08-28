import type { GateStatus } from "../api";

// Flat, top-down line drawing of one NR155 portal lane. State is shown with
// stroke colour and labels only: no gradients, glows or animation.
export default function GateHero({ status, antennas }: { status: GateStatus | null; antennas: boolean[] }) {
  const reader = status?.reader.connected ?? false;
  const sensor = status?.sensor.connected ?? false;
  const running = status?.inventory?.running ?? false;
  const on = "#e8e8e8";
  const off = "#3a3a3a";
  const line = "#2a2a2a";
  const accent = "#3e6ae1";
  const pos: [number, number][] = [
    [118, 96],
    [118, 204],
    [442, 96],
    [442, 204],
  ];
  const laneLabel = running
    ? `${(status?.inventory?.status || "INVENTORY").toUpperCase()} · ${status?.inventory?.reference ?? ""}`
    : "LANE";
  return (
    <div className="hero">
      <svg viewBox="0 0 560 300" role="img" aria-label="Gate portal view">
        {/* lane */}
        <rect x="150" y="44" width="260" height="212" fill="none" stroke={line} />
        <line x1="150" y1="150" x2="410" y2="150" stroke={line} strokeDasharray="2 6" />
        {/* direction */}
        <g stroke={running ? accent : off} strokeWidth="1.5" fill="none">
          <path d="M280 214 V 96" />
          <path d="M270 106 L280 94 L290 106" />
        </g>
        <text x="280" y="242" textAnchor="middle" fontSize="10" fill="#8a8a8a" letterSpacing="1.5">
          {laneLabel}
        </text>
        {/* pillars */}
        {[96, 420].map((x) => (
          <g key={x}>
            <rect x={x} y="56" width="44" height="188" rx="3" fill="#0d0d0d" stroke={reader ? "#5a5a5a" : line} />
            <line x1={x + 22} y1="70" x2={x + 22} y2="230" stroke={line} />
          </g>
        ))}
        {/* antennas */}
        {pos.map(([x, y], i) => {
          const active = antennas[i] && reader;
          return (
            <g key={i}>
              <circle cx={x} cy={y} r="5" fill={active ? on : "none"} stroke={active ? on : off} strokeWidth="1.5" />
              <text x={x} y={y + 20} textAnchor="middle" fontSize="9" fill={active ? "#b0b0b0" : off}>
                A{i + 1}
              </text>
            </g>
          );
        })}
        {/* sensor beam */}
        <line x1="140" y1="150" x2="420" y2="150" stroke={sensor ? on : off} strokeWidth="1" strokeDasharray={sensor ? "" : "3 5"} />
        <rect x="136" y="146" width="8" height="8" fill={sensor ? on : "none"} stroke={sensor ? on : off} />
        <rect x="416" y="146" width="8" height="8" fill={sensor ? on : "none"} stroke={sensor ? on : off} />
        {/* labels */}
        <text x="118" y="270" textAnchor="middle" fontSize="10" fill={reader ? "#b0b0b0" : off} letterSpacing="1.5">
          READER {reader ? "ON" : "OFF"}
        </text>
        <text x="442" y="270" textAnchor="middle" fontSize="10" fill={sensor ? "#b0b0b0" : off} letterSpacing="1.5">
          SENSOR {sensor ? "ON" : "OFF"}
        </text>
      </svg>
    </div>
  );
}
