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

restart_go2rtc_child() {
    _recovery_alias="${1:-unknown}"
    _cooldown_file=/tmp/go2rtc_recovery_last
    _cooldown="${GO2RTC_RECOVERY_COOLDOWN:-30}"
    _now=$(date +%s)
    _last=$(cat "${_cooldown_file}" 2>/dev/null | tr -cd '0-9')
    [ -z "${_last}" ] && _last=0
    if [ $((_now - _last)) -lt "${_cooldown}" ] 2>/dev/null; then
        echo "[GO2RTC_RECOVERY] ${_recovery_alias}: child restart throttled (cooldown=${_cooldown}s)" >&2
        return 0
    fi

    _signalled=0
    for _comm_file in /proc/[0-9]*/comm; do
        [ -r "${_comm_file}" ] || continue
        _comm=$(cat "${_comm_file}" 2>/dev/null || echo "")
        [ "${_comm}" = "go2rtc" ] || continue
        _pid=${_comm_file#/proc/}
        _pid=${_pid%/comm}
        if kill "${_pid}" 2>/dev/null; then
            _signalled=$((_signalled + 1))
        fi
    done

    if [ "${_signalled}" -gt 0 ]; then
        echo "${_now}" > "${_cooldown_file}"
        echo "[GO2RTC_RECOVERY] ${_recovery_alias}: signalled go2rtc child count=${_signalled}" >&2
    else
        echo "[GO2RTC_RECOVERY] ${_recovery_alias}: no exact go2rtc child found" >&2
    fi
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

_go2rtc_stream_connected() {
    # go2rtc keeps lazy/on-demand streams as URL-only producer placeholders.
    # Treat a producer as connected only after runtime/media metadata appears.
    printf '%s' "$2" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    details = data.get(sys.argv[1], {})
    producers = details.get('producers') or []
    fields = ('format_name', 'protocol', 'remote_addr', 'medias', 'receivers')
    connected = any(isinstance(p, dict) and any(p.get(field) for field in fields) for p in producers)
    print(1 if connected else 0)
except Exception:
    print(0)
" "$1" 2>/dev/null
}

start_go2rtc_health_monitor() {
    # Monitor go2rtc streams every 15s. Three per-stream checks:
    # 1. Producer check: when consumer demand exists but no producer is connected
    #    for >30s, force a reconnect. URL-only lazy placeholders with no consumers
    #    are intentional on-demand idle state and are skipped.
    # 2. Bytes-stall check: if a connected producer's receiver bytes don't increase
    #    for >45s, the stream is wedged (DTLS/RTSP connected but no video flowing).
    #    go2rtc can otherwise return HTTP 200 with 0-byte snapshots → black frames.
    #    Force-restart the stream to reconnect. (45s, not 30s, to avoid noise
    #    from low-motion cameras that naturally stall for one 15s check.)
    # 3. Keyframe consumer pileup: if >5 stuck keyframe consumers, force-restart.
    # Full-process RTSP wedge check: probe ALL connected aliases with DESCRIBE.
    #    Only restart the go2rtc process if DESCRIBE fails for EVERY connected
    #    alias for >60s. A single camera dropping should NOT trigger a full
    #    restart that drops all connections.
    _STATE_DIR=/tmp/go2rtc_health_state
    _QUARANTINE_DIR=/config/go2rtc_quarantine
    mkdir -p "${_STATE_DIR}" "${_QUARANTINE_DIR}"
    (
        wedge_since=0
        _process_restarted=0
        while :; do
            sleep 15
            if [ -z "${GO2RTC_API_BASE}" ]; then
                continue
            fi
            streams_json=$(curl -sf "${GO2RTC_API_BASE}/api/streams" 2>/dev/null || echo "")
            if [ -z "${streams_json}" ]; then
                continue
            fi
            # Get list of expected aliases from the go2rtc API (resilient to
            # missing config files) with config-file fallback for offline API.
            aliases=$(printf '%s' "${streams_json}" | python3 -c "
import json, sys, os
try:
    data = json.load(sys.stdin)
    for name in sorted(data.keys()):
        print(name)
except Exception:
    pass
" 2>/dev/null || echo "")
            if [ -z "${aliases}" ]; then
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
            fi
            now=$(date +%s)
            _process_restarted=0
            alive_aliases=""

            # Collect only connected aliases for the RTSP wedge probe.
            for alias in ${aliases}; do
                [ -n "${alias}" ] || continue
                # Read consumer demand, keyframe consumers, and receiver bytes.
                # A URL-only producer placeholder is not a connected producer.
                connected_producer=$(_go2rtc_stream_connected "${alias}" "${streams_json}")
                counts=$(printf '%s' "${streams_json}" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    s = data.get('${alias}', {})
    p = s.get('producers') or []
    c = s.get('consumers') or []
    kf = sum(1 for con in c if con.get('format_name') == 'keyframe')
    bytes_recv = 0
    if p:
        for r in (p[0].get('receivers') or []):
            bytes_recv += r.get('bytes', 0)
    print(len(c), kf, bytes_recv)
except Exception:
    print(0, 0, 0)
" 2>/dev/null || echo "0 0 0")
                consumer_count=$(echo "${counts}" | cut -d' ' -f1 | tr -cd '0-9')
                keyframe_count=$(echo "${counts}" | cut -d' ' -f2 | tr -cd '0-9')
                current_bytes=$(echo "${counts}" | cut -d' ' -f3 | tr -cd '0-9')
                [ -z "${connected_producer}" ] && connected_producer=0
                [ -z "${consumer_count}" ] && consumer_count=0
                [ -z "${keyframe_count}" ] && keyframe_count=0
                [ -z "${current_bytes}" ] && current_bytes=0
                # State files: one per alias per metric. No string parsing.
                _dead_file="${_STATE_DIR}/dead_${alias}"
                _stall_file="${_STATE_DIR}/stall_${alias}"
                _bytes_file="${_STATE_DIR}/bytes_${alias}"
                _stall_state_file="${_STATE_DIR}/stall_state_${alias}.json"
                _quarantine_file="${_QUARANTINE_DIR}/quarantine_${alias}.json"

                if [ "${connected_producer}" = "0" ] && [ "${consumer_count}" = "0" ]; then
                    # Intentional on-demand idle state. Clear volatile failure
                    # history so idle time can never escalate into a restart.
                    rm -f "${_dead_file}" "${_stall_file}" "${_bytes_file}" "${_stall_state_file}" 2>/dev/null
                    continue
                fi

                if [ "${connected_producer}" = "0" ]; then
                    # There is consumer demand but no connected producer.
                    if [ ! -f "${_dead_file}" ]; then
                        echo "[GO2RTC_HEALTH] ${alias}: consumer demand without connected producer, monitoring for recovery" >&2
                        echo "${now}" > "${_dead_file}"
                    else
                        last=$(cat "${_dead_file}" 2>/dev/null | tr -cd '0-9')
                        [ -z "${last}" ] && last=0
                        if [ $((now - last)) -gt 30 ]; then
                            echo "[GO2RTC_HEALTH] ${alias}: demand unserved for >30s, forcing restart" >&2
                            restart_go2rtc_child "${alias}"
                            sleep 2
                            rm -f "${_dead_file}" "${_stall_file}" "${_bytes_file}" "${_stall_state_file}" 2>/dev/null
                            echo "[GO2RTC_HEALTH] ${alias}: restart triggered" >&2
                        fi
                    fi
                elif [ "${keyframe_count}" -gt 5 ] 2>/dev/null; then
                    # Keyframe consumer pileup — stuck snapshot requests that
                    # go2rtc didn't clean up. Force-restart the stream to clear
                    # them before they prevent new snapshots from being served.
                    echo "[GO2RTC_HEALTH] ${alias}: keyframe consumer pileup (${keyframe_count}), forcing restart" >&2
                    restart_go2rtc_child "${alias}"
                    sleep 2
                    curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                    rm -f "${_stall_file}" "${_bytes_file}" "${_stall_state_file}" 2>/dev/null
                    echo "[GO2RTC_HEALTH] ${alias}: restart triggered (pileup cleared)" >&2
                    alive_aliases="${alive_aliases} ${alias}"
                else
                    # Producer is connected — check for bytes stall via Python
                    # escalation state machine. The helper tracks per-alias
                    # stall/restart_count state and returns an action:
                    _stall_err_file="${_STATE_DIR}/stall_err_$$.tmp"
                    # Run the helper ONCE: stdout → action, stderr → log lines.
                    _stall_action=$(PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "
import json, sys
from go2rtc_sidecar_helpers import check_bytes_stall
try:
    state = json.load(open('${_stall_state_file}'))
except Exception:
    state = {}
try:
    q = json.load(open('${_quarantine_file}'))
    state['quarantined_until'] = q.get('quarantined_until', 0)
    state['quarantine_count'] = q.get('quarantine_count', 0)
except Exception:
    pass
new_state, action, log = check_bytes_stall('${alias}', ${current_bytes}, ${now}, state)
# Split: volatile state → stall_state, quarantine → quarantine file
volatile = {k: v for k, v in new_state.items() if k not in ('quarantined_until', 'quarantine_count')}
json.dump(volatile, open('${_stall_state_file}', 'w'), separators=(',', ':'))
q_state = {'quarantined_until': new_state.get('quarantined_until', 0), 'quarantine_count': new_state.get('quarantine_count', 0)}
json.dump(q_state, open('${_quarantine_file}', 'w'), separators=(',', ':'))
if log:
    print(log, file=sys.stderr)
print(action)
" 2>"${_stall_err_file}")
                    # Emit any log lines to the sidecar's stderr
                    [ -s "${_stall_err_file}" ] && cat "${_stall_err_file}" >&2
                    rm -f "${_stall_err_file}" 2>/dev/null

                    if [ "${_stall_action}" = "restart_alias" ]; then
                        restart_go2rtc_child "${alias}"
                        sleep 2
                        curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                        echo "[GO2RTC_HEALTH] ${alias}: alias restart triggered" >&2
                    elif [ "${_stall_action}" = "restart_process" ]; then
                        echo "[GO2RTC_HEALTH] ${alias}: escalating to full go2rtc process restart" >&2
                        # Quarantine ALL aliases at max_restarts BEFORE killing go2rtc,
                        # so quarantine files are written even if the add-on restarts.
                        for _peer_state_file in "${_STATE_DIR}"/stall_state_*.json; do
                            [ -f "${_peer_state_file}" ] || continue
                            _peer_alias=$(basename "${_peer_state_file}" | sed 's/stall_state_//; s/\.json//')
                            [ "${_peer_alias}" = "${alias}" ] && continue
                            _peer_q_file="${_QUARANTINE_DIR}/quarantine_${_peer_alias}.json"
                            _peer_q=$(PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "
import json
from go2rtc_sidecar_helpers import quarantine_peer
try:
    state = json.load(open('${_peer_state_file}'))
except Exception:
    exit(0)
try:
    state.update(json.load(open('${_peer_q_file}')))
except Exception:
    pass
q = quarantine_peer(state, ${now})
if q:
    json.dump(q, open('${_peer_q_file}', 'w'), separators=(',', ':'))
    print(f\"[GO2RTC_HEALTH] ${_peer_alias}: quarantined alongside process restart ({q['quarantined_until'] - ${now}}s)\", file=__import__('sys').stderr)
" 2>&1)
                            [ -n "${_peer_q}" ] && echo "${_peer_q}" >&2
                        done
                        kill "${GO2RTC_PID}" 2>/dev/null || true
                        wait "${GO2RTC_PID}" 2>/dev/null || true
                        start_go2rtc_process
                        sleep 5
                        preload_go2rtc_aliases
                        # Clear volatile stall state (prev_bytes/stall_since/restart_count)
                        # but preserve quarantine files so dead aliases stay quarantined
                        rm -f "${_STATE_DIR}"/stall_state_*.json "${_STATE_DIR}"/dead_* "${_STATE_DIR}"/stall_* "${_STATE_DIR}"/bytes_* 2>/dev/null
                        echo "[GO2RTC_HEALTH] go2rtc process restarted (stall escalation)" >&2
                        # Signal outer loop to skip RTSP wedge probe this cycle
                        _process_restarted=1
                    fi

                    # Clear dead timer — stream is alive
                    if [ -f "${_dead_file}" ]; then
                        was_dead=$(cat "${_dead_file}" 2>/dev/null | tr -cd '0-9')
                        [ -z "${was_dead}" ] && was_dead=0
                        if [ "${was_dead}" != "0" ]; then
                            echo "[GO2RTC_HEALTH] ${alias}: recovered after $((now - was_dead))s" >&2
                        fi
                        rm -f "${_dead_file}" 2>/dev/null
                    fi
                    alive_aliases="${alive_aliases} ${alias}"
                fi
            done

            # RTSP wedge detection: probe ALL connected aliases. Only declare a wedge
            # if DESCRIBE fails for every single one of them (a single camera
            # dropping should NOT trigger a full go2rtc restart).
            # Skip if we just restarted the go2rtc process for stall escalation —
            # all streams were recreated and need time to settle.
            if [ "${_process_restarted}" = "1" ]; then
                wedge_since=0
            elif [ -n "${alive_aliases}" ]; then
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
            else
                wedge_since=0
            fi
        done
    )&
}

# ── Helper: extract camera IP from go2rtc producer URL ─────────────
_extract_cam_ip() {
    # Extract the IP address from a wyze:// URL in the go2rtc streams API.
    # Usage: _extract_cam_ip <alias> <streams_json>
    printf '%s' "$2" | python3 -c "
import json, sys, re
try:
    data = json.load(sys.stdin)
    s = data.get('$1', {})
    p = s.get('producers') or []
    if p:
        url = p[0].get('source', '') or p[0].get('url', '')
        m = re.search(r'wyze://(\d+\.\d+\.\d+\.\d+)', url)
        if m:
            print(m.group(1))
except Exception:
    pass
" 2>/dev/null
}

# ── Helper 1: Proactive TUTK session refresh ──────────────────────
# Wyze TUTK/DTLS sessions degrade after a few hours of continuous use.
# Instead of waiting for a bytes stall + 135s reactive outage, restart
# each healthy connected alias on a planned schedule to get a fresh TUTK
# session. Idle on-demand aliases are left alone. Tracks last-refresh time.
start_go2rtc_session_refresh_loop() {
    _REFRESH_INTERVAL="${GO2RTC_SESSION_REFRESH_INTERVAL:-7200}"  # 2 hours
    _REFRESH_STATE_DIR=/tmp/go2rtc_session_refresh
    mkdir -p "${_REFRESH_STATE_DIR}"
    echo "[GO2RTC_REFRESH] session refresh loop started (interval=${_REFRESH_INTERVAL}s)" >&2
    (
        while :; do
            sleep 300  # check every 5 minutes
            if [ -z "${GO2RTC_API_BASE}" ]; then
                continue
            fi
            streams_json=$(curl -sf "${GO2RTC_API_BASE}/api/streams" 2>/dev/null || echo "")
            if [ -z "${streams_json}" ]; then
                continue
            fi
            now=$(date +%s)
            aliases=$(printf '%s' "${streams_json}" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for name in sorted(data.keys()):
        print(name)
except Exception:
    pass
" 2>/dev/null || echo "")
            for alias in ${aliases}; do
                [ -n "${alias}" ] || continue
                # Skip quarantined aliases
                _q_file="${_QUARANTINE_DIR:-/config/go2rtc_quarantine}/quarantine_${alias}.json"
                if [ -f "${_q_file}" ]; then
                    _q_until=$(python3 -c "import json; print(json.load(open('${_q_file}')).get('quarantined_until', 0))" 2>/dev/null || echo 0)
                    if [ "${_q_until}" -gt "${now}" ] 2>/dev/null; then
                        continue
                    fi
                fi
                _has_producer=$(_go2rtc_stream_connected "${alias}" "${streams_json}")
                [ "${_has_producer}" = "1" ] || continue
                # Check last refresh time
                _refresh_file="${_REFRESH_STATE_DIR}/last_refresh_${alias}"
                _last_refresh=$(cat "${_refresh_file}" 2>/dev/null | tr -cd '0-9')
                [ -z "${_last_refresh}" ] && _last_refresh=0
                _age=$((now - _last_refresh))
                if [ "${_age}" -lt "${_REFRESH_INTERVAL}" ]; then
                    continue
                fi
                # Refresh: restart alias + preload
                echo "[GO2RTC_REFRESH] ${alias}: proactive TUTK session refresh (age=${_age}s)" >&2
                restart_go2rtc_child "${alias}"
                sleep 2
                curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                echo "${now}" > "${_refresh_file}"
                echo "[GO2RTC_REFRESH] ${alias}: session refreshed" >&2
            done
        done
    ) &
}

# ── Helper 2: WiFi degradation early warning ──────────────────────
# Ping each connected Wyze camera IP every 60s. If latency spikes >3x baseline
# or packet loss >5% for 2 consecutive samples, proactively restart the alias.
# Idle on-demand streams are skipped so WiFi monitoring cannot create demand.
start_wifi_health_monitor() {
    _WIFI_STATE_DIR=/tmp/go2rtc_wifi_health
    mkdir -p "${_WIFI_STATE_DIR}"
    if ! command -v ping >/dev/null 2>&1; then
        echo "[GO2RTC_WIFI] ping not available in container, WiFi monitor disabled" >&2
        return 0
    fi
    echo "[GO2RTC_WIFI] WiFi health monitor started (60s interval)" >&2
    (
        while :; do
            sleep 60
            if [ -z "${GO2RTC_API_BASE}" ]; then
                continue
            fi
            streams_json=$(curl -sf "${GO2RTC_API_BASE}/api/streams" 2>/dev/null || echo "")
            if [ -z "${streams_json}" ]; then
                continue
            fi
            now=$(date +%s)
            aliases=$(printf '%s' "${streams_json}" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for name in sorted(data.keys()):
        print(name)
except Exception:
    pass
" 2>/dev/null || echo "")
            for alias in ${aliases}; do
                [ -n "${alias}" ] || continue
                # Skip quarantined aliases
                _q_file="${_QUARANTINE_DIR:-/config/go2rtc_quarantine}/quarantine_${alias}.json"
                if [ -f "${_q_file}" ]; then
                    _q_until=$(python3 -c "import json; print(json.load(open('${_q_file}')).get('quarantined_until', 0))" 2>/dev/null || echo 0)
                    if [ "${_q_until}" -gt "${now}" ] 2>/dev/null; then
                        continue
                    fi
                fi
                _has_producer=$(_go2rtc_stream_connected "${alias}" "${streams_json}")
                [ "${_has_producer}" = "1" ] || continue
                # Extract camera IP from go2rtc producer URL
                cam_ip=$(_extract_cam_ip "${alias}" "${streams_json}")
                [ -z "${cam_ip}" ] && continue
                # Ping 5 packets, parse avg latency and loss
                ping_result=$(ping -c 5 -W 2 "${cam_ip}" 2>/dev/null || echo "")
                _loss=$(echo "${ping_result}" | grep "packet loss" | grep -o '[0-9]*%' | head -1 | tr -d '%')
                _avg=$(echo "${ping_result}" | grep "rtt min" | grep -o '[0-9.]*/[0-9.]*/[0-9.]*' | cut -d'/' -f2)
                [ -z "${_loss}" ] && _loss=100
                [ -z "${_avg}" ] && _avg=999
                # Track baseline latency (rolling, first 5 samples establish baseline)
                _baseline_file="${_WIFI_STATE_DIR}/baseline_${alias}"
                _degraded_file="${_WIFI_STATE_DIR}/degraded_${alias}"
                _baseline=$(cat "${_baseline_file}" 2>/dev/null | tr -cd '0-9.')
                [ -z "${_baseline}" ] && _baseline=0
                # If avg is 999 (unreachable), treat as 100% loss
                if [ "${_avg}" = "999" ]; then
                    _loss=100
                fi
                # Establish baseline after 5 samples (use first non-degraded sample)
                _sample_count_file="${_WIFI_STATE_DIR}/samples_${alias}"
                _sample_count=$(cat "${_sample_count_file}" 2>/dev/null | tr -cd '0-9')
                [ -z "${_sample_count}" ] && _sample_count=0
                _sample_count=$((_sample_count + 1))
                echo "${_sample_count}" > "${_sample_count_file}"
                if [ "${_sample_count}" -le 5 ] && [ "${_loss}" -eq 0 ] && [ "${_avg}" != "999" ]; then
                    if [ "${_baseline}" = "0" ]; then
                        echo "${_avg}" > "${_baseline_file}"
                    else
                        # Rolling average for baseline
                        _new_baseline=$(python3 -c "print(round((${_baseline} * ${_sample_count} + ${_avg}) / (${_sample_count} + 1), 1))" 2>/dev/null || echo "${_avg}")
                        echo "${_new_baseline}" > "${_baseline_file}"
                    fi
                    rm -f "${_degraded_file}" 2>/dev/null
                    continue
                fi
                [ "${_baseline}" = "0" ] && _baseline="${_avg}"
                # Check for degradation: latency >3x baseline OR loss >5%
                _degraded=0
                _threshold=$(python3 -c "print(round(${_baseline} * 3, 1))" 2>/dev/null || echo 999)
                if [ "${_loss}" -gt 5 ] 2>/dev/null; then
                    _degraded=1
                fi
                if [ "${_avg}" != "999" ] && [ "${_avg}" != "0" ]; then
                    _is_high=$(python3 -c "print(1 if ${_avg} > ${_threshold} else 0)" 2>/dev/null || echo 0)
                    [ "${_is_high}" = "1" ] && _degraded=1
                fi
                if [ "${_degraded}" = "1" ]; then
                    if [ ! -f "${_degraded_file}" ]; then
                        echo "${now}" > "${_degraded_file}"
                        echo "[GO2RTC_WIFI] ${alias}: WiFi degradation detected (ip=${cam_ip} avg=${_avg}ms loss=${_loss}% baseline=${_baseline}ms)" >&2
                        continue
                    fi
                    # Second consecutive degraded sample → proactive restart
                    _first_degraded=$(cat "${_degraded_file}" 2>/dev/null | tr -cd '0-9')
                    [ -z "${_first_degraded}" ] && _first_degraded=${now}
                    if [ $((now - _first_degraded)) -ge 60 ]; then
                        echo "[GO2RTC_WIFI] ${alias}: WiFi degraded for >60s, proactive alias restart (ip=${cam_ip} avg=${_avg}ms loss=${_loss}%)" >&2
                        restart_go2rtc_child "${alias}"
                        sleep 2
                        curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                        rm -f "${_degraded_file}" 2>/dev/null
                        echo "[GO2RTC_WIFI] ${alias}: proactive restart triggered" >&2
                    fi
                else
                    # WiFi healthy — clear degradation state
                    if [ -f "${_degraded_file}" ]; then
                        echo "[GO2RTC_WIFI] ${alias}: WiFi recovered (avg=${_avg}ms loss=${_loss}%)" >&2
                        rm -f "${_degraded_file}" 2>/dev/null
                    fi
                fi
            done
        done
    ) &
}

# ── Helper 4: Snapshot freshness canary ───────────────────────────
# Every 5 minutes, fetch a frame.jpeg snapshot from go2rtc for each connected
# healthy alias. Idle on-demand aliases are skipped. If the snapshot is <5KB
# or has the same SHA-256 hash for 2 consecutive checks, the stream is silently
# stalled (bytes may be flowing but video frames aren't decoding).
start_snapshot_canary() {
    _CANARY_STATE_DIR=/tmp/go2rtc_snapshot_canary
    mkdir -p "${_CANARY_STATE_DIR}"
    echo "[GO2RTC_CANARY] snapshot freshness canary started (300s interval)" >&2
    (
        while :; do
            sleep 300  # check every 5 minutes
            if [ -z "${GO2RTC_API_BASE}" ]; then
                continue
            fi
            streams_json=$(curl -sf "${GO2RTC_API_BASE}/api/streams" 2>/dev/null || echo "")
            if [ -z "${streams_json}" ]; then
                continue
            fi
            now=$(date +%s)
            aliases=$(printf '%s' "${streams_json}" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for name in sorted(data.keys()):
        print(name)
except Exception:
    pass
" 2>/dev/null || echo "")
            for alias in ${aliases}; do
                [ -n "${alias}" ] || continue
                # Skip quarantined aliases
                _q_file="${_QUARANTINE_DIR:-/config/go2rtc_quarantine}/quarantine_${alias}.json"
                if [ -f "${_q_file}" ]; then
                    _q_until=$(python3 -c "import json; print(json.load(open('${_q_file}')).get('quarantined_until', 0))" 2>/dev/null || echo 0)
                    if [ "${_q_until}" -gt "${now}" ] 2>/dev/null; then
                        continue
                    fi
                fi
                _has_producer=$(_go2rtc_stream_connected "${alias}" "${streams_json}")
                [ "${_has_producer}" = "1" ] || continue
                # Fetch snapshot from go2rtc frame.jpeg
                _snap_file="${_CANARY_STATE_DIR}/snap_${alias}.jpg"
                _http_code=$(curl -s -o "${_snap_file}" -w "%{http_code}" "${GO2RTC_API_BASE}/api/frame.jpeg?src=${alias}" 2>/dev/null || echo "000")
                if [ "${_http_code}" != "200" ]; then
                    echo "[GO2RTC_CANARY] ${alias}: snapshot fetch failed (HTTP ${_http_code})" >&2
                    continue
                fi
                _snap_size=$(wc -c < "${_snap_file}" 2>/dev/null | tr -cd '0-9')
                [ -z "${_snap_size}" ] && _snap_size=0
                if [ "${_snap_size}" -lt 5000 ]; then
                    echo "[GO2RTC_CANARY] ${alias}: snapshot too small (${_snap_size}B), possible silent stall" >&2
                    # Track consecutive small snapshots
                    _small_file="${_CANARY_STATE_DIR}/small_${alias}"
                    _small_count=$(cat "${_small_file}" 2>/dev/null | tr -cd '0-9')
                    [ -z "${_small_count}" ] && _small_count=0
                    _small_count=$((_small_count + 1))
                    echo "${_small_count}" > "${_small_file}"
                    if [ "${_small_count}" -ge 2 ]; then
                        echo "[GO2RTC_CANARY] ${alias}: 2 consecutive small snapshots, triggering alias restart" >&2
                        restart_go2rtc_child "${alias}"
                        sleep 2
                        curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                        rm -f "${_small_file}" 2>/dev/null
                        echo "[GO2RTC_CANARY] ${alias}: canary restart triggered" >&2
                    fi
                    rm -f "${_snap_file}" 2>/dev/null
                    continue
                fi
                # Snapshot is valid size — check hash for staleness
                _snap_hash=$(sha256sum "${_snap_file}" 2>/dev/null | cut -d' ' -f1)
                _hash_file="${_CANARY_STATE_DIR}/hash_${alias}"
                _prev_hash=$(cat "${_hash_file}" 2>/dev/null | tr -cd 'a-f0-9')
                echo "${_snap_hash}" > "${_hash_file}"
                rm -f "${_snap_file}" 2>/dev/null
                # Clear small-snapshot counter
                rm -f "${_CANARY_STATE_DIR}/small_${alias}" 2>/dev/null
                if [ -n "${_prev_hash}" ] && [ "${_snap_hash}" = "${_prev_hash}" ]; then
                    echo "[GO2RTC_CANARY] ${alias}: snapshot hash unchanged (stale frame), triggering alias restart" >&2
                    restart_go2rtc_child "${alias}"
                    sleep 2
                    curl -sf -X PUT "${GO2RTC_API_BASE}/api/preload?src=${alias}" >/dev/null 2>&1 || true
                    # Don't clear hash file — next check will compare against this hash
                    echo "[GO2RTC_CANARY] ${alias}: canary restart triggered (stale frame)" >&2
                fi
            done
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
        # the Wyze cloud API. Merge them immediately and start streaming.
        # The Wyze cloud API is only needed for camera *discovery* (IP/MAC/ENR
        # changes), which is handled by the bridge separately. If the extra
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
            start_go2rtc_session_refresh_loop
            start_wifi_health_monitor
            start_snapshot_canary
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
        start_go2rtc_session_refresh_loop
        start_wifi_health_monitor
        start_snapshot_canary
        curl -sf -X OPTIONS "${GO2RTC_API_BASE}/api/streams" 2>/dev/null | PYTHONPATH="${HELPERS_PYTHONPATH}" python3 -c "from go2rtc_sidecar_helpers import list_active_producers_verbose; list_active_producers_verbose()" >&2 || echo "[GO2RTC] WARNING: could not confirm active producer aliases yet" >&2
    ) &
}
