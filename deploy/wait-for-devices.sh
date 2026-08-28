#!/bin/sh
set -eu

: "${READER_DEVICE:?READER_DEVICE is required}"
: "${SENSOR_DEVICE:?SENSOR_DEVICE is required}"
: "${DIALOUT_GID:?DIALOUT_GID is required}"
: "${GATE_ID:?GATE_ID is required}"
: "${GATE_IMAGE:?GATE_IMAGE is required}"
: "${GATE_DATA_DIR:?GATE_DATA_DIR is required}"
: "${MQTT_HOST:?MQTT_HOST is required}"
: "${MQTT_USERNAME:?MQTT_USERNAME is required}"
: "${API_TOKEN_SECRET_FILE:?API_TOKEN_SECRET_FILE is required}"
: "${TLS_CERT_SECRET_FILE:?TLS_CERT_SECRET_FILE is required}"
: "${TLS_KEY_SECRET_FILE:?TLS_KEY_SECRET_FILE is required}"
: "${CALIBRATION_KEY_SECRET_FILE:?CALIBRATION_KEY_SECRET_FILE is required}"
: "${MQTT_PASSWORD_SECRET_FILE:?MQTT_PASSWORD_SECRET_FILE is required}"
: "${MQTT_CA_SECRET_FILE:?MQTT_CA_SECRET_FILE is required}"

case "$GATE_IMAGE" in
  *@sha256:*) image_digest=${GATE_IMAGE##*@sha256:} ;;
  *) echo "GATE_IMAGE must use an immutable @sha256 digest" >&2; exit 1 ;;
esac
if [ "${#image_digest}" -ne 64 ]; then
  echo "GATE_IMAGE digest must contain exactly 64 hexadecimal characters" >&2
  exit 1
fi
case "$image_digest" in
  *[!0-9a-f]*) echo "GATE_IMAGE digest must use lowercase hexadecimal" >&2; exit 1 ;;
esac

case "$GATE_DATA_DIR" in
  /*) ;;
  *) echo "GATE_DATA_DIR must be an absolute host path" >&2; exit 1 ;;
esac
if [ ! -d "$GATE_DATA_DIR" ]; then
  echo "GATE_DATA_DIR does not exist: $GATE_DATA_DIR" >&2
  exit 1
fi
data_metadata=$(stat -Lc '%u:%g:%a' -- "$GATE_DATA_DIR") || {
  echo "Cannot inspect GATE_DATA_DIR: $GATE_DATA_DIR" >&2
  exit 1
}
if [ "$data_metadata" != "10001:10001:750" ]; then
  echo "GATE_DATA_DIR must be owned by 10001:10001 with mode 0750 (found $data_metadata)" >&2
  exit 1
fi

case "$GATE_ID" in
  *[!A-Za-z0-9._-]*|[!A-Za-z0-9]*)
    echo "GATE_ID must start with a letter or digit and use only letters, digits, '.', '_' or '-'" >&2
    exit 1
    ;;
esac
if [ "${#GATE_ID}" -gt 64 ]; then
  echo "GATE_ID must contain at most 64 characters" >&2
  exit 1
fi

# Compose file-backed secrets retain the host file ownership and mode. The
# runtime image uses uid/gid 10001, so every secret must be root-owned and
# group-readable by gid 10001 without granting access to other host users.
check_secret_metadata() {
  secret_path=$1
  secret_name=$2
  if [ ! -f "$secret_path" ]; then
    echo "$secret_name secret file does not exist: $secret_path" >&2
    exit 1
  fi
  metadata=$(stat -Lc '%u:%g:%a' -- "$secret_path") || {
    echo "Cannot inspect $secret_name secret file: $secret_path" >&2
    exit 1
  }
  if [ "$metadata" != "0:10001:440" ]; then
    echo "$secret_name secret must be owned by uid 0, gid 10001 with mode 0440 (found $metadata)" >&2
    exit 1
  fi
}

check_secret_metadata "$API_TOKEN_SECRET_FILE" "API token"
check_secret_metadata "$TLS_CERT_SECRET_FILE" "TLS certificate"
check_secret_metadata "$TLS_KEY_SECRET_FILE" "TLS private key"
check_secret_metadata "$CALIBRATION_KEY_SECRET_FILE" "Calibration root key"
check_secret_metadata "$MQTT_PASSWORD_SECRET_FILE" "MQTT password"
check_secret_metadata "$MQTT_CA_SECRET_FILE" "MQTT CA"

case "$READER_DEVICE" in
  /dev/serial/by-id/*-if00*) ;;
  *) echo "READER_DEVICE must be a /dev/serial/by-id path for interface if00" >&2; exit 1 ;;
esac
case "$SENSOR_DEVICE" in
  /dev/serial/by-id/*-if02*) ;;
  *) echo "SENSOR_DEVICE must be a /dev/serial/by-id path for interface if02" >&2; exit 1 ;;
esac

deadline=$(( $(date +%s) + 60 ))
while [ ! -e "$READER_DEVICE" ] || [ ! -e "$SENSOR_DEVICE" ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "Timed out waiting for both NR155 interfaces" >&2
    exit 1
  fi
  sleep 1
done

reader_node=$(readlink -f "$READER_DEVICE")
sensor_node=$(readlink -f "$SENSOR_DEVICE")
if [ "$reader_node" = "$sensor_node" ]; then
  echo "Reader and sensor paths resolve to the same device" >&2
  exit 1
fi

check_nr155() {
  node=$1
  expected_interface=$2
  properties=$(udevadm info --query=property --name="$node")
  echo "$properties" | grep -q '^ID_VENDOR_ID=303a$' || {
    echo "$node is not NR155 VID 303a" >&2; exit 1;
  }
  echo "$properties" | grep -q '^ID_MODEL_ID=4002$' || {
    echo "$node is not NR155 PID 4002" >&2; exit 1;
  }
  echo "$properties" | grep -q "^ID_USB_INTERFACE_NUM=$expected_interface$" || {
    echo "$node is not NR155 interface $expected_interface" >&2; exit 1;
  }
}

check_nr155 "$reader_node" 00
check_nr155 "$sensor_node" 02

dialout_entry=$(getent group dialout) || {
  echo "Host group 'dialout' does not exist; the NR155 udev rule requires it" >&2
  exit 1
}
host_dialout_gid=$(printf '%s\n' "$dialout_entry" | cut -d: -f3)
if [ "$host_dialout_gid" != "$DIALOUT_GID" ]; then
  echo "DIALOUT_GID=$DIALOUT_GID does not match host dialout gid $host_dialout_gid" >&2
  exit 1
fi

check_device_group_access() {
  node=$1
  device_name=$2
  if [ ! -c "$node" ]; then
    echo "$device_name target is not a character device: $node" >&2
    exit 1
  fi
  metadata=$(stat -Lc '%g:%a' -- "$node") || {
    echo "Cannot inspect $device_name target: $node" >&2
    exit 1
  }
  node_gid=${metadata%%:*}
  node_mode=${metadata#*:}
  if [ "$node_gid" != "$DIALOUT_GID" ]; then
    echo "$device_name target $node must have gid $DIALOUT_GID (found $node_gid, mode $node_mode)" >&2
    exit 1
  fi
  owner_and_group=${node_mode%?}
  group_digit=${owner_and_group#${owner_and_group%?}}
  case "$group_digit" in
    6|7) ;;
    *)
      echo "$device_name target $node must grant group read/write access (found mode $node_mode)" >&2
      exit 1
      ;;
  esac
}

check_device_group_access "$reader_node" "Reader"
check_device_group_access "$sensor_node" "Sensor"

echo "NR155 and secret metadata ready: gate_id=$GATE_ID reader=$reader_node sensor=$sensor_node dialout_gid=$DIALOUT_GID"
