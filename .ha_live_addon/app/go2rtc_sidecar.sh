#!/bin/sh

SCRIPT_DIR=$(dirname "$0" 2>/dev/null || echo /app)
HELPERS_PYTHONPATH="${SCRIPT_DIR}/wyzebridge"

get_env_value() {
    var_name=$1
    eval "printf '%s' \"\${${var_name}:-}\""
}

set_env_if_empty_from_file() {
    var_name=$1
    file_path=$2
    if [ -n "$(get_env_value "${var_name}")" ] || [ ! -f "${file_path}" ]; then
        return
    fi
    value=$(cat "${file_path}" 2>/dev/null)
    export "${var_name}=${value}"
}

set_env_if_empty_from_options_json() {
    var_name=$1
    if [ -n "$(get_env_value "${var_name}")" ] || [ ! -f /data/options.json ]; then
        return
    fi
    value=$(PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import get_ha_option; print(get_ha_option('${var_name}'))" 2>/dev/null || echo "")
    export "${var_name}=${value}"
}

load_go2rtc_runtime_env() {
    for key in WYZE_EMAIL WYZE_PASSWORD API_ID API_KEY; do
        set_env_if_empty_from_file "${key}" "/run/secrets/${key}"
        set_env_if_empty_from_options_json "${key}"
    done

    for key in WB_IP DOMAIN WB_RTSP_URL WB_WEBRTC_URL WB_HLS_URL GO2RTC_LAN_IP_OVERRIDES GO2RTC_FORCE_LAN_IP_OVERRIDES; do
        set_env_if_empty_from_options_json "${key}"
    done
}

go2rtc_sidecar_cleanup() {
    GO2RTC_CLEANUP_PID="${GO2RTC_PID:-}"
    if [ -f /tmp/go2rtc.pid ]; then
        GO2RTC_CLEANUP_PID=$(cat /tmp/go2rtc.pid 2>/dev/null)
    fi
    if [ -n "${GO2RTC_CLEANUP_PID}" ]; then
        kill "${GO2RTC_CLEANUP_PID}" 2>/dev/null || true
    fi
}

start_go2rtc_process() {
    nohup sh -c '
child=""
stop() {
    if [ -n "${child}" ]; then
        kill "${child}" 2>/dev/null || true
        wait "${child}" 2>/dev/null || true
    fi
    exit 0
}
trap stop TERM INT
while :; do
    go2rtc -config "${GO2RTC_CONFIG}" >> /tmp/go2rtc.log 2>&1 &
    child=$!
    wait "${child}"
    status=$?
    child=""
    echo "[GO2RTC] process exited status=${status}; restarting in 2s" >> /tmp/go2rtc.log
    sleep 2 &
    child=$!
    wait "${child}"
    child=""
done
' </dev/null >/dev/null 2>&1 &
    GO2RTC_PID=$!
    printf '%s\n' "${GO2RTC_PID}" > "${GO2RTC_PID_FILE}"
}

normalize_go2rtc_config() {
    if [ ! -f "${GO2RTC_CONFIG}" ]; then
        return
    fi

    PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import rewrite_go2rtc_config; rewrite_go2rtc_config(__import__('os').environ['GO2RTC_CONFIG'], __import__('os').environ['GO2RTC_API_PORT'], __import__('os').environ['GO2RTC_RTSP_PORT'])"
}

preload_go2rtc_aliases() {
    if [ -z "${GO2RTC_API_BASE}" ]; then
        return
    fi

    python3 -c "import sys; sys.path.insert(0, '${HELPERS_PYTHONPATH}'); from go2rtc_sidecar_helpers import extract_yaml_aliases; extract_yaml_aliases(__import__('os').environ['GO2RTC_CONFIG'])" | while IFS= read -r alias; do
        [ -n "${alias}" ] || continue
        echo "[GO2RTC] Preloading native alias ${alias}" >&2
        curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
    done

    for attempt in 1 2 3 4 5; do
        sleep 2
        ready=$(curl -sf -X OPTIONS "${GO2RTC_API_BASE}/api/streams" 2>/dev/null | PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import list_active_producers; print(list_active_producers())" 2>/dev/null || true)
        echo "[GO2RTC] Native preload readiness attempt ${attempt}/5 active=${ready:-none}" >&2
    done
}

start_go2rtc_preload_refresh_loop() {
    (
        while :; do
            sleep 60
            preload_go2rtc_aliases
        done
    ) &
}

rtsp_describe_ok() {
    # Probe the RTSP server with a DESCRIBE request using Python (nc is not
    # available in the container). Returns 0 if the server responds with
    # "200 OK" within 5 seconds, 1 otherwise. This catches the case where
    # the go2rtc API shows producers alive but the RTSP server is wedged.
    alias="$1"
    python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('127.0.0.1', ${GO2RTC_RTSP_PORT}))
    req = 'DESCRIBE rtsp://127.0.0.1:${GO2RTC_RTSP_PORT}/${alias} RTSP/1.0\r\nCSeq: 1\r\n\r\n'
    s.sendall(req.encode())
    data = s.recv(256).decode('utf-8', errors='replace')
    s.close()
    if '200 OK' in data.split('\r\n')[0]:
        sys.exit(0)
    sys.exit(1)
except Exception:
    try:
        s.close()
    except Exception:
        pass
    sys.exit(1)
" 2>/dev/null
}

start_go2rtc_health_monitor() {
    # Monitor go2rtc streams every 15s. Two checks:
    # 1. Producer check: if producers drop to 0 for >30s, force-restart the stream.
    # 2. RTSP wedge check: if the API shows producers but RTSP DESCRIBE fails for
    #    >60s, restart the entire go2rtc process (the RTSP server can wedge after
    #    producer reconnections while the API still reports healthy producers).
    (
        dead_since=""
        wedge_since=0
        while :; do
            sleep 15
            if [ -z "${GO2RTC_API_BASE}" ]; then
                continue
            fi
            streams_json=$(curl -sf "${GO2RTC_API_BASE}/api/streams" 2>/dev/null || echo "")
            if [ -z "${streams_json}" ]; then
                continue
            fi
            # Get list of expected aliases from config (standalone parser —
            # avoid importing go2rtc_sidecar_helpers to prevent circular imports)
            aliases=$(python3 -c "
import os
path = os.environ.get('GO2RTC_CONFIG', '')
if not path:
    raise SystemExit
in_streams = False
for line in open(path, encoding='utf-8'):
    line = line.rstrip('\n')
    if line == 'streams:':
        in_streams = True
        continue
    if in_streams and line and not line.startswith((' ', '\t')):
        break
    if not in_streams:
        continue
    if line.startswith('  ') and line.rstrip().endswith(':') and not line.startswith('    '):
        print(line.strip()[:-1])
" 2>/dev/null || echo "")
            now=$(date +%s)

            # Pick the first alias with an active producer for the RTSP wedge probe
            wedge_probe_alias=""

            for alias in ${aliases}; do
                [ -n "${alias}" ] || continue
                producer_count=$(printf '%s' "${streams_json}" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    s = data.get('${alias}', {})
    p = s.get('producers') or []
    print(len(p))
except Exception:
    print(0)
" 2>/dev/null || echo "0")
                if [ "${producer_count}" = "0" ]; then
                    # Stream is dead — producer-level recovery
                    key="${alias}"
                    last=$(printf '%s' "${dead_since}" | grep "^${key}=" | cut -d= -f2)
                    if [ -z "${last}" ]; then
                        last=0
                    fi
                    if [ "${last}" = "0" ]; then
                        echo "[GO2RTC_HEALTH] ${alias}: producer dropped, monitoring for recovery" >&2
                        last=${now}
                        dead_since="${dead_since}${key}=${last}\n"
                    elif [ $((now - last)) -gt 30 ]; then
                        echo "[GO2RTC_HEALTH] ${alias}: dead for >30s, forcing restart" >&2
                        # Stop the stream
                        curl -sf -X POST "${GO2RTC_API_BASE}/api/streams?src=&dst=${alias}" >/dev/null 2>&1 || true
                        sleep 2
                        # Preload to restart
                        curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                        # Reset dead timer
                        dead_since=$(printf '%s' "${dead_since}" | grep -v "^${key}=")
                        dead_since="${dead_since}${key}=0\n"
                        echo "[GO2RTC_HEALTH] ${alias}: restart triggered" >&2
                    fi
                else
                    # Stream is alive — clear dead timer
                    was_dead=$(printf '%s' "${dead_since}" | grep "^${alias}=" | cut -d= -f2)
                    if [ -n "${was_dead}" ] && [ "${was_dead}" != "0" ]; then
                        echo "[GO2RTC_HEALTH] ${alias}: recovered after $((now - was_dead))s" >&2
                    fi
                    dead_since=$(printf '%s' "${dead_since}" | grep -v "^${alias}=")
                    # Use this alias for the RTSP wedge probe if we haven't picked one yet
                    if [ -z "${wedge_probe_alias}" ]; then
                        wedge_probe_alias="${alias}"
                    fi
                fi
            done

            # RTSP wedge detection: probe the RTSP server with a DESCRIBE request
            if [ -n "${wedge_probe_alias}" ]; then
                if rtsp_describe_ok "${wedge_probe_alias}"; then
                    if [ "${wedge_since}" -ne 0 ]; then
                        echo "[GO2RTC_HEALTH] RTSP server recovered after $((now - wedge_since))s" >&2
                    fi
                    wedge_since=0
                else
                    if [ "${wedge_since}" -eq 0 ]; then
                        echo "[GO2RTC_HEALTH] RTSP DESCRIBE failed for ${wedge_probe_alias}, monitoring for wedge" >&2
                        wedge_since=${now}
                    elif [ $((now - wedge_since)) -gt 60 ]; then
                        echo "[GO2RTC_HEALTH] RTSP server wedged for >60s, restarting go2rtc process" >&2
                        kill "${GO2RTC_PID}" 2>/dev/null || true
                        wait "${GO2RTC_PID}" 2>/dev/null || true
                        start_go2rtc_process
                        sleep 5
                        preload_go2rtc_aliases
                        wedge_since=0
                        echo "[GO2RTC_HEALTH] go2rtc process restarted" >&2
                    fi
                fi
            fi
        done
    ) &
}

start_go2rtc_sidecar() {
    if [ -x /config/go2rtc ] && ! command -v go2rtc >/dev/null 2>&1; then
        export PATH="/config:$PATH"
    fi

    load_go2rtc_runtime_env

    GO2RTC_BIN=$(command -v go2rtc 2>/dev/null || echo "NOT_FOUND")
    echo "[GO2RTC_DEBUG] binary=${GO2RTC_BIN} email_set=$([ -n "${WYZE_EMAIL}" ] && echo yes || echo no) secrets_dir=$(ls /run/secrets/ 2>/dev/null | tr '\n' ',' || echo NONE)" >&2

    trap go2rtc_sidecar_cleanup TERM INT

    if ! command -v go2rtc >/dev/null 2>&1 || [ -z "${WYZE_EMAIL}" ] || [ -z "${WYZE_PASSWORD}" ]; then
        return
    fi

    : "${GO2RTC_API_PORT:=11984}"
    : "${GO2RTC_RTSP_PORT:=19554}"
    : "${GO2RTC_CONFIG:=/config/go2rtc_wyze.yaml}"
    : "${GO2RTC_PID_FILE:=/tmp/go2rtc.pid}"
    : "${WB_APP_PORT:=5000}"
    GO2RTC_HAS_PERSISTED_STREAMS=0

    if [ ! -d /config ] && [ "${GO2RTC_CONFIG}" = "/config/go2rtc_wyze.yaml" ]; then
        GO2RTC_CONFIG=/tmp/go2rtc_wyze.yaml
    fi
    export GO2RTC_API_PORT GO2RTC_RTSP_PORT GO2RTC_CONFIG GO2RTC_PID_FILE WB_APP_PORT

    if [ -f "${GO2RTC_CONFIG}" ] && grep -A999 '^streams:$' "${GO2RTC_CONFIG}" | grep -q '^  [a-z0-9][a-z0-9_-]*:$'; then
        GO2RTC_HAS_PERSISTED_STREAMS=1
        echo "[GO2RTC] Preserving existing seeded config at ${GO2RTC_CONFIG}" >&2
    else
        PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "
import os
from go2rtc_sidecar_helpers import generate_initial_config
generate_initial_config(
    os.environ['GO2RTC_CONFIG'],
    os.environ['GO2RTC_API_PORT'],
    os.environ['GO2RTC_RTSP_PORT'],
    os.environ['WYZE_EMAIL'],
    os.environ.get('API_ID', ''),
    os.environ.get('API_KEY', ''),
    os.environ['WYZE_PASSWORD'],
)
"
    fi

    normalize_go2rtc_config

    echo "[GO2RTC] Starting go2rtc HD sidecar (RTSP :${GO2RTC_RTSP_PORT}, API :${GO2RTC_API_PORT}) config=${GO2RTC_CONFIG}" >&2
    start_go2rtc_process

    (
        add_host_candidate() {
            candidate_value=$1
            if [ -z "${candidate_value}" ]; then
                return
            fi
            case " ${GO2RTC_HOST_CANDIDATES} " in
                *" ${candidate_value} "*) ;;
                *) GO2RTC_HOST_CANDIDATES="${GO2RTC_HOST_CANDIDATES} ${candidate_value}" ;;
            esac
        }

        url_host() {
            PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import url_host; print(url_host('$1'))" 2>/dev/null
        }

        GO2RTC_API_BASE=""
        GO2RTC_HOST_CANDIDATES="127.0.0.1"
        add_host_candidate "${WB_IP}"
        add_host_candidate "${DOMAIN}"
        add_host_candidate "$(url_host "${WB_RTSP_URL:-}")"
        add_host_candidate "$(url_host "${WB_WEBRTC_URL:-}")"
        add_host_candidate "$(url_host "${WB_HLS_URL:-}")"
        HOSTNAME_IP=$(hostname -i 2>/dev/null | awk '{print $1}')
        add_host_candidate "${HOSTNAME_IP}"
        HOST_ROUTE_IP=$(PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import detect_outbound_ip; print(detect_outbound_ip())" 2>/dev/null || echo "")
        add_host_candidate "${HOST_ROUTE_IP}"

        for i in $(seq 1 40); do
            sleep 2
            for host in ${GO2RTC_HOST_CANDIDATES}; do
                candidate="http://${host}:${GO2RTC_API_PORT}"
                if curl -sf "${candidate}/api" > /dev/null 2>&1; then
                    GO2RTC_API_BASE="${candidate}"
                    echo "[GO2RTC] API ready after ${i}x2s via ${GO2RTC_API_BASE}" >&2
                    break 2
                fi
            done
        done
        if [ -z "${GO2RTC_API_BASE}" ]; then
            echo "[GO2RTC] WARNING: API did not become reachable on any candidate host (${GO2RTC_HOST_CANDIDATES})" >&2
            exit 0
        fi

        CAM_JSON=""
        for retry in $(seq 1 30); do
            CAM_JSON=$(curl -sf "${GO2RTC_API_BASE}/api/wyze?id=${WYZE_EMAIL}" 2>/dev/null)
            if [ -n "${CAM_JSON}" ] && [ "${CAM_JSON}" != "null" ] && [ "${CAM_JSON}" != "[]" ]; then
                break
            fi
            echo "[GO2RTC] /api/wyze not ready yet from ${GO2RTC_API_BASE} (attempt ${retry}/30), waiting 3s..." >&2
            sleep 3
        done
        if [ -z "${CAM_JSON}" ] || [ "${CAM_JSON}" = "null" ] || [ "${CAM_JSON}" = "[]" ]; then
            echo "[GO2RTC] WARNING: /api/wyze?id=${WYZE_EMAIL} still empty after retries - check credentials and camera list" >&2
            exit 0
        fi
        echo "[GO2RTC] Camera list received, refreshing native Wyze aliases..." >&2
        WB_APP_API_BASE=""
        BRIDGE_API_TOKEN=$(WYZE_EMAIL="${WYZE_EMAIL}" PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "import os; from go2rtc_sidecar_helpers import resolve_bridge_api_token; print(resolve_bridge_api_token(os.environ['WYZE_EMAIL']))")
        for retry in $(seq 1 30); do
            candidate="http://127.0.0.1:${WB_APP_PORT}"
            BRIDGE_API_PAYLOAD=$(curl -sf -H "api: ${BRIDGE_API_TOKEN}" "${candidate}/api" 2>/dev/null || true)
            if BRIDGE_API_PAYLOAD="${BRIDGE_API_PAYLOAD}" PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "import os; from go2rtc_sidecar_helpers import payload_has_cameras; exit(0 if payload_has_cameras(os.environ['BRIDGE_API_PAYLOAD']) else 1)"
            then
                WB_APP_API_BASE="${candidate}"
                echo "[GO2RTC] Bridge catalog ready after ${retry}x2s via ${WB_APP_API_BASE}" >&2
                break
            fi
            sleep 2
        done
        if [ -z "${WB_APP_API_BASE}" ]; then
            echo "[GO2RTC] WARNING: authenticated bridge catalog did not populate on http://127.0.0.1:${WB_APP_PORT}; keeping stale alias fallback and using helper-only alias filtering" >&2
        fi
        GO2RTC_CAM_JSON_FILE=/tmp/go2rtc_cam_sources.json
        printf '%s\n' "${CAM_JSON}" > "${GO2RTC_CAM_JSON_FILE}"
        GO2RTC_CONFIG="${GO2RTC_CONFIG}" GO2RTC_API_PORT="${GO2RTC_API_PORT}" GO2RTC_RTSP_PORT="${GO2RTC_RTSP_PORT}" GO2RTC_CAM_JSON_FILE="${GO2RTC_CAM_JSON_FILE}" WB_APP_API_BASE="${WB_APP_API_BASE}" WYZE_EMAIL="${WYZE_EMAIL}" API_ID="${API_ID}" API_KEY="${API_KEY}" WYZE_PASSWORD="${WYZE_PASSWORD}" PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import seed_go2rtc_aliases; seed_go2rtc_aliases()"
        echo "[GO2RTC] Restarting sidecar with direct DTLS helper URLs" >&2
        kill "${GO2RTC_PID}" 2>/dev/null || true
        wait "${GO2RTC_PID}" 2>/dev/null || true
        start_go2rtc_process
        sleep 5
        preload_go2rtc_aliases
        start_go2rtc_preload_refresh_loop
        start_go2rtc_health_monitor
        curl -sf -X OPTIONS "${GO2RTC_API_BASE}/api/streams" 2>/dev/null | PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import list_active_producers_verbose; list_active_producers_verbose()" >&2 || echo "[GO2RTC] WARNING: could not confirm active producer aliases yet" >&2
    ) &
}
