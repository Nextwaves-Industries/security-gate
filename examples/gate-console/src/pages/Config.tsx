import { useEffect, useRef, useState, type ReactNode } from "react";
import type { GateStatus, Settings } from "../api";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="srow">
      <span className="slabel">{label}</span>
      <span className="svalue">{children}</span>
    </div>
  );
}

export default function Config({
  status,
  settings,
  onSave,
}: {
  status: GateStatus | null;
  settings: Settings;
  onSave: (s: Settings) => void;
}) {
  const [draft, setDraft] = useState(settings);
  const timer = useRef<number | undefined>(undefined);

  // Apply shortly after typing stops, like a device settings screen.
  useEffect(() => {
    if (draft === settings) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onSave(draft), 600);
    return () => window.clearTimeout(timer.current);
  }, [draft, settings, onSave]);

  const gate: [string, unknown][] = status
    ? [
        ["Gate", status.gate_id],
        ["Software", status.service_version],
        ["State", status.state],
        ["Reader", `${status.reader.module} on ${status.reader.device}`],
        ["Sensor", status.sensor.device],
        ["Model", status.model?.version],
        ["Configured model", status.model?.configured_version],
        ["Calibration", status.calibration?.state],
        ["Hardware signature", status.calibration?.hardware_signature?.slice(0, 16)],
      ]
    : [];

  return (
    <>
      <div className="section-title">Connection</div>
      <div className="settings">
        <Row label="Gate URL">
          <input
            value={draft.baseUrl}
            onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
            placeholder="Proxy"
            spellCheck={false}
          />
        </Row>
        <Row label="Access token">
          <input
            type="password"
            value={draft.token}
            onChange={(e) => setDraft({ ...draft, token: e.target.value })}
            placeholder="Required"
            autoComplete="off"
          />
        </Row>
        <Row label="Operator">
          <input value={draft.operatorId} onChange={(e) => setDraft({ ...draft, operatorId: e.target.value })} />
        </Row>
      </div>
      <p className="hint">Saved on this device.</p>

      <div className="section-title">Gate</div>
      <div className="settings">
        {gate.length === 0 ? (
          <div className="empty">Not connected</div>
        ) : (
          gate.map(([k, v]) => (
            <Row key={k} label={k}>
              <span className={k === "Hardware signature" ? "mono" : undefined}>
                {v === undefined || v === "" ? "-" : String(v)}
              </span>
            </Row>
          ))
        )}
      </div>
    </>
  );
}
