import { useState } from "react";
import type { GateStatus, Settings } from "../api";
import { Field } from "../components/ui";

// "Config" = what the gate reports about itself + how this console reaches it.
// The service intentionally exposes no config-write endpoint; runtime
// configuration lives in gate.env / compose.yaml on the host.
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

  const effective: [string, unknown][] = status
    ? [
        ["Gate ID", status.gate_id],
        ["Service version", status.service_version],
        ["State", status.state],
        ["Reader module", status.reader.module],
        ["Reader device", status.reader.device],
        ["Sensor device", status.sensor.device],
        ["Model version", status.model?.version],
        ["Configured model", status.model?.configured_version],
        ["Calibration required", status.calibration?.required],
        ["Calibration valid days", status.calibration?.requirements?.valid_days],
        ["Hardware signature", status.calibration?.hardware_signature],
      ]
    : [];

  return (
    <>
      <div className="section-title">Connection</div>
      <div className="panel">
        <Field label="API base URL (leave empty to use the Vite proxy → VITE_GATE_URL)">
          <input
            value={draft.baseUrl}
            onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
            placeholder="(proxy)"
          />
        </Field>
        <Field label="Bearer token (contents of the api_token secret)">
          <input
            type="password"
            value={draft.token}
            onChange={(e) => setDraft({ ...draft, token: e.target.value })}
            autoComplete="off"
          />
        </Field>
        <Field label="Operator ID (sent as X-Operator-ID on every command)">
          <input value={draft.operatorId} onChange={(e) => setDraft({ ...draft, operatorId: e.target.value })} />
        </Field>
        <button className="btn primary" onClick={() => onSave(draft)}>
          Save
        </button>
        <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 0 }}>
          Stored in this browser only. The gate exposes no CORS headers, so a direct base URL only works when the
          console is served from the same origin (e.g. behind the site's reverse proxy).
        </p>
      </div>

      <div className="section-title">Effective gate configuration</div>
      <div className="panel">
        {effective.length === 0 ? (
          <div className="empty">Connect to a gate to see its reported configuration</div>
        ) : (
          <dl className="kv">
            {effective.map(([k, v]) => (
              <span key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd className={k === "Hardware signature" ? "mono" : undefined}>{v === undefined ? "-" : String(v)}</dd>
              </span>
            ))}
          </dl>
        )}
      </div>

      <div className="section-title">Host-side settings (read-only reference)</div>
      <div className="panel">
        <p style={{ marginTop: 0, color: "var(--muted)", fontSize: 14 }}>
          These are set in <code>deploy/gate.env</code> and cannot be changed from the API by design.
        </p>
        <dl className="kv">
          <dt>GATE_ID</dt>
          <dd>stable site identifier; also the MQTT topic segment</dd>
          <dt>READER_DEVICE / SENSOR_DEVICE</dt>
          <dd>
            <code>/dev/serial/by-id/…-if00</code> / <code>…-if02</code>
          </dd>
          <dt>MQTT_HOST / MQTT_PORT</dt>
          <dd>customer broker, MQTT 5 over TLS</dd>
          <dt>COMMAND_TIMEOUT_S</dt>
          <dd>hardware command and mutation-lock timeout</dd>
          <dt>REST_BIND_IP / GRPC_BIND_IP</dt>
          <dd>loopback or dedicated VLAN address only</dd>
        </dl>
      </div>

      {status && (
        <>
          <div className="section-title">Raw status</div>
          <pre className="json">{JSON.stringify(status, null, 2)}</pre>
        </>
      )}
    </>
  );
}
