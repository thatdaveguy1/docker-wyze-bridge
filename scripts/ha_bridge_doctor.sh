#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

REQUESTED_PROD_SLUG="${HA_PROD_ADDON_SLUG:-}"
PROD_SLUG="${HA_PROD_ADDON_SLUG:-wyze_bridge_v4}"
DEV_SLUG="${HA_DEV_ADDON_SLUG:-wyze_bridge_local}"
BRIDGE_BASE="${HA_BRIDGE_DOCTOR_BRIDGE_BASE:-http://192.0.2.10:5000}"
FRIGATE_BASE="${HA_BRIDGE_DOCTOR_FRIGATE_BASE:-http://frigate:5000}"
LINES="${HA_BRIDGE_DOCTOR_LOG_LINES:-80}"

# Source shared library for validate_slug, section, redact_api_keys
. "$SCRIPT_DIR/ha_bridge_probe.sh"

usage() {
  cat <<EOF
Usage: scripts/ha_bridge_doctor.sh

Runs read-only Home Assistant checks for Wyze Bridge production/dev handoff
state, MediaMTX health, duplicate bridge add-ons, host port visibility, and
Frigate FPS. It does not stop, start, rebuild, reboot, or edit anything.

Environment:
  HA_PROD_ADDON_SLUG            default: active running Wyze Bridge add-on
  HA_DEV_ADDON_SLUG             default: $DEV_SLUG
  HA_BRIDGE_DOCTOR_BRIDGE_BASE  default: $BRIDGE_BASE
  HA_BRIDGE_DOCTOR_FRIGATE_BASE default: $FRIGATE_BASE
  HA_BRIDGE_DOCTOR_LOG_LINES    default: $LINES
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

validate_lines() {
  case "$LINES" in
    ""|*[!0-9]*|0)
      echo "Invalid HA_BRIDGE_DOCTOR_LOG_LINES: use a positive integer." >&2
      exit 1
      ;;
  esac
}

validate_slug "HA_PROD_ADDON_SLUG" "$PROD_SLUG"
validate_slug "HA_DEV_ADDON_SLUG" "$DEV_SLUG"
validate_base_url "HA_BRIDGE_DOCTOR_BRIDGE_BASE" "$BRIDGE_BASE"
validate_base_url "HA_BRIDGE_DOCTOR_FRIGATE_BASE" "$FRIGATE_BASE"
validate_lines

remote() {
  "$SCRIPT_DIR/ha_ssh.sh" "$@"
}

resolve_prod_slug() {
  if [ -n "$REQUESTED_PROD_SLUG" ]; then
    prod_state=$(remote "ha apps --raw-json 2>/dev/null | jq -r --arg slug '$PROD_SLUG' '.data.addons[]? | select(.slug == \$slug) | .state' | head -n1" || true)
    case "$prod_state" in
      running)
        return 0
        ;;
      "")
        echo "Selected HA_PROD_ADDON_SLUG '$PROD_SLUG' was not found in Home Assistant." >&2
        exit 1
        ;;
      *)
        echo "Selected HA_PROD_ADDON_SLUG '$PROD_SLUG' is $prod_state; refusing to treat it as active." >&2
        exit 1
        ;;
    esac
  fi

  active_rows=$(remote 'ha apps --raw-json 2>/dev/null | jq -r ".data.addons[]? | select((.slug|test(\"wyze|bridge\";\"i\")) or (.name|test(\"wyze|bridge\";\"i\"))) | [.slug,.state] | @tsv"' || true)
  active_slug=""
  fallback_slug=""
  while IFS="$(printf '\t')" read -r slug state; do
    [ -n "$slug" ] || continue
    [ "$state" = "running" ] || continue
    case "$slug" in
      "$DEV_SLUG")
        active_slug="$slug"
        break
        ;;
      "$PROD_SLUG")
        [ -z "$active_slug" ] && active_slug="$slug"
        ;;
      *)
        [ -z "$fallback_slug" ] && fallback_slug="$slug"
        ;;
    esac
  done <<EOF
$active_rows
EOF

  if [ -n "$active_slug" ]; then
    PROD_SLUG="$active_slug"
    return 0
  fi
  if [ -n "$fallback_slug" ]; then
    PROD_SLUG="$fallback_slug"
    return 0
  fi

  stopped_rows=$(remote 'ha apps --raw-json 2>/dev/null | jq -r ".data.addons[]? | select((.slug|test(\"wyze|bridge\";\"i\")) or (.name|test(\"wyze|bridge\";\"i\"))) | [.slug,.state] | @tsv"' || true)
  if [ -n "$stopped_rows" ]; then
    echo "No running Wyze Bridge add-on was found. Candidates:" >&2
    printf '%s\n' "$stopped_rows" >&2
  else
    echo "No Wyze Bridge add-on was found in Home Assistant." >&2
  fi
  exit 1
}

resolve_prod_slug

# redact wraps the shared library's redact_api_keys
redact() { redact_api_keys "$@"; }

section "Bridge Add-ons"
remote 'ha apps --raw-json 2>/dev/null | jq -r ".data.addons[]? | select((.slug|test(\"wyze|bridge\";\"i\")) or (.name|test(\"wyze|bridge\";\"i\"))) | [.slug,.name,.state,.repository,.version] | @tsv"' \
  | redact || true

section "Production Health"
remote "curl -fsS --max-time 8 $BRIDGE_BASE/health || true" | redact || true

section "Production Supervisor Metadata"
remote "curl -fsS -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" http://supervisor/addons/$PROD_SLUG/info | jq '{slug:.data.slug,state:.data.state,version:.data.version,repository:.data.repository,host_network:.data.host_network,network:.data.network,option_keys:(.data.options|keys? // [])}'" \
  | redact || true

section "MediaMTX / Bridge Log Tail"
remote "ha apps logs $PROD_SLUG | sed -E 's/api=[^\" ]+/api=<redacted>/g' | grep -E 'MediaMTX|listen tcp|listener opened|empty catalog|alias refresh failed|ready|FILTER ALLOWING' | tail -n $LINES" \
  | redact || true

section "Host Port Visibility"
remote 'for cmd in "ss -ltnp" "netstat -ltnp" "lsof -nP -iTCP:58888 -sTCP:LISTEN" "fuser -n tcp 58888" "docker ps --format {{.ID}}\\t{{.Names}}\\t{{.Status}}\\t{{.Ports}}"; do printf "CMD %s\n" "$cmd"; sh -c "$cmd" 2>&1 | sed -E "s/api=[^\" ]+/api=<redacted>/g" | grep -E "58888|wyze|bridge|mediamtx|not found|Permission|Operation|COMMAND|LISTEN|tcp|docker" | head -n 30 || true; done' \
  | redact || true

section "Host Log Clues"
remote 'ha host logs -n 500 2>/dev/null | sed -E "s/api=[^\" ]+/api=<redacted>/g" | grep -Ei "58888|wyze|mediamtx|docker_wyze|bind|address already|net=host" | tail -n 80 || true' \
  | redact || true

section "Frigate FPS"
remote "curl -fsS --max-time 8 $FRIGATE_BASE/api/stats | jq -r \".cameras | to_entries[] | [.key, .value.camera_fps, .value.process_fps, .value.skipped_fps] | @tsv\" || true" \
  | redact || true
