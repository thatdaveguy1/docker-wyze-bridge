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

append_extra_streams() {
    # Merge extra streams (Wyze local DTLS + Reolink RTSP) from a YAML snippet
    # file into the go2rtc config so they survive seed_go2rtc_aliases() config
    # regeneration on restart.  The Wyze cloud API is only needed for camera
    # *discovery*; the wyze:// URLs are static local DTLS connections that work
    # without cloud auth.
    # Source file: /config/go2rtc_extra_streams.yaml (managed via addon_config)
    local extra_file="/config/go2rtc_extra_streams.yaml"
    if [ ! -f "${extra_file}" ]; then
        return
    fi
    PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "
import os, sys
from go2rtc_sidecar_helpers import merge_extra_streams
merge_extra_streams(os.environ['GO2RTC_CONFIG'], '${extra_file}')
" && echo "[GO2RTC] Merged extra streams from ${extra_file}" >&2
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
    # Monitor go2rtc streams every 15s. Three per-stream checks:
    # 1. Producer check: if producers drop to 0 for >30s, force-restart the stream.
    # 2. Bytes-stall check: if producers>0 but receiver bytes don't increase
    #    for >30s, the stream is wedged (DTLS/RTSP connected but no video
    #    flowing). go2rtc returns HTTP 200 with 0-byte snapshots → black frames.
    #    Force-restart the stream to reconnect.
    # 3. Keyframe consumer pileup: if >5 stuck keyframe consumers, force-restart.
    # Full-process RTSP wedge check: probe ALL alive aliases with DESCRIBE.
    #    Only restart the go2rtc process if DESCRIBE fails for EVERY alive
    #    alias for >60s. A single camera dropping should NOT trigger a full
    #    restart that drops all connections.
    (
        dead_since=""
        stall_since=""
        prev_bytes=""
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

            # Collect ALL aliases with active producers for the RTSP wedge probe.
            # Probing only one alias was too aggressive — a single camera dropping
            # would trigger a full go2rtc restart, dropping all 9 connections.
            alive_aliases=""

            for alias in ${aliases}; do
                [ -n "${alias}" ] || continue
                # Get producer count, keyframe consumer count, and receiver bytes
                # in one pass. Receiver bytes = total bytes received from the
                # camera by go2rtc. If this doesn't increase between checks, the
                # stream is wedged (connected but no video flowing).
                counts=$(printf '%s' "${streams_json}" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    s = data.get('${alias}', {})
    p = s.get('producers') or []
    c = s.get('consumers') or []
    kf = sum(1 for con in c if con.get('format_name') == 'keyframe')
    # Sum bytes across all receivers of the first producer
    bytes_recv = 0
    if p:
        for r in (p[0].get('receivers') or []):
            bytes_recv += r.get('bytes', 0)
    print(len(p), kf, bytes_recv)
except Exception:
    print(0, 0, 0)
" 2>/dev/null || echo "0 0 0")
                producer_count=$(echo "${counts}" | cut -d' ' -f1)
                keyframe_count=$(echo "${counts}" | cut -d' ' -f2)
                current_bytes=$(echo "${counts}" | cut -d' ' -f3)
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
                        # Reset dead timer and bytes tracking
                        dead_since=$(printf '%s' "${dead_since}" | grep -v "^${key}=")
                        dead_since="${dead_since}${key}=0\n"
                        stall_since=$(printf '%s' "${stall_since}" | grep -v "^${key}=")
                        prev_bytes=$(printf '%s' "${prev_bytes}" | grep -v "^${key}=")
                        echo "[GO2RTC_HEALTH] ${alias}: restart triggered" >&2
                    fi
                elif [ "${keyframe_count}" -gt 5 ] 2>/dev/null; then
                    # Keyframe consumer pileup — stuck snapshot requests that
                    # go2rtc didn't clean up. Force-restart the stream to clear
                    # them before they prevent new snapshots from being served.
                    echo "[GO2RTC_HEALTH] ${alias}: keyframe consumer pileup (${keyframe_count}), forcing restart" >&2
                    curl -sf -X POST "${GO2RTC_API_BASE}/api/streams?src=&dst=${alias}" >/dev/null 2>&1 || true
                    sleep 2
                    curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                    # Reset bytes tracking after restart
                    stall_since=$(printf '%s' "${stall_since}" | grep -v "^${alias}=")
                    prev_bytes=$(printf '%s' "${prev_bytes}" | grep -v "^${alias}=")
                    echo "[GO2RTC_HEALTH] ${alias}: restart triggered (pileup cleared)" >&2
                    alive_aliases="${alive_aliases} ${alias}"
                else
                    # Producer is alive — check for bytes stall (wedged stream)
                    key="${alias}"
                    prev_b=$(printf '%s' "${prev_bytes}" | grep "^${key}=" | cut -d= -f2)
                    if [ -z "${prev_b}" ]; then
                        prev_b=0
                    fi
                    if [ "${current_bytes}" -le "${prev_b}" ] 2>/dev/null; then
                        # Bytes didn't increase — stream is wedged
                        stall_last=$(printf '%s' "${stall_since}" | grep "^${key}=" | cut -d= -f2)
                        if [ -z "${stall_last}" ]; then
                            stall_last=0
                        fi
                        if [ "${stall_last}" = "0" ]; then
                            echo "[GO2RTC_HEALTH] ${alias}: bytes stalled at ${current_bytes}, monitoring" >&2
                            stall_since="${stall_since}${key}=${now}\n"
                        elif [ $((now - stall_last)) -gt 30 ]; then
                            echo "[GO2RTC_HEALTH] ${alias}: bytes stalled for >30s (bytes=${current_bytes}), forcing restart" >&2
                            curl -sf -X POST "${GO2RTC_API_BASE}/api/streams?src=&dst=${alias}" >/dev/null 2>&1 || true
                            sleep 2
                            curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                            stall_since=$(printf '%s' "${stall_since}" | grep -v "^${key}=")
                            prev_bytes=$(printf '%s' "${prev_bytes}" | grep -v "^${key}=")
                            echo "[GO2RTC_HEALTH] ${alias}: restart triggered (bytes stall cleared)" >&2
                        fi
                    else
                        # Bytes are increasing — stream is healthy
                        was_stalled=$(printf '%s' "${stall_since}" | grep "^${alias}=" | cut -d= -f2)
                        if [ -n "${was_stalled}" ] && [ "${was_stalled}" != "0" ]; then
                            echo "[GO2RTC_HEALTH] ${alias}: bytes flowing again after $((now - was_stalled))s stall" >&2
                        fi
                        stall_since=$(printf '%s' "${stall_since}" | grep -v "^${alias}=")
                    fi
                    # Update prev_bytes for next check
                    prev_bytes=$(printf '%s' "${prev_bytes}" | grep -v "^${key}=")
                    prev_bytes="${prev_bytes}${key}=${current_bytes}\n"
                    # Clear dead timer — stream is alive
                    was_dead=$(printf '%s' "${dead_since}" | grep "^${alias}=" | cut -d= -f2)
                    if [ -n "${was_dead}" ] && [ "${was_dead}" != "0" ]; then
                        echo "[GO2RTC_HEALTH] ${alias}: recovered after $((now - was_dead))s" >&2
                    fi
                    dead_since=$(printf '%s' "${dead_since}" | grep -v "^${alias}=")
                    alive_aliases="${alive_aliases} ${alias}"
                fi
            done

            # RTSP wedge detection: probe ALL alive aliases. Only declare a wedge
            # if DESCRIBE fails for every single one of them (a single camera
            # dropping should NOT trigger a full go2rtc restart).
            if [ -n "${alive_aliases}" ]; then
                any_describe_ok=0
                for alias in ${alive_aliases}; do
                    if rtsp_describe_ok "${alias}"; then
                        any_describe_ok=1
                        break
                    fi
                done
                if [ "${any_describe_ok}" = "1" ]; then
                    if [ "${wedge_since}" -ne 0 ]; then
                        echo "[GO2RTC_HEALTH] RTSP server recovered after $((now - wedge_since))s" >&2
                    fi
                    wedge_since=0
                else
                    if [ "${wedge_since}" -eq 0 ]; then
                        echo "[GO2RTC_HEALTH] RTSP DESCRIBE failed for all ${alive_aliases} , monitoring for wedge" >&2
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

        # Local streams (Wyze DTLS + Reolink RTSP) are static and don't need
        # the Wyze cloud API.  Merge them immediately and start streaming.
        # The Wyze cloud API is only needed for camera *discovery* (IP/MAC/ENR
        # changes), which is handled by the bridge separately.  If the extra
        # streams file is absent, fall back to the Wyze API discovery loop.
        if [ -f /config/go2rtc_extra_streams.yaml ]; then
            echo "[GO2RTC] Using local extra streams (Wyze API discovery skipped)" >&2
            append_extra_streams
            kill "${GO2RTC_PID}" 2>/dev/null || true
            wait "${GO2RTC_PID}" 2>/dev/null || true
            start_go2rtc_process
            sleep 5
            preload_go2rtc_aliases
            start_go2rtc_preload_refresh_loop
            start_go2rtc_health_monitor
            curl -sf -X OPTIONS "${GO2RTC_API_BASE}/api/streams" 2>/dev/null | PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import list_active_producers_verbose; list_active_producers_verbose()" >&2 || echo "[GO2RTC] WARNING: could not confirm active producer aliases yet" >&2
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
        append_extra_streams
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
