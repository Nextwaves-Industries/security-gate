#!/bin/sh
# Generate throw-away secrets for deploy/compose.dev.yaml.
#
# Creates, under deploy/dev/secrets/ (git-ignored):
#   dev_ca.pem / dev_ca.key          local CA (trusts both TLS listeners)
#   tls_cert.pem / tls_key.pem       gate-service REST+gRPC certificate
#   mqtt_cert.pem / mqtt_key.pem     mosquitto certificate
#   api_token                        bearer token (64 hex)
#   calibration_root_key             64 hex characters
#   mqtt_password                    broker password for user gate-dev
#   mosquitto_passwd                 hashed password file for mosquitto
#
# Runs only if secrets/api_token is absent or --force is given. When it runs
# it deletes and regenerates the whole secrets/ directory. Certificates are
# valid for 825 days. The directory is deliberately world-readable so both
# containers (uid 10001 and 1883) can read their files; mqtt_password is also
# mounted into the broker for its healthcheck.
# Never use this output on a customer gate.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
out="$here/secrets"
force=0
[ "${1:-}" = "--force" ] && force=1

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing tool: $1" >&2; exit 1; }; }
need openssl
need docker

if [ -d "$out" ] && [ "$force" -eq 0 ] && [ -f "$out/api_token" ]; then
  echo "dev secrets already exist in $out (use --force to regenerate)"
  exit 0
fi

umask 077
rm -rf "$out"
mkdir -p "$out"
cd "$out"

# --- CA -------------------------------------------------------------------
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
  -subj "/CN=Nextwaves Gate DEV CA" \
  -keyout dev_ca.key -out dev_ca.pem >/dev/null 2>&1

issue() { # issue <basename> <CN> <SAN list>
  base=$1; cn=$2; san=$3
  openssl req -newkey rsa:2048 -nodes -sha256 -subj "/CN=$cn" \
    -keyout "$base"_key.pem -out "$base".csr >/dev/null 2>&1
  printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\n' "$san" > "$base".ext
  openssl x509 -req -in "$base".csr -CA dev_ca.pem -CAkey dev_ca.key \
    -CAcreateserial -days 825 -sha256 -extfile "$base".ext \
    -out "$base"_cert.pem >/dev/null 2>&1
  rm -f "$base".csr "$base".ext
}

issue tls  gate-dev  "DNS:localhost,DNS:gate-service,IP:127.0.0.1"
issue mqtt mosquitto "DNS:localhost,DNS:mosquitto,IP:127.0.0.1"

# --- tokens / keys --------------------------------------------------------
openssl rand -hex 32 | tr -d '\n' > api_token
openssl rand -hex 32 | tr -d '\n' > calibration_root_key
openssl rand -hex 16 | tr -d '\n' > mqtt_password

# mosquitto_passwd lives in the broker image; hash the password with it.
docker run --rm -i eclipse-mosquitto:2.0.20 sh -c \
  "touch /tmp/p && chmod 0700 /tmp/p && mosquitto_passwd -b /tmp/p gate-dev '$(cat mqtt_password)' && cat /tmp/p" \
  > mosquitto_passwd

# --- permissions ----------------------------------------------------------
# gate-service runs as 10001; mosquitto runs as 1883. Compose file secrets and
# bind mounts keep host mode bits, so make the shared files world-readable
# (dev only) and keep private keys tight where possible.
chmod 0644 dev_ca.pem tls_cert.pem mqtt_cert.pem mosquitto_passwd
chmod 0644 api_token calibration_root_key mqtt_password tls_key.pem mqtt_key.pem
chmod 0600 dev_ca.key dev_ca.srl
# The broker (uid 1883) and gate-service (uid 10001) must traverse the dir.
chmod 0755 "$out"

echo "dev secrets written to $out"
echo "  API token:      $(cat api_token)"
echo "  MQTT user/pass: gate-dev / $(cat mqtt_password)"
echo "  CA for curl:    $out/dev_ca.pem"
