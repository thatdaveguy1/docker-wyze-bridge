# Changelog

All notable changes to Docker Wyze Bridge are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [5.0.1] - 2026-08-06

### Fixed
- Fix HA add-on store install: add `image:` so Supervisor pulls the prebuilt multiarch GHCR image instead of attempting a local build on the HA box (which failed with an unknown build error).

## [5.0.0] - 2026-06-24

### Changed — WHEP proxy upstream split
- Split `whep_proxy/upstream.go` (933 lines removed) into 3 themed files:
  - `upstream.go` — session lifecycle and close logic only
  - `upstream_sdp.go` — SDP offer/answer handling and KVS signaling
  - `upstream_websocket.go` — websocket read/write loops and close-error classification
- All three files stay in `package main` so tests access fields directly
- Canonical source is `whep_proxy/`; `scripts/build.sh --check` syncs to `home_assistant/` and `.ha_live_addon/`

### Changed — go2rtc sidecar helpers cleanup
- `go2rtc_sidecar_helpers.py`: replaced bare `except Exception` with typed exception tuples (`OSError`, `ValueError`, `AttributeError`, `TypeError`) with inline rationale
- Replaced `print()` debug statements with `logging.getLogger(__name__)` calls
- Inlined throwaway `json.dumps` alias for clarity
- Simplified producer-alias filter expressions

### Changed — Snapshot schedule extraction
- Extracted sunrise/sunset snapshot scheduling from `bridge_utils_sunset.py` into new `snapshot_schedule.py` (77 lines)
- Uses `astral` library for sun position calculations with cached sun times
- Old `bridge_utils_sunset.py` deleted

### Changed — Stream module cleanup
- Deleted `app/wyzebridge/stream.py` (38-line `Stream` Protocol stub that was unused after the `StreamManager` refactor)
- `wyze_stream.py` simplified (71 lines removed): inlined redundant properties, removed dead delegation code

### Changed — Code quality remediation (49 files)
- All `except Exception` clauses across `app/wyzebridge/` and `app/wyzecam/` audited and replaced with specific exception types where possible
- All debug `print()` statements replaced with `logging.getLogger(__name__)` calls
- `tutk_structures.py` refactored (188 lines changed) for cleaner FFI struct definitions
- `tutk_ffi.py` refactored (122 lines changed) for improved type safety
- `tutk_ioctl_mux.py` refactored (55 lines changed) for cleaner ioctl dispatch
- Ruff formatting applied consistently across all Python files
- Mypy type checking tightened across `app/` and `tests/`

### Added — Tooling and CI
- `.pre-commit-config.yaml` with ruff (lint+format) and mypy hooks
- `.github/workflows/quality.yml` CI pipeline (lint on push/PR to main/dev)
- `pyproject.toml` with full ruff, mypy, and pytest configuration
- `package.json` for npm-managed tooling

### Added — Documentation
- `docs/ARCHITECTURE.md` — module dependency map and design decisions
- `CONTRIBUTING.md` — build/test workflow and code style guide

### Added — Camera probe scripts
- `scripts/wyze_cam_smoke_test.py` — Wyze camera RTSP smoke test (Python, 19KB)
- `scripts/wyze_cam_smoke_test.sh` — Shell wrapper for quick RTSP probing
- `scripts/local_camera_smoke_test.py` — Local camera uptime smoke test
- `scripts/ha_backyard_snapshot_repair.sh` — camera snapshot cache repair helper
- `scripts/ffmpeg_helpers.py` — Shared ffmpeg/ffprobe command-building library
- `scripts/ha_bridge_probe.sh` — Shared HA bridge probe shell functions

### Added — Tests
- `tests/test_wyze_cam_smoke_test.py`
- `tests/test_local_camera_smoke_test.py`
- `tests/test_local_camera_uptime_smoke_test.py`
- `tests/test_reolink_direct_stability_probe.py`
- All existing tests updated for the new module structure and typed exceptions

### Fixed
- `go2rtc_sidecar_helpers.py` no longer swallows `OSError` on missing config files — now logs and continues with empty alias set instead of silently failing
- `wyze_stream.py` dead delegation code removed that could mask `StreamManager` errors during reconnect
- Test pollution from bare `except Exception` in sidecar helpers that could hide real failures during test runs

### Removed
- `app/wyzebridge/stream.py` — unused Protocol stub
- `app/wyzebridge/bridge_utils_sunset.py` — replaced by `snapshot_schedule.py`
- 5,643 lines of dead code, duplicated logic, and bare exception handlers across 199 files

## [4.4.0] - 2026-06-19

### Changed — Architecture refactor
- Extracted `SnapshotManager` into `app/wyzebridge/snapshot.py` (from `StreamManager`)
- Extracted `WyzeIOTCSession` into `app/wyzebridge/tutk_session.py` (from `wyze_stream.py`)
- Extracted native alias readiness into `app/wyzebridge/native_alias.py` (from `go2rtc.py`)
- Extracted HA cam options into `app/wyzebridge/camera_settings.py` (from `hass.py`)
- Extracted WyzeApi helpers into `app/wyzebridge/wyze_api_helpers.py` (from `wyze_api.py`)
- Extracted KVS/TUTK source selection into `app/wyzebridge/source_selector.py` (from `wyze_stream.py`)
- Extracted network snapshot utils into `app/wyzebridge/network_utils.py` (from `frontend.py`)
- Extracted `go2rtc_sidecar.sh` embedded Python into `app/wyzebridge/go2rtc_sidecar_helpers.py` (shell script 771→252 lines)
- Extracted `iotc.py` env/audio helpers into `app/wyzecam/iotc_helpers.py` (1307→1233 lines)
- Extracted connect/auth state machine into `app/wyzecam/iotc_connect.py` (from `iotc.py`)
- Extracted talkback logic into `app/wyzebridge/native_talkback.py` (from `native_alias.py`)
- Split `tutk_protocol.py` into 4 themed modules (1404→232 facade + 3 modules under 600 lines)
- Split `tutk.py` into 3 themed modules (1217→20 facade + 3 modules under 600 lines)
- Split `whep_proxy/main.go` into 6 themed files within package main
- Split `whep_proxy/upstream.go` into 3 themed files (upstream, SDP, websocket)

### Changed — Build system
- `scripts/build.sh --check` now enforces that `home_assistant/` and `.ha_live_addon/` are generated from canonical `app/` + `runtime_overlays/`, eliminating three-way drift

### Changed — Code quality
- All 349 tests pass (previously 7 pre-existing failures)
- `pyproject.toml` added with ruff, mypy, and pytest configuration
- Debug `print()` statements replaced with `logging` calls
- `except Exception` clauses audited and documented
- `docs/ARCHITECTURE.md` added for contributor onboarding
- `CONTRIBUTING.md` added with build/test workflow

### Fixed
- `native_stream_info` now gates `native_selected` on `api_reachable` so Scrypted doesn't cache a dead URL when go2rtc is down
- Test pollution from `test_bridge_substream_support.py` module-level `sys.modules` stubs
- Missing `Session` attribute on test `requests` stubs
- Missing `preview_validation` and `wyze_api_helpers` fake modules in `test_wyze_api_user_fallback.py`

## [4.3.5] - 2026-05-19

### Fixed
- Native SD-only Home Assistant cameras no longer seed a competing fake HD `go2rtc` alias when per-camera feed config explicitly disables HD
- When a selected native alias goes stale, the bridge now forces one fresh native preload before giving up on the native snapshot path
- `HL_CAM4` snapshots get one extra hidden HD recovery lane before dropping to RTSP fallback
- Native snapshot diagnostics now expose producer and go2rtc consumer counters; empty `frame.jpeg` responses with active producers fail fast

## [4.3.2] - 2026-05-10

### Added
- KVS-backed WebRTC bridge path for modern Wyze models
- Native Home Assistant `go2rtc` RTSP sidecar on `:19554`
- WHEP proxy for HomeKit WebRTC signaling
- MediaMTX integration for RTSP/RTMP/HLS serving
- MQTT discovery for Home Assistant
- Motion event polling for Boa-based cameras
- Talkback (two-way audio) via go2rtc native aliases
- Snapshot fallback chain: go2rtc → RTSP → Wyze API thumbnail
