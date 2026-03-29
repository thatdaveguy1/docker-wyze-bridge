#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -f "$SCRIPT_DIR/.ha_ssh.env" ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/.ha_ssh.env"
fi

DEV_SLUG="${HA_DEV_ADDON_SLUG:-local_docker_wyze_bridge_local}"
PROD_SLUG="${HA_PROD_ADDON_SLUG:-0eb0428f_docker_wyze_bridge_v4}"
DEV_WEB_PORT="${HA_DEV_WEB_PORT:-55000}"
TARGET="auto"
STREAM=""
INCLUDE_NETWORK=0

usage() {
  cat <<EOF
Usage: scripts/ha_bridge_diag.sh [--target auto|dev|prod] [--stream stream-uri] [--network]

Queries the active bridge add-on through its Flask diagnostics endpoint so
WHEP proxy and MediaMTX checks run inside the add-on namespace.

Examples:
  scripts/ha_bridge_diag.sh
  scripts/ha_bridge_diag.sh --stream dog-run
  scripts/ha_bridge_diag.sh --target dev --stream north-yard
  scripts/ha_bridge_diag.sh --target dev --stream north-yard --network
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --stream)
      STREAM="${2:-}"
      shift 2
      ;;
    --network)
      INCLUDE_NETWORK=1
      shift 1
      ;;
    --help|-h|help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
done

ha_apps() {
  "$SCRIPT_DIR/ha_ssh.sh" ha apps "$@"
}

addon_field() {
  slug="$1"
  field="$2"
  info=$(ha_apps info "$slug" --raw-json)
  printf '%s' "$info" | python3 -c 'import json,sys; data=json.load(sys.stdin).get("data", {}); value=data.get(sys.argv[1], ""); print(value if value is not None else "")' "$field"
}

addon_web_port() {
  slug="$1"
  if [ "$slug" = "$DEV_SLUG" ]; then
    printf '%s\n' "$DEV_WEB_PORT"
    return 0
  fi
  info=$(ha_apps info "$slug" --raw-json)
  ADDON_INFO_JSON="$info" python3 - <<'PY'
import json
import os

root = json.loads(os.environ["ADDON_INFO_JSON"]).get("data", {})
network = root.get("network") or {}
descriptions = root.get("network_description") or {}

for key, host_port in network.items():
    if "Web UI" in descriptions.get(key, ""):
        print(host_port)
        raise SystemExit

fallback = network.get("5000/tcp")
if fallback:
    print(fallback)
    raise SystemExit

print("5000")
PY
}

resolve_slug() {
  case "$TARGET" in
    dev)
      printf '%s\n' "$DEV_SLUG"
      ;;
    prod)
      printf '%s\n' "$PROD_SLUG"
      ;;
    auto)
      dev_state=$(addon_field "$DEV_SLUG" state 2>/dev/null || printf '')
      prod_state=$(addon_field "$PROD_SLUG" state 2>/dev/null || printf '')
      if [ "$dev_state" = "started" ]; then
        printf '%s\n' "$DEV_SLUG"
      elif [ "$prod_state" = "started" ]; then
        printf '%s\n' "$PROD_SLUG"
      else
        printf '%s\n' "$PROD_SLUG"
      fi
      ;;
    *)
      echo "Unknown target: $TARGET" >&2
      exit 1
      ;;
  esac
}

addon_base_url() {
  slug="$1"
  ip=$(addon_field "$slug" ip_address 2>/dev/null || printf '')
  port=$(addon_web_port "$slug" 2>/dev/null || printf '5000')
  if [ -z "$ip" ]; then
    echo "Could not determine IP address for add-on $slug." >&2
    exit 1
  fi
  printf 'http://%s:%s' "$ip" "$port"
}

slug=$(resolve_slug)
base_url=$(addon_base_url "$slug")
diag_url="$base_url/health/details"
if [ -n "$STREAM" ]; then
  stream_q=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$STREAM")
  diag_url="$diag_url?stream=$stream_q"
fi
if [ "$INCLUDE_NETWORK" -eq 1 ]; then
  if [ -n "$STREAM" ]; then
    diag_url="$diag_url&network=1"
  else
    diag_url="$diag_url?network=1"
  fi
fi

escaped_url=$(printf '%s' "$diag_url" | sed 's/&/\\&/g')
scripts/ha_ssh.sh curl -fsS "$escaped_url" | python3 -m json.tool
