#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TMP_DIR="$ROOT_DIR/tmp"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$TMP_DIR/ha_wyze_camera_matrix_probe_${STAMP}.txt"

PROD_SLUG="${HA_PROD_ADDON_SLUG:-wyze_bridge_v4}"
CAMERAS="${HA_WYZE_CAMERAS:-}"
SAMPLES="${HA_WYZE_MATRIX_SAMPLES:-3}"
INTERVAL="${HA_WYZE_MATRIX_INTERVAL_SECONDS:-10}"
BRIDGE_BASE="${HA_WYZE_BRIDGE_BASE:-http://192.0.2.10:5000}"
GO2RTC_BASE="${HA_WYZE_GO2RTC_BASE:-http://192.0.2.10:11984}"

# Source shared library for validate_slug, validate_base_url, section, mark_fail,
# redact_api_keys, derive_bridge_token
. "$SCRIPT_DIR/ha_bridge_probe.sh"

usage() {
  cat <<EOF
Usage: scripts/ha_wyze_camera_matrix_probe.sh

Runs a read-only Wyze-only route proof probe from the Home Assistant host. It
collects the authenticated bridge catalog, per-camera stream-config, per-camera
health/details, go2rtc stream-table presence, native alias frame checks, and
repeated bridge/native hash samples over time.

Environment:
  HA_PROD_ADDON_SLUG              default: $PROD_SLUG
  HA_WYZE_CAMERAS                 optional, space/comma-separated camera names
  HA_WYZE_MATRIX_SAMPLES          default: $SAMPLES
  HA_WYZE_MATRIX_INTERVAL_SECONDS default: $INTERVAL
  HA_WYZE_BRIDGE_BASE             default: $BRIDGE_BASE
  HA_WYZE_GO2RTC_BASE             default: $GO2RTC_BASE
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

normalize_cameras() {
  printf '%s' "$1" | tr ',' ' ' | xargs
}

CAMERA_LIST=$(normalize_cameras "$CAMERAS")
if [ -n "$CAMERA_LIST" ]; then
  for camera in $CAMERA_LIST; do
    case "$camera" in
      ""|*[!A-Za-z0-9_.-]*)
        echo "Invalid camera name '$camera': only letters, numbers, '.', '_' and '-' are allowed." >&2
        exit 1
        ;;
    esac
  done
fi

validate_slug "HA_PROD_ADDON_SLUG" "$PROD_SLUG"
validate_number "HA_WYZE_MATRIX_SAMPLES" "$SAMPLES"
validate_number "HA_WYZE_MATRIX_INTERVAL_SECONDS" "$INTERVAL"
validate_base_url "HA_WYZE_BRIDGE_BASE" "$BRIDGE_BASE"
validate_base_url "HA_WYZE_GO2RTC_BASE" "$GO2RTC_BASE"

mkdir -p "$TMP_DIR"

set +e
{
  cat "$SCRIPT_DIR/ha_bridge_probe.sh"
  cat <<'REMOTE'
set -eu

PROD_SLUG="$HA_WYZE_PROD_SLUG"
CAMERAS="$HA_WYZE_CAMERAS"
SAMPLES="$HA_WYZE_SAMPLES"
INTERVAL="$HA_WYZE_INTERVAL"
BRIDGE_BASE="$HA_WYZE_BRIDGE_BASE"
GO2RTC_BASE="$HA_WYZE_GO2RTC_BASE"
FAIL=0
API_TOKEN=""

# redact is defined by ha_bridge_probe.sh as redact_api_keys
redact() { redact_api_keys "$@"; }

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

fetch_bridge_json() {
  route="$1"
  out="$2"
  : > "$out"
  if [ -n "$API_TOKEN" ]; then
    code=$(curl -sS --max-time 15 -H "api: $API_TOKEN" -o "$out" -w '%{http_code}' "$BRIDGE_BASE$route" 2>/dev/null || printf '000')
  else
    code=$(curl -sS --max-time 15 -o "$out" -w '%{http_code}' "$BRIDGE_BASE$route" 2>/dev/null || printf '000')
  fi
  printf '%s' "$code"
}

fetch_plain() {
  url="$1"
  out="$2"
  : > "$out"
  code=$(curl -sS --max-time 15 -o "$out" -w '%{http_code}' "$url" 2>/dev/null || printf '000')
  printf '%s' "$code"
}

INFO=$(curl -fsS -H "Authorization: Bearer $SUPERVISOR_TOKEN" "http://supervisor/addons/$PROD_SLUG/info" 2>/dev/null || true)
if [ -n "$INFO" ]; then
  API_TOKEN=$(derive_bridge_token "$INFO")
fi

section "Wyze Camera Matrix Probe"
echo "prod_slug=$PROD_SLUG"
echo "samples=$SAMPLES"
echo "interval_seconds=$INTERVAL"
echo "bridge_base=$BRIDGE_BASE" | redact
echo "go2rtc_base=$GO2RTC_BASE" | redact
if [ -n "$API_TOKEN" ]; then echo "auth=api-header"; else echo "auth=none"; fi

section "Supervisor Selected Options"
printf '%s\n' "$INFO" \
  | jq -c '{slug:.data.slug,state:.data.state,repository:.data.repository,version:.data.version,SD_ONLY:(.data.options.SD_ONLY // ""),SNAPSHOT:(.data.options.SNAPSHOT // ""),CAM_OPTIONS:(.data.options.CAM_OPTIONS // "")}' 2>/dev/null \
  | redact || true

section "Authenticated Catalog"
api_body="/tmp/wyze-matrix-api.json"
api_code=$(fetch_bridge_json "/api" "$api_body")
camera_count=$(jq -r '(.cameras // {}) | length' "$api_body" 2>/dev/null || echo 0)
printf 'api_status=%s camera_count=%s\n' "$api_code" "$camera_count"
if [ "$api_code" != "200" ] || [ "$camera_count" -le 0 ]; then
  mark_fail "authenticated /api must return a non-empty Wyze camera catalog"
fi

section "go2rtc Stream Table"
streams_body="/tmp/wyze-matrix-go2rtc-streams.json"
streams_code=$(fetch_plain "$GO2RTC_BASE/api/streams" "$streams_body")
printf 'go2rtc_streams_status=%s\n' "$streams_code"
if [ "$streams_code" != "200" ]; then
  mark_fail "go2rtc /api/streams must return HTTP 200"
fi

if [ -n "$CAMERAS" ]; then
  camera_list="$CAMERAS"
else
  camera_list=$(jq -r '(.cameras // {}) | to_entries[] | select((.value.enabled // true) != false) | .key | sub("-sub$"; "")' "$api_body" 2>/dev/null | sort -u | tr '\n' ' ' | xargs)
fi

if [ -z "$camera_list" ]; then
  mark_fail "camera list is empty after catalog filtering"
fi

for camera in $camera_list; do
  section "Camera $camera"

  camera_file="/tmp/${camera}-matrix-camera.json"
  camera_code=$(fetch_bridge_json "/api/$camera" "$camera_file")
  printf 'route=/api/%s status=%s\n' "$camera" "$camera_code"
  if [ "$camera_code" != "200" ]; then
    mark_fail "$camera must return /api/$camera JSON"
    continue
  fi

  config_file="/tmp/${camera}-matrix-config.json"
  config_code=$(fetch_bridge_json "/api/$camera/stream-config" "$config_file")
  printf 'route=/api/%s/stream-config status=%s\n' "$camera" "$config_code"
  if [ "$config_code" != "200" ]; then
    mark_fail "$camera must return stream-config JSON"
    continue
  fi

  summary=$(jq -c --arg camera "$camera" '
    {
      camera: $camera,
      product_model: (.product_model // null),
      connected: (.connected // null),
      enabled: (.enabled // null),
      native_alias: (.native_alias // null),
      native_alias_ready: (.native_alias_ready // null),
      native_selected: (.native_selected // null),
      native_rtsp_url: (.native_rtsp_url // null),
      snapshot_source: (.snapshot_source // null)
    }' "$camera_file" 2>/dev/null || true)
  printf '%s\n' "${summary:-{\"camera\":\"$camera\",\"error\":\"invalid-camera-json\"}}" | redact

  config_summary=$(jq -c --arg camera "$camera" '
    {
      camera: $camera,
      sd_only: (.sd_only // null),
      enabled_feeds: ((.feeds // {}) | to_entries | map(select((.value.enabled // false) == true)) | map(.key)),
      feed_paths: ((.feeds // {}) | with_entries(.value = (.value.path // null))),
      feed_resolutions: ((.feeds // {}) | with_entries(.value = (.value.resolution // null)))
    }' "$config_file" 2>/dev/null || true)
  printf '%s\n' "${config_summary:-{\"camera\":\"$camera\",\"error\":\"invalid-config-json\"}}" | redact

  native_alias=$(jq -r '.native_alias // empty' "$camera_file" 2>/dev/null || true)
  native_selected=$(jq -r '.native_selected // false' "$camera_file" 2>/dev/null || echo false)
  selected_sd_sub=$(jq -r '.feeds.sd.enabled == true and .feeds.sd.path == "sub"' "$config_file" 2>/dev/null || echo false)
  if [ "$selected_sd_sub" = "true" ]; then
    detail_stream="${camera}-sub"
  else
    detail_stream="$camera"
  fi

  details_file="/tmp/${camera}-matrix-details.json"
  details_code=$(fetch_bridge_json "/health/details?stream=$detail_stream" "$details_file")
  printf 'route=/health/details?stream=%s status=%s\n' "$detail_stream" "$details_code" | redact
  if [ "$details_code" != "200" ]; then
    mark_fail "$camera selected bridge stream must return /health/details"
  fi
  details_summary=$(jq -c --arg stream "$detail_stream" '
    {
      stream: $stream,
      whep_reachable: (.whep_proxy.reachable // null),
      whep_status_code: (.whep_proxy.status_code // null),
      whep_upstream_state: (.whep_proxy.upstream_state // null),
      whep_video_ready: (.whep_proxy.video_ready // null),
      whep_audio_ready: (.whep_proxy.audio_ready // null),
      whep_audio_packets_seen: (.whep_proxy.audio_packets_seen // null),
      has_ever_had_media: (.whep_proxy.has_ever_had_media // null),
      go2rtc_aliases: (.go2rtc.aliases // .go2rtc.streams // [])
    }' "$details_file" 2>/dev/null || true)
  printf '%s\n' "${details_summary:-{\"stream\":\"$detail_stream\",\"error\":\"invalid-details-json\"}}" | redact

  alias_present=false
  if [ -n "$native_alias" ] && grep -Fq "\"$native_alias\"" "$streams_body" 2>/dev/null; then
    alias_present=true
  fi
  printf 'native_alias_present=%s native_alias=%s\n' "$alias_present" "${native_alias:-<none>}" | redact
  if [ "$native_selected" = "true" ] && [ -n "$native_alias" ] && [ "$alias_present" != "true" ]; then
    mark_fail "$camera native alias must be present in go2rtc /api/streams when native_selected=true"
  fi

  img_valid_count=0
  native_valid_count=0
  img_hash_1="<none>"
  img_hash_2="<none>"
  native_hash_1="<none>"
  native_hash_2="<none>"
  sample=1
  while [ "$sample" -le "$SAMPLES" ]; do
    img_file="/tmp/${camera}-matrix-img-${sample}.jpg"
    # Force a preview refresh so the probe measures the live preview path
    # rather than a previously cached /img file.
    img_code=$(fetch_bridge_json "/img/$camera.jpg?exp=0" "$img_file")
    img_hash=$(sha_of "$img_file")
    img_mime=$(mime_of "$img_file")
    img_bytes=$(bytes_of "$img_file")
    if [ "$img_code" = "200" ] && [ "${img_bytes:-0}" -gt 1024 ] && is_jpeg_file "$img_file"; then
      img_valid_count=$((img_valid_count + 1))
      if [ "$img_hash_1" = "<none>" ]; then
        img_hash_1="$img_hash"
      elif [ "$img_hash" != "$img_hash_1" ]; then
        img_hash_2="$img_hash"
      fi
    fi
    printf 'camera=%s sample=%s route=/img/%s.jpg?exp=0 code=%s bytes=%s mime=%s sha256=%s\n' \
      "$camera" "$sample" "$camera" "$img_code" "${img_bytes:-0}" "$img_mime" "$img_hash" | redact

    if [ -n "$native_alias" ]; then
      native_file="/tmp/${camera}-matrix-native-${sample}.jpg"
      native_code=$(fetch_plain "$GO2RTC_BASE/api/frame.jpeg?src=$native_alias" "$native_file")
      native_hash=$(sha_of "$native_file")
      native_mime=$(mime_of "$native_file")
      native_bytes=$(bytes_of "$native_file")
      if [ "$native_code" = "200" ] && [ "${native_bytes:-0}" -gt 1024 ] && is_jpeg_file "$native_file"; then
        native_valid_count=$((native_valid_count + 1))
        if [ "$native_hash_1" = "<none>" ]; then
          native_hash_1="$native_hash"
        elif [ "$native_hash" != "$native_hash_1" ]; then
          native_hash_2="$native_hash"
        fi
      fi
      printf 'camera=%s sample=%s route=/api/frame.jpeg?src=%s code=%s bytes=%s mime=%s sha256=%s\n' \
        "$camera" "$sample" "$native_alias" "$native_code" "${native_bytes:-0}" "$native_mime" "$native_hash" | redact
    fi

    sample=$((sample + 1))
    if [ "$sample" -le "$SAMPLES" ] && [ "$INTERVAL" -gt 0 ]; then
      sleep "$INTERVAL"
    fi
  done

  img_unique_hashes=0
  if [ "$img_hash_1" != "<none>" ]; then
    img_unique_hashes=1
  fi
  if [ "$img_hash_2" != "<none>" ] && [ "$img_hash_2" != "$img_hash_1" ]; then
    img_unique_hashes=2
  fi

  native_unique_hashes=0
  if [ "$native_hash_1" != "<none>" ]; then
    native_unique_hashes=1
  fi
  if [ "$native_hash_2" != "<none>" ] && [ "$native_hash_2" != "$native_hash_1" ]; then
    native_unique_hashes=2
  fi

  printf '{"camera":"%s","img_valid_count":%s,"img_unique_hashes":%s,"native_valid_count":%s,"native_unique_hashes":%s}\n' \
    "$camera" "$img_valid_count" "$img_unique_hashes" "$native_valid_count" "$native_unique_hashes"

  if [ "$img_valid_count" -eq 0 ]; then
    mark_fail "$camera bridge /img route must return at least one valid JPEG"
  fi
  if [ "$img_valid_count" -gt 1 ] && [ "$img_unique_hashes" -le 1 ]; then
    mark_fail "$camera bridge /img route must show changing content hash across repeated samples"
  fi
  if [ "$native_selected" = "true" ] && [ -n "$native_alias" ] && [ "$native_valid_count" -eq 0 ]; then
    mark_fail "$camera native alias must return at least one valid JPEG when native_selected=true"
  fi
  if [ "$native_selected" = "true" ] && [ -n "$native_alias" ] && [ "$native_valid_count" -gt 1 ] && [ "$native_unique_hashes" -le 1 ]; then
    mark_fail "$camera native alias must show changing content hash across repeated samples when native_selected=true"
  fi
done

section "Result"
if [ "$FAIL" -eq 0 ]; then
  echo "PASS: Wyze camera matrix probe passed."
else
  echo "FAIL: Wyze camera matrix probe failed."
fi

exit "$FAIL"
REMOTE
} | "$SCRIPT_DIR/ha_ssh.sh" "HA_WYZE_PROD_SLUG=$PROD_SLUG HA_WYZE_CAMERAS='$CAMERA_LIST' HA_WYZE_SAMPLES=$SAMPLES HA_WYZE_INTERVAL=$INTERVAL HA_WYZE_BRIDGE_BASE=$BRIDGE_BASE HA_WYZE_GO2RTC_BASE=$GO2RTC_BASE sh -s" > "$OUT"
rc=$?
set -e

cat "$OUT"
printf 'artifact=%s\n' "$OUT"

exit "$rc"
