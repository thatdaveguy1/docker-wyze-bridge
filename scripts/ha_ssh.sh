#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$SCRIPT_DIR/.ha_ssh.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

. "$ENV_FILE"

if [ $# -eq 0 ]; then
  echo "Usage: scripts/ha_ssh.sh <remote command>" >&2
  exit 1
fi

KEY_PATH=$(eval printf '%s' "$HA_SSH_KEY")
exec ssh -p "$HA_SSH_PORT" -i "$KEY_PATH" "$HA_SSH_TARGET" "$@"
