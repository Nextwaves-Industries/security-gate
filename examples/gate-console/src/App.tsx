import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, GateApi, loadSettings, saveSettings, type GateStatus, type Settings } from "./api";
import Overview from "./pages/Overview";
import Transactions from "./pages/Transactions";
import Calibration from "./pages/Calibration";
import Config from "./pages/Config";
import { titleCase } from "./components/ui";

type Page = "overview" | "transactions" | "calibration" | "config";
const POLL_MS = 2000;

export default function App() {
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [page, setPage] = useState<Page>("overview");
  const [status, setStatus] = useState<GateStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<number[]>([]);
  const [toast, setToast] = useState<{ msg: string; bad: boolean } | null>(null);
  const [tick, setTick] = useState(0);
  const toastTimer = useRef<number | undefined>(undefined);

  const api = useMemo(() => new GateApi(settings), [settings]);

  const notify = useCallback((msg: string, bad = false) => {
    setToast({ msg, bad });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }, []);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  // Poll the status endpoint; the gRPC stream is not reachable from a browser.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      const t0 = performance.now();
      try {
        const s = await api.status();
        if (cancelled) return;
        setStatus(s);
        setError(null);
        setHistory((h) => [...h.slice(-59), performance.now() - t0]);
      } catch (e) {
        if (cancelled) return;
        setStatus(null);
        setError(e instanceof ApiError ? `${e.status} ${e.code}: ${e.message}` : (e as Error).message);
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, POLL_MS);
      }
    };
    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, tick]);

  const headline = status ? (status.ready ? "Ready" : titleCase(status.state)) : error ? "Unreachable" : "Connecting";
  const tone = status ? (status.ready ? "ok" : "warn") : error ? "bad" : "";
  const toneLabel = status ? (status.ready ? "OK" : "NOT READY") : error ? "OFFLINE" : "";

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">
            {status?.gate_id ?? "Gate"} <span className="chev">›</span>{" "}
            <span>{status ? `v${status.service_version}` : settings.token ? "authenticating" : "no token"}</span>
          </div>
          <h1 className="headline">
            {headline}
            {toneLabel && <span className={`pill ${tone}`}>{toneLabel}</span>}
          </h1>
          {error && (
            <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 6 }}>
              {error} - check the token and gate URL under Config.
            </div>
          )}
        </div>
      </header>

      <nav className="nav">
        {(["overview", "transactions", "calibration", "config"] as Page[]).map((p) => (
          <button key={p} className={page === p ? "active" : ""} onClick={() => setPage(p)}>
            {titleCase(p)}
          </button>
        ))}
      </nav>

      {page === "overview" && <Overview api={api} status={status} history={history} notify={notify} refresh={refresh} />}
      {page === "transactions" && <Transactions api={api} notify={notify} />}
      {page === "calibration" && <Calibration api={api} status={status} notify={notify} />}
      {page === "config" && (
        <Config
          status={status}
          settings={settings}
          onSave={(s) => {
            saveSettings(s);
            setSettings(s);
            notify("Settings saved");
          }}
        />
      )}

      {toast && <div className={`toast ${toast.bad ? "bad" : ""}`}>{toast.msg}</div>}
    </div>
  );
}
