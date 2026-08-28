// Thin typed client for the Nextwaves Gate Service REST API (contracts/openapi.json).

export type GateStatus = {
  gate_id: string;
  service_version: string;
  state: string;
  ready: boolean;
  reader: { connected: boolean; device: string; module: string; message?: string };
  sensor: { connected: boolean; device: string; message?: string };
  model?: { available: boolean; version: string; configured_version: string };
  calibration?: {
    required: boolean;
    valid: boolean;
    state: string;
    reason?: string;
    profile_valid?: boolean;
    profile_state?: string;
    hardware_signature?: string;
    requirements?: Record<string, number>;
  };
  inventory?: { running: boolean; transaction_id: string; reference: string; status: string };
  last_error?: string;
};

export type Transaction = Record<string, unknown> & {
  transaction_id: string;
  reference?: string;
  operation?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
};

export type CalibrationRun = Record<string, unknown> & {
  calibration_id: string;
  status: string;
  updated_at?: string;
  created_at?: string;
  notes?: string;
};

export type Page<T> = { items: T[]; limit: number; offset: number };

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string,
  ) {
    super(message);
  }
}

export type Settings = { baseUrl: string; token: string; operatorId: string };

const KEY = "gate-console.settings";

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { baseUrl: "", token: "", operatorId: "operator-01", ...JSON.parse(raw) };
  } catch {
    /* ignore */
  }
  return { baseUrl: "", token: "", operatorId: "operator-01" };
}

export function saveSettings(s: Settings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

function idempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `k-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class GateApi {
  constructor(private settings: Settings) {}

  private url(path: string) {
    return `${this.settings.baseUrl.replace(/\/$/, "")}${path}`;
  }

  private async request<T>(path: string, init: RequestInit = {}, mutation = false): Promise<T> {
    const headers = new Headers(init.headers);
    if (this.settings.token) headers.set("Authorization", `Bearer ${this.settings.token}`);
    if (mutation) {
      headers.set("X-Operator-ID", this.settings.operatorId || "operator");
      headers.set("Idempotency-Key", idempotencyKey());
    }
    const res = await fetch(this.url(path), { ...init, headers });
    const text = await res.text();
    const body = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const err = body?.error ?? {};
      throw new ApiError(
        res.status,
        err.code ?? "http_error",
        err.message ?? res.statusText,
        err.request_id ?? res.headers.get("x-request-id") ?? "",
      );
    }
    return body as T;
  }

  private post<T>(path: string, payload?: unknown) {
    const init: RequestInit = { method: "POST" };
    if (payload !== undefined) {
      init.body = JSON.stringify(payload);
      init.headers = { "Content-Type": "application/json" };
    }
    return this.request<T>(path, init, true);
  }

  // --- health / status -----------------------------------------------------
  healthz() {
    return this.request<{ status: string }>("/healthz");
  }
  readyz() {
    return fetch(this.url("/readyz")).then(async (r) => ({ ok: r.ok, body: await r.json() }));
  }
  status() {
    return this.request<GateStatus>("/api/v1/status");
  }

  // --- transactions --------------------------------------------------------
  transactions(params: { status?: string; limit?: number; offset?: number } = {}) {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    q.set("limit", String(params.limit ?? 50));
    q.set("offset", String(params.offset ?? 0));
    return this.request<Page<Transaction>>(`/api/v1/transactions?${q}`);
  }
  transaction(id: string) {
    return this.request<{ transaction: Transaction; reconciliation: Record<string, unknown> }>(
      `/api/v1/transactions/${encodeURIComponent(id)}`,
    );
  }
  transactionTags(id: string) {
    return this.request<{ items: Record<string, unknown>[] }>(`/api/v1/transactions/${encodeURIComponent(id)}/tags`);
  }
  transactionPassages(id: string) {
    return this.request<{ items: Record<string, unknown>[] }>(
      `/api/v1/transactions/${encodeURIComponent(id)}/passages`,
    );
  }
  transactionAudit(id: string) {
    return this.request<Page<Record<string, unknown>>>(
      `/api/v1/transactions/${encodeURIComponent(id)}/audit?limit=200`,
    );
  }

  // --- commands ------------------------------------------------------------
  startInventory(payload: {
    reference: string;
    operation: "INBOUND" | "OUTBOUND";
    expected_epcs: string[];
    antennas: boolean[];
    session: number;
    target: "A" | "B";
  }) {
    return this.post("/api/v1/commands/start-inventory", payload);
  }
  stopInventory() {
    return this.post("/api/v1/commands/stop-inventory");
  }
  commitTransaction() {
    return this.post("/api/v1/commands/commit-transaction");
  }
  cancelTransaction(reason: string) {
    return this.post("/api/v1/commands/cancel-transaction", { reason });
  }

  // --- calibration ---------------------------------------------------------
  calibration() {
    return this.request<Record<string, unknown>>("/api/v1/calibration");
  }
  calibrationRuns(limit = 50) {
    return this.request<Page<CalibrationRun>>(`/api/v1/calibration/runs?limit=${limit}`);
  }
  calibrationRun(id: string) {
    return this.request<CalibrationRun>(`/api/v1/calibration/runs/${encodeURIComponent(id)}`);
  }
  startCalibration(notes: string) {
    return this.post<CalibrationRun>("/api/v1/calibration/runs", { notes });
  }
  calibrationBackground(id: string, duration_seconds: number) {
    return this.post<CalibrationRun>(`/api/v1/calibration/runs/${encodeURIComponent(id)}/background`, {
      duration_seconds,
    });
  }
  calibrationPass(id: string, direction: "IN" | "OUT", expected_epcs: string[], timeout_seconds: number) {
    return this.post<CalibrationRun>(`/api/v1/calibration/runs/${encodeURIComponent(id)}/passes`, {
      direction,
      expected_epcs,
      timeout_seconds,
    });
  }
  evaluateCalibration(id: string) {
    return this.post<CalibrationRun>(`/api/v1/calibration/runs/${encodeURIComponent(id)}/evaluate`);
  }
  abortCalibration(id: string, reason: string) {
    return this.post<CalibrationRun>(`/api/v1/calibration/runs/${encodeURIComponent(id)}/abort`, { reason });
  }
}
