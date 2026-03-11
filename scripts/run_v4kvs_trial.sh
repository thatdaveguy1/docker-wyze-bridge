#!/usr/bin/env bash
set -euo pipefail

HA_BASE_URL="${HA_BASE_URL:-}"
HA_TOKEN="${HA_TOKEN:-}"
ADDON_CANDIDATES=(
  "local_docker_wyze_bridge_v4kvs_trial"
  "docker_wyze_bridge_v4kvs_trial"
)

if [[ -z "$HA_BASE_URL" || -z "$HA_TOKEN" ]]; then
  echo "Missing required environment variables."
  echo "Set HA_BASE_URL (e.g. http://homeassistant.local:8123) and HA_TOKEN (long-lived access token)."
  exit 2
fi

api() {
  local method="$1"
  local path="$2"
  shift 2
  curl -sS -X "$method" \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    "$HA_BASE_URL$path" "$@"
}

pick_addon() {
  local addon
  for addon in "${ADDON_CANDIDATES[@]}"; do
    if api GET "/api/hassio/addons/$addon/info" | grep -q '"result":"ok"'; then
      echo "$addon"
      return 0
    fi
  done
  return 1
}

echo "Checking HA reachability..."
api GET "/api/" >/dev/null

echo "Resolving add-on slug..."
ADDON_SLUG="$(pick_addon || true)"
if [[ -z "${ADDON_SLUG:-}" ]]; then
  echo "Could not resolve add-on slug. Checked: ${ADDON_CANDIDATES[*]}"
  echo "Install the local repo in HA and confirm the trial add-on is visible in Add-on Store."
  exit 3
fi

echo "Using add-on: $ADDON_SLUG"

echo "Installing add-on (idempotent)..."
api POST "/api/hassio/addons/$ADDON_SLUG/install" >/dev/null || true

echo "Starting add-on..."
api POST "/api/hassio/addons/$ADDON_SLUG/start" >/dev/null

echo "Waiting 15s for startup..."
sleep 15

echo "Fetching add-on info..."
api GET "/api/hassio/addons/$ADDON_SLUG/info"

echo
echo "Fetching logs..."
api GET "/api/hassio/addons/$ADDON_SLUG/logs"
