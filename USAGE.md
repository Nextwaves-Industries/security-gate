# Nextwaves RFID Portal Gate Service: Usage Guide

How to build, run, configure and operate the gate service with Docker Compose,
and how to talk to it. For customer gates the normative deployment runbook is
[deploy/README.md](deploy/README.md); this guide links to it rather than
repeating it. Commands are POSIX `sh` unless stated otherwise.

| Environment | Compose file | Purpose |
|---|---|---|
| Development and qualification | `deploy/compose.dev.yaml` | Laptop or CI host. Bundled Mosquitto broker, self-signed TLS, optional hardware. |
| Production, customer gate | `deploy/compose.yaml` | Digest-pinned signed image, host-provisioned secrets, NR155 USB passthrough, systemd and udev integration. Contract enforced in CI. |

Both run the same container with the same six file-backed secrets, a read-only
root filesystem, dropped capabilities, and REST on `8443` / gRPC on `50051`
inside the container.

## Contents

1. [Glossary](#glossary)
2. [Quick start, development](#quick-start-development)
3. [Production deployment](#production-deployment)
4. [Configuration reference](#configuration-reference)
5. [Health, readiness and logs](#health-readiness-and-logs)
6. [REST API walkthrough](#rest-api-walkthrough)
7. [gRPC stream](#grpc-stream)
8. [MQTT](#mqtt)
9. [Data, backup, restore](#data-backup-restore)
10. [Tests and release checks](#tests-and-release-checks)
11. [Troubleshooting](#troubleshooting)

## Glossary

| Term | Meaning |
|---|---|
| Gate, portal | One physical NR155 installation: two pillars with antennas and a sensor beam across a lane. One container manages exactly one gate. |
| Transaction | One inventory session from `start-inventory` to `commit` or `cancel`, identified by `transaction_id` and an operator-supplied `reference` such as an ASN. |
| Passage | One crossing of the sensor beam by a pallet, cart or person during a transaction. |
| Stray read | A tag the reader heard that did not move through the portal, for example stock on a nearby shelf. The detection model filters these out. |
| Net tags | The final set of EPCs judged to have passed through the portal during a transaction. |
| Reconciliation | Comparison of `expected_epcs` with the net tags: seen, missing, unexpected. |
| Outbox | Table in the gate's SQLite database where MQTT events are written in the same database transaction as the business change, then delivered at least once. |
| Calibration run | A commissioning procedure: background capture with an empty gate, labelled `IN` and `OUT` passes with known tags, then an evaluation that produces the detection profile. |
| Calibration root key | 64 hexadecimal characters (32 bytes) that protect the calibration profile. Customer-owned. Losing it forces re-calibration. |
| Idempotency key | Client-chosen unique string sent on every command. A replay returns the cached result; the same key with a different payload is rejected with `409`. |

## Quick start, development

Prerequisites: an x86_64 host, or Docker with x86 emulation (the runtime is
built for `linux/amd64` only); Docker Engine 24+ or Docker Desktop/OrbStack;
Docker Compose v2; `openssl`. The bootstrap script also pulls the
`eclipse-mosquitto` image, so Docker must be running and have network access.

```sh
git clone <this repository> && cd RFID_Portal_release

# 1. Throw-away CA, TLS certificates, API token, calibration key, MQTT password
sh deploy/dev/bootstrap-dev-secrets.sh

# 2. Build the image, start Mosquitto and the gate service
docker compose -f deploy/compose.dev.yaml up --build -d

# 3. Watch it come up (JSON logs on stdout)
docker compose -f deploy/compose.dev.yaml logs -f gate-service
```

Verify:

```sh
CA=deploy/dev/secrets/dev_ca.pem
TOKEN=$(cat deploy/dev/secrets/api_token)

curl --cacert "$CA" https://127.0.0.1:8443/healthz    # 200 {"status":"ok"}
curl --cacert "$CA" https://127.0.0.1:8443/readyz     # 503 {"error":{"code":"degraded",...}} without hardware
curl --cacert "$CA" -H "Authorization: Bearer $TOKEN" https://127.0.0.1:8443/api/v1/status
```

Swagger UI: `https://127.0.0.1:8443/docs` (development mode only).

Without an NR155 attached `readyz` stays `503` and `/api/v1/status` reports
`state: DEGRADED`, `reader.connected: false`. That is expected: liveness,
authentication, MQTT connectivity, persistence and all read APIs work without
hardware; only inventory commands are refused.

To attach real hardware on a Linux host, export the by-id links before `up`:

```sh
export READER_DEVICE=/dev/serial/by-id/usb-Nextwaves_NR155_XXXX-if00
export SENSOR_DEVICE=/dev/serial/by-id/usb-Nextwaves_NR155_XXXX-if02
export DIALOUT_GID=$(getent group dialout | cut -d: -f3)
docker compose -f deploy/compose.dev.yaml up -d
```

Stop and delete the dev volumes: `docker compose -f deploy/compose.dev.yaml down -v`.
Regenerate secrets: `sh deploy/dev/bootstrap-dev-secrets.sh --force`.

## Production deployment

Follow [deploy/README.md](deploy/README.md) from section 1 to section 5 in
order. It is the only authoritative sequence; the preflight script
`wait-for-devices.sh` enforces the exact ownership and permission rules it
describes, so shortcuts fail at preflight. Then complete
[deploy/ACCEPTANCE.md](deploy/ACCEPTANCE.md).

Two things the runbook assumes you know:

- `GATE_ID` is fixed for the life of the gate. It is written into the
  persisted configuration, MQTT topics and every event envelope. Changing it on
  a commissioned gate orphans the existing database, topics and calibration.
- One Compose project owns one physical gate. Never scale to several replicas
  and never point two containers at the same serial interfaces.

## Configuration reference

### Runtime environment, inside the container

Set by `deploy/compose.yaml` from `gate.env`. Only this allowlist reaches the
process.

| Variable | Default | Notes |
|---|---|---|
| `GATE_ID` | required | 1 to 64 characters from `[A-Za-z0-9._-]`, first one alphanumeric. Immutable after commissioning, see above. |
| `RFID_READER_MODULE` | `ZK` | Reader protocol family. |
| `READER_DEVICE`, `SENSOR_DEVICE` | `/dev/rfid-reader`, `/dev/rfid-sensor` | Container-side paths, fixed. Host paths are mapped in `devices:`. |
| `RFID_PORTAL_DATA_DIR` | `/var/lib/nextwaves` | SQLite database, config, backups. The only writable mount. |
| `REST_PORT`, `GRPC_PORT` | `8443`, `50051` | Fixed inside the container (contract). |
| `API_TOKEN_FILE` | `/run/secrets/api_token` | Bearer token for REST and gRPC. |
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | `/run/secrets/tls_cert`, `/run/secrets/tls_key` | PEM, used by REST and gRPC. Validity window checked at startup. |
| `CALIBRATION_KEY_FILE` | `/run/secrets/calibration_root_key` | 64 hexadecimal characters. |
| `MQTT_HOST`, `MQTT_PORT` | required, `8883` | MQTT 5 over TLS broker. |
| `MQTT_USERNAME`, `MQTT_PASSWORD_FILE`, `MQTT_CA_FILE` | required, `/run/secrets/mqtt_password`, `/run/secrets/mqtt_ca` | Password and CA are file secrets. |
| `MQTT_CLIENT_ID` | `nextwaves-${GATE_ID}` when blank | Must be unique per gate. |
| `COMMAND_TIMEOUT_S` | `10` | Hardware command and mutation-lock timeout. |
| `MAX_BODY_BYTES` | `1048576` | REST body cap and gRPC maximum message size. The `413` message text always says "1 MiB" regardless of the value. |
| `DATABASE_BACKUP_RETENTION` | `10` | 1 to 100 backups kept per safety-backup class. |
| `GATE_DEVELOPMENT` | `false` | `true` enables `/docs` and unauthenticated `/openapi.json`. Must be `false` in production (CI-enforced). |
| `GATE_ALLOW_INSECURE` | `false` | `true` disables TLS. Must be `false` in production (CI-enforced). |
| `LOG_LEVEL` | `INFO` | Dev stack only. `DEBUG` for tracing. |
| `GATE_CONFIG_PATH`, `GATE_SECRETS_DIR` | `<data>/config.json`, `/run/secrets` | Advanced overrides, normally unset. |
| `RFID_PORTAL_MQTT_PASSWORD` | unset | Alternative env-var password path. Do not use on gates; environment variables are visible in `docker inspect`. |

### Host-only variables, Compose interpolation

These never enter the container. "Prod" means honoured by `deploy/compose.yaml`;
`deploy/compose.dev.yaml` hardcodes loopback binds, `10m`/`3` log rotation and
sets no CPU limit.

| Variable | Default | Scope | Notes |
|---|---|---|---|
| `GATE_IMAGE` | required | prod | Must be `...@sha256:<64 hex>`; mutable tags are rejected. Dev builds locally. |
| `GATE_DATA_DIR` | `./data` in Compose | prod | Production preflight requires an absolute path that already exists, owned `10001:10001`, mode `0750`. |
| `READER_DEVICE`, `SENSOR_DEVICE` | required (prod), `/dev/null` (dev) | both | Host `/dev/serial/by-id` links, `if00` and `if02`. |
| `DIALOUT_GID` | required (prod), `20` (dev) | both | `getent group dialout`. |
| `REST_HOST_PORT`, `GRPC_HOST_PORT` | `8443`, `50051` | both | Published host ports. If changed, pass the same values to `verify_compose_contract.py`. |
| `REST_BIND_IP`, `GRPC_BIND_IP` | `127.0.0.1` | prod | Loopback or a dedicated VLAN/VPN address. Wildcards are rejected by CI and `validate-running.sh`. |
| `GATE_MEM_LIMIT`, `GATE_CPUS` | `1g`, `2.0` | prod (`GATE_MEM_LIMIT` also dev) | cgroup ceilings for the container. |
| `LOG_MAX_SIZE`, `LOG_MAX_FILE` | `10m`, `5` | prod | Docker `json-file` rotation. |
| `*_SECRET_FILE` (six) | required | prod | Host paths of the file secrets. |

Script-only: `REQUIRE_READY` (default `1`), `GATE_API_URL`, `GATE_API_CA_FILE`,
`GATE_ENV_FILE`, `GATE_COMPOSE_FILE` for `validate-running.sh`.

## Health, readiness and logs

| Endpoint | Auth | Semantics |
|---|---|---|
| `GET /healthz` | none | `200` while the process is alive, regardless of hardware. Used by the Docker `HEALTHCHECK`. |
| `GET /readyz` | none | `200 {"status":"ready","state":"..."}` when the gate can run inventory; otherwise `503` with `error.code` equal to the lower-cased gate state, for example `degraded` or `calibration_required`. |
| `GET /api/v1/status` | bearer | Full status snapshot: `state`, `ready`, `reader`, `sensor`, `model`, `calibration`, `inventory`, `last_error`. |

Logs are structured JSON on stdout. The production container is named
`nextwaves-<GATE_ID>`; run Compose commands from the directory that holds
`gate.env` (`/opt/nextwaves-gate/deploy`).

```sh
docker compose --env-file gate.env logs --follow gate-service      # production, from /opt/nextwaves-gate/deploy
docker compose -f deploy/compose.dev.yaml logs -f gate-service      # dev
docker inspect --format '{{json .State.Health}}' nextwaves-GATE-01 | jq
```

Every REST response carries `X-Request-ID` (echoed from the client when it
matches `[A-Za-z0-9_.:-]{1,128}`, otherwise generated), `Cache-Control: no-store`
and `X-Content-Type-Options: nosniff`. Error bodies always have the shape
`{"error": {"code", "message", "request_id"}}`.

## REST API walkthrough

All examples use:

```sh
BASE=https://127.0.0.1:8443
CA=deploy/dev/secrets/dev_ca.pem                 # production: /etc/nextwaves-gate/secrets/tls_cert.pem
TOKEN=$(cat deploy/dev/secrets/api_token)        # production: sudo cat /etc/nextwaves-gate/secrets/api_token
AUTH="Authorization: Bearer $TOKEN"
OP="X-Operator-ID: op-01"
```

Every command and calibration mutation also needs `Idempotency-Key`; the
examples generate one per call with `uuidgen`.

The full schema is in [contracts/openapi.json](contracts/openapi.json). In
production the live `/openapi.json` requires the bearer token.

### Status

```sh
curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/status" | jq
```

### Inventory transaction lifecycle

```sh
# Start
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"reference":"ASN-100","operation":"INBOUND","expected_epcs":["E2000017221101441890A1B2"],
       "antennas":[true,true,false,false],"session":0,"target":"A"}' \
  "$BASE/api/v1/commands/start-inventory"

# Stop (empty body required)
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -X POST "$BASE/api/v1/commands/stop-inventory"

# Commit (empty body), or cancel with a reason
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -X POST "$BASE/api/v1/commands/commit-transaction"
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"reason":"operator abort"}' \
  "$BASE/api/v1/commands/cancel-transaction"
```

Command responses: `{"command": "...", "accepted": true, "result": {...}}`.
Common error codes: `401 unauthorized`, `503 reader_not_ready`,
`503 calibration_required`, `504 command_timeout`, `409 idempotency_conflict`,
`422 <validation code>`.

### Querying transactions

```sh
TX=paste-a-transaction-id
curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions?status=COMMITTED&limit=50&offset=0"
curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions/$TX"             # record plus reconciliation
curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions/$TX/tags"        # net tags
curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions/$TX/passages"
curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/transactions/$TX/audit?limit=200"
```

`status` accepts `OPEN`, `ACTIVE`, `REVIEW`, `COMMITTED`, `CANCELLED`,
case-insensitive.

### Calibration, commissioning

```sh
curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/calibration"                     # current state

curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"notes":"site commissioning"}' \
  "$BASE/api/v1/calibration/runs"                                                # 201, returns calibration_id
CAL=paste-the-calibration-id

# 1. Background capture with no tags in the portal
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"duration_seconds":30}' \
  "$BASE/api/v1/calibration/runs/$CAL/background"

# 2. Labelled passes; repeat for IN and OUT with the tags on the pallet
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' \
  -d '{"direction":"IN","expected_epcs":["E2000017221101441890A1B2"],"timeout_seconds":60}' \
  "$BASE/api/v1/calibration/runs/$CAL/passes"

# 3. Evaluate, or abort with a reason
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -X POST "$BASE/api/v1/calibration/runs/$CAL/evaluate"
curl -s --cacert "$CA" -H "$AUTH" -H "$OP" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"reason":"wrong tags"}' \
  "$BASE/api/v1/calibration/runs/$CAL/abort"

curl -s --cacert "$CA" -H "$AUTH" "$BASE/api/v1/calibration/runs?limit=50"      # history
```

Mutation responses are intentionally minimal (`calibration_id`, `status`,
`updated_at`) so raw radio measurements are never copied into the idempotency
cache.

## gRPC stream

Proto: [contracts/gate_stream.proto](contracts/gate_stream.proto). TLS with the
same certificate; bearer token in `authorization` metadata.

```sh
grpcurl -cacert "$CA" -H "authorization: Bearer $TOKEN" \
  -proto contracts/gate_stream.proto 127.0.0.1:50051 \
  nextwaves.gate.v1.GateStreamService/GetStatus

grpcurl -cacert "$CA" -H "authorization: Bearer $TOKEN" \
  -proto contracts/gate_stream.proto -d '{"event_type":["gate.state.changed"]}' \
  127.0.0.1:50051 nextwaves.gate.v1.GateStreamService/WatchEvents
```

`WatchEvents` is a live, non-durable stream: a slow client is disconnected with
`RESOURCE_EXHAUSTED` after 1000 buffered events. Resynchronise through REST and
reconnect. MQTT and SQLite are the durable sources.

## MQTT

Contract: [contracts/MQTT.md](contracts/MQTT.md). Topic root
`rfid/portal/v1/{GATE_ID}`.

| Topic | QoS | Retained | Content |
|---|---|---|---|
| `.../state` | 1 | yes | Connection and gate-state snapshot, refreshed every 30 s. Last Will publishes `connection: offline`. |
| `.../events/{event_type}` | 1 | no | `inventory.*` events are durable (outbox, at least once, deduplicate on `event_id`); `gate.*` events are best effort. |

Known limitation in 1.0.0-rc1: while the gate is `DEGRADED` (reader
disconnected) the client connects and sets its Last Will but publishes no
state snapshot. See [REVIEW.md](REVIEW.md) item 25.

Dev broker subscription:

```sh
mosquitto_sub -h 127.0.0.1 -p 8883 --cafile deploy/dev/secrets/dev_ca.pem \
  -u gate-dev -P "$(cat deploy/dev/secrets/mqtt_password)" \
  -t 'rfid/portal/v1/GATE-DEV/#' -v
```

Inbound MQTT commands are disabled by design; use REST.

## Data, backup, restore

- Data lives only under the `/var/lib/nextwaves` mount (`GATE_DATA_DIR` on the
  host; the named volume `gate-data` in dev). SQLite runs in WAL mode.
- Before every migration the service takes an online backup into
  `state/db-backups/` and keeps `DATABASE_BACKUP_RETENTION` of each class.
- Never `cp` the live `.db`. Use the restore tool; it refuses to run while the
  service holds the lock and takes a `pre-restore` safety backup first.

Production restore, run on the gate host:

```sh
cd /opt/nextwaves-gate/deploy
sudo systemctl stop nextwaves-gate.service
set -a; . ./gate.env; set +a                       # defines GATE_DATA_DIR and GATE_IMAGE
ls "$GATE_DATA_DIR/state/db-backups/"              # pick the backup matching the image you are restoring to
BACKUP=rfid_portal.pre-migration.TIMESTAMP.db
sudo docker run --rm --user 10001:10001 --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,uid=10001,gid=10001 \
  --volume "$GATE_DATA_DIR:/var/lib/nextwaves" --entrypoint python "$GATE_IMAGE" \
  -m gate_service.restore_database \
  --backup "/var/lib/nextwaves/state/db-backups/$BACKUP" \
  --confirm RESTORE:rfid_portal.db
sudo systemctl start nextwaves-gate.service
```

Upgrade: stop writes, back up `GATE_DATA_DIR`, change the `GATE_IMAGE` digest
in `gate.env`, `sudo systemctl restart nextwaves-gate.service`. Rollback: the
previous digest plus its matching pre-migration backup, as in
[deploy/README.md](deploy/README.md#6-upgrade-and-rollback).

## Tests and release checks

The tests cover the plaintext transport adapters (`gate_service.api.rest`,
`gate_service.api.grpc_server`) with the compiled modules stubbed, so they run
on any OS with CPython 3.11. Hardware and domain logic are validated through
`deploy/ACCEPTANCE.md`.

```sh
python3.11 -m venv ~/.venvs/gate && . ~/.venvs/gate/bin/activate
pip install -r tests/requirements-test.txt
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider -q tests
```

The complete list of checks CI runs, reproduced locally, is in
[README.md](README.md#verification-and-release-integrity).

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `docker compose config` fails with `Set READER_DEVICE...` or `Set DIALOUT_GID...` | required host variable unset in `gate.env` | `ls -l /dev/serial/by-id/`, `getent group dialout` |
| `wait-for-devices.sh` fails on `GATE_DATA_DIR` | relative path, or directory missing or not `10001:10001` mode `0750` | `sudo install -d -o 10001 -g 10001 -m 0750 /var/lib/nextwaves-gate` and use the absolute path |
| Container exits with code `2`, log says `Gate service cannot start` | settings or secret problem: missing file, wrong permissions, certificate outside its validity window, key not 64 hex | check `/etc/nextwaves-gate/secrets` is `root:10001 0440`; run `wait-for-devices.sh` |
| `/healthz` 200 but `/readyz` 503 `degraded` | reader not mapped, wrong `if00`/`if02` link, or dialout GID mismatch | `udevadm info`, verify `DIALOUT_GID`, replug once after installing the udev rule |
| `/readyz` 503 `calibration_required` | fresh Linux install, or calibration key replaced | run the calibration flow above; Windows DPAPI calibration is not portable |
| `401 unauthorized` | token mismatch or missing `Bearer` scheme | compare the `api_token` file; header is `Authorization: Bearer <token>` |
| `409 idempotency_conflict` | same `Idempotency-Key` reused with a different body | use a fresh key per attempt |
| `504 command_timeout` or `calibration_command_timeout` | hardware busy, or another mutation held the lock longer than `COMMAND_TIMEOUT_S` | retry with a new key; the timed-out key stays recorded as failed |
| gRPC `RESOURCE_EXHAUSTED` | client consumed events too slowly | resync via REST, reconnect |
| MQTT `state` shows `offline` | broker unreachable, TLS, or credentials | check `MQTT_*`, CA validity, broker logs; events wait in the outbox and are delivered on reconnect |
| Broker shows the gate connected but no retained `.../state` | known issue, REVIEW.md item 25: no snapshot while `DEGRADED` | attach hardware or reach `readyz` 200; upstream fix pending |
| `docker compose stop` takes 30 s and exits `137` while the reader is unplugged | known issue, REVIEW.md item 24: shutdown hangs after uvicorn stops with no NR155 attached | reconnect the reader before stopping; upstream fix pending |
| `validate-running.sh` exits with `REST_BIND_IP must be loopback...` | wildcard bind configured | set `127.0.0.1` or the dedicated VLAN/VPN address |
| Container OOM-killed | model load exceeds `GATE_MEM_LIMIT` | raise `GATE_MEM_LIMIT` (default `1g`) in `gate.env` |
