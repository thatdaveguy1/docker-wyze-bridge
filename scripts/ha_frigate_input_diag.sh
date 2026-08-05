#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

CAMERAS="${HA_FRIGATE_DIAG_CAMERAS:-}"
FRIGATE_SLUG="${HA_FRIGATE_ADDON_SLUG:-frigate}"
SCRYPTED_SLUG="${HA_SCRYPTED_ADDON_SLUG:-scrypted}"
FRIGATE_BASE="${HA_FRIGATE_DIAG_BASE_URL:-http://frigate:5000}"
LINES="${HA_FRIGATE_DIAG_LOG_LINES:-160}"

# Source shared library for validate_slug, section, redact_api_keys
. "$SCRIPT_DIR/ha_bridge_probe.sh"

usage() {
  cat <<EOF
Usage: HA_FRIGATE_DIAG_CAMERAS="camera-name" scripts/ha_frigate_input_diag.sh

Runs a read-only Home Assistant diagnostic for Frigate/Scrypted RTSP input
health. It prints current Frigate FPS, the named cameras' configured input
paths, Frigate ffprobe results for those exact paths, recent Frigate/Scrypted
log clues, and sanitized add-on state. If HA_FRIGATE_DIAG_CAMERAS is omitted,
it derives the camera list from live Frigate config or stats. It does not
stop, start, rebuild, restart, reboot, or edit anything.

Environment:
  HA_FRIGATE_DIAG_CAMERAS    optional, space/comma-separated Frigate camera names
  HA_FRIGATE_ADDON_SLUG      default: $FRIGATE_SLUG
  HA_SCRYPTED_ADDON_SLUG     default: $SCRYPTED_SLUG
  HA_FRIGATE_DIAG_BASE_URL   default: $FRIGATE_BASE
  HA_FRIGATE_DIAG_LOG_LINES  default: $LINES
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

CAMERA_LIST=""
if [ -n "$CAMERAS" ]; then
  CAMERA_LIST=$(printf '%s' "$CAMERAS" | tr ',' ' ' | xargs)
  for camera in $CAMERA_LIST; do
    case "$camera" in
      ""|*[!A-Za-z0-9_.-]*)
        echo "Invalid camera name '$camera': only letters, numbers, '.', '_' and '-' are allowed." >&2
        exit 1
        ;;
    esac
  done
fi

validate_slug "HA_FRIGATE_ADDON_SLUG" "$FRIGATE_SLUG"
validate_slug "HA_SCRYPTED_ADDON_SLUG" "$SCRYPTED_SLUG"
validate_base_url "HA_FRIGATE_DIAG_BASE_URL" "$FRIGATE_BASE"
validate_number "HA_FRIGATE_DIAG_LOG_LINES" "$LINES"

{
  cat "$SCRIPT_DIR/ha_bridge_probe.sh"
  cat <<'REMOTE'
set -eu

CAMERAS="$HA_FRIGATE_DIAG_CAMERAS"
FRIGATE_SLUG="$HA_FRIGATE_DIAG_SLUG"
SCRYPTED_SLUG="$HA_SCRYPTED_DIAG_SLUG"
FRIGATE_BASE="$HA_FRIGATE_DIAG_BASE"
LINES="$HA_FRIGATE_DIAG_LINES"

# redact extends the shared library's redact_api_keys with rtsp:// credential redaction
redact() {
  redact_api_keys "$@" | sed -E 's#(rtsp://)[^/@[:space:]]+:[^/@[:space:]]+@#\1<redacted>@#g'
}

urlencode() {
  jq -rn --arg v "$1" '$v|@uri'
}

derive_cameras_from_json() {
  printf '%s\n' "$1" \
    | jq -r '.cameras | keys[]?' 2>/dev/null \
    | paste -sd' ' - 2>/dev/null || true
}

section "Frigate/Scrypted Input Diagnostic"
if [ -n "$CAMERAS" ]; then
  CAMERA_SOURCE=explicit
else
  CONFIG=$(curl -fsS --max-time 8 ${FRIGATE_BASE}/api/config 2>/dev/null || true)
  STATS=$(curl -fsS --max-time 8 ${FRIGATE_BASE}/api/stats 2>/dev/null || true)
  CAMERAS=$(derive_cameras_from_json "$CONFIG")
  CAMERA_SOURCE=live_config
  if [ -z "$CAMERAS" ]; then
    CAMERAS=$(derive_cameras_from_json "$STATS")
    CAMERA_SOURCE=live_stats
  fi
fi

if [ -z "$CAMERAS" ]; then
  echo "Unable to derive Frigate cameras from live config or stats." >&2
  exit 1
fi

echo "camera_source=$CAMERA_SOURCE"
echo "cameras=$CAMERAS"
echo "frigate_slug=$FRIGATE_SLUG"
echo "scrypted_slug=$SCRYPTED_SLUG"

section "Current Frigate Stats"
if [ -z "${STATS:-}" ]; then
  STATS=$(curl -fsS --max-time 8 ${FRIGATE_BASE}/api/stats 2>/dev/null || true)
fi
if [ -z "$STATS" ]; then
  echo "<empty>"
else
  printf '%s\n' "$STATS" \
    | jq -r '.cameras | to_entries[] | [.key, .value.camera_fps, .value.process_fps, .value.skipped_fps, (.value.ffmpeg_pid // ""), (.value.capture_pid // "")] | @tsv' \
    | redact
fi

if [ -z "${CONFIG:-}" ]; then
  CONFIG=$(curl -fsS --max-time 8 ${FRIGATE_BASE}/api/config 2>/dev/null || true)
fi

for camera in $CAMERAS; do
  section "Camera $camera Config Inputs"
  paths=""
  if [ -z "$CONFIG" ]; then
    echo "<empty config>"
  else
    printf '%s\n' "$CONFIG" \
      | jq -r --arg camera "$camera" '.cameras[$camera].ffmpeg.inputs[]? | .path as $p | (.roles // [])[] as $r | [$r,$p] | @tsv' \
      | redact
    paths=$(printf '%s\n' "$CONFIG" | jq -r --arg camera "$camera" '.cameras[$camera].ffmpeg.inputs[]?.path' 2>/dev/null || true)
  fi

  section "Camera $camera FFprobe"
  if [ -z "$paths" ]; then
    echo "paths=<none>"
  else
    for path in $paths; do
      printf 'path=%s\n' "$path" | redact
      encoded=$(urlencode "$path")
      body=$(curl -fsS --max-time 20 "${FRIGATE_BASE}/api/ffprobe?paths=$encoded" 2>/dev/null || true)
      if [ -z "$body" ]; then
        echo "ffprobe=<empty>"
      else
        printf '%s\n' "$body" | jq -c . 2>/dev/null | redact || printf '%s\n' "$body" | redact
      fi
    done
  fi

  section "Camera $camera Recent Frigate Logs"
  ha apps logs "$FRIGATE_SLUG" 2>/dev/null \
    | grep -Ei "$camera|Bad Request|Unable to read frames|Ffmpeg process crashed|No new recording segments|skipped" \
    | tail -n "$LINES" \
    | redact || true
done

section "Recent Scrypted RTSP Logs"
ha apps logs "$SCRYPTED_SLUG" 2>/dev/null \
  | grep -Ei 'rebroadcast|rtsp|Bad Request|Unsupported Transport|EADDRINUSE|ECONN|Unable to|error' \
  | tail -n "$LINES" \
  | redact || true

section "Add-on State"
for slug in "$FRIGATE_SLUG" "$SCRYPTED_SLUG"; do
  curl -fsS -H "Authorization: Bearer $SUPERVISOR_TOKEN" "http://supervisor/addons/$slug/info" 2>/dev/null \
    | jq '{slug:.data.slug,state:.data.state,version:.data.version,repository:.data.repository}' \
    | redact || true
done
REMOTE
} | "$SCRIPT_DIR/ha_ssh.sh" "HA_FRIGATE_DIAG_CAMERAS='$CAMERA_LIST' HA_FRIGATE_DIAG_SLUG=$FRIGATE_SLUG HA_SCRYPTED_DIAG_SLUG=$SCRYPTED_SLUG HA_FRIGATE_DIAG_BASE=$FRIGATE_BASE HA_FRIGATE_DIAG_LINES=$LINES sh -s"
