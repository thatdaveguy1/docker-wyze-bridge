#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -f "$SCRIPT_DIR/.ha_ssh.env" ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/.ha_ssh.env"
fi

DEV_SLUG="${HA_DEV_ADDON_SLUG:-docker_wyze_bridge_dev}"
PROD_SLUG="${HA_PROD_ADDON_SLUG:-docker_wyze_bridge_v4}"
PROD_INFO_FILE="$REPO_DIR/.orig_addon_info.json"
DEV_INFO_FILE="$REPO_DIR/.live_local_info.json"

usage() {
  cat <<EOF
Usage: scripts/ha_dev_build.sh <command>

Commands:
  sync                Sync the local dev add-on tree and reload Supervisor metadata
  install             Sync the dev tree and install the dev add-on if needed
  status              Show a concise prod/dev status summary
  capture-prod        Save current production add-on info to $PROD_INFO_FILE
  copy-prod-options   Copy production add-on settings into the dev add-on
  swap-to-dev         Stop prod, sync/rebuild dev, copy prod settings, start dev, smoke-check
  smoke-check         Verify the active bridge responds on health/UI endpoints
  restore-prod        Stop dev and restart production

Environment overrides:
  HA_DEV_ADDON_SLUG   default: $DEV_SLUG
  HA_PROD_ADDON_SLUG  default: $PROD_SLUG
EOF
}

ha_apps() {
  "$SCRIPT_DIR/ha_ssh.sh" ha apps "$@"
}

ha_supervisor() {
  "$SCRIPT_DIR/ha_ssh.sh" ha supervisor "$@"
}

ha_store() {
  "$SCRIPT_DIR/ha_ssh.sh" ha store "$@"
}

remote_supervisor_request() {
  method="$1"
  path="$2"
  "$SCRIPT_DIR/ha_ssh.sh" sh -s -- "$method" "$path" <<'EOF'
set -eu

method="$1"
path="$2"
tmp_body=$(mktemp)
cat >"$tmp_body"

cfg_file="$HOME/.homeassistant.yaml"
endpoint="${SUPERVISOR_ENDPOINT:-}"
token="${SUPERVISOR_TOKEN:-}"

if [ -f "$cfg_file" ]; then
  if [ -z "$endpoint" ]; then
    endpoint=$(sed -n 's/^endpoint:[[:space:]]*//p' "$cfg_file" | tail -n 1 | tr -d '"' | tr -d "'")
  fi
  if [ -z "$token" ]; then
    token=$(sed -n 's/^api-token:[[:space:]]*//p' "$cfg_file" | tail -n 1 | tr -d '"' | tr -d "'")
  fi
fi

if [ -z "$token" ]; then
  echo "Missing Supervisor API token. Set SUPERVISOR_TOKEN remotely or add api-token to ~/.homeassistant.yaml." >&2
  exit 1
fi

endpoint="${endpoint:-supervisor}"
case "$endpoint" in
  *://*) ;;
  *) endpoint="http://$endpoint" ;;
esac

if [ "$method" = "GET" ]; then
  curl -fsSL -X "$method" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    "${endpoint%/}${path}"
else
  curl -fsSL -X "$method" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    --data-binary @"$tmp_body" \
    "${endpoint%/}${path}"
  fi
EOF
}

remote_supervisor_post_json_file() {
  path="$1"
  local_json="$2"
  remote_tmp="/tmp/wyze_bridge_supervisor_payload_$$.json"
  KEY_PATH=$(eval printf '%s' "$HA_SSH_KEY")
  scp -P "${HA_SSH_PORT:-22}" -i "$KEY_PATH" "$local_json" "${HA_SSH_TARGET}:$remote_tmp" >/dev/null
  "$SCRIPT_DIR/ha_ssh.sh" sh -s -- "$path" "$remote_tmp" <<'EOF'
set -eu

path="$1"
tmp_body="$2"

cfg_file="$HOME/.homeassistant.yaml"
endpoint="${SUPERVISOR_ENDPOINT:-}"
token="${SUPERVISOR_TOKEN:-}"

if [ -f "$cfg_file" ]; then
  if [ -z "$endpoint" ]; then
    endpoint=$(sed -n 's/^endpoint:[[:space:]]*//p' "$cfg_file" | tail -n 1 | tr -d '"' | tr -d "'")
  fi
  if [ -z "$token" ]; then
    token=$(sed -n 's/^api-token:[[:space:]]*//p' "$cfg_file" | tail -n 1 | tr -d '"' | tr -d "'")
  fi
fi

if [ -z "$token" ]; then
  echo "Missing Supervisor API token. Set SUPERVISOR_TOKEN remotely or add api-token to ~/.homeassistant.yaml." >&2
  exit 1
fi

endpoint="${endpoint:-supervisor}"
case "$endpoint" in
  *://*) ;;
  *) endpoint="http://$endpoint" ;;
esac

curl -fsSL -X POST \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  --data-binary @"$tmp_body" \
  "${endpoint%/}${path}"
EOF
}

capture_addon_info() {
  slug="$1"
  dest="$2"
  remote_supervisor_request GET "/addons/$slug/info" </dev/null >"$dest"
}

capture_addon_cli_info() {
  slug="$1"
  dest="$2"
  ha_apps info "$slug" --raw-json >"$dest"
}

addon_field() {
  slug="$1"
  field="$2"
  set +e
  info=$(ha_apps info "$slug" --raw-json 2>/dev/null)
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    return $rc
  fi
  printf '%s' "$info" | python3 -c 'import json,sys; data=json.load(sys.stdin).get("data", {}); value=data.get(sys.argv[1], ""); print(value if value is not None else "")' "$field"
}

addon_known() {
  addon_field "$1" name >/dev/null 2>&1
}

addon_installed() {
  [ "$(addon_field "$1" installed 2>/dev/null || printf 'False')" = "True" ]
}

sync_dev() {
  "$SCRIPT_DIR/deploy_ha_local_addon.sh" --target dev --no-rebuild
  store_log=$(mktemp)
  if ha_store reload >"$store_log" 2>&1; then
    printf '%s\n' "Home Assistant store metadata reloaded for local add-ons."
  else
    printf '%s\n' "Store reload failed; continuing to Supervisor metadata reload." >&2
    cat "$store_log" >&2
  fi
  reload_log=$(mktemp)
  if ha_supervisor reload >"$reload_log" 2>&1; then
    printf '%s\n' "Dev add-on source synced and Supervisor metadata reloaded."
    return 0
  fi

  if addon_known "$DEV_SLUG"; then
    printf '%s\n' "Dev add-on source synced. Supervisor metadata reload is unavailable on this HA, so the existing indexed dev slot will be reused." >&2
    cat "$reload_log" >&2
    return 0
  fi

  cat "$reload_log" >&2
  exit 1
}

install_dev() {
  sync_dev
  if addon_installed "$DEV_SLUG"; then
    printf '%s\n' "Dev add-on is already installed."
    return 0
  fi
  if ! addon_known "$DEV_SLUG"; then
    echo "Dev add-on is not indexed in Home Assistant yet. Either restore Supervisor reload support or point HA_DEV_ADDON_SLUG at an existing local add-on slot." >&2
    exit 1
  fi
  ha_apps install "$DEV_SLUG"
  printf '%s\n' "Dev add-on installed: $DEV_SLUG"
}

capture_prod() {
  capture_addon_info "$PROD_SLUG" "$PROD_INFO_FILE"
  printf '%s\n' "Saved production add-on info to $PROD_INFO_FILE"
}

copy_prod_options() {
  capture_prod
  payload_file=$(mktemp)
  python3 - "$PROD_INFO_FILE" >"$payload_file" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    root = json.load(handle)

data = root.get("data", {})
payload = data.get("options")

if not payload:
    raise SystemExit("Production add-on info did not include an options payload to copy.")

json.dump({"options": payload}, sys.stdout, separators=(",", ":"))
PY
  remote_supervisor_post_json_file "/addons/$DEV_SLUG/options" "$payload_file" >/dev/null
  printf '%s\n' "Copied production settings into $DEV_SLUG"
}

status() {
  prod_state=$(addon_field "$PROD_SLUG" state 2>/dev/null || printf 'unavailable')
  prod_installed=$(addon_field "$PROD_SLUG" installed 2>/dev/null || printf 'False')
  dev_state=$(addon_field "$DEV_SLUG" state 2>/dev/null || printf 'unavailable')
  dev_installed=$(addon_field "$DEV_SLUG" installed 2>/dev/null || printf 'False')
  cat <<EOF
Production:
  slug: $PROD_SLUG
  installed: $prod_installed
  state: $prod_state
Dev:
  slug: $DEV_SLUG
  installed: $dev_installed
  state: $dev_state
EOF
}

smoke_check() {
  capture_addon_cli_info "$DEV_SLUG" "$DEV_INFO_FILE"
  dev_state=$(python3 - "$DEV_INFO_FILE" <<'PY'
import json,sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    print(json.load(handle).get("data", {}).get("state", "unknown"))
PY
)
  if [ "$dev_state" != "started" ]; then
    echo "Dev add-on is not started (state=$dev_state)." >&2
    exit 1
  fi
  "$SCRIPT_DIR/ha_ssh.sh" curl -fsS http://127.0.0.1:5000/health >/dev/null
  "$SCRIPT_DIR/ha_ssh.sh" curl -fsS http://127.0.0.1:5000/ >/dev/null
  "$SCRIPT_DIR/ha_ssh.sh" curl -fsS http://127.0.0.1:5000/static/site.js | grep -q "copyToClipboard"
  printf '%s\n' "Smoke check passed: health endpoint, Web UI, and site.js responded."
}

stop_addon_if_started() {
  slug="$1"
  state=$(addon_field "$slug" state 2>/dev/null || printf 'unavailable')
  if [ "$state" = "started" ]; then
    ha_apps stop "$slug" >/dev/null
  fi
}

swap_to_dev() {
  capture_prod
  install_dev
  copy_prod_options
  stop_addon_if_started "$DEV_SLUG"
  "$SCRIPT_DIR/deploy_ha_local_addon.sh" --target dev
  prod_was_started=$(addon_field "$PROD_SLUG" state 2>/dev/null || printf 'unavailable')
  rollback() {
    status=$?
    trap - EXIT INT TERM
    if [ $status -ne 0 ] && [ "$prod_was_started" = "started" ]; then
      echo "swap-to-dev failed; attempting to restore production..." >&2
      set +e
      stop_addon_if_started "$DEV_SLUG"
      ha_apps start "$PROD_SLUG" >/dev/null
      set -e
    fi
    exit $status
  }
  trap rollback EXIT INT TERM
  stop_addon_if_started "$PROD_SLUG"
  ha_apps start "$DEV_SLUG" >/dev/null
  smoke_check
  trap - EXIT INT TERM
  printf '%s\n' "Dev build is active. Production remains stopped until restore-prod is run."
}

restore_prod() {
  stop_addon_if_started "$DEV_SLUG"
  ha_apps start "$PROD_SLUG" >/dev/null
  capture_addon_cli_info "$PROD_SLUG" "$PROD_INFO_FILE"
  prod_state=$(python3 - "$PROD_INFO_FILE" <<'PY'
import json,sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    print(json.load(handle).get("data", {}).get("state", "unknown"))
PY
)
  if [ "$prod_state" != "started" ]; then
    echo "Production add-on did not return to started state (state=$prod_state)." >&2
    exit 1
  fi
  "$SCRIPT_DIR/ha_ssh.sh" curl -fsS http://127.0.0.1:5000/health >/dev/null
  "$SCRIPT_DIR/ha_ssh.sh" curl -fsS http://127.0.0.1:5000/ >/dev/null
  printf '%s\n' "Production add-on restarted."
}

if [ $# -ne 1 ]; then
  usage >&2
  exit 1
fi

case "$1" in
  sync)
    sync_dev
    ;;
  install)
    install_dev
    ;;
  status)
    status
    ;;
  capture-prod)
    capture_prod
    ;;
  copy-prod-options)
    copy_prod_options
    ;;
  swap-to-dev)
    swap_to_dev
    ;;
  smoke-check)
    smoke_check
    ;;
  restore-prod)
    restore_prod
    ;;
  --help|-h|help)
    usage
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
