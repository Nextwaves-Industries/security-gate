# Nextwaves Gate Service deployment

The target is a Linux x86_64 host running Docker Engine directly, Docker
Compose v2 and Python 3.8+ (used only by `validate-running.sh`). One Compose
project owns one physical NR155 gate. Do not start the desktop runtime against
the same serial interfaces. Follow sections 1 to 5 in order; the preflight
script enforces every ownership and permission rule stated here.

## 1. Obtain the signed image

The customer deployment archive intentionally contains no application runtime
or internal build inputs. Read the immutable image reference from the release
manifest beside the `deploy/` directory, verify it using `VERIFY_RELEASE.md`,
then pull that exact digest:

```sh
grep '^image=' ../RELEASE_MANIFEST.txt
docker pull ghcr.io/OWNER/rfid-portal-gate-service@sha256:RELEASE_DIGEST
```

Private GHCR packages require `docker login ghcr.io` with a customer token that
has read-only package access. Copy the complete verified digest reference into
`GATE_IMAGE`; production must not deploy `latest` or another mutable tag.
Authorized maintainers performing a local qualification build must use the
complete protected product repository and follow its root `README.md`.

## 2. Prepare the host

```sh
sudo install -d -m 0750 /opt/nextwaves-gate/deploy
sudo install -d -o 0 -g 10001 -m 0750 /etc/nextwaves-gate/secrets
sudo install -d -o 10001 -g 10001 -m 0750 /var/lib/nextwaves-gate
sudo cp deploy/compose.yaml deploy/wait-for-devices.sh \
  deploy/validate-running.sh deploy/ACCEPTANCE.md deploy/README.md \
  deploy/gate.env.example /opt/nextwaves-gate/deploy/
sudo cp deploy/nextwaves-gate.service \
  deploy/nextwaves-gate-hotplug.service /etc/systemd/system/
sudo cp deploy/99-nextwaves-rfid.rules /etc/udev/rules.d/
sudo cp /opt/nextwaves-gate/deploy/gate.env.example \
  /opt/nextwaves-gate/deploy/gate.env
sudo chown root:root /opt/nextwaves-gate/deploy/gate.env
sudo chmod 0640 /opt/nextwaves-gate/deploy/gate.env
```

`gate.env` is sourced by the preflight script as shell, so it is a trust
boundary: keep it root-owned and not world-readable.

Find the stable interfaces and dialout GID:

```sh
ls -l /dev/serial/by-id/
getent group dialout
udevadm info --query=property --name=/dev/ttyACM0 | \
  grep -E 'ID_VENDOR_ID|ID_MODEL_ID|ID_USB_INTERFACE_NUM'
```

Set `READER_DEVICE` to the `if00` link, `SENSOR_DEVICE` to `if02`, and set
`DIALOUT_GID` to the numeric host group ID in `gate.env`. Set `GATE_ID` to a
stable site identifier containing 1-64 ASCII letters, digits, `.`, `_` or `-`;
the first character must be a letter or digit. The service applies this value
to persisted runtime configuration, MQTT topics and event envelopes.
Keep `gate.env` in the simple unquoted `KEY=VALUE` form shown by the template;
deployment preflight deliberately sources this operator-owned file with POSIX
`sh`. Leave `MQTT_CLIENT_ID` blank unless the broker requires an explicit ID;
the default `nextwaves-${GATE_ID}` prevents collisions between cloned gates.

`REST_HOST_PORT` and `GRPC_HOST_PORT` control only the ports published on the
host. REST and gRPC remain fixed at `8443` and `50051` inside the container, so
custom host ports do not change the health check or service listeners.
If you change the host ports, pass the same values to
`scripts/verify_compose_contract.py --rest-host-port/--grpc-host-port`.

`REST_BIND_IP` and `GRPC_BIND_IP` default to `127.0.0.1`. For remote clients:

1. set both to the gate host's dedicated VLAN or VPN address;
2. configure the host firewall to allow only approved customer source networks;
3. use that address, or its TLS DNS name, as `GATE_API_URL` when validating;
4. never use `0.0.0.0` or `::`; CI and `validate-running.sh` reject them.

Other tunables in `gate.env`: `RFID_READER_MODULE` (reader protocol family,
default `ZK`), `COMMAND_TIMEOUT_S` (hardware command and mutation-lock
timeout, default 10), `MAX_BODY_BYTES` (REST body and gRPC message cap,
default 1 MiB), `DATABASE_BACKUP_RETENTION` (1-100 backups of each
safety-backup class, default 10), `GATE_MEM_LIMIT` and `GATE_CPUS` (container
cgroup ceilings, default `1g` and `2.0`; raise `GATE_MEM_LIMIT` if the
container is OOM-killed while loading the model). Docker's `json-file` log is
rotated at 10 MiB and five files by default; `LOG_MAX_SIZE` and `LOG_MAX_FILE`
may lower those bounds to match the customer's retention policy.

## 3. Provision secrets

```sh
openssl rand -hex 32 | sudo tee /etc/nextwaves-gate/secrets/api_token >/dev/null
openssl rand -hex 32 | sudo tee \
  /etc/nextwaves-gate/secrets/calibration_root_key >/dev/null
sudo sh -c 'umask 077; printf "%s" "BROKER_PASSWORD" > \
  /etc/nextwaves-gate/secrets/mqtt_password'
sudo cp gate-fullchain.pem /etc/nextwaves-gate/secrets/tls_cert.pem
sudo cp gate-private-key.pem /etc/nextwaves-gate/secrets/tls_key.pem
sudo cp customer-mqtt-ca.pem /etc/nextwaves-gate/secrets/mqtt_ca.pem
sudo chown root:10001 /etc/nextwaves-gate/secrets/*
sudo chmod 0440 /etc/nextwaves-gate/secrets/*
```

The runtime image runs as `10001:10001`. Compose file-backed secrets preserve
host ownership and permissions, so `root:root 0400` is not readable by the
service. Keep the secrets directory `root:10001 0750` and every secret file
`root:10001 0440`. `wait-for-devices.sh` verifies this metadata without reading
or printing secret contents.

The calibration root key must remain exactly 64 hexadecimal characters. Back
it up in the customer's secret manager. Losing or replacing it intentionally
makes readiness `CALIBRATION_REQUIRED`; the service never creates a new
production key. Windows DPAPI calibration cannot be reused on Linux.

## 4. Validate and start

```sh
cd /opt/nextwaves-gate/deploy
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
# Now unplug and reconnect the NR155 once so the new group/mode rule is
# applied to both tty nodes. Continue only after it re-enumerates.
sudo sh -c 'set -a; . ./gate.env; set +a; sh ./wait-for-devices.sh'
sudo docker compose --env-file gate.env -f compose.yaml config --quiet
sudo systemctl enable --now nextwaves-gate.service
sudo REQUIRE_READY=0 sh ./validate-running.sh
```

`REQUIRE_READY=0` must precede `sh` on the command line so `sudo` passes it
through; the gate is not calibrated yet, so readiness is not required at this
stage.

Check liveness/readiness and logs:

```sh
curl --cacert /etc/nextwaves-gate/secrets/tls_cert.pem \
  https://127.0.0.1:8443/healthz
curl --cacert /etc/nextwaves-gate/secrets/tls_cert.pem \
  https://127.0.0.1:8443/readyz
sudo docker compose --env-file gate.env logs --follow gate-service
```

`healthz` remains HTTP 200 while USB/model/calibration is unavailable;
`readyz` returns HTTP 503 until the complete gate is usable. After first Linux
commissioning, run the site's calibration workflow before production traffic.
If `REST_HOST_PORT` is not `8443`, replace `8443` in client URLs with that host
port. Likewise, gRPC clients use `GRPC_HOST_PORT`.

The process validates the REST certificate validity window and matching private
key before opening the APIs. When MQTT is enabled it also requires a username,
non-empty password secret and a usable, currently valid PEM CA trust store.
`validate-running.sh` checks that the running container uses the configured
image digest and gate ID, and exercises both REST and gRPC TLS/bearer auth.

## 5. Call a command

```sh
TOKEN=$(sudo cat /etc/nextwaves-gate/secrets/api_token)
curl --cacert /etc/nextwaves-gate/secrets/tls_cert.pem \
  -H "Authorization: Bearer $TOKEN" \
  -H 'X-Operator-ID: operator-01' \
  -H 'Idempotency-Key: 8d25f7ea-0001' \
  -H 'Content-Type: application/json' \
  -d '{"reference":"ASN-100","operation":"INBOUND","expected_epcs":[],"antennas":[true,true,false,false],"session":0,"target":"A"}' \
  https://127.0.0.1:8443/api/v1/commands/start-inventory
```

For an interrupted gRPC stream, query REST transactions to resynchronize and
then reconnect. MQTT/SQLite, not the gRPC stream, is the durable event source.

Commissioning uses the authenticated endpoints under
`/api/v1/calibration`. Every calibration mutation requires the same operator
and idempotency headers as commands. Worked curl examples for background,
labelled pass, evaluate and abort are in the release bundle's `USAGE.md`
(section "Calibration, commissioning"); the schema is `../contracts/openapi.json`.
After evaluation passes, run `sudo sh ./validate-running.sh` with no
`REQUIRE_READY` override (the default `1` requires `/readyz` to return 200).

## 6. Upgrade and rollback

Stop writes, back up `/var/lib/nextwaves-gate`, replace `GATE_IMAGE` with the
new digest, then restart the unit. Before opening/migrating an existing DB, the
application uses SQLite's online backup API so committed WAL transactions are
included. It integrity-checks and atomically publishes the result under
`state/db-backups/`.

To roll back, stop systemd first, select the previous immutable image digest,
and restore its matching pre-migration DB backup. The restore refuses to run
while gate-service holds the database lock, validates the backup, and creates
a `pre-restore` safety backup of the current database:

```sh
cd /opt/nextwaves-gate/deploy
sudo systemctl stop nextwaves-gate.service
# Edit GATE_IMAGE in gate.env to the previous digest before restarting.
set -a
. ./gate.env
set +a
sudo docker run --rm \
  --user 10001:10001 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,uid=10001,gid=10001 \
  --volume "$GATE_DATA_DIR:/var/lib/nextwaves" \
  --entrypoint python \
  "$GATE_IMAGE" \
  -m gate_service.restore_database \
  --backup /var/lib/nextwaves/state/db-backups/rfid_portal.pre-migration.TIMESTAMP.db \
  --confirm RESTORE:rfid_portal.db
sudo systemctl start nextwaves-gate.service
```

Never replace the live `.db` file with `cp`; that can omit WAL data and bypass
the service lock. The restore command intentionally does not map either USB
interface, so recovery remains possible while failed hardware is disconnected.

## Appendix: runtime structure

The container entrypoint remains `python -m gate_service.main`. The headless
package is split by responsibility so desktop adapters and transport code do
not leak into the hardware/domain core:

```text
gate_service.main                 thin process entrypoint
  -> gate_service.application     composition, REST/gRPC and shutdown order
     -> gate_service.reader_engine    transaction, passage and gate state
        -> gate_service.reader_protocol  VM64/ZK parsing and inventory commands
        -> rfid_portal.*              SQLite, outbox and domain services
     -> gate_service.api.*        REST/gRPC transport adapters
     -> gate_service.event_bus    live fan-out and MQTT event bridge
```

`gate_service.contracts` is the stable boundary used by control/API adapters.
Protocol names, HTTP routes, gRPC schema, MQTT topics, entrypoint, persistent
paths and Compose device mappings are compatibility boundaries and must not be
changed by internal refactors.
