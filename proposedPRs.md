# Proposed Upstream PRs for `idisposable/docker-wyze-bridge`

This file documents the fixes from this debugging session that look appropriate to upstream as pull requests.

## PR 1 - Fix child-process assertion during shutdown and stream stop

- Purpose: prevent `AssertionError: can only test a child process` during cleanup, restart, and stop paths.
- Why upstream: this is a general stability bug, not specific to Home Assistant or Wyze Cam V4.

### Problem

- `WyzeStream.stop()` could call `multiprocessing.Process.is_alive()` from the wrong process context.
- `WyzeBridge.clean_up()` could also run in child processes that inherited signal handlers from the main process.
- Result: bridge shutdown and stream cleanup could crash with `AssertionError: can only test a child process`.

### Files

- `app/wyze_bridge.py`
  - store `main_pid`
  - only run bridge cleanup in the main process
- `app/wyzebridge/wyze_stream.py`
  - guard `tutk_stream_process.is_alive()` with `try/except AssertionError`
  - treat cross-process access as not running instead of crashing

### Suggested tests to include

- existing regression coverage from `test_fixes.py`
- ideally add or upstream a focused unit test that exercises `WyzeStream.stop()` with a mock process raising `AssertionError`

### Suggested PR scope

- keep this PR narrow and only include the process-safety fix
- do not mix in timeout, V4, or instrumentation changes

## PR 2 - Keep TUTK session alive for the bridge read loop

- Purpose: fix a session lifetime bug where the stream session could close before ffmpeg bridge reads completed.
- Why upstream: this is a correctness bug that can affect normal streaming behavior, especially audio/control paths.

### Problem

- `start_tutk_stream()` could exit the session context too early.
- The ffmpeg bridge/read loop then continued after session teardown.
- Result: downstream code could touch cleared session state such as `av_chan_id`, leading to crashes or unstable streaming behavior.

### Files

- `app/wyzebridge/wyze_stream.py`
  - move bridge frame reading and ffmpeg piping fully inside the active session context

### Suggested tests to include

- `test_audio_session_lifetime.py`
  - proves frames are read while the session is still open

### Suggested PR scope

- keep this PR focused on session lifetime only
- if desired, it can be merged with PR 3 below, but separate is cleaner

## PR 3 - Fix connect watchdog and MediaMTX on-demand timeout sizing

- Purpose: make stream startup timeouts match the real connect and retry window instead of failing early.
- Why upstream: this is a general reliability fix for slower camera connects and retry scenarios.

### Problem

- stream health/watchdog logic used a hardcoded timeout window that was shorter than the actual retry budget
- MediaMTX `runOnDemandStartTimeout` also did not account for connect retries and retry delays
- Result: on-demand readers could give up before the bridge finished a legitimate connect attempt sequence

### Files

- `app/wyzebridge/wyze_stream.py`
  - add `connect_watchdog_timeout()`
  - use computed timeout in `health_check()` instead of a hardcoded value
- `app/wyzebridge/mtx_server.py`
  - add `run_on_demand_start_timeout()`
  - compute `pathDefaults.runOnDemandStartTimeout` from `CONNECT_TIMEOUT`, `CONNECT_RETRIES`, and `CONNECT_RETRY_DELAY`
- `app/wyzecam/iotc.py`
  - propagate configured connect timeout into sessions
  - retry `-13 IOTC_ER_TIMEOUT` connect errors based on configured retry settings

### Suggested tests to include

- `test_connect_watchdog_timeout.py`
  - verifies the stream remains in `CONNECTING` while still inside the computed retry window
- relevant tests from `test_v4_timeout_fix.py`
  - `test_session_uses_configured_connect_timeout`
  - `test_connect_retries_timeout_errors`
  - `test_mtx_run_on_demand_timeout_covers_connect_retries`

### Suggested PR scope

- keep this PR as a reliability/timeout PR
- do not include V4-only switches or debug logging

## Changes Not Recommended For Upstream PRs Yet

- `FORCE_V4_PARALLEL` in `app/wyzecam/iotc.py`
  - our HA testing showed it changed the failure mode from `-13` to `-19`, so it is not a validated fix
- V4-specific debug instrumentation in `app/wyzecam/iotc.py` and `app/wyzecam/tutk/tutk_protocol.py`
  - useful for investigation, but too noisy for upstream as-is
- any claimed fix for Wyze Cam V4 firmware `4.52.9.5332`
  - current evidence points to a firmware/TUTK compatibility issue, not a validated bridge-side code fix

## Recommended PR Order

1. PR 1 - child-process assertion fix
2. PR 2 - session lifetime fix
3. PR 3 - timeout/watchdog sizing fix

That ordering keeps the most clearly correct and least controversial fixes first.
