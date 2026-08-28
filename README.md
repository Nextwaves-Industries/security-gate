# Nextwaves RFID Portal Gate Service

Headless, container-packaged control service for **one physical RFID portal
gate** built on the Nextwaves NR155 reader/sensor unit. It runs the reader,
detects tag passages with the bundled model, keeps an authoritative SQLite
ledger of inventory transactions, and exposes three integration surfaces:

| Surface | Port | Purpose |
|---|---|---|
| **REST / HTTPS** | `8443` | commands (start / stop / commit / cancel inventory), transaction queries, calibration workflow, health |
| **gRPC / TLS** | `50051` | status + live (non-durable) event stream |
| **MQTT 5 / TLS** | customer broker | durable business events via a transactional outbox, retained gate state with Last Will |

Version **1.0.0-rc1** - release candidate for canary and hardware acceptance on
a real NR155 gate before `1.0.0`.

> This repository is the **customer release bundle**. The business runtime and
> model ship as CPython 3.11 / Linux x86_64 extensions (`.so`). It contains no
> protected Python source, plaintext models, databases, calibration keys or
> credentials. See [REVIEW.md](REVIEW.md) for what is and is not editable here.

---

## Contents

- [What it does](#what-it-does)
- [Typical use cases](#typical-use-cases)
- [Quick start (laptop, no hardware)](#quick-start-laptop-no-hardware)
- [Production deployment (customer gate)](#production-deployment-customer-gate)
- [Using the API](#using-the-api)
- [Example operator console](#example-operator-console)
- [Repository layout](#repository-layout)
- [Documentation map](#documentation-map)
- [Security and operating rules](#security-and-operating-rules)
- [Verification and release integrity](#verification-and-release-integrity)

---

## What it does

```text
             ┌───────────────────────────── gate host (Linux x86_64, Docker) ─────────────────────────────┐
 NR155 USB   │  gate-service container (read-only rootfs, uid 10001, cap_drop ALL)                        │
 303a:4002 ──┼─▶ reader_engine ─▶ tag model ─▶ transactions / passages / audit (SQLite, WAL, outbox) ──┐   │
  if00 reader│        │                                                                             │   │
  if02 sensor│        ├─▶ REST 8443 ── commands, queries, calibration, /healthz /readyz            │   │
             │        ├─▶ gRPC 50051 ── GetStatus, WatchEvents                                      │   │
             │        └─▶ MQTT 5/TLS ── rfid/portal/v1/{GATE_ID}/state (retained) + events/… ◀──────┘   │
             └───────────────────────────────────────────────────────────────────────────────────────────┘
```

1. An operator (WMS, handheld, or the [example console](#example-operator-console))
   calls `POST /api/v1/commands/start-inventory` with a reference (ASN, order)
   and direction (`INBOUND` / `OUTBOUND`).
2. The gate arms the reader antennas; the sensor beam detects a pallet/cart
   passing through; the model decides which tags actually moved through the
   portal versus stray reads.
3. The operator commits (or cancels) the transaction. The result is written to
   SQLite **and** to the MQTT outbox in the same transaction, then delivered
   at-least-once to the customer broker (`inventory.*` events, dedupe on
   `event_id`).
4. Everything is queryable afterwards: net tags, individual passages,
   reconciliation against expected EPCs, and a full audit trail.

Calibration is a first-class workflow (`/api/v1/calibration/*`): background
capture, labelled IN/OUT passes with known EPCs, evaluate. Until a site is
calibrated the gate reports `readyz = 503 calibration_required`.

## Typical use cases

| Case | How the service is used |
|---|---|
| **Warehouse dock door - inbound receiving** | WMS starts an `INBOUND` inventory with the ASN's expected EPC list; forklift drives the pallet through; WMS commits and reads `/transactions/{id}` → `reconciliation` (expected vs. seen) to auto-close the receipt or flag shortages. |
| **Outbound shipping verification** | `OUTBOUND` inventory per shipment; the net tag set is compared against the pick list before the truck door closes; mismatches cancel the transaction with a reason that lands in the audit log. |
| **Production line / WIP gate** | Continuous small transactions per cart; consumers subscribe to `rfid/portal/v1/{gate}/events/#` on MQTT and never poll REST. |
| **Retail back-of-store or library portal** | Same flow with `expected_epcs` empty; the gate simply reports what passed and in which direction (`passages`). |
| **Multi-gate site** | One container per physical gate (`GATE_ID` unique); a central dashboard aggregates via MQTT and scrapes `/readyz` for fleet health. Never scale one container to several replicas. |
| **Commissioning / field service** | Technician uses the calibration endpoints (or the example console) on site; `deploy/ACCEPTANCE.md` is the sign-off checklist. |
| **Integration development without hardware** | `deploy/compose.dev.yaml` brings up the service + a local MQTT broker on a laptop; all read APIs, auth, health, MQTT connectivity and the console work; inventory stays disabled because no reader is attached. |

## Quick start (laptop, no hardware)

Requirements: Docker Engine 24+ (or Docker Desktop / OrbStack), Compose v2, `openssl`.

```sh
git clone <this repository> && cd RFID_Portal_release

sh deploy/dev/bootstrap-dev-secrets.sh                 # dev CA, certs, API token, MQTT password
docker compose -f deploy/compose.dev.yaml up --build -d
docker compose -f deploy/compose.dev.yaml logs -f gate-service

CA=deploy/dev/secrets/dev_ca.pem
TOKEN=$(cat deploy/dev/secrets/api_token)
curl --cacert $CA https://127.0.0.1:8443/healthz                                   # {"status":"ok"}
curl --cacert $CA https://127.0.0.1:8443/readyz                                    # 503 degraded (no NR155) - expected
curl --cacert $CA -H "Authorization: Bearer $TOKEN" https://127.0.0.1:8443/api/v1/status | jq
open https://127.0.0.1:8443/docs                                                   # Swagger UI (dev mode only)
```

Attach real hardware on a Linux host by exporting `READER_DEVICE`,
`SENSOR_DEVICE` (the `/dev/serial/by-id/…-if00` / `-if02` links) and
`DIALOUT_GID` before `up`. Tear down with `docker compose -f deploy/compose.dev.yaml down -v`.

Full walkthrough: [USAGE.md](USAGE.md).

## Production deployment (customer gate)

Production uses `deploy/compose.yaml` - digest-pinned signed image, six
file-backed secrets, USB passthrough, loopback/VLAN binds, systemd + udev
hot-plug integration. The complete runbook is
[deploy/README.md](deploy/README.md); the short version:

```sh
# 1. Verify and pull the signed image digest from RELEASE_MANIFEST.txt / VERIFY_RELEASE.md
docker pull ghcr.io/OWNER/rfid-portal-gate-service@sha256:<digest>

# 2. Host layout
sudo install -d -m 0750 /opt/nextwaves-gate/deploy
sudo install -d -o 0 -g 10001 -m 0750 /etc/nextwaves-gate/secrets
sudo install -d -o 10001 -g 10001 -m 0750 /var/lib/nextwaves-gate
sudo cp deploy/compose.yaml deploy/wait-for-devices.sh deploy/validate-running.sh \
        deploy/ACCEPTANCE.md deploy/gate.env.example /opt/nextwaves-gate/deploy/
sudo install -m 0644 deploy/nextwaves-gate.service deploy/nextwaves-gate-hotplug.service /etc/systemd/system/
sudo install -m 0644 deploy/99-nextwaves-rfid.rules /etc/udev/rules.d/

# 3. Configure: copy gate.env.example → gate.env; set GATE_IMAGE (digest), GATE_ID,
#    READER_DEVICE / SENSOR_DEVICE (by-id links), DIALOUT_GID, MQTT_*, *_SECRET_FILE paths.

# 4. Secrets (root:10001 0440): api_token, calibration_root_key (64 hex), mqtt_password,
#    tls_cert.pem, tls_key.pem, mqtt_ca.pem  → /etc/nextwaves-gate/secrets/

# 5. Preflight, start, validate
cd /opt/nextwaves-gate/deploy
sudo systemctl daemon-reload && sudo udevadm control --reload-rules   # replug the NR155 once
sudo sh -c 'set -a; . ./gate.env; set +a; sh ./wait-for-devices.sh'
sudo docker compose --env-file gate.env config
sudo systemctl enable --now nextwaves-gate.service
sudo REQUIRE_READY=0 sh ./validate-running.sh
```

Then calibrate on site (Linux gates must be re-calibrated; Windows DPAPI state
is not portable), complete [deploy/ACCEPTANCE.md](deploy/ACCEPTANCE.md), and
run `validate-running.sh` again with `REQUIRE_READY=1`.

Upgrade = new digest in `gate.env` + `systemctl restart`; the service takes an
online SQLite backup before migrating. Rollback and restore procedure:
[deploy/README.md §6](deploy/README.md).

## Using the API

Authentication is a bearer token shared by REST and gRPC. Every **mutation**
needs `X-Operator-ID` and a unique `Idempotency-Key` (replays return the cached
result; a reused key with a different payload is `409 idempotency_conflict`).

```sh
BASE=https://127.0.0.1:8443; CA=…/tls_cert.pem; AUTH="Authorization: Bearer $(cat …/api_token)"

# Start → commit an inbound receipt
curl --cacert $CA -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"reference":"ASN-100","operation":"INBOUND","expected_epcs":["E2000017221101441890A1B2"],
       "antennas":[true,true,false,false],"session":0,"target":"A"}' \
  $BASE/api/v1/commands/start-inventory
curl --cacert $CA -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" -X POST $BASE/api/v1/commands/commit-transaction

# Query
curl --cacert $CA -H "$AUTH" "$BASE/api/v1/transactions?status=COMMITTED&limit=20"
curl --cacert $CA -H "$AUTH"  $BASE/api/v1/transactions/<id>          # record + reconciliation
curl --cacert $CA -H "$AUTH"  $BASE/api/v1/transactions/<id>/tags     # net EPC set
curl --cacert $CA -H "$AUTH"  $BASE/api/v1/transactions/<id>/audit

# Live status over gRPC; durable events over MQTT
grpcurl -cacert $CA -H "authorization: Bearer $TOKEN" -proto contracts/gate_stream.proto \
  127.0.0.1:50051 nextwaves.gate.v1.GateStreamService/GetStatus
mosquitto_sub -h broker -p 8883 --cafile ca.pem -u gate-01 -P … -t 'rfid/portal/v1/GATE-01/#' -v
```

Contracts: [REST OpenAPI](contracts/openapi.json) ·
[gRPC proto](contracts/gate_stream.proto) ·
[MQTT delivery contract](contracts/MQTT.md) ·
[MQTT envelope schema](contracts/mqtt/event-envelope.schema.json).
Health semantics, error codes, calibration flow and troubleshooting:
[USAGE.md](USAGE.md).

## Example operator console

[`examples/gate-console`](examples/gate-console) is a Vite + React reference UI
for one gate - a top-down live view of the portal (reader, sensor beam,
antennas, direction), a plain status headline, start / stop /
commit / cancel actions, transaction browser with reconciliation and audit
timeline, the full calibration flow, and a Config page showing the gate's
effective configuration.

```sh
cd examples/gate-console && npm install && cp .env.example .env && npm run dev   # http://localhost:5173
```

Paste the API token under **Config**. The Vite dev server proxies to the gate
(`VITE_GATE_URL`), because the service intentionally emits no CORS headers; in
production serve `dist/` from the same origin as the API. Details:
[examples/gate-console/README.md](examples/gate-console/README.md).

## Repository layout

```text
runtime/            protected cp311 Linux runtime (.so) + plaintext transport adapters (api/rest.py, api/grpc_server.py)
deploy/             compose.yaml (prod), compose.dev.yaml (dev), systemd, udev, preflight/validation, acceptance checklist
deploy/dev/         Mosquitto config + bootstrap-dev-secrets.sh for the dev stack
contracts/          REST OpenAPI, gRPC proto, MQTT contract + JSON Schemas
examples/           gate-console reference UI (Vite + React)
tests/              pytest suite for the transport adapters (compiled modules stubbed); runs in CI
scripts/            product checksum manifest, leakage scanner, Compose contract checker, image smoke test
release/            protected-runtime module manifest
Dockerfile          runtime-only image (no protected source is compiled here)
USAGE.md            end-to-end usage guide · REVIEW.md  production-readiness review
```

## Documentation map

| Need | Read |
|---|---|
| Run it locally, call the API, troubleshoot | [USAGE.md](USAGE.md) |
| Deploy to a customer gate, secrets, backup/restore, upgrade | [deploy/README.md](deploy/README.md) |
| Hardware commissioning sign-off | [deploy/ACCEPTANCE.md](deploy/ACCEPTANCE.md) |
| What was reviewed, what was fixed, known runtime issues | [REVIEW.md](REVIEW.md) |
| Build an integration | [contracts/](contracts/) |
| Build an operator UI | [examples/gate-console/README.md](examples/gate-console/README.md) |

## Security and operating rules

- One container ⇔ one physical gate. Never run replicas or the desktop runtime
  against the same serial interfaces.
- REST and gRPC bind to loopback by default; for remote clients use the host's
  dedicated VLAN/VPN address. Wildcard binds are rejected by CI and by
  `validate-running.sh`. Do not expose the service to the Internet.
- TLS ≥ 1.2 everywhere; secrets are files (`root:10001 0440`), never environment
  variables. `GATE_DEVELOPMENT` and `GATE_ALLOW_INSECURE` must stay `false` in
  production (CI-enforced).
- The calibration root key is customer-owned; losing it forces re-calibration.
- Never commit `gate.env`, secrets, databases or customer data to this repository.

## Verification and release integrity

Every file is locked by `PRODUCT_SHA256SUMS`; CI fails on drift. After an
intended edit regenerate it with Python 3.11:

```sh
python3.11 -B scripts/verify_product.py . --write
python3.11 -B scripts/scan_headless_release.py --root runtime --manifest release/protected_modules_headless.json
docker compose --env-file deploy/gate.env.example -f deploy/compose.yaml config --format json > /tmp/c.json
python3.11 -B scripts/verify_compose_contract.py /tmp/c.json --rest-host-port 8443 --grpc-host-port 50051
pip install -r tests/requirements-test.txt && python -B -m pytest -p no:cacheprovider -q tests
```

Release images are built by `.github/workflows/product-image.yml`, signed with
Cosign, shipped with SBOM + provenance, and must be deployed by **digest**,
never by tag. Verify per `VERIFY_RELEASE.md` in the release bundle.
