# Architecture Refactor: Multi-Candidate Extraction

Last updated: 2026-06-19
Status: complete (all 10 candidates committed)

## Summary

All 10 architecture review candidates have been extracted and committed.
Each candidate extracted a self-contained responsibility from a large
monolith into a new module, with tests updated to patch the new location.

## Completed Candidates

### #3 — Snapshot Pipeline Extraction (commit 38f0332)
- Extracted `SnapshotManager` from `StreamManager` (474 lines)
- New file: `app/wyzebridge/snapshot.py`
- `StreamManager` became a dispatcher

### #8 — StreamManager/WyzeStream Coupling (commit 478c8b2)
- Extracted TUTK session logic from `wyze_stream.py`
- New file: `app/wyzebridge/tutk_session.py`
- `WyzeStream` delegates to `WyzeIOTCSession`

### #9 — Native Alias Readiness (commit db7db0e)
- Extracted native alias readiness/selection/talkback from `go2rtc.py`
- New file: `app/wyzebridge/native_alias.py`
- `go2rtc.py` retains core API/probe functions

### #2 — Configuration Explosion (commit 1fc0221)
- Extracted HA cam options processing from `hass.py`
- New file: `app/wyzebridge/camera_settings.py`
- `hass.py` calls `apply_ha_cam_options()`

### #5 — WyzeApi Auth/Token/Cache/Credentials (commit 5413a3a)
- Extracted module-level helpers from `wyze_api.py`
- New file: `app/wyzebridge/wyze_api_helpers.py`
- `wyze_api.py` imports helpers, original definitions removed

### #4 — KVS/TUTK Source Selection (commit 29abca3)
- Extracted source selection logic from `wyze_stream.py`
- New file: `app/wyzebridge/source_selector.py`

### #10 — Camera Command Surface (commit aa60c3d area)
- Split `whep_proxy/main.go` into 6 themed files within package main
- Each file owns a responsibility: state, upstream, config, etc.

### #14 — Web UI Route Shapes (commit 4a2c8c9)
- Extracted network snapshot logic from `frontend.py`
- New file: `app/wyzebridge/network_utils.py`

### #12 — go2rtc_sidecar.sh Extract Embedded Python (commit bf16127)
- Extracted 404 lines of embedded Python heredocs from 771-line shell script
- New file: `app/wyzebridge/go2rtc_sidecar_helpers.py` (596 lines)
- Shell script reduced to 252 lines
- Extracted: `extract_yaml_aliases`, `list_active_producers`,
  `rewrite_go2rtc_config`, `generate_initial_config`,
  `resolve_bridge_api_token`, `payload_has_cameras`,
  `seed_go2rtc_aliases` (the main 400-line alias seeding logic)
- Tests updated to read both shell + Python via `_sidecar_helper_texts()`

### #11 — TUTK Session Monolith Split (commit 9a74dfa)
- Extracted module-level env/config helpers and audio codec mapping
  from 1307-line `app/wyzecam/iotc.py`
- New file: `app/wyzecam/iotc_helpers.py` (143 lines)
- Extracted: `hl_cam4_main_probe_mode`, `tutk_trace_enabled`,
  `hl_cam4_connect_watchdog_secs`, `truthy_env`,
  `configure_tutk_native_log`, `get_audio_sample_rate`,
  `resolve_audio_codec`, `redact_password`
- Kept `_log_tutk_trace` wrapper in iotc.py for test logger patching
- iotc.py reduced to 1233 lines

### #6 — Three-way app/ Duplication (already resolved)
- The build system (`scripts/build.sh --check`) already enforces that
  `home_assistant/` and `.ha_live_addon/` are generated from canonical
  `app/` + `runtime_overlays/`. No changes needed.

## Test Status

All tests pass with only 6 pre-existing failures:
- `test_addon_build_env_version_matches_public_addon_version` (version mismatch)
- `test_connect_watchdog_stops_wedged_dtls_connect` (pre-existing)
- `test_connect_watchdog_stops_wedged_parallel_connect` (pre-existing)
- `test_watchdog_induced_fail_connect_search_is_retried` (pre-existing)
- `test_connect_retries_timeout_errors` (pre-existing)
- `test_get_user_builds_fallback_profile_when_api_returns_none` (pre-existing)

Build check passes: `./scripts/build.sh --check` reports both targets match.
