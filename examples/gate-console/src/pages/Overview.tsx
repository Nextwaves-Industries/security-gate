import { useState } from "react";
import type { GateApi, GateStatus } from "../api";
import GateHero from "../components/GateHero";
import { Antenna, Beam, Brain, Check, Cross, Play, Stop, Target, Alert } from "../components/Icons";
import { Field, Row, Segmented, Tile, titleCase } from "../components/ui";

const DEFAULT_ANTENNAS = [true, true, false, false];

export default function Overview({
  api,
  status,
  history,
  notify,
  refresh,
}: {
  api: GateApi;
  status: GateStatus | null;
  history: number[];
  notify: (msg: string, bad?: boolean) => void;
  refresh: () => void;
}) {
  const [showStart, setShowStart] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reference, setReference] = useState("");
  const [operation, setOperation] = useState<"INBOUND" | "OUTBOUND">("INBOUND");
  const [epcs, setEpcs] = useState("");
  const [antennas, setAntennas] = useState(DEFAULT_ANTENNAS);
  const [cancelReason, setCancelReason] = useState("");

  const running = status?.inventory?.running ?? false;
  const [showDiagram, setShowDiagram] = useState(() => {
    try {
      return localStorage.getItem("gate-console.diagram") !== "hidden";
    } catch {
      return true;
    }
  });
  const toggleDiagram = () => {
    const next = !showDiagram;
    setShowDiagram(next);
    try {
      localStorage.setItem("gate-console.diagram", next ? "shown" : "hidden");
    } catch {
      /* ignore */
    }
  };

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      notify(`${label} accepted`);
      refresh();
    } catch (e) {
      notify(`${label} failed: ${(e as Error).message}`, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="hero">
        <div className="hero-head">
          <span>Portal</span>
          <button className="link" onClick={toggleDiagram}>
            {showDiagram ? "Hide diagram" : "Show diagram"}
          </button>
        </div>
        {showDiagram && <GateHero status={status} antennas={antennas} />}
      </div>

      <div className="hero-actions">
        <button disabled={busy || running || !status?.ready} onClick={() => setShowStart((v) => !v)}>
          <Play />
          Start
        </button>
        <button disabled={busy || !running} onClick={() => run("Stop inventory", () => api.stopInventory())}>
          <Stop />
          Stop
        </button>
        <button disabled={busy || !running} onClick={() => run("Commit", () => api.commitTransaction())}>
          <Check />
          Commit
        </button>
        <button
          disabled={busy || !running}
          onClick={() => {
            const reason = cancelReason || "operator cancel";
            run("Cancel", () => api.cancelTransaction(reason));
          }}
        >
          <Cross />
          Cancel
        </button>
      </div>

      {showStart && !running && (
        <div className="panel">
          <div className="grid-2">
            <Field label="Reference (ASN / order)">
              <input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="ASN-100" />
            </Field>
            <Field label="Operation">
              <Segmented
                value={operation}
                onChange={setOperation}
                options={[
                  { value: "INBOUND", label: "Inbound" },
                  { value: "OUTBOUND", label: "Outbound" },
                ]}
              />
            </Field>
          </div>
          <Field label="Expected EPCs (one per line, optional)">
            <textarea rows={3} value={epcs} onChange={(e) => setEpcs(e.target.value)} placeholder="E2000017…" />
          </Field>
          <Field label="Antennas">
            <div className="seg">
              {antennas.map((on, i) => (
                <button
                  key={i}
                  className={on ? "active" : ""}
                  onClick={() => setAntennas(antennas.map((v, j) => (j === i ? !v : v)))}
                >
                  A{i + 1}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Cancel reason (used by the Cancel action)">
            <input value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} placeholder="operator cancel" />
          </Field>
          <button
            className="btn primary block"
            disabled={busy || !reference.trim() || !antennas.some(Boolean)}
            onClick={() =>
              run("Start inventory", () =>
                api.startInventory({
                  reference: reference.trim(),
                  operation,
                  expected_epcs: epcs
                    .split(/\s+/)
                    .map((s) => s.trim())
                    .filter(Boolean),
                  antennas,
                  session: 0,
                  target: "A",
                }),
              ).then(() => setShowStart(false))
            }
          >
            Start inventory
          </button>
        </div>
      )}

      <div className="tiles">
        <Tile
          title="Readiness"
          value={status ? (status.ready ? "Ready" : titleCase(status.state)) : "-"}
          sub={status?.last_error || (status?.ready ? "All subsystems nominal" : "See subsystems below")}
          tone={status ? (status.ready ? "ok" : "warn") : undefined}
        />
        <Tile
          title="Inventory"
          value={running ? "Running" : "Idle"}
          sub={running ? `${status?.inventory?.reference} · ${status?.inventory?.status}` : "No open transaction"}
          tone={running ? "ok" : undefined}
        />
        <Tile
          title="Poll latency"
          value={history.length ? Math.round(history[history.length - 1]) : "-"}
          unit="ms"
          sub="status endpoint, last 60 polls"
          spark={history}
        />
      </div>

      <div className="section-title">Subsystems</div>
      <div className="list">
        <Row
          icon={<Antenna />}
          title="Reader"
          sub={status?.reader.message || status?.reader.device}
          right={<span className={`pill ${status?.reader.connected ? "ok" : "bad"}`}>{status?.reader.connected ? "Connected" : "Offline"}</span>}
        />
        <Row
          icon={<Beam />}
          title="Sensor"
          sub={status?.sensor.message || status?.sensor.device}
          right={<span className={`pill ${status?.sensor.connected ? "ok" : "bad"}`}>{status?.sensor.connected ? "Connected" : "Offline"}</span>}
        />
        <Row
          icon={<Brain />}
          title="Detection model"
          sub={status?.model ? `${status.model.version} (configured ${status.model.configured_version})` : "-"}
          right={<span className={`pill ${status?.model?.available ? "ok" : "bad"}`}>{status?.model?.available ? "Loaded" : "Missing"}</span>}
        />
        <Row
          icon={<Target />}
          title="Calibration"
          sub={status?.calibration?.reason || status?.calibration?.profile_state}
          right={
            <span className={`pill ${status?.calibration?.valid ? "ok" : "warn"}`}>
              {titleCase(status?.calibration?.state)}
            </span>
          }
        />
        {status?.last_error && <Row icon={<Alert />} title="Last error" sub={status.last_error} />}
      </div>
    </>
  );
}
