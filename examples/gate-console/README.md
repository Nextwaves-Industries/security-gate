# Gate Console (example)

A single-page operator console for one Nextwaves RFID gate, built with
Vite + React + TypeScript and no UI framework. Black canvas, one status word,
the portal drawn as a flat-shaded isometric render, icon actions, hairline
lists. Figtree for text, JetBrains Mono for identifiers. No gradients, shadows
or animation.

It is an example, not part of the signed product image. It talks only to the
public REST contract in `contracts/openapi.json`.

## Pages

| Page | Shows | Actions |
|---|---|---|
| Overview | Portal render (pillars, antenna panels A1 to A4, sensor beam, direction while an inventory runs), readiness, inventory, poll latency, subsystem rows for reader, sensor, model, calibration, last error | Start (opens a form: reference, inbound/outbound, expected EPCs, antenna mask), Stop, Commit, Cancel (sends the fixed reason "Cancelled by operator"). "Hide diagram" collapses the render; the choice is remembered on this device. |
| Transactions | List with All / Active / Committed / Cancelled filter; detail with net tags, passages, reconciliation, full record, audit timeline | none |
| Calibration | State, required passes and background duration, run history | Start run, Capture background, Record pass, Evaluate, Abort |
| Config | Connection rows (Gate URL, Access token, Operator) and the gate's reported configuration (gate, software, state, reader, sensor, model, calibration, hardware signature) | Settings apply about 0.6 s after you stop typing. There is no Save button. |

Every command sends `X-Operator-ID` and a fresh `Idempotency-Key`, the same as
the curl examples in `USAGE.md`. Start is enabled only while `/api/v1/status`
reports `ready: true`; without a reader attached it stays disabled.

## Run it

```sh
cd examples/gate-console
npm install
cp .env.example .env            # VITE_GATE_URL=https://127.0.0.1:8443
npm run dev                     # http://localhost:5173
```

Open Config and paste the access token (`deploy/dev/secrets/api_token` for
the dev stack, or the gate's `api_token` secret). Operator defaults to
`operator-01`. Leave Gate URL empty to use the proxy. The headline changes
from Connecting to the gate state within two seconds.

The dev server proxies `/api`, `/healthz` and `/readyz` to `VITE_GATE_URL`
with TLS verification disabled, because gates use site-issued certificates.
The gate service sends no CORS headers, so in production serve the built
`dist/` from the same origin as the API, for example behind an nginx or Caddy
reverse proxy on the gate host, and keep Gate URL empty.

```sh
npm run build                   # dist/, static files
npm run preview                 # serves dist/ on http://localhost:4173 with the same proxy
```

## Notes and limits

- Status is polled every 2 s. The gRPC `WatchEvents` stream is not reachable
  from a browser without grpc-web; MQTT is the durable event source.
- The token is stored in `localStorage` of the operator's browser. Treat the
  console as an operator tool on the gate VLAN, not a public site.
- Transaction detail renders whatever fields the API returns, so it keeps
  working as the contract evolves.
