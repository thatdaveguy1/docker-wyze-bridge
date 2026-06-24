# Architecture — Docker Wyze Bridge

## Overview

Docker Wyze Bridge connects to Wyze cameras over the local network and serves
their video feeds as standard streaming protocols. The system runs three
processes: the Python bridge, a go2rtc sidecar, and a MediaMTX sidecar.

```
                    ┌─────────────────────────────────────────────────┐
                    │              Docker Wyze Bridge                  │
                    │                                                 │
  Wyze Cloud API ◄──┤  wyze_api.py                                    │
  (login, cameras)  │  │                                              │
                    │  ▼                                              │
                    │  stream_manager.py                              │
                    │  │                                              │
                    │  ├──► wyze_stream.py (per camera)               │
                    │  │    ├── KVS path ──► whep_proxy/ (Go)         │
                    │  │    └── TUTK path ──► tutk_session.py         │
                    │  │                    └── wyzecam/iotc.py       │
                    │  │                        └── libIOTCAPIs_ALL   │
                    │  │                                              │
                    │  ├──► snapshot.py (fallback chain)              │
                    │  │    1. go2rtc /api/frame.jpeg                 │
                    │  │    2. ffmpeg RTSP snapshot                   │
                    │  │    3. Wyze API thumbnail                     │
                    │  │                                              │
                    │  └──► mqtt.py (discovery + motion)              │
                    │                                                 │
                    │  ┌─────────────┐    ┌──────────────────┐        │
                    │  │   go2rtc    │    │    MediaMTX       │        │
                    │  │  (sidecar)  │    │   (sidecar)       │        │
                    │  │  :11984     │    │  :18554/:18888    │        │
                    │  └──────┬──────┘    └────────┬─────────┘        │
                    │         │                    │                  │
                    └─────────┼────────────────────┼──────────────────┘
                              │                    │
                     RTSP/WebRTC            RTSP/RTMP/HLS
                              │                    │
                    ┌─────────▼───────────▼────────┐
                    │     Consumers                 │
                    │  • Frigate (RTSP)             │
                    │  • Scrypted (RTSP/snapshot)   │
                    │  • HomeKit (WHEP/snapshot)    │
                    │  • Home Assistant (MQTT)      │
                    └───────────────────────────────┘
```

## Stream Lifecycle

### 1. Startup
1. `wyze_api.py` authenticates with the Wyze cloud API and fetches the camera
   list
2. `stream_manager.py` creates a `WyzeStream` for each enabled camera
3. `go2rtc_sidecar.sh` starts go2rtc and creates native aliases for each camera
4. MediaMTX starts and registers RTSP paths

### 2. Connection (per camera)
`wyze_stream.py` uses `source_selector.py` to choose between:

- **KVS path** (default for HL_CAM4, WYZE_CAKP2JFUS):
  1. `whep_proxy/` fetches KVS WebRTC config from the Wyze API
  2. Establishes a WebRTC peer connection to the camera
  3. Receives RTP packets and forwards them to MediaMTX

- **TUTK path** (default for older models, fallback for HL_CAM4):
  1. `iotc_connect.py` establishes a DTLS connection via `libIOTCAPIs_ALL`
  2. `tutk_session.py` exchanges IOCtrl messages for configuration
  3. Receives H.264/H.265 frames and forwards them to MediaMTX

### 3. Health Monitoring
`stream_manager.py.monitor_streams()` runs the main loop:
- Reads MediaMTX RTSP events (stream start/stop)
- Checks stream health via `active_streams()`
- Runs snapshot monitoring
- Polls for motion events (Boa cameras)
- Publishes MQTT status updates

### 4. Snapshot
`snapshots.py` owns the three-tier fallback chain:
1. **go2rtc**: `GET /api/frame.jpeg?src=<alias>&timeout=2` — fastest, direct
   from the go2rtc producer
2. **RTSP**: `ffmpeg -rtsp_transport tcp -i <rtsp_url> -frames:v 1` — works
   even if go2rtc is down but MediaMTX has the stream
3. **Wyze API**: Fetch the camera's cloud thumbnail URL — last resort, may be
   stale

Each result is validated (non-empty, valid JPEG, SHA-256 differs from previous)
and written to `/media/wyze/img/<camera>.jpg`.

### 5. Talkback
`native_talkback.py` enables two-way audio via go2rtc:
1. Consumer sends audio to `go2rtc/api/webrtc?src=<alias>`
2. go2rtc forwards audio to the camera via TUTK
3. Camera plays audio through its speaker

Only available on the primary native alias (not substrems).

## WHEP Proxy (Go)

The WHEP proxy is a Go application in `whep_proxy/` that bridges KVS WebRTC to
standard WHEP consumers. File structure:

| File | Responsibility |
|------|---------------|
| `state.go` | StreamStateMachine: lifecycle, reaper, reuse |
| `upstream.go` | UpstreamSession: signaling, SDP |
| `upstream_sdp.go` | SDP offer/answer, codec negotiation |
| `upstream_websocket.go` | WebSocket connection, message loop |
| `kvs_config.go` | KVSConfigClient: fetch, retry |
| `media.go` | MediaForwarder: RTP, H.264, keyframe |
| `handlers.go` | HTTP handlers |
| `main.go` | Entry point, shared types |

The state machine (`state.go`) manages stream lifecycle:
`new → connecting → connected → ready → idle → (reuse or reaped)`

The reaper kills streams that have been idle for too long without consumers.
Stream reuse allows a second WHEP consumer to attach to an existing upstream
session without reconnecting to the camera.

## Build System

`scripts/build.sh` generates Home Assistant add-on trees from:
- `app/` — canonical Python/web source
- `whep_proxy/` — canonical Go source
- `runtime_overlays/<target>/` — target-specific overrides (.env, build.env)

`./scripts/build.sh --check` verifies that `home_assistant/` and
`.ha_live_addon/` match canonical + overlay. This must pass before any commit.

## Testing

```bash
# Python tests (349 tests)
.venv/bin/python -m pytest tests/ --timeout=60 -q

# Go tests (30 tests)
go test ./whep_proxy/... -v -count=1

# Build check
./scripts/build.sh --check

# Master local gate
./scripts/run_master_local_gates.sh
```

## Module Dependency Graph

```
wyze_bridge.py (entry)
  ├── stream_manager.py
  │   ├── snapshot.py
  │   ├── wyze_stream.py
  │   │   ├── source_selector.py
  │   │   ├── tutk_session.py
  │   │   └── native_alias.py
  │   │       └── native_talkback.py
  │   ├── mqtt.py
  │   └── mtx_event.py
  ├── wyze_api.py
  │   └── wyze_api_helpers.py
  ├── wyze_control.py
  ├── frontend.py
  │   └── network_utils.py
  └── config.py
      └── build_config.py

wyzecam/
  ├── iotc.py
  │   ├── iotc_connect.py
  │   ├── iotc_helpers.py
  │   └── tutk/
  │       ├── tutk.py (facade)
  │       ├── tutk_core.py
  │       ├── tutk_ffi.py
  │       ├── tutk_structures.py
  │       ├── tutk_protocol.py (facade)
  │       ├── protocol_core.py
  │       ├── protocol_messages.py
  │       └── protocol_messages_ptz.py
  ├── api.py
  └── api_models.py
```
