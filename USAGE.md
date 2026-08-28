# Nextwaves RFID Portal Gate Service - Usage Guide

This guide explains how to build, run, configure, and operate the headless gate
service with Docker Compose. It complements the production runbook in
[`deploy/README.md`](deploy/README.md) and the hardware commissioning checklist
in [`deploy/ACCEPTANCE.md`](deploy/ACCEPTANCE.md).

| Environment | Compose file | Purpose |
|---|---|---|
| **Development / qualification** | `deploy/compose.dev.yaml` | Laptop or CI host. Bundled Mosquitto broker, self-signed TLS, optional hardware. |
| **Production (customer gate)** | `deploy/compose.yaml` | Digest-pinned signed image, host-provisioned secrets, NR155 USB passthrough, systemd + udev integration. Contract enforced in CI. |

Both stacks run the **same container** with the same environment allowlist,
the same six file-backed secrets, a read-only root filesystem, dropped
capabilities, and the same port layout (REST `8443`, gRPC `50051`).

---

## 1. Quick start (development)

Prerequisites: Docker Engine 24+ / Docker Desktop, Docker Compose v2, `openssl`.

```sh
git clone <this repo> && cd RFID_Portal_release

# 1. Generate throw-away CA, certs, API token, calibration key, MQTT password
sh deploy/dev/bootstrap-dev-secrets.sh

# 2. Build the image and start Mosquitto + gate-service
docker compose -f deploy/compose.dev.yaml up --build -d

# 3. Watch it come up (JSON logs on stdout)
docker compose -f deploy/compose.dev.yaml logs -f gate-service
```

Verify:

```sh
CA=deploy/dev/secrets/dev_ca.pem
TOKEN=$(cat deploy/dev/secrets/api_token)

curl --cacert $CA https://127.0.0.1:8443/healthz      # 200 {"status":"ok"}
curl --cacert $CA https://127.0.0.1:8443/readyz       # 503 until hardware+calibration are present
curl --cacert $CA -H "Authorization: Bearer $TOKEN" https://127.0.0.1:8443/api/v1/status
open https://127.0.0.1:8443/docs                       # Swagger UI (dev only)
```

Without an NR155 attached, `readyz` stays `503` with a state such as
`reader_disconnected` / `calibration_required`. That is expected: liveness,
authentication, MQTT connectivity, persistence, and the transaction/calibration
read APIs are all exercisable without hardware.

To attach real hardware on a Linux dev host, export the by-id links before
`up`:

```sh
export READER_DEVICE=/dev/serial/by-id/usb-Nextwaves_NR155_XXXX-if00
export SENSOR_DEVICE=/dev/serial/by-id/usb-Nextwaves_NR155_XXXX-if02
export DIALOUT_GID=$(getent group dialout | cut -d: -f3)
docker compose -f deploy/compose.dev.yaml up -d
```

Stop and wipe the dev volumes: `docker compose -f deploy/compose.dev.yaml down -v`.
Regenerate secrets: `sh deploy/dev/bootstrap-dev-secrets.sh --force`.

---

## 2. Production deployment (summary)

Follow [`deploy/README.md`](deploy/README.md) in full. The condensed sequence:

1. **Obtain the signed image digest** from `RELEASE_MANIFEST.txt`, verify with
   `VERIFY_RELEASE.md`, `docker pull ghcr.io/OWNER/rfid-portal-gate-service@sha256:…`.
2. **Prepare the host** (`/opt/nextwaves-gate/deploy`, `/etc/nextwaves-gate/secrets` as `root:10001 0750`,
   `/var/lib/nextwaves-gate` as `10001:10001 0750`), install the systemd units and udev rule.
3. **Fill `gate.env`** from `deploy/gate.env.example`: `GATE_IMAGE` (digest), `GATE_ID`,
   `READER_DEVICE`/`SENSOR_DEVICE` (`/dev/serial/by-id/...-if00` / `-if02`), `DIALOUT_GID`,
   `MQTT_HOST`/`MQTT_USERNAME`, and the six `*_SECRET_FILE` paths.
4. **Provision secrets** (`root:10001 0440`): `api_token`, `calibration_root_key` (64 hex),
   `mqtt_password`, `tls_cert.pem`, `tls_key.pem`, `mqtt_ca.pem`.
5. **Preflight, start, validate**:

   ```sh
   cd /opt/nextwaves-gate/deploy
   sudo sh -c 'set -a; . ./gate.env; set +a; sh ./wait-for-devices.sh'
   sudo docker compose --env-file gate.env config
   sudo systemctl enable --now nextwaves-gate.service
   sudo REQUIRE_READY=0 sh ./validate-running.sh
   ```

6. **Calibrate** through `/api/v1/calibration/*` (see §5.4), then re-run
   `validate-running.sh` without `REQUIRE_READY=0`.

One Compose project owns exactly one physical gate; never scale to multiple
replicas and never point two containers at the same serial interfaces.

---

## 3. Configuration reference

### 3.1 Runtime environment (inside the container)

Set in `compose.yaml` from `gate.env`. Only this allowlist reaches the process.

| Variable | Default | Notes |
|---|---|---|
| `GATE_ID` | - (required) | 1-64 chars `[A-Za-z0-9._-]`, first char alphanumeric. Used in MQTT topics, envelopes, DB config. |
| `RFID_READER_MODULE` | `ZK` | Reader protocol family. |
| `READER_DEVICE` / `SENSOR_DEVICE` | `/dev/rfid-reader` / `/dev/rfid-sensor` | Container-side paths; fixed. Host paths are mapped via `devices:`. |
| `RFID_PORTAL_DATA_DIR` | `/var/lib/nextwaves` | SQLite DB, config, backups. The only writable mount. |
| `REST_PORT` / `GRPC_PORT` | `8443` / `50051` | Fixed inside the container (contract). |
| `API_TOKEN_FILE` | `/run/secrets/api_token` | Bearer token for REST and gRPC. |
| `TLS_CERT_FILE` / `TLS_KEY_FILE` | `/run/secrets/tls_cert` / `tls_key` | PEM; used by both REST and gRPC. Validity window checked at startup. |
| `CALIBRATION_KEY_FILE` | `/run/secrets/calibration_root_key` | Exactly 64 hex chars. Losing it makes readiness `CALIBRATION_REQUIRED`. |
| `MQTT_HOST` / `MQTT_PORT` | - / `8883` | MQTT 5 over TLS broker. |
| `MQTT_USERNAME` / `MQTT_PASSWORD_FILE` / `MQTT_CA_FILE` | - / `/run/secrets/mqtt_password` / `/run/secrets/mqtt_ca` | Password and CA are file secrets. |
| `MQTT_CLIENT_ID` | `nextwaves-${GATE_ID}` when blank | Must be unique per gate. |
| `COMMAND_TIMEOUT_S` | `10` | Hardware command and mutation-lock timeout. |
| `MAX_BODY_BYTES` | `1048576` | REST body cap and gRPC max message size. |
| `DATABASE_BACKUP_RETENTION` | `10` | 1-100 backups per safety-backup class. |
| `GATE_DEVELOPMENT` | `false` | `true` enables `/docs` and unauthenticated `/openapi.json`. Must be `false` in production (CI-enforced). |
| `GATE_ALLOW_INSECURE` | `false` | `true` disables TLS. Must be `false` in production (CI-enforced). |
| `GATE_CONFIG_PATH` | `<data>/config.json` | Advanced override; normally unset. |
| `GATE_SECRETS_DIR` | `/run/secrets` | Advanced override; normally unset. |
| `LOG_LEVEL` | `INFO` | Runtime log level (`DEBUG` for tracing). Exposed in `compose.dev.yaml` only. |
| `RFID_PORTAL_MQTT_PASSWORD` | unset | Alternative env-var password path. **Do not use** on gates; env vars are visible in `docker inspect`. Use the file secret. |

### 3.2 Host-only variables (Compose interpolation, never enter the container)

| Variable | Default | Notes |
|---|---|---|
| `GATE_IMAGE` | - (required) | Must be `…@sha256:<64 hex>`; mutable tags are rejected. |
| `GATE_DATA_DIR` | `./data` | Host path for the data volume (`10001:10001 0750`). |
| `READER_DEVICE` / `SENSOR_DEVICE` | - (required) | Host `/dev/serial/by-id` links (`if00`, `if02`). |
| `DIALOUT_GID` | - (required) | `getent group dialout`. |
| `REST_HOST_PORT` / `GRPC_HOST_PORT` | `8443` / `50051` | Published host ports. |
| `REST_BIND_IP` / `GRPC_BIND_IP` | `127.0.0.1` | Loopback or a dedicated VLAN/VPN address. Wildcards are rejected by CI and `validate-running.sh`. |
| `GATE_MEM_LIMIT` / `GATE_CPUS` | `1g` / `2.0` | cgroup ceilings for the container. |
| `LOG_MAX_SIZE` / `LOG_MAX_FILE` | `10m` / `5` | Docker `json-file` rotation. |
| `*_SECRET_FILE` (6) | - (required) | Host paths of the file secrets. |

Script-only: `REQUIRE_READY` (default `1`), `GATE_API_URL`, `GATE_API_CA_FILE`,
`GATE_ENV_FILE`, `GATE_COMPOSE_FILE` for `validate-running.sh`.

---

## 4. Health, readiness and logs

| Endpoint | Auth | Semantics |
|---|---|---|
| `GET /healthz` | none | `200` while the process is alive, regardless of hardware. Used by the Docker `HEALTHCHECK`. |
| `GET /readyz` | none | `200 {"status":"ready","state":…}` when the gate can run inventory; `503` with `error.code` = lowercase gate state otherwise (`reader_disconnected`, `calibration_required`, …). |
| `GET /api/v1/status` | bearer | Full status snapshot from the control plane. |

Logs are structured JSON on stdout:

```sh
docker compose --env-file gate.env logs --follow gate-service          # production
docker compose -f deploy/compose.dev.yaml logs -f gate-service          # dev
docker inspect --format '{{json .State.Health}}' nextwaves-GATE-01 | jq
```

Every REST response carries `X-Request-ID` (echoed from the client if it
matches `[A-Za-z0-9_.:-]{1,128}`, otherwise generated), `Cache-Control: no-store`
and `X-Content-Type-Options: nosniff`. Error bodies always have the shape
`{"error": {"code", "message", "request_id"}}`.

---

## 5. REST API walkthrough

All examples use:

```sh
BASE=https://127.0.0.1:8443
CA=deploy/dev/secrets/dev_ca.pem                 # prod: /etc/nextwaves-gate/secrets/tls_cert.pem
TOKEN=$(cat deploy/dev/secrets/api_token)        # prod: sudo cat /etc/nextwaves-gate/secrets/api_token
AUTH="Authorization: Bearer $TOKEN"
```

Every **command and calibration mutation** additionally requires:

- `X-Operator-ID: <operator>` - recorded in the audit trail
- `Idempotency-Key: <unique per attempt>` - replays return the cached result;
  reuse with a different payload returns `409 idempotency_conflict`

The full schema is in [`contracts/openapi.json`](contracts/openapi.json). In
production the live `/openapi.json` requires the bearer token.

### 5.1 Status

```sh
curl -s --cacert $CA -H "$AUTH" $BASE/api/v1/status | jq
```

### 5.2 Inventory transaction lifecycle

```sh
# Start
curl -s --cacert $CA -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"reference":"ASN-100","operation":"INBOUND","expected_epcs":["E2000017221101441890A1B2"],
       "antennas":[true,true,false,false],"session":0,"target":"A"}' \
  $BASE/api/v1/commands/start-inventory

# Stop (empty body required)
curl -s --cacert $CA -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" \
  -X POST $BASE/api/v1/commands/stop-inventory

# Commit (empty body) - or cancel with a reason
curl -s --cacert $CA -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" \
  -X POST $BASE/api/v1/commands/commit-transaction
curl -s --cacert $CA -H "$AUTH" -H 'X-Operator-ID: op-01' -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"reason":"operator abort"}' \
  $BASE/api/v1/commands/cancel-transaction
```

Command responses: `{"command": "...", "accepted": true, "result": {...}}`.
Common error codes: `401 unauthorized`, `503 reader_not_ready`,
`503 calibration_required`, `504 command_timeout`, `409 idempotency_conflict`,
`422 <validation code>`.

### 5.3 Querying transactions

```sh
curl -s --cacert $CA -H "$AUTH" "$BASE/api/v1/transactions?status=COMMITTED&limit=50&offset=0"
curl -s --cacert $CA -H "$AUTH" $BASE/api/v1/transactions/<transaction_id>            # record + reconciliation
curl -s --cacert $CA -H "$AUTH" $BASE/api/v1/transactions/<transaction_id>/tags       # net EPC set
curl -s --cacert $CA -H "$AUTH" $BASE/api/v1/transactions/<transaction_id>/passages
curl -s --cacert $CA -H "$AUTH" "$BASE/api/v1/transactions/<transaction_id>/audit?limit=200"
```

`status` accepts `OPEN`, `ACTIVE`, `COMMITTED`, `CANCELLED` (case-insensitive).

### 5.4 Calibration (commissioning)

```sh
H=(-H "$AUTH" -H 'X-Operator-ID: commissioner' -H 'Content-Type: application/json')

curl -s --cacert $CA "${H[@]}" $BASE/api/v1/calibration                       # current state
curl -s --cacert $CA "${H[@]}" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"notes":"site commissioning"}' $BASE/api/v1/calibration/runs            # 201 -> calibration_id
CAL=<calibration_id>

# 1. Background capture (no tags in the portal)
curl -s --cacert $CA "${H[@]}" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"duration_seconds":30}' $BASE/api/v1/calibration/runs/$CAL/background
# 2. Labelled passes, repeat for IN and OUT with known EPCs
curl -s --cacert $CA "${H[@]}" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"direction":"IN","expected_epcs":["E2000017221101441890A1B2"],"timeout_seconds":60}' \
  $BASE/api/v1/calibration/runs/$CAL/passes
# 3. Evaluate; abort with a reason if needed
curl -s --cacert $CA "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -X POST \
  $BASE/api/v1/calibration/runs/$CAL/evaluate
curl -s --cacert $CA "${H[@]}" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"reason":"wrong tags"}' $BASE/api/v1/calibration/runs/$CAL/abort

curl -s --cacert $CA -H "$AUTH" "$BASE/api/v1/calibration/runs?limit=50"      # history
```

Mutation responses are intentionally minimal
(`calibration_id`, `status`, `updated_at`) so RF evidence never enters
idempotency storage.

---

## 6. gRPC stream

Proto: [`contracts/gate_stream.proto`](contracts/gate_stream.proto). TLS with
the same certificate; bearer token in `authorization` metadata.

```sh
grpcurl -cacert $CA -H "authorization: Bearer $TOKEN" \
  -proto contracts/gate_stream.proto 127.0.0.1:50051 \
  nextwaves.gate.v1.GateStreamService/GetStatus

grpcurl -cacert $CA -H "authorization: Bearer $TOKEN" \
  -proto contracts/gate_stream.proto -d '{"event_type":["gate.state.changed"]}' \
  127.0.0.1:50051 nextwaves.gate.v1.GateStreamService/WatchEvents
```

`WatchEvents` is a **live, non-durable** stream: a slow client is disconnected
with `RESOURCE_EXHAUSTED` after 1000 buffered events. Resynchronise through
REST (`/api/v1/transactions`) and reconnect. MQTT + SQLite are the durable
sources.

---

## 7. MQTT

Contract: [`contracts/MQTT.md`](contracts/MQTT.md). Topic root
`rfid/portal/v1/{GATE_ID}`.

| Topic | QoS | Retained | Content |
|---|---|---|---|
| `…/state` | 1 | yes | Connection + gate-state snapshot; Last Will publishes `connection: offline` |
| `…/events/{event_type}` | 1 | no | `inventory.*` (durable, transactional outbox, at-least-once - dedupe on `event_id`) and `gate.*` (best effort) |

Dev broker subscription:

```sh
mosquitto_sub -h 127.0.0.1 -p 8883 --cafile deploy/dev/secrets/dev_ca.pem \
  -u gate-dev -P "$(cat deploy/dev/secrets/mqtt_password)" \
  -t 'rfid/portal/v1/GATE-DEV/#' -v
```

Inbound MQTT commands are disabled by design; use REST.

---

## 8. Data, backup, restore

- Data lives only under the `/var/lib/nextwaves` mount (`GATE_DATA_DIR` on the
  host, named volume `gate-data` in dev). SQLite runs in WAL mode.
- Before every migration the service takes an online backup into
  `state/db-backups/` (retention `DATABASE_BACKUP_RETENTION`).
- **Never** `cp` the live `.db`; use the restore tool, which refuses to run
  while the service holds the lock and takes a `pre-restore` safety backup:

```sh
docker compose --env-file gate.env down
docker run --rm --user 10001:10001 --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,uid=10001,gid=10001 \
  --volume "$GATE_DATA_DIR:/var/lib/nextwaves" --entrypoint python "$GATE_IMAGE" \
  -m gate_service.restore_database \
  --backup /var/lib/nextwaves/state/db-backups/rfid_portal.pre-migration.<TIMESTAMP>.db \
  --confirm RESTORE:rfid_portal.db
docker compose --env-file gate.env up -d
```

Upgrade: stop writes → back up `GATE_DATA_DIR` → change `GATE_IMAGE` digest →
`systemctl restart nextwaves-gate.service`. Rollback: previous digest + matching
pre-migration backup (see `deploy/README.md` §6).

---

## 9. Running the tests and release checks

```sh
python3.11 -m venv ~/.venvs/gate-tests && . ~/.venvs/gate-tests/bin/activate
pip install -r tests/requirements-test.txt
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider -q tests
```

The tests cover the plaintext transport adapters (`gate_service.api.rest`,
`gate_service.api.grpc_server`) with stubbed compiled modules; hardware and
domain logic are validated through `deploy/ACCEPTANCE.md`.

Release-integrity checks run by CI, reproducible locally:

```sh
python3.11 scripts/verify_product.py .                     # checksum manifest (add --write after intended edits)
python3.11 scripts/scan_headless_release.py --root runtime --manifest release/protected_modules_headless.json
docker compose --env-file deploy/gate.env.example -f deploy/compose.yaml config --format json > /tmp/c.json
python3.11 scripts/verify_compose_contract.py /tmp/c.json --rest-host-port 8443 --grpc-host-port 50051
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `docker compose config` fails with `Set READER_DEVICE…` / `Set DIALOUT_GID…` | required host variable unset in `gate.env` | `ls -l /dev/serial/by-id/`, `getent group dialout` |
| Container exits code `2`, log `Gate service cannot start` | settings/secret problem (missing file, wrong perms, bad cert window, key not 64 hex) | check `/etc/nextwaves-gate/secrets` is `root:10001 0440`, run `wait-for-devices.sh` |
| `/healthz` 200 but `/readyz` 503 `reader_disconnected` | USB not mapped, wrong `if00/if02`, or dialout GID mismatch | `udevadm info`, verify `DIALOUT_GID`, replug once after installing the udev rule |
| `/readyz` 503 `calibration_required` | fresh Linux install, or calibration key replaced | run §5.4; Windows DPAPI calibration is not portable |
| `401 unauthorized` | token mismatch / missing `Bearer` scheme | compare `api_token` file contents; header is `Authorization: Bearer <token>` |
| `409 idempotency_conflict` | same `Idempotency-Key` reused with a different body | use a fresh key per attempt |
| `504 command_timeout` / `calibration_command_timeout` | hardware busy or another mutation holding the lock > `COMMAND_TIMEOUT_S` | retry with a **new** key; inspect logs |
| gRPC `RESOURCE_EXHAUSTED` | client consumed events too slowly | resync via REST, reconnect |
| MQTT `state` shows `offline` | broker unreachable / TLS / credentials | check `MQTT_*`, CA file validity, broker logs; events are queued in the outbox and delivered on reconnect |
| `validate-running.sh` exits `REST_BIND_IP must be loopback…` | wildcard bind configured | set to `127.0.0.1` or the dedicated VLAN/VPN address |
| `docker compose stop` takes 30 s and exits `137` while the reader is unplugged | known runtime issue (REVIEW.md #24): shutdown hangs after uvicorn stops when no NR155 is attached | reconnect the reader before stopping; upstream fix pending |
| Broker shows the gate connected but no retained `…/state` message | known runtime issue (REVIEW.md #25): state snapshot is not published while the gate is `DEGRADED` | attach hardware / reach `readyz` 200; upstream fix pending |
| Container OOM-killed | model load exceeds `GATE_MEM_LIMIT` | raise `GATE_MEM_LIMIT` (default `1g`) in `gate.env` |
