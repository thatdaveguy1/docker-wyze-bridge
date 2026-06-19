# ha_bridge_probe.sh — shared HA bridge probe library
#
# This file is NOT executable. It defines functions that are either:
#   1. Sourced locally:  . "$SCRIPT_DIR/ha_bridge_probe.sh"
#   2. Cat'd into a remote SSH heredoc:
#        { cat "$SCRIPT_DIR/ha_bridge_probe.sh"; cat <<'REMOTE' ... REMOTE; } | ha_ssh.sh
#
# Functions: section, mark_fail, redact_api_keys, derive_bridge_token,
#            validate_slug, validate_base_url, bool_true

section() {
  printf '\n## %s\n' "$1"
}

mark_fail() {
  echo "FAIL: $1"
  FAIL=1
}

redact_api_keys() {
  sed -E 's/api=[^" ]+/api=<redacted>/g'
}

# derive_bridge_token <supervisor_info_json>
# Extracts WB_API from Supervisor add-on info, or derives it from WYZE_EMAIL.
# Prints the token to stdout (empty if neither is available).
derive_bridge_token() {
  _info="$1"
  _stored_api=$(printf '%s\n' "$_info" | jq -r '.data.options.WB_API // .data.options.wb_api // ""' 2>/dev/null || true)
  _wyze_email=$(printf '%s\n' "$_info" | jq -r '.data.options.WYZE_EMAIL // ""' 2>/dev/null || true)
  if [ -n "$_stored_api" ] && [ "$_stored_api" != "null" ]; then
    printf '%s' "$_stored_api"
  elif [ -n "$_wyze_email" ] && [ "$_wyze_email" != "null" ]; then
    printf '%s' "$_wyze_email" | sha256sum | awk '{print $1}' | xxd -r -p | base64 | tr '+/' '-_' | tr -d '=\n' | cut -c1-40
  fi
}

# validate_slug <name> <value>
# Exits 1 if value contains anything other than letters, digits, _ or -.
validate_slug() {
  _name="$1"
  _value="$2"
  case "$_value" in
    ""|*[!A-Za-z0-9_-]*)
      echo "Invalid $_name: only letters, numbers, '_' and '-' are allowed." >&2
      exit 1
      ;;
  esac
}

# validate_base_url <name> <value>
# Exits 1 if value is not a simple http(s) URL without a path or query string.
validate_base_url() {
  _name="$1"
  _value="$2"
  case "$_value" in
    http://*) _host="${_value#http://}" ;;
    https://*) _host="${_value#https://}" ;;
    *)
      echo "Invalid $_name: use a simple http(s) URL without a path." >&2
      exit 1
      ;;
  esac
  case "$_host" in
    ""|*/*|*\?*|*\&*|*\=*|*[!A-Za-z0-9_.:-]*)
      echo "Invalid $_name: use a simple http(s) URL without a path." >&2
      exit 1
      ;;
  esac
}

# validate_number <name> <value>
# Exits 1 if value contains anything other than digits.
validate_number() {
  _name="$1"
  _value="$2"
  case "$_value" in
    ""|*[!0-9]*)
      echo "Invalid $_name: only digits are allowed." >&2
      exit 1
      ;;
  esac
}

# bool_true <value>
# Returns 0 if value is true/1/yes/on (case-insensitive), 1 otherwise.
bool_true() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}
