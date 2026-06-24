#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TMP_DIR="$ROOT_DIR/tmp"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$TMP_DIR/ha_backyard_snapshot_repair_${STAMP}.txt"

SCRYPTED_SLUG="${HA_SCRYPTED_ADDON_SLUG:-09e60fb6_scrypted}"
BACK_YARD_ID="${HA_BACK_YARD_ID:-265}"
SOUTH_DRIVEWAY_ID="${HA_SOUTH_DRIVEWAY_ID:-182}"
MAX_ATTEMPTS="${HA_BACKYARD_REPAIR_MAX_ATTEMPTS:-5}"
SAMPLES="${HA_BACKYARD_REPAIR_SAMPLES:-2}"
INTERVAL="${HA_BACKYARD_REPAIR_INTERVAL_SECONDS:-2}"

mkdir -p "$TMP_DIR"

is_posint() {
  case "$1" in
    ""|*[!0-9]*|0) return 1 ;;
    *) return 0 ;;
  esac
}

if ! is_posint "$BACK_YARD_ID" || ! is_posint "$SOUTH_DRIVEWAY_ID" || ! is_posint "$MAX_ATTEMPTS" || ! is_posint "$SAMPLES"; then
  echo "Invalid numeric input for ids/attempts/samples" >&2
  exit 1
fi

attempt=1
repaired=0

{
  echo "## Back Yard Snapshot Repair"
  echo "scrypted_slug=$SCRYPTED_SLUG"
  echo "back_yard_id=$BACK_YARD_ID"
  echo "south_driveway_id=$SOUTH_DRIVEWAY_ID"
  echo "max_attempts=$MAX_ATTEMPTS"
  echo "samples=$SAMPLES"
  echo "interval_seconds=$INTERVAL"

  while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    echo
    echo "### Attempt $attempt"

    PROBE_OUT="$TMP_DIR/ha_wyze_scrypted_snapshot_probe_repair_attempt_${STAMP}_${attempt}.txt"

    HA_SCRYPTED_ADDON_SLUG="$SCRYPTED_SLUG" \
    HA_WYZE_SCRYPTED_DEVICES="back-yard:$BACK_YARD_ID south-driveway:$SOUTH_DRIVEWAY_ID" \
    HA_WYZE_SCRYPTED_SAMPLES="$SAMPLES" \
    HA_WYZE_SCRYPTED_INTERVAL_SECONDS="$INTERVAL" \
      "$SCRIPT_DIR/ha_wyze_scrypted_snapshot_probe.sh" > "$PROBE_OUT" 2>&1 || true

    back_hash=$(grep '^camera=back-yard sample=1 ' "$PROBE_OUT" | sed -n 's/.*sha256=//p' | head -n1)
    south_hash=$(grep '^camera=south-driveway sample=1 ' "$PROBE_OUT" | sed -n 's/.*sha256=//p' | head -n1)

    echo "back_hash=${back_hash:-<none>}"
    echo "south_hash=${south_hash:-<none>}"
    echo "probe_artifact=$PROBE_OUT"

    if [ -n "${back_hash:-}" ] && [ -n "${south_hash:-}" ] && [ "$back_hash" != "$south_hash" ]; then
      echo "status=diverged"
      repaired=1
      break
    fi

    echo "status=colliding_or_missing"
    echo "action=restart_scrypted"
    "$SCRIPT_DIR/ha_ssh.sh" "ha apps restart $SCRYPTED_SLUG" >/dev/null 2>&1 || true
    sleep 8

    attempt=$((attempt + 1))
  done

  echo
  if [ "$repaired" -eq 1 ]; then
    echo "PASS: Back Yard and South Driveway snapshots diverged."
  else
    echo "FAIL: Snapshot collision persisted after restart attempts."
  fi
} | tee "$OUT"

echo "artifact=$OUT"

[ "$repaired" -eq 1 ]
