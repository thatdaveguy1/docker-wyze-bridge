# Architecture Refactor: Candidate #3 — Snapshot Pipeline Extraction

Last updated: 2026-06-19
Status: in-progress

## Objective

Extract the snapshot pipeline from `StreamManager` (474 lines) into a
`SnapshotManager` class in `app/wyzebridge/snapshot.py`. This is the
first step of the #3+#8+#9 sequence: once snapshots are extracted,
`StreamManager` becomes a dispatcher (candidate #8), and native alias
management can be consolidated (candidate #9).

## Current State

`StreamManager` mixes 4 responsibilities:
1. Stream lifecycle (add, get, stop_all, monitor_streams)
2. Snapshot pipeline (snap_all, get_snapshot, refresh_preview, go2rtc, rtsp, api fallback)
3. Command dispatch (send_cmd)
4. MQTT/health monitoring

The snapshot pipeline (lines 227-474, ~250 lines) is the most self-contained
piece. It has clear inputs (cam_name, stream info) and outputs (dict with
ok/source). 30+ tests in `test_go2rtc_snapshot_and_diagnostics.py` cover it.

## Approach

### 1. Create `app/wyzebridge/snapshot.py`

Move these methods and helpers into `SnapshotManager`:

**Module-level helpers (move):**
- `_snapshot_decode_failed()`
- `_snapshot_matches_existing()`
- `_finalize_snapshot_output()`

**SnapshotManager class:**
- Constructor takes a `StreamManager` reference (for streams, api, stop_flag access)
- Owns snapshot-specific state: `rtsp_snapshots`, `native_preloads`, `last_snap`, `monitor_snapshots_thread`
- Methods: `snap_all`, `get_snapshot`, `refresh_preview`, `_go2rtc_snapshot`,
  `get_rtsp_snap`, `rtsp_snap_popen`, `_restart_stream_for_snapshot`,
  `stop_subprocess`, `monitor_snapshots`, `remove_from_rtsp_snapshots`,
  `stop_monitoring`

### 2. Update `StreamManager` to delegate

- `self._snapshots = SnapshotManager(self)` in `__init__`
- Thin delegate methods: `get_snapshot()`, `refresh_preview()`, `snap_all()`,
  `monitor_snapshots()`, `stop_subprocess()`, `rtsp_snap_popen()`,
  `remove_from_rtsp_snapshots()`
- Properties for backward-compatible attribute access:
  `rtsp_snapshots`, `native_preloads`, `last_snap`, `monitor_snapshots_thread`
- `stop_all()` calls `self._snapshots.stop_monitoring()`
- `send_cmd()` calls `self._snapshots.snap_all()` etc.
- Remove `__slots__` entries for moved state (or keep them pointing to properties)

### 3. Update test patch targets

Tests in `test_go2rtc_snapshot_and_diagnostics.py` patch:
- `stream_manager_module.preload_native_stream` → `snapshot_module.preload_native_stream`
- `stream_manager_module.write_native_snapshot` → `snapshot_module.write_native_snapshot`
- `stream_manager_module.rtsp_snap_cmd` → `snapshot_module.rtsp_snap_cmd`
- `stream_manager_module.TimeoutExpired` → `snapshot_module.TimeoutExpired`

Also import `snapshot_module` in the test file.

### 4. Sync to overlays

`scripts/build.sh --check` verifies `home_assistant/app/` and
`.ha_live_addon/app/` match canonical `app/`. Sync the new file.

## Success Criteria

1. `app/wyzebridge/snapshot.py` exists with `SnapshotManager` class
2. `StreamManager` delegates snapshot calls to `SnapshotManager`
3. All existing tests pass (with updated patch targets)
4. `./scripts/build.sh --check` passes
5. External callers (`frontend.py`) unchanged — `wb.streams.get_snapshot()` still works

## What This Does NOT Do (future candidates)

- Does not extract interfaces for snapshot sources (go2rtc/rtsp/api adapters)
- Does not make the fallback chain event-driven
- Does not consolidate native alias management (candidate #9)
- Does not simplify StreamManager to a pure dispatcher (candidate #8)
