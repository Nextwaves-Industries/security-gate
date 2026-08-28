# Gate Console (example)

A single-page operator console for one Nextwaves RFID gate, built with
**Vite + React + TypeScript** and no UI framework. The layout follows the
Tesla / Starlink apps: black canvas, one status word, the hardware drawn as a
flat line diagram with state shown on it, a row of actions, stat tiles and
list rows. Inter for text, JetBrains Mono for identifiers. No gradients,
shadows or animation.

It is an example, not part of the signed product image. It talks only to the
public REST contract in `contracts/openapi.json`.

## What it shows

| Page | Data | Actions |
|---|---|---|
| **Overview** | live portal diagram (reader, sensor beam, antennas A1-A4, direction while inventory runs), readiness, inventory, poll-latency sparkline, subsystem rows (reader / sensor / model / calibration / last error) | Start inventory (reference, inbound/outbound, expected EPCs, antenna mask), Stop, Commit, Cancel |
| **Transactions** | list with status filter; detail with net tags, passages, reconciliation, full record, audit timeline | - |
| **Calibration** | state, requirements, run history, raw calibration status | the 4-step commissioning flow: start → background → labelled passes → evaluate, plus abort |
| **Config** | connection settings (token, operator id, base URL), the gate's *effective* configuration as reported by `/api/v1/status`, a read-only reference of host-side `gate.env` keys, raw status JSON | save connection settings (browser-local) |

Every command sends `X-Operator-ID` and a fresh `Idempotency-Key`, exactly like
the curl examples in `USAGE.md`.

## Run it

```sh
cd examples/gate-console
npm install
cp .env.example .env            # VITE_GATE_URL=https://127.0.0.1:8443
npm run dev                     # http://localhost:5173
```

Open **Config**, paste the bearer token (`deploy/dev/secrets/api_token` for the
dev stack, or the gate's `api_token` secret), set an operator id, Save. The
headline turns from *Connecting* to the gate state within two seconds.

The dev server **proxies** `/api`, `/healthz` and `/readyz` to `VITE_GATE_URL`
with TLS verification disabled (gates use site-issued certificates). The gate
service deliberately sends no CORS headers, so in production serve the built
`dist/` from the same origin as the API - e.g. an nginx/Caddy reverse proxy on
the gate host or the customer's operator network - and leave "API base URL"
empty.

```sh
npm run build                   # dist/ - static files, ~55 kB gzipped
npm run preview                 # serves dist/ with the same proxy
```

## Notes and limits

- Status is polled every 2 s; the gRPC `WatchEvents` stream is not reachable
  from a browser without grpc-web, and MQTT is the durable event source.
- The token is stored in `localStorage` of the operator's browser. Treat the
  console as an operator tool on the gate VLAN, not a public site.
- Without hardware attached (dev stack) the Start action is disabled because
  `readyz` is 503; everything else works and is useful for integration work.
- Field names in the transaction detail are rendered generically from the API
  payload so the page keeps working as the contract evolves.
