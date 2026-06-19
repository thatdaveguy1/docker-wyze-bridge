#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TMP_DIR="$ROOT_DIR/tmp"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$TMP_DIR/ha_wyze_scrypted_snapshot_probe_${STAMP}.txt"
SCRYPTED_CONFIG_PATH="/homeassistant/.storage/core.config_entries"

SCRYPTED_SLUG="${HA_SCRYPTED_ADDON_SLUG:-09e60fb6_scrypted}"
DEVICES="${HA_WYZE_SCRYPTED_DEVICES:-garage:153 deck:154 south-yard:155 hamster:223 north-yard:211}"
SAMPLES="${HA_WYZE_SCRYPTED_SAMPLES:-3}"
INTERVAL="${HA_WYZE_SCRYPTED_INTERVAL_SECONDS:-10}"

# Source shared library for validate_slug, section, mark_fail, redact_api_keys
. "$SCRIPT_DIR/ha_bridge_probe.sh"

usage() {
  cat <<EOF
Usage: scripts/ha_wyze_scrypted_snapshot_probe.sh

Runs a read-only Scrypted snapshot proof probe from the Home Assistant host for
the named Wyze HomeKit-facing devices. It verifies the exact Snapshot plugin
endpoint shape, validates repeated JPEG responses, and records changing content
hashes over time.

Environment:
  HA_SCRYPTED_ADDON_SLUG             default: $SCRYPTED_SLUG
  HA_WYZE_SCRYPTED_DEVICES           default: $DEVICES
  HA_WYZE_SCRYPTED_SAMPLES           default: $SAMPLES
  HA_WYZE_SCRYPTED_INTERVAL_SECONDS  default: $INTERVAL
EOF
}

case "${1:-}" in
  --help|-h|help)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

validate_number() {
  name="$1"
  value="$2"
  case "$value" in
    ""|*[!0-9]*|0)
      echo "Invalid $name: use a positive integer." >&2
      exit 1
      ;;
  esac
}

validate_devices() {
  for item in $DEVICES; do
    name=${item%%:*}
    id=${item##*:}
    case "$name" in
      ""|*[!A-Za-z0-9_.-]*)
        echo "Invalid HA_WYZE_SCRYPTED_DEVICES camera name '$name'." >&2
        exit 1
        ;;
    esac
    case "$id" in
      ""|*[!0-9]*)
        echo "Invalid HA_WYZE_SCRYPTED_DEVICES device id '$id'." >&2
        exit 1
        ;;
    esac
  done
}

validate_slug "HA_SCRYPTED_ADDON_SLUG" "$SCRYPTED_SLUG"
validate_number "HA_WYZE_SCRYPTED_SAMPLES" "$SAMPLES"
validate_number "HA_WYZE_SCRYPTED_INTERVAL_SECONDS" "$INTERVAL"
validate_devices

mkdir -p "$TMP_DIR"

set +e
{
  cat "$SCRIPT_DIR/ha_bridge_probe.sh"
  cat <<'REMOTE'
set -eu

SCRYPTED_SLUG="$HA_WYZE_SCRYPTED_SLUG"
DEVICES="$HA_WYZE_SCRYPTED_DEVICES"
SAMPLES="$HA_WYZE_SCRYPTED_SAMPLES"
INTERVAL="$HA_WYZE_SCRYPTED_INTERVAL"
SCRYPTED_CONFIG_PATH="/homeassistant/.storage/core.config_entries"
FAIL=0
SCRYPTED_HOST=""
SCRYPTED_USERNAME=""
SCRYPTED_PASSWORD=""
SCRYPTED_TOKEN=""

# redact extends the shared library's redact_api_keys with Bearer token redaction
redact() {
  redact_api_keys "$@" | sed -E 's/(Bearer )[A-Za-z0-9._~-]+/\1<redacted>/g'
}

sha_of() {
  path="$1"
  if [ -s "$path" ]; then
    sha256sum "$path" | awk '{print $1}'
  else
    printf '<none>\n'
  fi
}

mime_of() {
  path="$1"
  file -b --mime-type "$path" 2>/dev/null || echo unknown
}

bytes_of() {
  path="$1"
  if [ -f "$path" ]; then
    wc -c "$path" 2>/dev/null | awk '{print $1}'
  else
    echo 0
  fi
}

is_jpeg_file() {
  path="$1"
  [ -s "$path" ] || return 1
  magic=$(od -An -tx1 -N2 "$path" 2>/dev/null | tr -d ' \n')
  [ "$magic" = "ffd8" ]
}

load_scrypted_credentials() {
  entry=$(jq -c '.data.entries[] | select(.domain=="scrypted" and (.disabled_by | not)) | .data' "$SCRYPTED_CONFIG_PATH" 2>/dev/null | head -n1 || true)
  if [ -z "$entry" ]; then
    mark_fail "active Home Assistant Scrypted config entry not found in $SCRYPTED_CONFIG_PATH"
    return 1
  fi

  SCRYPTED_HOST=$(printf '%s' "$entry" | jq -r '.host // empty')
  SCRYPTED_USERNAME=$(printf '%s' "$entry" | jq -r '.username // empty')
  SCRYPTED_PASSWORD=$(printf '%s' "$entry" | jq -r '.password // empty')

  if [ -z "$SCRYPTED_HOST" ] || [ -z "$SCRYPTED_USERNAME" ] || [ -z "$SCRYPTED_PASSWORD" ]; then
    mark_fail "active Home Assistant Scrypted config entry is missing host or credentials"
    return 1
  fi

  return 0
}

login_scrypted() {
  auth=$(printf '%s:%s' "$SCRYPTED_USERNAME" "$SCRYPTED_PASSWORD" | base64 | tr -d '\n')
  response=$(curl -k -sS --max-time 15 -X GET \
    -H "authorization: Basic $auth" \
    -H "Content-Type: application/json" \
    --data "{\"username\":\"$SCRYPTED_USERNAME\"}" \
    "https://$SCRYPTED_HOST/login" 2>/dev/null || true)
  SCRYPTED_TOKEN=$(printf '%s' "$response" | jq -r '.token // empty' 2>/dev/null || true)
  if [ -z "$SCRYPTED_TOKEN" ]; then
    mark_fail "unable to mint Scrypted bearer token from https://$SCRYPTED_HOST/login"
    return 1
  fi

  return 0
}

section "Wyze Scrypted Snapshot Probe"
echo "scrypted_slug=$SCRYPTED_SLUG"
echo "samples=$SAMPLES"
echo "interval_seconds=$INTERVAL"
echo "devices=$DEVICES"

section "Scrypted Add-on State"
curl -fsS -H "Authorization: Bearer $SUPERVISOR_TOKEN" "http://supervisor/addons/$SCRYPTED_SLUG/info" 2>/dev/null \
  | jq -c '{slug:.data.slug,state:.data.state,version:.data.version,repository:.data.repository}' \
  | redact || true

section "Scrypted Login"
if load_scrypted_credentials && login_scrypted; then
  echo "scrypted_host=$SCRYPTED_HOST"
  echo "login_status=ok"
else
  echo "login_status=failed"
fi

for item in $DEVICES; do
  camera=${item%%:*}
  device_id=${item##*:}

  section "Camera $camera"
  echo "device_id=$device_id"

  valid_count=0
  hash_1="<none>"
  hash_2="<none>"
  sample=1
  while [ "$sample" -le "$SAMPLES" ]; do
    out="/tmp/${camera}-scrypted-snapshot-${sample}.jpg"
    route="https://$SCRYPTED_HOST/endpoint/@scrypted/snapshot/$device_id/Camera"
    : > "$out"
    if [ -n "$SCRYPTED_TOKEN" ]; then
      code=$(curl -k -sS --max-time 15 -H "Authorization: Bearer $SCRYPTED_TOKEN" -o "$out" -w '%{http_code}' "$route" 2>/dev/null || printf '000')
    else
      code=000
    fi
    bytes=$(bytes_of "$out")
    mime=$(mime_of "$out")
    sha=$(sha_of "$out")
    if [ "$code" = "200" ] && [ "${bytes:-0}" -gt 2048 ] && is_jpeg_file "$out"; then
      valid_count=$((valid_count + 1))
      if [ "$hash_1" = "<none>" ]; then
        hash_1="$sha"
      elif [ "$sha" != "$hash_1" ]; then
        hash_2="$sha"
      fi
    fi
    printf 'camera=%s sample=%s route=/endpoint/@scrypted/snapshot/%s/Camera code=%s bytes=%s mime=%s sha256=%s\n' \
      "$camera" "$sample" "$device_id" "$code" "${bytes:-0}" "$mime" "$sha" | redact

    sample=$((sample + 1))
    if [ "$sample" -le "$SAMPLES" ] && [ "$INTERVAL" -gt 0 ]; then
      sleep "$INTERVAL"
    fi
  done

  unique_hashes=0
  if [ "$hash_1" != "<none>" ]; then
    unique_hashes=1
  fi
  if [ "$hash_2" != "<none>" ] && [ "$hash_2" != "$hash_1" ]; then
    unique_hashes=2
  fi

  printf '{"camera":"%s","device_id":%s,"valid_count":%s,"unique_hashes":%s}\n' \
    "$camera" "$device_id" "$valid_count" "$unique_hashes"

  if [ "$valid_count" -eq 0 ]; then
    mark_fail "$camera Scrypted snapshot endpoint must return at least one valid JPEG"
  fi
  if [ "$valid_count" -gt 1 ] && [ "$unique_hashes" -le 1 ]; then
    mark_fail "$camera Scrypted snapshot endpoint must show changing content hash across repeated samples"
  fi
done

section "Result"
if [ "$FAIL" -eq 0 ]; then
  echo "PASS: Wyze Scrypted snapshot probe passed."
else
  echo "FAIL: Wyze Scrypted snapshot probe failed."
fi

exit "$FAIL"
REMOTE
} | "$SCRIPT_DIR/ha_ssh.sh" "HA_WYZE_SCRYPTED_SLUG=$SCRYPTED_SLUG HA_WYZE_SCRYPTED_DEVICES='$DEVICES' HA_WYZE_SCRYPTED_SAMPLES=$SAMPLES HA_WYZE_SCRYPTED_INTERVAL=$INTERVAL sh -s" > "$OUT"
rc=$?
set -e

cat "$OUT"
printf 'artifact=%s\n' "$OUT"

exit "$rc"
