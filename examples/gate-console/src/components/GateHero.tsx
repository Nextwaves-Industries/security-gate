import type { GateStatus } from "../api";

// Isometric render of one NR155 portal lane. Flat-shaded faces only.
const C = 0.866;
const S = 0.5;
const OX = 268;
const OY = 212;
type P = [number, number, number];
const iso = ([x, y, z]: P) => [OX + (x - y) * C, OY + (x + y) * S - z] as const;
const poly = (pts: P[]) => pts.map((p) => iso(p).join(",")).join(" ");

function Box({
  x,
  y,
  z,
  w,
  d,
  h,
  top,
  front,
  side,
  stroke,
}: {
  x: number;
  y: number;
  z: number;
  w: number;
  d: number;
  h: number;
  top: string;
  front: string;
  side: string;
  stroke?: string;
}) {
  return (
    <g stroke={stroke ?? "none"} strokeWidth="0.75" strokeLinejoin="round">
      <polygon points={poly([[x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]])} fill={top} />
      <polygon points={poly([[x, y + d, z], [x + w, y + d, z], [x + w, y + d, z + h], [x, y + d, z + h]])} fill={front} />
      <polygon points={poly([[x + w, y, z], [x + w, y + d, z], [x + w, y + d, z + h], [x + w, y, z + h]])} fill={side} />
    </g>
  );
}

export default function GateHero({ status, antennas }: { status: GateStatus | null; antennas: boolean[] }) {
  const reader = status?.reader.connected ?? false;
  const sensor = status?.sensor.connected ?? false;
  const running = status?.inventory?.running ?? false;

  const pillar = { top: "#2c2c2c", front: "#1b1b1b", side: "#232323" };
  const panelOn = { top: "#f2f2f2", front: "#d9d9d9", side: "#e6e6e6" };
  const panelOff = { top: "#383838", front: "#262626", side: "#2f2f2f" };

  // world: lane runs along y (toward viewer), pillars at x=0..40 and x=260..300
  const H = 190;
  const panelZ = [118, 40];
  const left = panelZ.map((z, i) => ({ x: 40, z, active: antennas[i] && reader }));
  const right = panelZ.map((z, i) => ({ x: 254, z, active: antennas[i + 2] && reader }));
  const beamA = iso([46, 20, 92]);
  const beamB = iso([254, 20, 92]);

  return (
    <svg viewBox="0 0 640 450" role="img" aria-label="Portal render">
      {/* floor */}
      <polygon points={poly([[-40, -80, 0], [340, -80, 0], [340, 120, 0], [-40, 120, 0]])} fill="#0b0b0b" />
      <polygon points={poly([[46, -80, 0.2], [254, -80, 0.2], [254, 120, 0.2], [46, 120, 0.2]])} fill="#101010" />
      {/* contact shadows */}
      <polygon points={poly([[-6, -6, 0.3], [52, -6, 0.3], [52, 52, 0.3], [-6, 52, 0.3]])} fill="#050505" />
      <polygon points={poly([[254, -6, 0.3], [312, -6, 0.3], [312, 52, 0.3], [254, 52, 0.3]])} fill="#050505" />
      {/* direction */}
      {running && (
        <g fill="#3e6ae1">
          <polygon points={poly([[144, 100, 0.5], [156, 100, 0.5], [156, -30, 0.5], [144, -30, 0.5]])} opacity="0.9" />
          <polygon points={poly([[130, -30, 0.5], [170, -30, 0.5], [150, -62, 0.5]])} />
        </g>
      )}
      {/* pillars */}
      <Box x={0} y={0} z={0} w={40} d={40} h={H} {...pillar} stroke={reader ? "#5a5a5a" : "#303030"} />
      {left.map((p, i) => (
        <Box key={`l${i}`} x={p.x} y={8} z={p.z} w={5} d={24} h={44} {...(p.active ? panelOn : panelOff)} />
      ))}
      {/* sensor emitter on left pillar */}
      <Box x={40} y={16} z={88} w={4} d={8} h={8} top={sensor ? "#f2f2f2" : "#3a3a3a"} front={sensor ? "#d9d9d9" : "#262626"} side={sensor ? "#e6e6e6" : "#2f2f2f"} />
      {sensor && <line x1={beamA[0]} y1={beamA[1]} x2={beamB[0]} y2={beamB[1]} stroke="#ffffff" strokeOpacity="0.55" strokeWidth="1" />}
      <Box x={260} y={0} z={0} w={40} d={40} h={H} {...pillar} stroke={reader ? "#5a5a5a" : "#303030"} />
      {right.map((p, i) => (
        <Box key={`r${i}`} x={p.x} y={8} z={p.z} w={6} d={24} h={44} {...(p.active ? panelOn : panelOff)} />
      ))}
      <Box x={254} y={16} z={88} w={6} d={8} h={8} top={sensor ? "#f2f2f2" : "#3a3a3a"} front={sensor ? "#d9d9d9" : "#262626"} side={sensor ? "#e6e6e6" : "#2f2f2f"} />
      {/* antenna labels */}
      {[
        { p: [20, 72, 0] as P, t: "A1 A2", on: reader && (antennas[0] || antennas[1]) },
        { p: [280, 72, 0] as P, t: "A3 A4", on: reader && (antennas[2] || antennas[3]) },
      ].map(({ p, t, on }) => {
        const [x, y] = iso(p);
        return (
          <text key={t} x={x} y={y} textAnchor="middle" fontSize="11" fill={on ? "#ffffff" : "#5c5c5c"} letterSpacing="1">
            {t}
          </text>
        );
      })}
    </svg>
  );
}
