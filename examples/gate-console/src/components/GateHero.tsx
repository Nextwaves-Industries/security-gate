import type { GateStatus } from "../api";

// Top-down diagram of one NR155 portal lane. Plain strokes, two tones
// (active white / inactive grey), no effects.
export default function GateHero({ status, antennas }: { status: GateStatus | null; antennas: boolean[] }) {
  const reader = status?.reader.connected ?? false;
  const sensor = status?.sensor.connected ?? false;
  const running = status?.inventory?.running ?? false;
  const onStroke = "#e8e8e8";
  const offStroke = "#4d4d4d";
  const frame = "#3a3a3a";
  const label = "#8a8a8a";
  const accent = "#3e6ae1";
  const font = "Inter, Helvetica, Arial, sans-serif";

  // antenna panels: two per pillar
  const panels: { x: number; y: number }[] = [
    { x: 108, y: 70 },
    { x: 108, y: 166 },
    { x: 432, y: 70 },
    { x: 432, y: 166 },
  ];

  return (
    <svg viewBox="0 0 560 260" role="img" aria-label="Gate portal diagram" fontFamily={font}>
      {/* lane floor */}
      <rect x="150" y="30" width="260" height="200" fill="#0a0a0a" stroke={frame} />
      {/* pillars */}
      {[{ x: 96 }, { x: 420 }].map(({ x }) => (
        <rect key={x} x={x} y="40" width="44" height="180" rx="2" fill="#161616" stroke={reader ? onStroke : offStroke} />
      ))}
      {/* antenna panels */}
      {panels.map((p, i) => {
        const active = antennas[i] && reader;
        return (
          <g key={i}>
            <rect
              x={p.x - 6}
              y={p.y}
              width="12"
              height="28"
              rx="1"
              fill={active ? onStroke : "#161616"}
              stroke={active ? onStroke : offStroke}
            />
            <text
              x={i < 2 ? p.x - 22 : p.x + 22}
              y={p.y + 18}
              textAnchor="middle"
              fontSize="11"
              fill={active ? "#e8e8e8" : label}
            >
              A{i + 1}
            </text>
          </g>
        );
      })}
      {/* sensor beam */}
      <line x1="140" y1="130" x2="420" y2="130" stroke={sensor ? onStroke : offStroke} strokeWidth="1" />
      <rect x="136" y="126" width="8" height="8" fill={sensor ? onStroke : "#161616"} stroke={sensor ? onStroke : offStroke} />
      <rect x="416" y="126" width="8" height="8" fill={sensor ? onStroke : "#161616"} stroke={sensor ? onStroke : offStroke} />
      {/* lane state */}
      {running ? (
        <g>
          <g stroke={accent} strokeWidth="2" fill="none">
            <path d="M280 190 V 70" />
            <path d="M268 82 L280 68 L292 82" />
          </g>
          <text x="280" y="214" textAnchor="middle" fontSize="12" fill="#e8e8e8">
            {status?.inventory?.reference}
          </text>
        </g>
      ) : (
        <text x="280" y="134" textAnchor="middle" fontSize="12" fill={label} letterSpacing="1">
          IDLE
        </text>
      )}
      {/* captions */}
      <text x="118" y="248" textAnchor="middle" fontSize="11" fill={label}>
        Reader{" "}
        <tspan fill={reader ? "#e8e8e8" : "#c0392b"} fontWeight="500">
          {reader ? "connected" : "offline"}
        </tspan>
      </text>
      <text x="442" y="248" textAnchor="middle" fontSize="11" fill={label}>
        Sensor{" "}
        <tspan fill={sensor ? "#e8e8e8" : "#c0392b"} fontWeight="500">
          {sensor ? "connected" : "offline"}
        </tspan>
      </text>
    </svg>
  );
}
