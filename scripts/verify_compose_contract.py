"""Fail CI when the customer Compose security/port contract drifts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def fail(message: str) -> None:
    raise SystemExit(f"Compose contract violation: {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config_json", type=Path)
    parser.add_argument("--rest-host-port", type=int, required=True)
    parser.add_argument("--grpc-host-port", type=int, required=True)
    args = parser.parse_args()

    # utf-8-sig also accepts plain UTF-8 and keeps local PowerShell validation
    # compatible with its BOM-emitting Set-Content implementation.
    config = json.loads(args.config_json.read_text(encoding="utf-8-sig"))
    services = config.get("services", {})
    if set(services) != {"gate-service"}:
        fail("exactly one gate-service must be defined")
    service = services["gate-service"]

    image = str(service.get("image", ""))
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
        fail("GATE_IMAGE must resolve to an immutable 64-hex sha256 digest")

    environment = service.get("environment", {})
    if str(environment.get("REST_PORT")) != "8443":
        fail("REST_PORT inside the container must remain 8443")
    if str(environment.get("GRPC_PORT")) != "50051":
        fail("GRPC_PORT inside the container must remain 50051")
    for name in ("GATE_ID", "MQTT_HOST", "MQTT_USERNAME"):
        if not str(environment.get(name, "")).strip():
            fail(f"{name} must be passed explicitly to the runtime")
    if str(environment.get("GATE_DEVELOPMENT", "")).lower() != "false":
        fail("development mode must be disabled")
    if str(environment.get("GATE_ALLOW_INSECURE", "")).lower() != "false":
        fail("insecure REST/gRPC mode must be disabled")
    leaked_host_variables = {
        "GATE_IMAGE",
        "GATE_DATA_DIR",
        "DIALOUT_GID",
        "API_TOKEN_SECRET_FILE",
        "TLS_CERT_SECRET_FILE",
        "TLS_KEY_SECRET_FILE",
        "CALIBRATION_KEY_SECRET_FILE",
        "MQTT_PASSWORD_SECRET_FILE",
        "MQTT_CA_SECRET_FILE",
        "REST_HOST_PORT",
        "GRPC_HOST_PORT",
        "REST_BIND_IP",
        "GRPC_BIND_IP",
        "GATE_MEM_LIMIT",
        "GATE_CPUS",
        "LOG_MAX_SIZE",
        "LOG_MAX_FILE",
    } & set(environment)
    if leaked_host_variables:
        fail(f"host-only variables leaked into the container: {sorted(leaked_host_variables)}")

    published = {
        int(item["target"]): int(item["published"])
        for item in service.get("ports", [])
    }
    expected_ports = {
        8443: args.rest_host_port,
        50051: args.grpc_host_port,
    }
    if published != expected_ports:
        fail(f"published ports are {published}, expected {expected_ports}")
    forbidden_bind_addresses = {"", "0.0.0.0", "::", "[::]"}
    for item in service.get("ports", []):
        host_ip = str(item.get("host_ip", ""))
        if host_ip in forbidden_bind_addresses:
            fail(
                f"port {item.get('target')} must bind loopback or a dedicated "
                "VLAN/VPN address, not a wildcard"
            )

    device_targets = {
        item.get("target"): item.get("permissions")
        for item in service.get("devices", [])
    }
    if device_targets != {
        "/dev/rfid-reader": "rw",
        "/dev/rfid-sensor": "rw",
    }:
        fail("NR155 reader/sensor device mappings changed (expected rw, no mknod)")

    if service.get("read_only") is not True:
        fail("root filesystem must be read-only")
    if service.get("privileged", False) is not False:
        fail("privileged mode must remain disabled")
    if "ALL" not in service.get("cap_drop", []):
        fail("all Linux capabilities must be dropped")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        fail("no-new-privileges must be enabled")
    if service.get("init") is not True:
        fail("init: true must remain enabled for signal forwarding and reaping")
    if not service.get("pids_limit"):
        fail("pids_limit must be set")
    if not service.get("mem_limit") and not (
        service.get("deploy", {}).get("resources", {}).get("limits", {}).get("memory")
    ):
        fail("a memory limit must be set")
    if service.get("restart") != "unless-stopped":
        fail("restart policy must remain unless-stopped")
    if service.get("stop_grace_period") != "30s":
        fail("stop grace period must remain 30 seconds")
    logging = service.get("logging", {})
    if logging.get("driver") != "json-file":
        fail("Docker logging must use the bounded json-file driver")
    log_options = logging.get("options", {})
    if str(log_options.get("max-size", "")).casefold() != "10m":
        fail("default Docker log max-size must remain 10m")
    if str(log_options.get("max-file", "")) != "5":
        fail("default Docker log max-file must remain 5")
    replicas = service.get("deploy", {}).get("replicas", 1)
    if replicas != 1:
        fail("one gate-service replica must own the physical gate")

    volumes = service.get("volumes", [])
    if len(volumes) != 1 or volumes[0].get("target") != "/var/lib/nextwaves":
        fail("the only persistent mount must target /var/lib/nextwaves")
    if volumes[0].get("read_only", False):
        fail("the persistent data mount must be writable")
    if not any(str(item).startswith("/tmp:") for item in service.get("tmpfs", [])):
        fail("/tmp must remain a dedicated tmpfs")

    secret_targets = {
        item.get("source"): item.get("target")
        for item in service.get("secrets", [])
    }
    expected_secrets = {
        "api_token": "/run/secrets/api_token",
        "tls_cert": "/run/secrets/tls_cert",
        "tls_key": "/run/secrets/tls_key",
        "calibration_root_key": "/run/secrets/calibration_root_key",
        "mqtt_password": "/run/secrets/mqtt_password",
        "mqtt_ca": "/run/secrets/mqtt_ca",
    }
    if secret_targets != expected_secrets:
        fail("the six mounted production secrets changed")

    print("customer Compose contract verified")


if __name__ == "__main__":
    main()
