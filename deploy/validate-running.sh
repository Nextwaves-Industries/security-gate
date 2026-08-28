#!/bin/sh
set -eu

# Read-only production checks for a running gate. This script never prints a
# secret value and does not issue inventory/calibration mutations.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file=${GATE_ENV_FILE:-"$script_dir/gate.env"}
compose_file=${GATE_COMPOSE_FILE:-"$script_dir/compose.yaml"}

if [ ! -f "$env_file" ]; then
  echo "Gate environment file is unavailable: $env_file" >&2
  exit 1
fi

set -a
# gate.env is an operator-owned deployment file and intentionally uses the
# portable KEY=VALUE format accepted by both Compose and POSIX sh.
. "$env_file"
set +a

: "${GATE_IMAGE:?GATE_IMAGE is required}"
: "${API_TOKEN_SECRET_FILE:?API_TOKEN_SECRET_FILE is required}"
: "${TLS_CERT_SECRET_FILE:?TLS_CERT_SECRET_FILE is required}"

case "$GATE_IMAGE" in
  *@sha256:*) image_digest=${GATE_IMAGE##*@sha256:} ;;
  *)
    echo "GATE_IMAGE must use an immutable @sha256 digest" >&2
    exit 1
    ;;
esac
if [ "${#image_digest}" -ne 64 ]; then
  echo "GATE_IMAGE digest must contain exactly 64 hexadecimal characters" >&2
  exit 1
fi
case "$image_digest" in
  *[!0-9a-f]*)
    echo "GATE_IMAGE digest must use lowercase hexadecimal" >&2
    exit 1
    ;;
esac

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

compose config --quiet
container_id=$(compose ps -q gate-service)
if [ -z "$container_id" ] || [ "$(printf '%s\n' "$container_id" | wc -l | tr -d ' ')" -ne 1 ]; then
  echo "Exactly one running gate-service container is required" >&2
  exit 1
fi

runtime_user=$(docker inspect --format '{{.Config.User}}' "$container_id")
runtime_image=$(docker inspect --format '{{.Config.Image}}' "$container_id")
readonly_root=$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$container_id")
cap_drop=$(docker inspect --format '{{json .HostConfig.CapDrop}}' "$container_id")
security_opt=$(docker inspect --format '{{json .HostConfig.SecurityOpt}}' "$container_id")

[ "$runtime_user" = "10001:10001" ] || {
  echo "Unexpected runtime user: $runtime_user" >&2
  exit 1
}
case "$runtime_image" in
  *@sha256:"$image_digest") ;;
  *)
    echo "Running image digest does not match GATE_IMAGE: $runtime_image" >&2
    exit 1
    ;;
esac
[ "$readonly_root" = "true" ] || {
  echo "Container root filesystem is not read-only" >&2
  exit 1
}
case "$cap_drop" in
  *ALL*) ;;
  *) echo "Container does not drop all Linux capabilities" >&2; exit 1 ;;
esac
case "$security_opt" in
  *no-new-privileges*) ;;
  *) echo "Container does not enable no-new-privileges" >&2; exit 1 ;;
esac

compose exec -T gate-service python -c \
  "import os,pathlib,sys; paths=['/run/secrets/api_token','/run/secrets/tls_cert','/run/secrets/tls_key','/run/secrets/calibration_root_key','/run/secrets/mqtt_password','/run/secrets/mqtt_ca']; assert os.getuid()==10001 and os.getgid()==10001; assert sys.version_info[:2]==(3,11); assert all(pathlib.Path(p).is_file() and os.access(p,os.R_OK) for p in paths)"

if [ -n "${GATE_API_URL:-}" ]; then
  api_url=$GATE_API_URL
else
  case "${REST_BIND_IP:-127.0.0.1}" in
    0.0.0.0|::|'[::]'|'')
      echo "REST_BIND_IP must be loopback or a dedicated VLAN/VPN address, not a wildcard" >&2
      exit 1 ;;
    127.0.0.1) api_host=127.0.0.1 ;;
    *) api_host=$REST_BIND_IP ;;
  esac
  case "${GRPC_BIND_IP:-127.0.0.1}" in
    0.0.0.0|::|'[::]'|'')
      echo "GRPC_BIND_IP must be loopback or a dedicated VLAN/VPN address, not a wildcard" >&2
      exit 1 ;;
  esac
  api_url="https://${api_host}:${REST_HOST_PORT:-8443}"
fi
api_ca_file=${GATE_API_CA_FILE:-"$TLS_CERT_SECRET_FILE"}
require_ready=${REQUIRE_READY:-1}

GATE_API_URL="$api_url" \
GATE_API_CA_FILE="$api_ca_file" \
GATE_API_TOKEN_FILE="$API_TOKEN_SECRET_FILE" \
REQUIRE_READY="$require_ready" \
EXPECTED_GATE_ID="$GATE_ID" \
python3 - <<'PY'
import json
import os
import ssl
import urllib.error
import urllib.request

base = os.environ["GATE_API_URL"].rstrip("/")
token = open(os.environ["GATE_API_TOKEN_FILE"], encoding="utf-8").read().strip()
context = ssl.create_default_context(cafile=os.environ["GATE_API_CA_FILE"])


def request(path, supplied_token=None):
    headers = {}
    if supplied_token is not None:
        headers["Authorization"] = f"Bearer {supplied_token}"
    req = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(req, context=context, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


health_code, _ = request("/healthz")
if health_code != 200:
    raise SystemExit(f"healthz returned HTTP {health_code}")

ready_code, ready_body = request("/readyz")
if ready_code not in {200, 503}:
    raise SystemExit(f"readyz returned unexpected HTTP {ready_code}")
if os.environ.get("REQUIRE_READY", "1") == "1" and ready_code != 200:
    state = ready_body.get("error", {}).get("code", "unknown")
    raise SystemExit(f"gate is not ready: {state}")

status_code, status_body = request("/api/v1/status", token)
if status_code != 200:
    raise SystemExit(f"authenticated status returned HTTP {status_code}")
if status_body.get("gate_id") != os.environ["EXPECTED_GATE_ID"]:
    raise SystemExit(
        "runtime gate_id does not match gate.env: "
        f"{status_body.get('gate_id', 'unknown')}"
    )

wrong_code, wrong_body = request("/api/v1/status", token + "-invalid")
if wrong_code != 401 or wrong_body.get("error", {}).get("code") != "unauthorized":
    raise SystemExit("invalid bearer token was not rejected with unauthorized")

print(
    "running gate validated: "
    f"gate_id={status_body.get('gate_id', 'unknown')} "
    f"state={status_body.get('state', 'unknown')} "
    f"ready_http={ready_code}"
)
PY

compose exec -T gate-service python - <<'PY'
from pathlib import Path
import os

import grpc
from cryptography import x509
from cryptography.x509.oid import NameOID

from gate_service.proto import gate_stream_pb2, gate_stream_pb2_grpc

certificate_bytes = Path("/run/secrets/tls_cert").read_bytes()
certificate = x509.load_pem_x509_certificate(certificate_bytes)
server_name = ""
try:
    san = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    dns_names = san.get_values_for_type(x509.DNSName)
    ip_names = [str(value) for value in san.get_values_for_type(x509.IPAddress)]
    server_name = (dns_names + ip_names)[0]
except (x509.ExtensionNotFound, IndexError):
    attributes = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if attributes:
        server_name = attributes[0].value
if not server_name:
    raise SystemExit("TLS certificate has no DNS/IP SAN or common name")

token = Path("/run/secrets/api_token").read_text(encoding="utf-8").strip()
credentials = grpc.ssl_channel_credentials(root_certificates=certificate_bytes)
options = (("grpc.ssl_target_name_override", server_name),)
with grpc.secure_channel(
    "127.0.0.1:50051", credentials, options=options
) as channel:
    grpc.channel_ready_future(channel).result(timeout=5)
    stub = gate_stream_pb2_grpc.GateStreamServiceStub(channel)
    status = stub.GetStatus(
        gate_stream_pb2.GetStatusRequest(),
        metadata=(("authorization", f"Bearer {token}"),),
        timeout=5,
    )
    if status.gate_id != os.environ["GATE_ID"]:
        raise SystemExit(
            f"gRPC gate_id mismatch: {status.gate_id or 'empty'}"
        )
    try:
        stub.GetStatus(
            gate_stream_pb2.GetStatusRequest(),
            metadata=(("authorization", "Bearer invalid"),),
            timeout=5,
        )
    except grpc.RpcError as exc:
        if exc.code() != grpc.StatusCode.UNAUTHENTICATED:
            raise
    else:
        raise SystemExit("gRPC accepted an invalid bearer token")
print(f"gRPC TLS/auth validated: gate_id={status.gate_id}")
PY

echo "Container digest/hardening, secrets, REST and gRPC validation passed"
