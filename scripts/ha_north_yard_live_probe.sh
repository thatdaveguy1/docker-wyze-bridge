#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

CAMERA="${HA_NORTH_YARD_CAMERA:-}"
PROD_SLUG="${HA_PROD_ADDON_SLUG:-wyze_bridge_v4}"
SAMPLES="${HA_NORTH_YARD_PROBE_SAMPLES:-3}"
INTERVAL="${HA_NORTH_YARD_PROBE_INTERVAL_SECONDS:-20}"
IPS="${HA_NORTH_YARD_PROBE_IPS:-}"
MAC="${HA_NORTH_YARD_MAC:-}"
BRIDGE_BASE="${HA_NORTH_YARD_BRIDGE_BASE:-http://192.0.2.10:5000}"
GO2RTC_BASE="${HA_NORTH_YARD_GO2RTC_BASE:-http://192.0.2.11:11984}"

# Source shared library for validate_slug, validate_base_url, section,
# redact_api_keys, derive_bridge_token
. "$SCRIPT_DIR/ha_bridge_probe.sh"

usage() {
  cat <<EOF
Usage: scripts/ha_north_yard_live_probe.sh

Runs a read-only Home Assistant probe for the North Yard Phase 1 blocker. It
checks bridge API state, snapshot hash registry, health/details, authenticated
/snapshot and /img routes, go2rtc frame routes, and camera LAN reachability.
It does not stop, start, rebuild, restart, reboot, or edit anything.

Environment:
  HA_NORTH_YARD_CAMERA                  required, camera name to probe
  HA_PROD_ADDON_SLUG                    default: $PROD_SLUG
  HA_NORTH_YARD_PROBE_SAMPLES           default: $SAMPLES
  HA_NORTH_YARD_PROBE_INTERVAL_SECONDS  default: $INTERVAL
  HA_NORTH_YARD_PROBE_IPS               default: $IPS
  HA_NORTH_YARD_BRIDGE_BASE             default: $BRIDGE_BASE
  HA_NORTH_YARD_GO2RTC_BASE             default: $GO2RTC_BASE
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

if [ -z "$CAMERA" ]; then
  echo "HA_NORTH_YARD_CAMERA is required: set it to the camera name to probe." >&2
  exit 1
fi

validate_name() {
  name="$1"
  value="$2"
  case "$value" in
    ""|*[!A-Za-z0-9_.-]*)
      echo "Invalid $name: only letters, numbers, '.', '_' and '-' are allowed." >&2
      exit 1
      ;;
  esac
}

validate_number() {
  name="$1"
  value="$2"
  case "$value" in
    ""|*[!0-9]*)
      echo "Invalid $name: use a non-negative integer." >&2
      exit 1
      ;;
  esac
}

validate_ips() {
  for ip in $IPS; do
    case "$ip" in
      [0-9]*.[0-9]*.[0-9]*.[0-9]*)
        ;;
      *)
        echo "Invalid HA_NORTH_YARD_PROBE_IPS: '$ip' is not an IPv4 address." >&2
        exit 1
        ;;
    esac
  done
}

validate_name "HA_NORTH_YARD_CAMERA" "$CAMERA"
validate_slug "HA_PROD_ADDON_SLUG" "$PROD_SLUG"
validate_number "HA_NORTH_YARD_PROBE_SAMPLES" "$SAMPLES"
validate_number "HA_NORTH_YARD_PROBE_INTERVAL_SECONDS" "$INTERVAL"
validate_base_url "HA_NORTH_YARD_BRIDGE_BASE" "$BRIDGE_BASE"
validate_base_url "HA_NORTH_YARD_GO2RTC_BASE" "$GO2RTC_BASE"
validate_ips

{
  cat "$SCRIPT_DIR/ha_bridge_probe.sh"
  cat <<'REMOTE'
set -eu

CAMERA="$HA_NY_CAMERA"
PROD_SLUG="$HA_NY_PROD_SLUG"
SAMPLES="$HA_NY_SAMPLES"
INTERVAL="$HA_NY_INTERVAL"
IPS="$HA_NY_IPS"
MAC="$HA_NY_MAC"
BRIDGE_BASE="$HA_NY_BRIDGE_BASE"
GO2RTC_BASE="$HA_NY_GO2RTC_BASE"
API_TOKEN=""

# redact is defined by ha_bridge_probe.sh as redact_api_keys
redact() { redact_api_keys "$@"; }

file_summary() {
  path="$1"
  if [ -s "$path" ]; then
    bytes=$(wc -c < "$path" | tr -d ' ')
    hash=$(sha256sum "$path" | awk '{print $1}')
    mime=$(file -b --mime-type "$path" 2>/dev/null || echo unknown)
    printf 'bytes=%s mime=%s sha256=%s\n' "$bytes" "$mime" "$hash"
  else
    bytes=$(wc -c < "$path" 2>/dev/null | tr -d ' ' || echo 0)
    printf 'bytes=%s mime=<none> sha256=<none>\n' "${bytes:-0}"
  fi
}

fetch_bridge() {
  route="$1"
  out="$2"
  : > "$out"
  if [ -n "$API_TOKEN" ]; then
    code=$(curl -sS --max-time 15 -H "api: $API_TOKEN" -o "$out" -w '%{http_code}' "$BRIDGE_BASE$route" 2>/dev/null || printf '000')
  else
    code=$(curl -sS --max-time 15 -o "$out" -w '%{http_code}' "$BRIDGE_BASE$route" 2>/dev/null || printf '000')
  fi
  printf 'route=%s code=%s ' "$route" "$code" | redact
  file_summary "$out" | redact
}

fetch_plain() {
  url="$1"
  out="$2"
  : > "$out"
  code=$(curl -sS --max-time 15 -o "$out" -w '%{http_code}' "$url" 2>/dev/null || printf '000')
  printf 'url=%s code=%s ' "$url" "$code" | redact
  file_summary "$out" | redact
}

INFO=$(curl -fsS -H "Authorization: Bearer $SUPERVISOR_TOKEN" "http://supervisor/addons/$PROD_SLUG/info" 2>/dev/null || true)
if [ -n "$INFO" ]; then
  API_TOKEN=$(derive_bridge_token "$INFO")
fi

section "North Yard Live Probe"
echo "camera=$CAMERA"
echo "prod_slug=$PROD_SLUG"
echo "samples=$SAMPLES"
echo "interval_seconds=$INTERVAL"
if [ -n "$API_TOKEN" ]; then echo "auth=api-header"; else echo "auth=none"; fi

section "Supervisor Selected Options"
printf '%s\n' "$INFO" \
  | jq -c '{slug:.data.slug,state:.data.state,repository:.data.repository,version:.data.version,GO2RTC_LAN_IP_OVERRIDES:(.data.options.GO2RTC_LAN_IP_OVERRIDES // ""),GO2RTC_FORCE_LAN_IP_OVERRIDES:(.data.options.GO2RTC_FORCE_LAN_IP_OVERRIDES // ""),SD_ONLY:(.data.options.SD_ONLY // ""),SNAPSHOT:(.data.options.SNAPSHOT // "")}' 2>/dev/null \
  | redact || true

section "Bridge API Camera"
api_body="/tmp/${CAMERA}-api.json"
fetch_bridge "/api/$CAMERA" "$api_body"
cat "$api_body" 2>/dev/null \
  | jq -c '{name_uri:(.name_uri // .nickname // ""),connected:(.connected // null),enabled:(.enabled // null),product_model:(.product_model // null),native_alias:(.native_alias // null),native_alias_ready:(.native_alias_ready // null),snapshot_source:(.snapshot_source // null),native_rtsp_host:((.native_rtsp_url // "") | sub("^rtsp://";"") | split("/")[0])}' 2>/dev/null \
  | redact || true

section "Snapshot Hash Registry"
hash_body="/tmp/${CAMERA}-hashes.json"
fetch_bridge "/api/snapshot-hashes" "$hash_body"
cat "$hash_body" 2>/dev/null \
  | jq -c --arg camera "$CAMERA" '.cameras[$camera] // .registry[$camera] // .[$camera] // .' 2>/dev/null \
  | redact || true

section "Health Details"
details="/tmp/${CAMERA}-details.json"
: > "$details"
curl -sS --max-time 15 -o "$details" "$BRIDGE_BASE/health/details?stream=$CAMERA" 2>/dev/null || true
cat "$details" 2>/dev/null \
  | jq -c '{stream:.stream,whep_reachable:(.whep_proxy.reachable // null),whep_status:(.whep_proxy.status_code // null),whep_error:(.whep_proxy.error // null),mediamtx_reachable:(.mediamtx.reachable // null),mediamtx_status:(.mediamtx.status_code // null),mediamtx_error:(.mediamtx.error // null)}' 2>/dev/null \
  | redact || true

section "Frame Samples"
i=1
while [ "$i" -le "$SAMPLES" ]; do
  printf 'sample=%s\n' "$i"
  fetch_bridge "/snapshot/$CAMERA.jpg" "/tmp/${CAMERA}-snapshot-$i.jpg"
  fetch_bridge "/img/$CAMERA.jpg" "/tmp/${CAMERA}-img-$i.jpg"
  fetch_plain "$GO2RTC_BASE/api/frame.jpeg?src=$CAMERA" "/tmp/${CAMERA}-go2rtc-main-$i.jpg"
  fetch_plain "$GO2RTC_BASE/api/frame.jpeg?src=${CAMERA}-sd" "/tmp/${CAMERA}-go2rtc-sd-$i.jpg"
  i=$((i + 1))
  if [ "$i" -le "$SAMPLES" ] && [ "$INTERVAL" -gt 0 ]; then
    sleep "$INTERVAL"
  fi
done

section "LAN Reachability"
for ip in $IPS; do
  ping -c 1 -W 1 "$ip" >/dev/null 2>&1 && state=reachable || state=unreachable
  printf '%s\t%s\n' "$ip" "$state"
done
ip_regex=$(printf '%s' "$IPS" | sed 's/ /|/g')
if [ -n "$MAC" ]; then
  ip_regex="$ip_regex|$(printf '%s' "$MAC" | tr -d ':')"
fi
ip neigh show 2>/dev/null | grep -Ei "$ip_regex" || true
REMOTE
} | "$SCRIPT_DIR/ha_ssh.sh" "HA_NY_CAMERA=$CAMERA HA_NY_PROD_SLUG=$PROD_SLUG HA_NY_SAMPLES=$SAMPLES HA_NY_INTERVAL=$INTERVAL HA_NY_IPS='$IPS' HA_NY_MAC=$MAC HA_NY_BRIDGE_BASE=$BRIDGE_BASE HA_NY_GO2RTC_BASE=$GO2RTC_BASE sh -s"
