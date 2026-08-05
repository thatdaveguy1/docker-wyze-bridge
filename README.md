# Docker Wyze Bridge

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/thatdaveguy1/docker-wyze-bridge?logo=github)](https://github.com/thatdaveguy1/docker-wyze-bridge/releases/latest)
[![GHCR Package](https://img.shields.io/badge/ghcr-package-blue?logo=github)](https://ghcr.io/thatdaveguy1/docker-wyze-bridge)
[![Home Assistant Add-on](https://img.shields.io/badge/home_assistant-add--on-blue.svg?logo=homeassistant&logoColor=white)](./docs/user_guide/install_ha.md)

### WebRTC/RTSP/RTMP/HLS Bridge for Wyze Cameras

![Wyze Bridge UI](https://user-images.githubusercontent.com/67088095/224595527-05242f98-c4ab-4295-b9f5-07051ced1008.png)

Create local WebRTC, RTSP, RTMP, and HLS streams for Wyze cameras without custom firmware. This fork focuses on newer Wyze camera behavior, Home Assistant packaging, and the real limitations and runtime behavior validated for the 5.0 release.

- No camera firmware mods required.
- Home Assistant add-on with visible Wyze login fields by default.
- WebRTC/KVS-backed bridge path for modern Wyze models.
- Native Home Assistant `go2rtc` RTSP sidecar on `:19554` for supported 5.0 workflows.

## 5.0.0 Highlights

- **Native snapshot health tracking**: DWB now tracks per-camera snapshot health internally via `SnapshotHealthTracker` in `app/wyzebridge/snapshot_health.py`. The tracker records consecutive snapshot failures, stale-hash duration (frozen frame detection), and per-camera state transitions (`online` → `snapshot_down` → `stale_snapshot`). This moves the babysitter's Wyze snapshot monitoring into DWB itself, making the babysitter a last line of defense for Reolink camera reboots only.
- **Proactive stream restart on sustained failure**: `refresh_preview()` in `app/wyzebridge/snapshot.py` now uses the health tracker to decide when to restart a stream. On sustained failure (3+ consecutive failures), the tracker triggers a restart with a 60s cooldown to prevent restart loops. On stale snapshots (hash unchanged for 10+ minutes), the tracker triggers one restart to get a fresh frame.
- **`/api/snapshot-health` endpoint**: New authenticated endpoint exposes per-camera snapshot health state, consecutive failure count, last success time, and stale status. Also integrated into `/health/details` under the `snapshot_health` key.
- **MQTT snapshot health publishing**: State changes are published to `wyzebridge/<cam>/snapshot_health` (retained) so Home Assistant can monitor snapshot health without the babysitter.
- **Babysitter reduced to Reolink-only**: Wyze cameras are removed from the babysitter's polling list; it now only monitors Reolink cameras for ONVIF reboot. DWB handles all Wyze snapshot health monitoring natively.
- **Snapshot stability fix**: `write_native_snapshot()` in `app/wyzebridge/native_alias.py` now passes `&cache=10s` to go2rtc's `frame.jpeg` endpoint. Without this parameter, every snapshot request created a new keyframe consumer inside go2rtc that was never cleaned up (known go2rtc issue #1733). Over minutes, affected streams accumulated 150-450 leaked consumers, triggering the health monitor's restart loop and causing `Snapshot timed out` errors in HomeKit. With the cache parameter, go2rtc serves a cached JPEG for repeated requests within 10 seconds, eliminating the consumer leak entirely.
- **WHEP proxy upstream split**: `upstream.go` (933 lines) split into 3 themed files — `upstream.go`, `upstream_sdp.go`, `upstream_websocket.go` — all in `package main` for direct test access.
- **Code quality remediation across 199 files**: bare `except Exception` clauses replaced with typed exceptions, debug `print()` replaced with `logging`, ruff formatting applied, mypy tightened. 5,643 lines of dead code removed.
- **go2rtc sidecar helpers**: `print()` → `logging`, typed exceptions with inline rationale, simplified producer-alias filters.
- **Snapshot schedule extraction**: sunrise/sunset scheduling moved to new `snapshot_schedule.py` using `astral`; old `bridge_utils_sunset.py` deleted.
- **Stream module cleanup**: unused `stream.py` Protocol stub deleted; `wyze_stream.py` simplified (71 lines removed).
- **TUTK FFI refactoring**: `tutk_structures.py`, `tutk_ffi.py`, `tutk_ioctl_mux.py` refactored for cleaner type safety and ioctl dispatch.
- **Tooling**: `.pre-commit-config.yaml` (ruff + mypy), `.github/workflows/quality.yml` CI, `pyproject.toml` with full lint/type/test config.
- **New probe scripts**: `wyze_cam_smoke_test.py`, `local_camera_smoke_test.py`, shared `ffmpeg_helpers.py` and `ha_bridge_probe.sh` libraries.
- **New tests**: 4 new test files covering camera smoke tests, uptime tests, and Reolink stability probes.
- **Documentation**: `docs/ARCHITECTURE.md` and `CONTRIBUTING.md` added for contributor onboarding.

## 4.4.0 Highlights

- **Architecture refactor**: 10 monolith candidates extracted into focused modules for testability and AI-navigability. No user-facing behavior changes; all tests pass with the same pre-existing failure set.
  - `SnapshotManager` → `app/wyzebridge/snapshot.py` (from `StreamManager`)
  - `WyzeIOTCSession` → `app/wyzebridge/tutk_session.py` (from `wyze_stream.py`)
  - Native alias readiness → `app/wyzebridge/native_alias.py` (from `go2rtc.py`)
  - HA cam options → `app/wyzebridge/camera_settings.py` (from `hass.py`)
  - WyzeApi helpers → `app/wyzebridge/wyze_api_helpers.py` (from `wyze_api.py`)
  - KVS/TUTK source selection → `app/wyzebridge/source_selector.py` (from `wyze_stream.py`)
  - Network snapshot utils → `app/wyzebridge/network_utils.py` (from `frontend.py`)
  - `go2rtc_sidecar.sh` embedded Python → `app/wyzebridge/go2rtc_sidecar_helpers.py` (shell script 771→252 lines)
  - `iotc.py` env/audio helpers → `app/wyzecam/iotc_helpers.py` (1307→1233 lines)
  - `whep_proxy/main.go` split into 6 themed files within package main
- **Build system**: `scripts/build.sh --check` now enforces that `home_assistant/` and `.ha_live_addon/` are generated from canonical `app/` + `runtime_overlays/`, eliminating three-way drift.
- This is an internal-only refactor release; all runtime behavior, ports, configs, and camera paths are unchanged from 4.3.5.

## 4.3.5 Highlights

- Native SD-only Home Assistant cameras no longer seed a competing fake HD `go2rtc` alias when per-camera feed config explicitly disables HD. This keeps the intended `*-sd` RTSP target stable for downstream consumers like Scrypted and HomeKit.
- When a selected native alias goes stale, the bridge now forces one fresh native preload before giving up on the native snapshot path. That narrows the split-brain case where producer metadata still exists but `frame.jpeg` has stopped returning real images.
- `HL_CAM4` snapshots get an optional hidden HD recovery lane before dropping to RTSP fallback. The lane is opt-in via the `GO2RTC_HD_RECOVERY_CAMERAS` env var (comma-separated camera/uri names, default empty = feature off): when enabled, each named camera seeds a hidden `-v4-hd-recovery` alias based on live proof that `subtype=hd` can return real JPEGs when the usual SD still-image lane is the one wedging. No specific camera is baked in by default.
- Native snapshot diagnostics now expose producer and go2rtc consumer counters, and empty `frame.jpeg` responses with active producers fail fast instead of burning the whole snapshot timeout. That makes native-wedge cases easier to prove and lets the bridge fall through sooner.
- This is a narrow reliability patch for the `4.3` line; the broader Home Assistant startup, snapshot, SD-only, WHEP, and packaging hygiene work remains from `4.3.2`.

## 4.3.4 Highlights

- WHEP reconnects now reject missing upstream peer connections as recoverable stream errors instead of panicking the proxy process during camera reconnect storms.
- Default camera behavior is now tuned for the validated 4.3 models: V3 keeps the established bridge path defaults, V4 defaults stay on stable KVS-first probing, and Bulb Cam SD feeds default to the bridge `sub` route unless explicitly overridden.
- This is a narrow reliability patch for the `4.3` line; the broader Home Assistant startup, snapshot, SD-only, WHEP, and packaging hygiene changes remain from `4.3.2`.

## 4.3.3 Highlights

- SD-only native `go2rtc` streams now stay selected when the quick per-stream readiness probe times out but the full `go2rtc` stream table still proves the alias is alive. This prevents a working `*-sd` feed from falling back to a dead main/KVS path under load.
- Snapshot consumers that capture stills from a stable SD RTSP feed benefit from the same fix because the bridge keeps advertising the working native alias instead of briefly publishing the wrong stream target.
- This is a narrow reliability patch for the `4.3` line; the broader Home Assistant startup, snapshot, SD-only, WHEP, and packaging hygiene changes remain from `4.3.2`.

## 4.3.2 Highlights

- Home Assistant startup now waits for the authenticated camera catalog and semantic `/api/ready` state before claiming the add-on is ready, so an empty catalog can no longer wipe native `go2rtc` aliases during boot.
- Snapshot refreshes now reject tiny, non-image, unchanged, and stale frames by content hash instead of trusting HTTP success or file timestamps.
- `SD_ONLY=true` is now treated as an authoritative Home Assistant mode: one SD feed per camera, HD paths hidden or rejected, and only `*-sd` native aliases exposed in the production proof path.
- The bundled WHEP proxy keeps recently healthy streams in a bounded recovering state during short reconnects, then recreates them if media does not return.
- Release packaging now excludes local agent notes, scratch files, option payloads, SSH env files, and private SDK values from public artifacts.
- Live proof is intentionally precise: Phase 1 snapshots, Phase 2 startup/API, Phase 3 SD-only, and Phase 5 overlay/API are green; the strict one-hour Phase 4 gate remains blocked by intermittent Frigate/Scrypted skipped-FPS blips even though WHEP media stayed ready through the hour.

## 4.3.1 Highlights

- Cached preview files are now validated as real images before the bridge serves them, blocking zero-byte files, HTML/login responses saved as `.jpg`, and repeated vertical-smear corruption.
- Thumbnail refreshes now prefer a fresh local snapshot path first, including one stream restart attempt, before falling back to a recent valid Wyze API thumbnail.
- Native `go2rtc` snapshots now reject `503`, empty, invalid, and byte-identical stale frames instead of silently replacing the cache with bad data.
- The Web UI now prefers the freshest valid main or `-sub` preview image, and `/health/details` includes `go2rtc` plus WHEP log tails for faster live debugging.
- Home Assistant startup now cleans stale `whep_proxy` supervisor loops before relaunching the proxy.

## 4.3 Highlights

- Home Assistant API snapshot mode now avoids slow RTSP snapshot attempts and uses the fast Wyze thumbnail path instead.
- `-sub` thumbnail requests can fall back to the base camera thumbnail, which helps Scrypted/HomeKit-style consumers that ask for substream image names.
- Native `go2rtc` V4 metadata now covers the validated SD alias as well as the main alias.
- Advanced Home Assistant users can set `GO2RTC_LAN_IP_OVERRIDES` when Wyze helper URLs keep a stale LAN address after DHCP changes; no private LAN overrides are built in.
- The late 4.2 WHEP and MediaMTX stability fixes are included in this release line, reducing stuck no-media sessions and port conflicts on shared Home Assistant hosts.

## 4.2 Highlights

- Home Assistant and root Docker runtimes now share the same native `go2rtc` bootstrap path, with the supported RTSP sidecar surface on `:19554`.
- Camera metadata, `/health/details`, and the Web UI now expose per-camera native-vs-bridge selection plus granular `HD` and `SD` feed publishing controls.
- `frontend.py`, `site.js`, and `index.html` are normalized across the three runtime trees where behavior should match, while preserving the intentional dev add-on `:55000` talkback loopback port.
- API-first native talkback remains limited to native-selected cameras, with uploaded-audio talkback validated on native-selected V4 paths.
- Public docs and add-on/package version surfaces are aligned for the `4.2.9` release; `4.3.0` adds the Home Assistant snapshot and native V4 follow-up fixes above.

## 4.2 Patch Releases

### 4.2.9

- Fixes the native `go2rtc` startup race that could make native-only cameras look offline after every bridge restart when alias readiness lagged behind the sidecar API coming up.
- Keeps `native_alias_ready` as a diagnostic signal, but no longer lets that transient startup state block native RTSP URL assignment or camera selection.
- Hardens the Home Assistant `4.2.9` validation path for the current host by honoring configured MediaMTX listener addresses from env and completing a 20-minute soak on the validated HLS reroute when `58888` was already occupied on-box.

### 4.2.8

- Stops the Home Assistant WHEP proxy from retrying forever when `/kvs-config/<camera>` reports `404 camera [x] not found`, so removed or unpublished bridge paths stop churning logs.
- Stops reusing stale startup-only WHEP sessions that never reached audio/video readiness, so bridge-managed substreams can be recreated cleanly instead of getting stuck behind dead `upstream_state="new"` sessions.
- On the live validation host, affected bridge-managed substreams settled back into connected audio/video paths, and the final bridge/Scrypted/Frigate log sweep cleared the old `400 Bad Request` / `503` churn.

### 4.2.7

- Fixes Home Assistant `HL_CAM3P` SD-only routing so validated native `go2rtc` `-sd` feeds can stay available even when the bridge-managed `-sub` path is intentionally absent or unreliable.
- Keeps ordinary V3-class substreams on the established bridge WebRTC/KVS path instead of broadly forcing them onto the TUTK fallback, while still letting `HL_CAM3P` and `HL_CAM4` take the special-case paths that were actually validated.
- Keeps Home Assistant `/api` and the Web UI camera catalog populated even when a camera's only enabled feed is native-only, and makes those native-selected cards advertise the real `:19554` RTSP target instead of a misleading bridge URL.

### 4.2.6

- Fixes the remaining Home Assistant ingress asset-path gap by switching the last hardcoded Web UI JavaScript includes to ingress-aware `url_for('static', ...)` calls, including the dedicated `/webrtc/<camera>` page.
- Makes the native `go2rtc` sidecar alias refresh follow the bridge's live published `/api` catalog when available, so disabled or filtered cameras and unsupported `HL_BC` HD feeds are no longer prepared as native aliases just because `/api/wyze` returned a helper URL.

### 4.2.5

- Refreshes preserved native `go2rtc` Wyze aliases from the current `/api/wyze` helper output at startup instead of freezing them when a seeded config already exists.
- Installs `curl` in the runtime images so the sidecar refresh helper can actually reach the local `go2rtc` API from the running add-on.

### 4.2.4

- Fixes Home Assistant feed-selection precedence so explicit `CAM_OPTIONS` `HD` and `SD` values override stale saved per-camera feed settings.
- Stops creating a competing bridge-managed `-sub` path when the SD feed is native-selected, which prevents stray `-sub` churn from surviving an explicit `SD=false` setting.

### 4.2.3

- Fixes the Web UI asset path regression that could leave the app page effectively unstyled even though camera content still rendered.
- Makes the frontend bind its own `static/` and `templates/` directories explicitly in all three runtime trees and uses ingress-aware template asset URLs so CSS and JS resolve correctly under Home Assistant ingress and normal app routing.

### 4.2.2

- Hardens MQTT motion semantics for Home Assistant and Scrypted workflows.
- Fixes the BOA/LAN motion topic so MQTT publishes land on `wyzebridge/<camera>/motion` instead of the wrong double-prefixed path.
- Normalizes BOA/LAN motion payloads to the same `1` and `2` contract already used by the API motion path.
- Uses bridge receipt time for event-driven motion latching and checks expiry from the monitor loop so `motion=2` no longer depends on a later UI or API poll.

### 4.2.1

- Fixes Home Assistant per-camera feed defaults so explicit `CAM_OPTIONS` `HD` and `SD` values apply at runtime even when `/config/wyze_camera_settings.json` is absent.
- Fixes the bundled Home Assistant native `go2rtc` sidecar so it no longer keeps the upstream default WebRTC listener on `:8555`, which blocked Frigate startup on shared Home Assistant hosts.
- Normalizes preserved `/config/go2rtc_wyze.yaml` listener blocks on startup so stale `api`, `rtsp`, or `webrtc` settings cannot silently bring the `:8555` conflict back.

## Quick Start

| Platform | Guide |
| :--- | :--- |
| Home Assistant add-on | [Install Guide](./docs/user_guide/install_ha.md) |
| Docker / Compose | [Docker Install Guide](./docs/user_guide/install_docker.md) |
| Upgrade from an older fork | [Upgrade Guide](./docs/user_guide/upgrade.md) |

## Camera Support Snapshot

The `4.2` release documents the current validated ceilings rather than promising ideal output on every camera.

| Model | Default path | Main stream | Substream | Current 4.2 limit |
| :--- | :--- | :--- | :--- | :--- |
| Wyze Cam V3 | Bridge WebRTC/KVS | Validated up to `1920x1080` on V3-class paths | Supported on firmware `4.36.10+`; validated V3-class substream paths have reached `1920x1080` | `QUALITY` values do not force a higher resolution than the camera/firmware actually provides |
| Wyze Cam V3 Pro | Bridge WebRTC/KVS | Validated up to `2560x1440` | Supported on firmware `4.58.0+`; a bridge `-sub` alias is not proof of a true low-bandwidth split on every install | On the Home Assistant validation host, the native `go2rtc` `-sd` alias reached `640x360` while bridge-managed `-sub` remained unreliable |
| Wyze Cam V4 | Bridge WebRTC/KVS, plus HA native `go2rtc` on `:19554` | Standard bridge path may remain `640x360`; validated HA native `go2rtc` main reached `2560x1440` | Standard bridge substream is not a reliable high/low split; validated HA native `go2rtc` sub reached `640x360` | TUTK fallback is not a reliable quality-rescue path in `4.2` |
| Wyze Bulb Cam | Bridge RTC/WHEP, plus HA native `go2rtc` on `:19554` | Validated compatibility, with current `4.2` main ceiling of `640x360` | No validated distinct main/sub split in `4.2`; `-sd` may mirror the same `640x360` feed | No software-only 2K path has been validated in this release |

Full caveats, firmware notes, and public limitations live in [Camera Support](./docs/user_guide/camera_support.md).

## Home Assistant Notes

- Supported native sidecar surface: `rtsp://<home-assistant-host>:19554/<camera-name>`
- Native `-sd` aliases may be available when the camera exposes a meaningful second stream.
- The sidecar API on `:11984` is an internal implementation detail and is not part of the stable public interface.
- The visible add-on name is `Docker Wyze Bridge`, while the existing Home Assistant slug stays in place for migration stability.
- The add-on keeps required login fields at the top, trims the HA form down to common day-to-day settings, uses clear optional-setting descriptions, and supports per-camera feed selection through `CAM_OPTIONS` and the Web UI with independent `HD`/`SD` toggles, per-feed kbps targets, surfaced feed resolution labels, and disabled controls for unsupported feeds. Rare power-user knobs are kept out of the HA form so the page stays manageable.
- Explicit per-camera `CAM_OPTIONS` `HD`/`SD` values now apply as the runtime defaults even when `/config/wyze_camera_settings.json` is absent, and beat any stale saved per-camera values. A native-selected SD feed does not create a competing bridge-managed `-sub` path.
- Preserved native `go2rtc` Wyze aliases are refreshed from the live helper output on every startup, so stale helper URLs do not keep a camera pinned to an old producer address after the host or camera IP changes.
- The bundled native `go2rtc` sidecar disables its default WebRTC listener so it no longer silently grabs host port `8555` and blocks Frigate on shared Home Assistant hosts.
- MQTT motion semantics are hardened for Home Assistant and Scrypted workflows: the BOA/LAN motion path publishes on `wyzebridge/<camera>/motion` with the same `1`/`2` payload contract as the API motion path, and event-driven motion expiry uses bridge receipt time with a deterministic monitor-loop check.

## Documentation

- [4.3 Release Notes](./docs/user_guide/release_notes_v4.md)
- [Camera Support and Limits](./docs/user_guide/camera_support.md)
- [Home Assistant Add-on Docs](./home_assistant/DOCS.md)
- [Troubleshooting Guide](./docs/user_guide/troubleshooting.md)
- [Upgrade Guide](./docs/user_guide/upgrade.md)

## Local Smoke Test

For a Mac-side camera smoke run that does not SSH into Home Assistant or mutate live config, use:

```bash
python3 scripts/local_camera_smoke_test.py --duration 3600 --heartbeat-interval 60 --test-name local-smoke
```

What it does:

- Reolink cameras use the existing direct RTSP probe with `ffprobe` metadata plus sustained `ffmpeg` streaming proof.
- Wyze cameras use bridge `/api/<camera>` connected-state checks plus `go2rtc` `frame.jpeg` fetches.
- The wrapper prints heartbeat updates while both child probes run and writes a combined summary under `tmp/`.

Required local env:

- `REOLINK_USERNAME`
- `REOLINK_PASSWORD`
- `WYZE_BRIDGE_API_KEY`

Optional local env:

- `WYZE_BRIDGE_BASE` — Wyze bridge base URL for Wyze probes (e.g. `http://<YOUR_HA_HOST>:5000`); set it when your bridge is not reachable at the default local address
- `WYZE_GO2RTC_BASE` — go2rtc base URL for Wyze probes (e.g. `http://<YOUR_HA_HOST>:1984`); set it when go2rtc is not reachable at the default local address

## Operational Notes

- Run `./scripts/run_master_local_gates.sh` for the non-live proof bundle: it checks the canonical app overlays, snapshot tests, packaging safety, WHEP proxy tests, the master status summary, and the Python suite.
- For troubleshooting common runtime issues, see the [Troubleshooting Guide](./docs/user_guide/troubleshooting.md).
- For a local mixed-camera smoke test, see the `Local Smoke Test` section above. The required local secret is `WYZE_BRIDGE_API_KEY`, not a repo hardcoded value.

## Attribution

This fork builds on work from several upstream projects:

- `idisposable/docker-wyze-bridge`
- `akeslo/docker-wyze-bridge`
- `kroo/wyzecam`
- `aler9/mediamtx`
- `AlexxIT/go2rtc`

The Home Assistant native sidecar work in `4.2` bundles `go2rtc` from `AlexxIT/go2rtc`, which is licensed under MIT. See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

> [!IMPORTANT]
> This project is not affiliated with Wyze Labs, Inc. Use at your own risk.
