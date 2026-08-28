import { useCallback, useEffect, useState } from "react";
import type { CalibrationRun, GateApi, GateStatus } from "../api";
import { Target } from "../components/Icons";
import { Field, Row, Segmented, fmtTime, titleCase } from "../components/ui";

export default function Calibration({
  api,
  status,
  notify,
}: {
  api: GateApi;
  status: GateStatus | null;
  notify: (m: string, bad?: boolean) => void;
}) {
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [runs, setRuns] = useState<CalibrationRun[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");
  const [duration, setDuration] = useState(30);
  const [direction, setDirection] = useState<"IN" | "OUT">("IN");
  const [epcs, setEpcs] = useState("");
  const [timeout, setTimeoutS] = useState(60);
  const [reason, setReason] = useState("");

  const load = useCallback(() => {
    Promise.all([api.calibration(), api.calibrationRuns(50)])
      .then(([s, r]) => {
        setSummary(s);
        setRuns(r.items);
      })
      .catch((e) => notify(`Calibration: ${(e as Error).message}`, true));
  }, [api, notify]);

  useEffect(load, [load]);

  const run = async (label: string, fn: () => Promise<CalibrationRun>) => {
    setBusy(true);
    try {
      const r = await fn();
      notify(`${label}: ${r.status}`);
      if (r.calibration_id) setActive(r.calibration_id);
      load();
    } catch (e) {
      notify(`${label} failed: ${(e as Error).message}`, true);
    } finally {
      setBusy(false);
    }
  };

  const cal = status?.calibration;
  const req = (cal?.requirements ?? {}) as Record<string, number>;

  return (
    <>
      <div className="tiles">
        <div className="tile">
          <h4>
            State <span className={`pill ${cal?.valid ? "ok" : "warn"}`}>{cal?.valid ? "OK" : "ATTENTION"}</span>
          </h4>
          <div>
            <div className="big">{titleCase(cal?.state)}</div>
            <div className="sub">{cal?.reason || cal?.profile_state}</div>
          </div>
        </div>
        <div className="tile">
          <h4>Passes / direction</h4>
          <div className="big">{req.min_passes_per_direction ?? "-"}</div>
          <div className="sub">required minimum</div>
        </div>
        <div className="tile">
          <h4>Background</h4>
          <div className="big">
            {req.min_background_duration_s ?? "-"}
            <small>s</small>
          </div>
          <div className="sub">{req.min_background_reads ?? "-"} reads minimum</div>
        </div>
      </div>

      <div className="section-title">Commissioning</div>
      <div className="panel">
        <Field label="Active run">
          <input value={active ?? ""} onChange={(e) => setActive(e.target.value || null)} placeholder="calibration_id" />
        </Field>
        <div className="grid-2">
          <div>
            <Field label="Notes">
              <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="site commissioning" />
            </Field>
            <button className="btn primary block" disabled={busy} onClick={() => run("Start", () => api.startCalibration(notes))}>
              1 · Start new run
            </button>
          </div>
          <div>
            <Field label="Background duration (s)">
              <input type="number" min={5} max={300} value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
            </Field>
            <button
              className="btn block"
              disabled={busy || !active}
              onClick={() => run("Background", () => api.calibrationBackground(active!, duration))}
            >
              2 · Capture background
            </button>
          </div>
        </div>
        <div style={{ height: 12 }} />
        <div className="grid-2">
          <Field label="Direction">
            <Segmented
              value={direction}
              onChange={setDirection}
              options={[
                { value: "IN", label: "In" },
                { value: "OUT", label: "Out" },
              ]}
            />
          </Field>
          <Field label="Timeout (s)">
            <input type="number" min={1} max={300} value={timeout} onChange={(e) => setTimeoutS(Number(e.target.value))} />
          </Field>
        </div>
        <Field label="Expected EPCs on the pallet (one per line)">
          <textarea rows={3} value={epcs} onChange={(e) => setEpcs(e.target.value)} />
        </Field>
        <button
          className="btn block"
          disabled={busy || !active || !epcs.trim()}
          onClick={() =>
            run("Pass", () =>
              api.calibrationPass(
                active!,
                direction,
                epcs.split(/\s+/).map((s) => s.trim()).filter(Boolean),
                timeout,
              ),
            )
          }
        >
          3 · Record labelled pass
        </button>
        <div style={{ height: 12 }} />
        <div className="grid-2">
          <button className="btn primary block" disabled={busy || !active} onClick={() => run("Evaluate", () => api.evaluateCalibration(active!))}>
            4 · Evaluate
          </button>
          <div>
            <Field label="Abort reason">
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="wrong tags" />
            </Field>
            <button
              className="btn danger block"
              disabled={busy || !active || reason.trim().length < 3}
              onClick={() => run("Abort", () => api.abortCalibration(active!, reason))}
            >
              Abort run
            </button>
          </div>
        </div>
      </div>

      <div className="section-title">Runs</div>
      <div className="list">
        {runs.length === 0 && <div className="empty">No calibration runs</div>}
        {runs.map((r) => (
          <Row
            key={r.calibration_id}
            icon={<Target />}
            title={r.calibration_id}
            sub={`${r.notes ?? ""} · ${fmtTime(r.updated_at ?? r.created_at)}`}
            right={<span className="pill">{r.status}</span>}
            onClick={() => setActive(r.calibration_id)}
          />
        ))}
      </div>

      {summary && (
        <>
          <div className="section-title">Raw calibration status</div>
          <pre className="json">{JSON.stringify(summary, null, 2)}</pre>
        </>
      )}
    </>
  );
}
