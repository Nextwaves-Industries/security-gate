# Customer acceptance and production qualification

Run this checklist on the target Linux x86_64 gate host. Save command output,
timestamps, image digest and the MQTT subscriber log as the commissioning
evidence for that physical gate.

## 1. Static host preflight

Complete `gate.env`, provision all six secret files, install the systemd/udev
files as documented in `README.md`, reload both managers, then connect or
reconnect the NR155 before running:

```sh
cd /opt/nextwaves-gate/deploy
sudo sh -c 'set -a; . ./gate.env; set +a; sh ./wait-for-devices.sh'
sudo docker compose --env-file gate.env -f compose.yaml config --quiet
```

Pass criteria:

- reader resolves to NR155 `303a:4002` interface `if00`;
- sensor resolves to a different node on interface `if02`;
- both resolved tty nodes use the configured `dialout` GID and grant that group
  read/write access;
- every secret is owned by `root:10001` with mode `0440`;
- Compose resolves an immutable `GATE_IMAGE` digest, one service only, loopback
  or dedicated VLAN/VPN port bindings, and bounded `json-file` log rotation.

The host must provide Docker Engine, Docker Compose v2 and Python 3.8+. Keep
`gate.env` in the template's unquoted `KEY=VALUE` format because the Linux
preflight scripts source this trusted operator file using POSIX `sh`. The
`dialout` ownership and mode are assigned when each tty interface is added.
Keep the default loopback bindings for host-local clients. If customer clients
connect remotely, set `REST_BIND_IP` and `GRPC_BIND_IP` to the dedicated
VLAN/VPN address (never a wildcard), restrict both ports to approved source
CIDRs in the host firewall, and record one allowed and one denied connection
test as acceptance evidence.

## 2. Start and validate hardening

```sh
sudo systemctl enable --now nextwaves-gate.service
sudo REQUIRE_READY=0 sh ./validate-running.sh
```

The first run may report readiness HTTP 503 until Linux calibration is
complete. Liveness must remain 200. The validation script also confirms
CPython 3.11, the actual running image digest and gate ID, uid/gid `10001`,
read-only root filesystem, dropped capabilities, `no-new-privileges`, readable
mounted secrets, plus REST/gRPC TLS and bearer-token rejection.

If the server certificate does not contain `127.0.0.1`, use its real DNS name
and issuing CA:

```sh
sudo GATE_API_URL=https://gate-01.customer.example:8443 \
  GATE_API_CA_FILE=/etc/customer-ca/gate-api-ca.pem \
  REQUIRE_READY=0 sh ./validate-running.sh
```

## 3. Commissioning calibration

Use the authenticated `/api/v1/calibration` endpoints documented in
`contracts/openapi.json`:

1. create one calibration run with the commissioning operator ID;
2. submit the empty-gate background capture;
3. submit the required labelled `IN` and `OUT` passes;
4. evaluate the run and retain its calibration ID and metrics;
5. call `GET /api/v1/calibration/runs/{calibration_id}` and confirm the
   persisted run has the same ID, `status: PASSED`, the expected metrics and
   no failed acceptance checks;
6. confirm `GET /readyz` becomes HTTP 200;
7. run `sudo sh ./validate-running.sh` again with the default
   `REQUIRE_READY=1`.

Never copy the Windows DPAPI calibration database into a Linux gate. Preserve
the 32-byte calibration root key in the customer's secret manager.

## 4. Functional API and event checks

- Start, stop, commit and cancel transactions through REST using unique
  `Idempotency-Key` values.
- Repeat the same command/key/payload and confirm the stored result is returned
  without a second hardware execution.
- Reuse a key with a changed payload and confirm HTTP 409.
- Start concurrently from two clients and confirm only one request is accepted.
- Query transaction tags, passages and audit records after commit.
- Observe the matching MQTT QoS 1 event and deduplicate it by `event_id`.
- Verify topics, retention and delivery behavior against the
  [MQTT delivery contract](../contracts/MQTT.md).
- Connect a gRPC `WatchEvents` client, filter one event type, disconnect and
  resynchronize from REST before reconnecting.

Do not run the desktop application against the same serial ports during these
tests.

## 5. Recovery qualification

Perform each test in a controlled maintenance window and record the outcome:

| Test | Required result |
|---|---|
| Unplug NR155 | `/healthz` stays 200; `/readyz` becomes 503; state is degraded |
| Reconnect NR155 | udev/systemd recreates the container; the same by-id nodes return; gate reaches READY |
| Broker unavailable for 30 minutes | Local commit succeeds; outbox remains pending; every event is sent after reconnect/PUBACK |
| Restart during an active transaction | SQLite recovery produces no duplicate transaction or `event_id` |
| SIGTERM while idle/running/review | New commands are rejected and shutdown completes inside 30 seconds |
| Reboot host | Gate identity, DB, calibration, config and volume data remain unchanged |
| Wrong API token/certificate | REST and gRPC reject the client |
| Second serial owner | A second process cannot open either mapped serial interface |

Before a rollback, stop the service and use the restore tool documented in
`README.md` with the pre-migration backup matching the previous image digest.

## 6. Release acceptance

- verify the customer archive with `sha256sum -c`;
- verify the image with Cosign using the release workflow identity;
- record the source commit, release tag and immutable image digest;
- confirm the SBOM/provenance attestations exist in GHCR;
- accept tag `v1.0.0-rc1` (pattern `vX.Y.Z-rcN`) on one canary gate before
  creating the corresponding final `vX.Y.Z` tag;
- approve production only after every row above has evidence and no
  tag/direction regression versus the desktop baseline.
