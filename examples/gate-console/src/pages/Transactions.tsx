import { useEffect, useState } from "react";
import type { GateApi, Transaction } from "../api";
import { Layers } from "../components/Icons";
import { Row, Segmented, fmtTime } from "../components/ui";

type Filter = "" | "OPEN" | "ACTIVE" | "COMMITTED" | "CANCELLED";

export default function Transactions({ api, notify }: { api: GateApi; notify: (m: string, bad?: boolean) => void }) {
  const [filter, setFilter] = useState<Filter>("");
  const [items, setItems] = useState<Transaction[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .transactions({ status: filter || undefined, limit: 100 })
      .then((p) => !cancelled && setItems(p.items))
      .catch((e) => notify(`Transactions: ${(e as Error).message}`, true));
    return () => {
      cancelled = true;
    };
  }, [api, filter, notify]);

  if (selected) return <Detail api={api} id={selected} onBack={() => setSelected(null)} notify={notify} />;

  return (
    <>
      <div style={{ margin: "8px 0 14px" }}>
        <Segmented
          value={filter}
          onChange={setFilter}
          options={[
            { value: "", label: "All" },
            { value: "ACTIVE", label: "Active" },
            { value: "COMMITTED", label: "Committed" },
            { value: "CANCELLED", label: "Cancelled" },
          ]}
        />
      </div>
      <div className="list">
        {items.length === 0 && <div className="empty">No transactions</div>}
        {items.map((t) => (
          <Row
            key={t.transaction_id}
            icon={<Layers />}
            title={t.reference || t.transaction_id}
            sub={`${t.operation ?? ""} · ${fmtTime(t.updated_at ?? t.created_at)}`}
            right={<span className={`pill ${pillTone(t.status)}`}>{t.status}</span>}
            onClick={() => setSelected(t.transaction_id)}
          />
        ))}
      </div>
    </>
  );
}

function pillTone(status?: string) {
  if (status === "COMMITTED") return "ok";
  if (status === "CANCELLED") return "bad";
  if (status === "ACTIVE") return "warn";
  return "";
}

function Detail({
  api,
  id,
  onBack,
  notify,
}: {
  api: GateApi;
  id: string;
  onBack: () => void;
  notify: (m: string, bad?: boolean) => void;
}) {
  const [tx, setTx] = useState<Record<string, unknown> | null>(null);
  const [recon, setRecon] = useState<Record<string, unknown> | null>(null);
  const [tags, setTags] = useState<Record<string, unknown>[]>([]);
  const [passages, setPassages] = useState<Record<string, unknown>[]>([]);
  const [audit, setAudit] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    Promise.all([api.transaction(id), api.transactionTags(id), api.transactionPassages(id), api.transactionAudit(id)])
      .then(([t, tg, ps, au]) => {
        setTx(t.transaction);
        setRecon(t.reconciliation);
        setTags(tg.items);
        setPassages(ps.items);
        setAudit(au.items);
      })
      .catch((e) => notify(`Transaction: ${(e as Error).message}`, true));
  }, [api, id, notify]);

  return (
    <>
      <button className="back" onClick={onBack}>
        ‹ Transactions
      </button>
      <h2 style={{ margin: "0 0 14px", fontSize: 28, letterSpacing: "-0.02em" }}>
        {(tx?.reference as string) || id}
      </h2>
      <div className="tiles">
        <div className="tile">
          <h4>Net tags</h4>
          <div className="big">{tags.length}</div>
        </div>
        <div className="tile">
          <h4>Passages</h4>
          <div className="big">{passages.length}</div>
        </div>
        <div className="tile">
          <h4>Status</h4>
          <div className="big">{(tx?.status as string) ?? "-"}</div>
        </div>
      </div>

      <div className="section-title">Reconciliation</div>
      <div className="panel">
        <dl className="kv">
          {Object.entries(recon ?? {}).map(([k, v]) => (
            <Pair key={k} k={k} v={v} />
          ))}
        </dl>
      </div>

      <div className="section-title">Transaction</div>
      <div className="panel">
        <dl className="kv">
          {Object.entries(tx ?? {}).map(([k, v]) => (
            <Pair key={k} k={k} v={v} />
          ))}
        </dl>
      </div>

      <div className="section-title">Tags ({tags.length})</div>
      <div className="panel mono">
        {tags.length === 0 && <div className="empty">No tags</div>}
        {tags.map((t, i) => (
          <div key={i}>{String(t.epc ?? JSON.stringify(t))}</div>
        ))}
      </div>

      <div className="section-title">Audit trail</div>
      <div className="panel">
        <ul className="timeline">
          {audit.length === 0 && <li className="empty">No audit entries</li>}
          {audit.map((a, i) => {
            const ev = String(a.event ?? a.action ?? a.event_type ?? "");
            const tone = /FAIL|ERROR|CANCEL/.test(ev) ? "" : /SUCCEED|COMMIT|COMPLETE/.test(ev) ? "ok" : "neutral";
            return (
              <li key={i}>
                <span className={`tdot ${tone}`} />
                <span style={{ color: "var(--muted)" }}>{fmtTime(a.created_at ?? a.timestamp ?? a.at)}</span>
                <span>{ev}</span>
                <span style={{ color: "var(--muted)" }}>{String(a.actor ?? "")}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </>
  );
}

function Pair({ k, v }: { k: string; v: unknown }) {
  const text = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "-");
  return (
    <>
      <dt>{k}</dt>
      <dd className={typeof v === "object" ? "mono" : undefined}>{/_at$/.test(k) ? fmtTime(v) : text}</dd>
    </>
  );
}
