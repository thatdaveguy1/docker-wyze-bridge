# KVS-Only Migration and Log Noise Reduction Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. This repository has split source trees; changes MUST be applied identically to both `app/` and `.ha_live_addon/app/` unless otherwise noted.

**Goal:** Force all WebRTC-capable cameras to use the KVS proxy exclusively, disable TUTK streaming/controls to eliminate `IOTC_ER_TIMEOUT` thrash, and drastically reduce WHEP proxy and MediaMTX log noise.

**Architecture:** We will strip the trial logic so all WebRTC-capable cameras are treated as `is_kvs=True` permanently. We will short-circuit TUTK stream initialization in `WyzeStream.start()` and disable TUTK-only commands in the API. We will tune MediaMTX path generation and add logging rate-limits to the Go WHEP proxy to quiet the noisy observability layer.

**Tech Stack:** Python (Flask, MediaMTX integration), Go (WHEP proxy), Pytest

---

## Chunk 1: Unify KVS Routing and Deprecate Env Toggles

**Files:**
- Modify: `app/wyzecam/api_models.py`
- Modify: `.ha_live_addon/app/wyzecam/api_models.py`
- Modify: `test_all_rtc_proxy_config.py`
- Modify: `test_live_v4_kvs_routing.py`
- Modify: `test_v4_kvs_trial_routing.py`

- [ ] **Step 1:** In both `api_models.py` files, update the `is_kvs` property on `WyzeCamera` so it returns `True` for *all* models not in the `NO_WEBRTC` list. Remove the dependencies on `v4_kvs_trial_enabled()` and `all_rtc_trial_enabled()`.
- [ ] **Step 2:** Run `pytest test_all_rtc_proxy_config.py test_live_v4_kvs_routing.py test_v4_kvs_trial_routing.py test_frontend_kvs_config_auth.py test_kvs_stream_get_info.py`. Expect failures because the tests mock the env vars which are now ignored.
- [ ] **Step 3:** Update the tests to assert KVS routing is the default behavior without any env var patching.
- [ ] **Step 4:** Run tests again and verify they pass.
- [ ] **Step 5:** Commit: `git commit -m "feat: make KVS the default routing for all WebRTC cameras"`

---

## Chunk 2: Disable TUTK Stream Startup and Health Checks

**Files:**
- Modify: `app/wyzebridge/wyze_stream.py`
- Modify: `.ha_live_addon/app/wyzebridge/wyze_stream.py`

- [ ] **Step 1:** In `start()`, replace the conditional `if self.camera.is_kvs:` with a hard return if it's not a KVS camera (fail non-WebRTC cams gracefully), and *never* spawn `self.tutk_stream_process`.
- [ ] **Step 2:** In `health_check()`, remove the `-13, -19, -68` TUTK-specific error catching entirely.
- [ ] **Step 3:** In `stop()`, remove the TUTK process termination block since it will never be spawned.
- [ ] **Step 4:** Run `pytest` on all tests to ensure the stream mock behavior hasn't broken.
- [ ] **Step 5:** Commit: `git commit -m "refactor: remove TUTK stream initialization in favor of KVS proxy"`

---

## Chunk 3: Standardize MediaMTX Config and Increase Demand Timeout

**Files:**
- Modify: `app/wyzebridge/mtx_server.py`
- Modify: `.ha_live_addon/app/wyzebridge/mtx_server.py`

- [ ] **Step 1:** In `add_path()`, default all paths to the KVS syntax (`source: whep://localhost:8080/whep/{uri}`).
- [ ] **Step 2:** For KVS on-demand, increase `sourceOnDemandCloseAfter` from `1s` to `15s` to debounce the `dog-run` style rapid disconnect/reconnect churn that causes MediaMTX `processing errors`.
- [ ] **Step 3:** Run `pytest` on all tests to ensure mtx config parsing hasn't broken.
- [ ] **Step 4:** Commit: `git commit -m "fix: debounce MediaMTX KVS paths and increase close timeout"`

---

## Chunk 4: Deprecate TUTK API Controls and Update UI

**Files:**
- Modify: `app/wyzebridge/wyze_commands.py`
- Modify: `app/templates/index.html`
- Modify: `.ha_live_addon/app/templates/index.html`

- [ ] **Step 1:** In `wyze_commands.py`, comment out all TUTK-specific commands in `GET_CMDS` and `SET_CMDS` (e.g., `take_photo`, `night_vision`, `ptz_position`, `caminfo`, etc.), leaving only `state`, `power`, and `notifications`.
- [ ] **Step 2:** In both `index.html` templates, remove or comment out the `<button>` elements for `night_vision` and the PTZ/Camera Info UI blocks that depend on TUTK payload data.
- [ ] **Step 3:** Run `pytest test_web_ui_preview_streams.py` and ensure the UI still renders correctly.
- [ ] **Step 4:** Commit: `git commit -m "refactor: deprecate TUTK UI controls and API commands"`

---

## Chunk 5: Quiet the Go WHEP Proxy Spam

**Files:**
- Modify: `.ha_live_addon/whep_proxy/main.go`
- Modify: `scripts/deploy_ha_local_addon.sh`

- [ ] **Step 1:** In `main.go`, change `log.Printf("[WHEP_PROXY] Skipping empty keepalive frame")` to only log if a `WHEP_DEBUG` environment variable is true, or remove it entirely.
- [ ] **Step 2:** Rate-limit or remove `log.Printf("[WHEP_PROXY] Skipping keyframe request... video source unavailable")`.
- [ ] **Step 3:** Mute the `Requested keyframe (periodic downstream refresh)` log message.
- [ ] **Step 4:** In `deploy_ha_local_addon.sh`, ensure `whep_proxy/main.go` and `app/wyzebridge/wyze_commands.py` are explicitly copied during deployment.
- [ ] **Step 5:** Commit: `git commit -m "fix: silence WHEP proxy keepalive and keyframe spam"`

---

## Chunk 6: Deployment and Validation

- [ ] **Step 1:** Run `scripts/deploy_ha_local_addon.sh` to push all the above changes to the live Home Assistant instance.
- [ ] **Step 2:** Observe the Home Assistant add-on logs (`ha apps logs local_docker_wyze_bridge_local`). Confirm the `north-yard` `-13` timeout loop is gone.
- [ ] **Step 3:** Confirm the WHEP proxy `keepalive` and `keyframe` spam is gone.
- [ ] **Step 4:** Confirm `processing errors` on `north-yard` and `dog-run` have significantly dropped due to the increased `sourceOnDemandCloseAfter` timeout.
- [ ] **Step 5:** Test the live UI manually via the browser and confirm it loads and streams correctly without the TUTK controls.