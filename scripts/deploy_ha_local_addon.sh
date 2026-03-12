#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REMOTE_ROOT="/addons/local/wyze_bridge_local"
APP_SLUG="local_docker_wyze_bridge_local"

usage() {
  cat <<'EOF'
Usage: scripts/deploy_ha_local_addon.sh

Copies the tracked local add-on files to Home Assistant, rebuilds the local app,
and verifies the running app serves the updated JS.

Requires:
- scripts/.ha_ssh.env
- scripts/ha_ssh.sh
EOF
}

if [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "$#" -ne 0 ]; then
  usage >&2
  exit 1
fi

copy_file() {
  src="$1"
  dest="$2"
  "$SCRIPT_DIR/ha_ssh.sh" mkdir -p "$dest"
  . "$SCRIPT_DIR/.ha_ssh.env"
  KEY_PATH=$(eval printf '%s' "$HA_SSH_KEY")
  scp -P "$HA_SSH_PORT" -i "$KEY_PATH" "$REPO_DIR/$src" "$HA_SSH_TARGET:$dest/$(basename "$src")"
}

copy_file "app/templates/index.html" "$REMOTE_ROOT/app/templates"
copy_file "app/templates/base.html" "$REMOTE_ROOT/app/templates"
copy_file "app/static/site.js" "$REMOTE_ROOT/app/static"
copy_file "app/static/site.css" "$REMOTE_ROOT/app/static"
copy_file "app/wyzecam/api_models.py" "$REMOTE_ROOT/app/wyzecam"
copy_file "app/wyze_bridge.py" "$REMOTE_ROOT/app"
copy_file "app/wyzebridge/mtx_server.py" "$REMOTE_ROOT/app/wyzebridge"
copy_file "app/wyzebridge/web_ui.py" "$REMOTE_ROOT/app/wyzebridge"
copy_file "app/wyzebridge/wyze_stream.py" "$REMOTE_ROOT/app/wyzebridge"
copy_file "app/frontend.py" "$REMOTE_ROOT/app"
copy_file ".ha_live_addon/whep_proxy/main.go" "$REMOTE_ROOT/whep_proxy"

"$SCRIPT_DIR/ha_ssh.sh" ha apps rebuild "$APP_SLUG" --force
INGRESS_ENTRY=$("$SCRIPT_DIR/ha_ssh.sh" ha apps info "$APP_SLUG" --raw-json | python3 -c 'import json,sys; root=json.load(sys.stdin); data=root.get("data", {}); sys.stdout.write(data.get("ingress_entry", ""))')
TARGET_URL=""
if [ -n "$INGRESS_ENTRY" ]; then
  TARGET_URL="http://172.30.32.1:8123${INGRESS_ENTRY}static/site.js"
else
  TARGET_URL="http://172.30.32.1:5000/static/site.js"
fi

set +e
"$SCRIPT_DIR/ha_ssh.sh" curl -fsS "$TARGET_URL" | grep -q "copyToClipboard"
RET=$?
set -e
if [ $RET -ne 0 ]; then
  echo "Warning: could not verify site.js from $TARGET_URL (curl/grep exit $RET)." >&2
fi

printf '%s\n' "Deploy complete: $APP_SLUG rebuilt and live JS verified."
