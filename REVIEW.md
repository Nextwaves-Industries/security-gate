# Production-readiness review - Gate Service 1.0.0-rc1

Scope: the complete customer release repository (`runtime/`, `deploy/`,
`scripts/`, `contracts/`, CI). Reviewed on 2026-08-28.

**Important context.** This repository is a *release bundle*, not the source
tree. `runtime/` holds 42 Cython-compiled `.so` modules
(`gate_service.application`, `settings`, `reader_engine`, `event_bus`,
`persistence`, all of `rfid_portal.*`, `utils.*`, `models.*`) and 13 plaintext
Python files. Findings marked **Upstream** can only be addressed in the
protected source repository and are recorded here for that backlog.

## What you can change in this bundle

| Editable here | Compiled, needs the upstream source repository |
|---|---|
| `runtime/gate_service/main.py`, `restore_database.py` | `gate_service.application`, `settings`, `reader_engine`, `reader_protocol`, `device_supervisor`, `headless_control`, `event_bus`, `persistence`, `calibration_capture`, `observability`, `contracts`, `key_provider` |
| `runtime/gate_service/api/rest.py`, `grpc_server.py`, `schemas.py` | all of `rfid_portal.*` (config, repository, MQTT, calibration, domain) |
| `runtime/gate_service/proto/*.py` (generated from `contracts/gate_stream.proto`) | all of `utils.*` and `models.*` |
| `deploy/`, `contracts/`, `scripts/`, `tests/`, `examples/`, `Dockerfile`, docs, CI | |

After any edit run `python3.11 -B scripts/verify_product.py . --write` to
regenerate `PRODUCT_SHA256SUMS`, otherwise CI fails on checksum drift.

Overall assessment: the deployment posture is strong (digest-pinned image,
read-only rootfs, `cap_drop: ALL`, file-backed secrets, loopback binds,
Cosign-signed releases with SBOM/provenance, a CI-enforced Compose contract, an
online SQLite backup path). The material defects were concentrated in the
readable transport layer and in a handful of Compose/host-script gaps; all of
those are fixed in this change. There was no automated behavioural test
coverage; a test suite for the readable code is now included.

Legend: **Fixed** = changed in this release · **Documented** = behaviour kept,
now documented · **Upstream** = requires protected source.

## 1. Findings

| # | Sev | Location | Finding | Status |
|---|---|---|---|---|
| 1 | **High** | `api/rest.py` transactions endpoints (`list_transactions`, `get_transaction_record`, `transaction_reconciliation`, `net_transaction_tags`, `list_passages`, `list_transaction_audit`) and `readyz` → `engine.status()` | Blocking SQLite / engine calls executed directly inside `async def` handlers. A slow query (WAL contention, large audit page) stalls the event loop, which also stalls `/healthz`, `/readyz`, every other REST request and the gRPC `WatchEvents` stream on the same loop. | **Fixed** - all offloaded through `_db()` / `run_in_threadpool`; regression tests assert the calls run on a non-loop thread. |
| 2 | **High** | `api/rest.py` `mutation_lock = getattr(runtime, "remote_command_lock", threading.Lock())` | Silent fallback to a *private* lock. If the attribute were ever missing (version skew, refactor), calibration mutations would no longer serialise against the reader engine and would race hardware. | **Fixed** - explicit attribute access; a missing lock is now a startup error. Test included. |
| 3 | **Med** | `api/rest.py` exception handlers, `unknown_failure`, early 413/400 returns | `X-Request-ID`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` were only added on the normal path of the middleware. Early returns (413/400) and every 500 produced by the global handler (runs in `ServerErrorMiddleware`, outside the header block) lacked them. Handlers also dereferenced `request.state.request_id`, which could itself raise inside the handler and degrade the response to a plain-text 500 without the error envelope. | **Fixed** - `_error()` is self-contained; `_request_id()` never raises. Tests cover 400/401/404/413/422/500. |
| 4 | **Med** | `api/rest.py` `openapi_url="/openapi.json"` | Full API schema served unauthenticated in production even though `/docs` was gated on `GATE_DEVELOPMENT`. | **Fixed** - public only in development; in production `/openapi.json` requires the bearer token. Not a contract path; docs reference the bundled `contracts/openapi.json`. |
| 5 | **Med** | `api/rest.py` body cap | Chunked requests were only measured for `POST/PUT/PATCH`. | **Fixed** - measured for every non-`GET/HEAD/OPTIONS` method. Note: the chunked path bounds the *accepted* size, not peak memory of an in-flight chunked upload; a true streaming bound would need `411`-for-chunked, which is a contract change → **Upstream** decision. |
| 6 | **Med** | `api/grpc_server.py` `GetStatus` | A non-`TimeoutError`/`RuntimeError` exception surfaced as `UNKNOWN: Unexpected <type>: <message>` - internal exception text leaked to the client; `status` was technically unbound after `abort()`. | **Fixed** - generic exceptions logged server-side and returned as `INTERNAL "Unexpected server error"`; success path moved inside `try`. |
| 7 | **Low** | `api/grpc_server.py` `_timestamp` in `WatchEvents` | A malformed `occurred_at` raised `ValueError` and killed the whole stream. | **Fixed** - falls back to current time with a warning. |
| 8 | **Low** | `api/grpc_server.py` literals `max_events=1000`, `keepalive_time_ms=30_000` | Hardcoded transport tunables; no keepalive timeout; keepalive not permitted on idle streams (long-idle `WatchEvents`). | **Fixed** - named constants, `keepalive_timeout_ms`, `keepalive_permit_without_calls`. `settings.so` exposes no such fields, so they remain constants (**Upstream** to make configurable). |
| 9 | **Med** | `deploy/compose.yaml` `${READER_DEVICE}`, `${SENSOR_DEVICE}`, `${DIALOUT_GID}` | No `:?` guard: an unset value produced an opaque Docker error instead of a clear message. | **Fixed**. |
| 10 | **Med** | `deploy/compose.yaml` devices `:rwm` | `m` (mknod) granted needlessly on CDC serial devices, contradicting `cap_drop: ALL`. `verify_compose_contract.py` enforced `rwm`. | **Fixed** - `rw`; checker updated. |
| 11 | **Med** | `deploy/compose.yaml` | No `init`, `pids_limit`, memory/CPU limits or `nofile` ulimit. Python was PID 1 without a reaper; lightgbm/pandas/scikit-learn could OOM the host. | **Fixed** - `init: true`, `pids_limit: 256`, `mem_limit ${GATE_MEM_LIMIT:-1g}`, `cpus ${GATE_CPUS:-2.0}`, `nofile 4096`; checker asserts `init` + `pids_limit` + memory limit. |
| 12 | **Low** | `Dockerfile` / `compose.yaml` healthcheck | Used private stdlib API `ssl._create_unverified_context()`. | **Fixed** - public `SSLContext` API, same semantics (loopback, verification off). |
| 13 | **Med** | `deploy/validate-running.sh` | `REST_BIND_IP=0.0.0.0` was silently remapped to `127.0.0.1` for validation, so a wildcard-bound gate - forbidden by README/ACCEPTANCE - passed host validation. | **Fixed** - wildcard `REST_BIND_IP`/`GRPC_BIND_IP` now fail hard. |
| 14 | **Low** | `deploy/nextwaves-gate.service` | `StartLimitIntervalSec/Burst` on a `Type=oneshot` unit without `Restart=` govern nothing; restart behaviour is Docker's. Misleading for operators. | **Fixed** - removed with an explanatory comment. |
| 15 | **Low** | `deploy/gate.env.example` | `GATE_IMAGE` placeholder used `ghcr.io/hyzie/…` while CI publishes `ghcr.io/${OWNER}/…` and `deploy/README.md` says `OWNER`. `GATE_CONFIG_PATH`, `GATE_SECRETS_DIR`, `RFID_PORTAL_MQTT_PASSWORD` exist in `settings.so` but were undocumented. | **Fixed** - placeholder aligned; advanced keys documented (commented out). |
| 16 | **Med** | `rfid_portal/settings.so` `RFID_PORTAL_MQTT_PASSWORD` | A live env-var path for the broker password bypasses the file-secret design (visible in `docker inspect`, `/proc/*/environ`). Not in the CI leak set. | **Documented** (USAGE.md warns against it). **Upstream**: remove or gate behind `GATE_DEVELOPMENT`. |
| 17 | **Low** | `api/rest.py` `/readyz` | Unauthenticated readiness exposes the internal gate state string. Used by ops tooling. | **Documented** - intentional; loopback/VLAN only. |
| 18 | **Low** | `api/rest.py` `/api/v1/status` vs gRPC `GetStatus` | REST maps `TimeoutError`/`RuntimeError` from `control.status` to a generic 500 while gRPC maps to `DEADLINE_EXCEEDED`/`UNAVAILABLE`. | **Upstream** - behaviour change; 503/504 are in the contract but the mapping should be decided with the domain owners. |
| 19 | **Low** | `api/rest.py` lock-timeout path | A mutation-lock timeout stores a permanent 504 against the idempotency key; the client must use a new key. | **Documented** (USAGE.md troubleshooting). Design decision. |
| 20 | **Low** | `requirements-headless.txt` | Fully pinned but no `--hash=`; apt layer (`ca-certificates`, `libgomp1`) unpinned. Verified 2026-08-28 that every pin resolves from PyPI and that the image builds for `linux/amd64`. | **Upstream** - add `pip-compile --generate-hashes`. |
| 21 | **Med** | repo-wide | No behavioural tests existed; only packaging/checksum assertions. | **Fixed** - `tests/` (30 cases) run in CI on every push; covers headers/envelope, body cap, lock fail-fast, OpenAPI gating, threadpool offloading, audit payload decoding, minimal calibration response, route/response-code parity with `contracts/openapi.json`, gRPC status mapping and stream overflow. |
| 22 | **Low** | `deploy/validate-running.sh`, `README.md` | `gate.env` is sourced as shell (`. ./gate.env`) - arbitrary code execution from an operator-owned config file. Documented as intentional. | **Documented** - treat `gate.env` as a trust boundary (root-owned, `0640`). |
| 24 | **High** | `gate_service/application.so` shutdown path (reproduced with `deploy/compose.dev.yaml`, no NR155 attached, `init: true` **and** `init: false`) | On SIGTERM uvicorn logs `Shutting down … Finished server process` within ~0.1 s, but the process never exits; Docker SIGKILLs it at the 30 s `stop_grace_period` (exit 137). The MQTT session is closed only by the kill, so the broker publishes the Last Will instead of a clean offline state. `ACCEPTANCE.md` §recovery claims shutdown completes inside 30 s - that was evidently validated only with the reader connected. Most likely a non-daemon reconnect/supervisor or MQTT worker thread that does not observe the stop signal while the reader is disconnected. | **Upstream** - must be fixed before GA; until then a gate whose reader is unplugged will always take 30 s to stop and will not publish a clean offline state. Repro: `sh deploy/dev/bootstrap-dev-secrets.sh && docker compose -f deploy/compose.dev.yaml up -d && time docker compose -f deploy/compose.dev.yaml stop gate-service`. |
| 25 | **Med** | MQTT transport (`rfid_portal/mqtt_sdk.so`) while state is `DEGRADED` | The client connects (MQTT 5, correct Last Will on `…/state`, retained, QoS 1) but in >70 s connected it never publishes the retained `state` snapshot or the 30 s heartbeat, and emits no log line at any level (`LOG_LEVEL=DEBUG`). `contracts/MQTT.md` says an `online` state is published after connect. Consumers therefore cannot distinguish "gate up, reader unplugged" from "gate never started". | **Upstream** - publish the state snapshot on connect regardless of readiness (the payload already carries `ready:false`), and log connect/disconnect at INFO. |
| 23 | **Info** | observability | No `/metrics`; JSON logs on stdout only. Adequate for a single-gate appliance; central monitoring should scrape `healthz`/`readyz` and container health. | **Upstream** backlog. |

No hardcoded secrets, customer IPs or credentials were found anywhere in the
tree (including string scans of every `.so`).

End-to-end verification performed on 2026-08-28 with the built image and
`deploy/compose.dev.yaml` (no hardware): `/healthz` 200, `/readyz` 503
`degraded`, bearer auth 401/200, 413 with headers, transaction/calibration
reads, `/openapi.json` gating, gRPC `GetStatus` (valid token → status, invalid →
`UNAUTHENTICATED`), MQTT 5/TLS connect with Last Will. Findings 24-25 come from
that run.

## 2. What changed in this release

- `runtime/gate_service/api/rest.py`, `api/grpc_server.py` - findings 1-8. No
  route, method, status code, response body or header contract changed; the
  new contract-parity test enforces that against `contracts/openapi.json`.
- `deploy/compose.yaml`, `Dockerfile`, `scripts/verify_compose_contract.py`,
  `deploy/gate.env.example`, `deploy/validate-running.sh`,
  `deploy/nextwaves-gate.service` - findings 9-15.
- New `deploy/compose.dev.yaml`, `deploy/dev/` (Mosquitto config, secret
  bootstrap) - local qualification stack; not part of the customer contract.
- New `tests/`, CI step in `.github/workflows/product-image.yml` - finding 21.
- New `USAGE.md`; `README.md` rewritten in English (use cases, quick start, doc map); `examples/gate-console` reference UI.
- `PRODUCT_SHA256SUMS` regenerated.

## 3. Operator-visible behaviour changes

| Change | Impact |
|---|---|
| `/openapi.json` now requires the bearer token in production | Clients that fetched the live schema unauthenticated must send `Authorization` or use the bundled `contracts/openapi.json`. |
| Devices mapped `rw` instead of `rwm` | None for NR155 CDC serial. |
| New `init`, `pids_limit`, memory/CPU limits | Default `1g` / `2.0` CPUs. Raise `GATE_MEM_LIMIT` in `gate.env` on constrained model workloads. |
| `validate-running.sh` fails on wildcard bind | Previously passed silently; wildcard binds were already forbidden. |
| Missing `READER_DEVICE`/`SENSOR_DEVICE`/`DIALOUT_GID` now fail `compose config` with a clear message | Previously an opaque Docker error. |
