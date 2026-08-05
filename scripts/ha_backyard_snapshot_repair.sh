#!/bin/sh
set -eu

# Back Yard snapshot repair — probes the back-yard Scrypted snapshot device
# and only restarts Scrypted when run with --repair and snapshots are missing
# or invalid.
# (Previously compared back-yard vs south-driveway for collision detection,
# but south_driveway was unplugged 2026-06-27 and removed 2026-06-29.)

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TMP_DIR="$ROOT_DIR/tmp"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$TMP_DIR/ha_backyard_snapshot_repair_${STAMP}.txt"

SCRYPTED_SLUG="${HA_SCRYPTED_ADDON_SLUG:-scrypted}"
BACK_YARD_ID="${HA_BACK_YARD_ID:-}"
MAX_ATTEMPTS="${HA_BACKYARD_REPAIR_MAX_ATTEMPTS:-5}"
SAMPLES="${HA_BACKYARD_REPAIR_SAMPLES:-2}"
INTERVAL="${HA_BACKYARD_REPAIR_INTERVAL_SECONDS:-2}"
REPAIR=0

mkdir -p "$TMP_DIR"

is_posint() {
  case "$1" in
    ""|*[!0-9]*|0) return 1 ;;
    *) return 0 ;;
  esac
}

usage() {
  cat <<EOF
Usage: scripts/ha_backyard_snapshot_repair.sh [--repair]

Without --repair, the script is probe-only and never restarts Scrypted.
With --repair, it will retry and may restart Scrypted between attempts.

Environment:
  HA_SCRYPTED_ADDON_SLUG            default: $SCRYPTED_SLUG
  HA_BACK_YARD_ID                   required, Scrypted device id for the back-yard camera
  HA_BACKYARD_REPAIR_MAX_ATTEMPTS   default: $MAX_ATTEMPTS
  HA_BACKYARD_REPAIR_SAMPLES        default: $SAMPLES
  HA_BACKYARD_REPAIR_INTERVAL_SECONDS default: $INTERVAL
EOF
}

case "${1:-}" in
  --repair)
    REPAIR=1
    shift
    ;;
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

if [ "$#" -ne 0 ]; then
  usage >&2
  exit 1
fi

if [ -z "$BACK_YARD_ID" ]; then
  echo "HA_BACK_YARD_ID is required: set it to the Scrypted device id for the back-yard camera." >&2
  exit 1
fi

if ! is_posint "$BACK_YARD_ID" || ! is_posint "$MAX_ATTEMPTS" || ! is_posint "$SAMPLES"; then
  echo "Invalid numeric input for ids/attempts/samples" >&2
  exit 1
fi

attempt=1
repaired=0
effective_attempts="$MAX_ATTEMPTS"
if [ "$REPAIR" -ne 1 ]; then
  effective_attempts=1
fi

{
  echo "## Back Yard Snapshot Repair"
  echo "scrypted_slug=$SCRYPTED_SLUG"
  echo "back_yard_id=$BACK_YARD_ID"
  echo "max_attempts=$MAX_ATTEMPTS"
  echo "samples=$SAMPLES"
  echo "interval_seconds=$INTERVAL"
  echo "repair_enabled=$REPAIR"

  while [ "$attempt" -le "$effective_attempts" ]; do
    echo
    echo "### Attempt $attempt"

    PROBE_OUT="$TMP_DIR/ha_wyze_scrypted_snapshot_probe_repair_attempt_${STAMP}_${attempt}.txt"

    HA_SCRYPTED_ADDON_SLUG="$SCRYPTED_SLUG" \
    HA_WYZE_SCRYPTED_DEVICES="back-yard:$BACK_YARD_ID" \
    HA_WYZE_SCRYPTED_SAMPLES="$SAMPLES" \
    HA_WYZE_SCRYPTED_INTERVAL_SECONDS="$INTERVAL" \
      "$SCRIPT_DIR/ha_wyze_scrypted_snapshot_probe.sh" > "$PROBE_OUT" 2>&1 || true

    back_hash=$(grep '^camera=back-yard sample=1 ' "$PROBE_OUT" | sed -n 's/.*sha256=//p' | head -n1)

    echo "back_hash=${back_hash:-<none>}"
    echo "probe_artifact=$PROBE_OUT"

    if [ -n "${back_hash:-}" ]; then
      echo "status=ok"
      repaired=1
      break
    fi

    if [ "$REPAIR" -ne 1 ]; then
      echo "status=probe_only"
      break
    fi

    echo "status=missing"
    echo "action=restart_scrypted"
    "$SCRIPT_DIR/ha_ssh.sh" "ha apps restart $SCRYPTED_SLUG" >/dev/null 2>&1 || true
    sleep 8

    attempt=$((attempt + 1))
  done

  echo
  if [ "$repaired" -eq 1 ]; then
    echo "PASS: Back Yard snapshot returned a valid hash."
  else
    echo "FAIL: Back Yard snapshot missing after restart attempts."
  fi
} | tee "$OUT"

echo "artifact=$OUT"

[ "$repaired" -eq 1 ]
