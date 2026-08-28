# Nextwaves RFID Portal Gate Service

Headless, container-packaged control service for **one physical RFID portal
gate** built on the Nextwaves NR155 reader/sensor unit. It drives the reader,
decides which tags actually passed through the portal, keeps an authoritative
SQLite ledger of inventory transactions, and exposes three integration
surfaces:

| Surface | Port | Direction | Durability | Use it for |
|---|---|---|---|---|
| REST / HTTPS | `8443` | client to gate | request/response | commands (start, stop, commit, cancel), queries, calibration, health |
| gRPC / TLS | `50051` | client to gate | live stream, not durable | `GetStatus`, `WatchEvents` for dashboards |
| MQTT 5 / TLS | customer broker | gate to broker | at-least-once via a transactional outbox | business events, retained gate state, Last Will |

Version **1.0.0-rc1**: release candidate for canary and hardware acceptance on
a real NR155 gate before `1.0.0`.

> This repository is the **customer release bundle**. The business runtime and
> model ship as CPython 3.11 / Linux x86_64 extensions (`.so`). It contains no
> protected Python source, plaintext models, databases, calibration keys or
> credentials. [REVIEW.md](REVIEW.md#what-you-can-change-in-this-bundle) lists
> exactly which files are editable here.

## Contents

- [How it works](#how-it-works)
- [Typical use cases](#typical-use-cases)
- [Quick start on a laptop](#quick-start-on-a-laptop)
- [Production deployment](#production-deployment)
- [Using the API](#using-the-api)
- [Example operator console](#example-operator-console)
- [Repository layout](#repository-layout)
- [Documentation map](#documentation-map)
- [Security and operating rules](#security-and-operating-rules)
- [Verification and release integrity](#verification-and-release-integrity)

## How it works

1. An operator system (WMS, handheld, or the [example console](#example-operator-console))
   calls `POST /api/v1/commands/start-inventory` with a reference (ASN, order
   number) and a direction, `INBOUND` or `OUTBOUND`.
2. The gate powers the selected antennas. The sensor beam detects a pallet or
   cart crossing the lane (a *passage*). The detection model separates tags
   that moved through the portal from *stray reads* of tags that only sat
   nearby.
3. The operator commits or cancels the transaction. On commit the result and
   an `inventory.*` event are written to SQLite in the same database
   transaction; the event is then delivered to the customer MQTT broker at
   least once (consumers deduplicate on `event_id`).
4. Everything is queryable afterwards: the *net tags* (final set of tags that
   passed), each passage, a *reconciliation* of expected versus seen EPCs, and
   the full audit trail.

Calibration is a first-class workflow (`/api/v1/calibration/*`): capture the
empty-gate background, record labelled `IN` and `OUT` passes with known tags,
then evaluate. Until a site is calibrated `GET /readyz` returns `503`.

Terms used throughout the docs are defined in the [glossary](USAGE.md#glossary).

## Typical use cases

| Case | How the service is used |
|---|---|
| Warehouse dock door, inbound receiving | WMS starts an `INBOUND` inventory with the ASN's expected EPCs; the pallet is driven through; WMS commits and reads `reconciliation` to close the receipt or flag shortages. |
| Outbound shipping verification | `OUTBOUND` inventory per shipment; net tags are compared with the pick list before the door closes; mismatches are cancelled with a reason that lands in the audit log. |
| Production line or WIP gate | Many small transactions per cart; consumers subscribe to `rfid/portal/v1/{GATE_ID}/events/#` and never poll REST. |
| Retail back-of-store or library portal | Same flow with an empty expected list; the gate reports what passed and in which direction. |
| Multi-gate site | One container per physical gate, each with a unique `GATE_ID`; a central dashboard aggregates over MQTT and scrapes `/readyz`. Never run several replicas of one gate. |
| Commissioning and field service | Technicians use the calibration endpoints or the console on site; `deploy/ACCEPTANCE.md` is the sign-off checklist. |
| Integration development without hardware | `deploy/compose.dev.yaml` runs the service and a local broker on a laptop. Read APIs, auth, health, MQTT connectivity and the console all work; inventory stays disabled without a reader. |

## Quick start on a laptop

Requirements: an x86_64 host or Docker with x86 emulation (the runtime is
`linux/amd64` only), Docker Engine 24+ or Docker Desktop/OrbStack, Compose v2,
`openssl`.

```sh
git clone <this repository> && cd RFID_Portal_release

sh deploy/dev/bootstrap-dev-secrets.sh     # dev CA, TLS certs, API token, calibration key, MQTT password
docker compose -f deploy/compose.dev.yaml up --build -d
docker compose -f deploy/compose.dev.yaml logs -f gate-service

CA=deploy/dev/secrets/dev_ca.pem
TOKEN=$(cat deploy/dev/secrets/api_token)
curl --cacert "$CA" https://127.0.0.1:8443/healthz     # {"status":"ok"}
curl --cacert "$CA" https://127.0.0.1:8443/readyz      # 503, error.code "degraded": no reader attached, expected
curl --cacert "$CA" -H "Authorization: Bearer $TOKEN" https://127.0.0.1:8443/api/v1/status
```

Swagger UI is at `https://127.0.0.1:8443/docs` (development mode only).
To attach a real NR155 on a Linux host, export `READER_DEVICE`,
`SENSOR_DEVICE` (the `/dev/serial/by-id/...-if00` and `-if02` links) and
`DIALOUT_GID` before `up`. Stop and delete the volumes with
`docker compose -f deploy/compose.dev.yaml down -v`.

Full walkthrough: [USAGE.md](USAGE.md).

## Production deployment

The normative runbook is [deploy/README.md](deploy/README.md). Follow it in
order; it covers obtaining the signed image by digest, host layout, secrets,
udev/systemd installation, preflight, start, validation, calibration, upgrade
and rollback. In short:

1. Pull the image by the digest listed in the release bundle's
   `RELEASE_MANIFEST.txt` (generated by CI at tag time; not present in a
   source checkout).
2. Install `deploy/` files, fill `gate.env`, provision the six secret files.
3. Reload udev/systemd, replug the NR155, run `wait-for-devices.sh`, then
   `systemctl enable --now nextwaves-gate.service`.
4. Run `validate-running.sh`, calibrate on site, run it again, complete
   [deploy/ACCEPTANCE.md](deploy/ACCEPTANCE.md).

Do not copy the steps from memory; the runbook contains the exact ownership
and permission requirements that the preflight script enforces.

## Using the API

Authentication is a bearer token shared by REST and gRPC. Every mutation also
needs `X-Operator-ID` and a unique `Idempotency-Key`: a replay returns the
cached result, a reused key with a different payload returns
`409 idempotency_conflict`.

```sh
BASE=https://127.0.0.1:8443
CA=deploy/dev/secrets/dev_ca.pem                   # production: /etc/nextwaves-gate/secrets/tls_cert.pem
TOKEN=$(cat deploy/dev/secrets/api_token)          # production: sudo cat /etc/nextwaves-gate/secrets/api_token
AUTH="Authorization: Bearer $TOKEN"

# Start, then commit, an inbound receipt
curl --cacert "$CA" -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"reference":"ASN-100","operation":"INBOUND","expected_epcs":["E2000017221101441890A1B2"],
       "antennas":[true,true,false,false],"session":0,"target":"A"}' \
  "$BASE/api/v1/commands/start-inventory"
curl --cacert "$CA" -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" \
  -X POST "$BASE/api/v1/commands/commit-transaction"

# Query
TX=paste-a-transaction-id
curl --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions?status=COMMITTED&limit=20"
curl --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions/$TX"        # record plus reconciliation
curl --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions/$TX/tags"   # net tags
curl --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions/$TX/audit"

# Live status over gRPC
grpcurl -cacert "$CA" -H "authorization: Bearer $TOKEN" -proto contracts/gate_stream.proto \
  127.0.0.1:50051 nextwaves.gate.v1.GateStreamService/GetStatus

# Durable events over MQTT (dev broker)
mosquitto_sub -h 127.0.0.1 -p 8883 --cafile "$CA" -u gate-dev \
  -P "$(cat deploy/dev/secrets/mqtt_password)" -t 'rfid/portal/v1/GATE-DEV/#' -v
```

Contracts: [REST OpenAPI](contracts/openapi.json),
[gRPC proto](contracts/gate_stream.proto),
[MQTT delivery contract](contracts/MQTT.md),
[MQTT envelope schema](contracts/mqtt/event-envelope.schema.json).
Health semantics, error codes, the calibration flow and troubleshooting are in
[USAGE.md](USAGE.md).

## Example operator console

[`examples/gate-console`](examples/gate-console) is a Vite + React reference UI
for one gate: a rendered view of the portal with reader, sensor and antenna
state, Start / Stop / Commit / Cancel, a transaction browser with
reconciliation and audit timeline, the calibration flow, and a Config page
for the connection settings and the gate's reported configuration.

```sh
cd examples/gate-console && npm install && cp .env.example .env && npm run dev   # http://localhost:5173
```

Open Config and paste the access token; settings apply as you type. The dev
server proxies to the gate (`VITE_GATE_URL`) because the service sends no CORS
headers. Details: [examples/gate-console/README.md](examples/gate-console/README.md).

## Repository layout

```text
runtime/            compiled cp311 Linux runtime (.so) plus plaintext entrypoints and transport
                    adapters: gate_service/main.py, restore_database.py, api/rest.py,
                    api/grpc_server.py, api/schemas.py, proto/*.py
deploy/             compose.yaml (production), compose.dev.yaml (development), systemd, udev,
                    preflight and validation scripts, acceptance checklist, runbook
deploy/dev/         Mosquitto config and bootstrap-dev-secrets.sh for the dev stack
contracts/          REST OpenAPI, gRPC proto, MQTT contract and JSON Schemas
examples/           gate-console reference UI (Vite + React)
tests/              pytest suite for the transport adapters (compiled modules stubbed); runs in CI
scripts/            verify_product.py (checksum manifest), scan_headless_release.py (leakage scan),
                    verify_compose_contract.py, smoke_headless_image.py, stage_headless_runtime.py
release/            protected-runtime module manifest
Dockerfile          runtime-only image; no protected source is compiled here
USAGE.md            usage guide      REVIEW.md   production-readiness review and known issues
```

## Documentation map

| Need | Read |
|---|---|
| Run it locally, call the API, troubleshoot, glossary | [USAGE.md](USAGE.md) |
| Deploy to a customer gate, secrets, backup, restore, upgrade | [deploy/README.md](deploy/README.md) |
| Hardware commissioning sign-off | [deploy/ACCEPTANCE.md](deploy/ACCEPTANCE.md) |
| What was reviewed, what was fixed, known runtime issues, what is editable | [REVIEW.md](REVIEW.md) |
| Build an integration | [contracts/](contracts/) |
| Build an operator UI | [examples/gate-console/README.md](examples/gate-console/README.md) |

## Security and operating rules

- One container is one physical gate. Never run replicas or the desktop
  runtime against the same serial interfaces.
- REST and gRPC bind to loopback by default. For remote clients use the host's
  dedicated VLAN or VPN address; wildcard binds are rejected by CI and by
  `validate-running.sh`. Do not expose the service to the Internet.
- TLS 1.2 or newer everywhere. Secrets are files owned `root:10001` mode
  `0440`, never environment variables. `GATE_DEVELOPMENT` and
  `GATE_ALLOW_INSECURE` must stay `false` in production (CI-enforced).
- The container runs with a read-only root filesystem, no capabilities, an
  init process, and memory/CPU/PID limits (`GATE_MEM_LIMIT`, `GATE_CPUS`).
- The calibration root key is customer-owned; losing it forces re-calibration.
- Never commit `gate.env`, secrets, databases or customer data to this
  repository.

## Verification and release integrity

Every file is locked by `PRODUCT_SHA256SUMS` and CI fails on drift. The same
checks CI runs, reproduced locally (CPython 3.11 required; `-B` prevents cache
directories, which the manifest check rejects):

```sh
python3.11 -m venv ~/.venvs/gate && . ~/.venvs/gate/bin/activate
pip install -r tests/requirements-test.txt
export PYTHONDONTWRITEBYTECODE=1

python -B scripts/verify_product.py . --write        # regenerate the manifest after intended edits
python -B scripts/verify_product.py .
python -B scripts/scan_headless_release.py --root runtime --manifest release/protected_modules_headless.json
resolved=$(mktemp)
docker compose --env-file deploy/gate.env.example -f deploy/compose.yaml config --format json > "$resolved"
python -B scripts/verify_compose_contract.py "$resolved" --rest-host-port 8443 --grpc-host-port 50051
sh -n deploy/wait-for-devices.sh deploy/validate-running.sh deploy/dev/bootstrap-dev-secrets.sh
python -B -m pytest -p no:cacheprovider -q tests
(cd examples/gate-console && npm ci && npm run build)
git ls-files | grep -vE '\.so$|PRODUCT_SHA256SUMS' | xargs grep -nP '[\x{2013}\x{2014}]|[ \t]+$|\r$' && echo LINT FAILED
```

`--rest-host-port` and `--grpc-host-port` must equal `REST_HOST_PORT` and
`GRPC_HOST_PORT` in the env file you pass.

Release images are built by `.github/workflows/product-image.yml`, signed with
Cosign, published with SBOM and provenance, and must be deployed by **digest**,
never by tag. The tag-time job also produces the customer bundle with
`RELEASE_MANIFEST.txt` and `VERIFY_RELEASE.md`.
