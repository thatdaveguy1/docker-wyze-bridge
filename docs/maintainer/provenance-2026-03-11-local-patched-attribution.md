# Provenance and Attribution for `v3.12.3-local`

## Baseline

- Base fork: `idisposable/docker-wyze-bridge`
- Upstream reference behind that fork: `mrlt8/docker-wyze-bridge`
- Local baseline in this repo before our additional commits: `b886424` (`v3.12.3`)
- Local committed history on top of that baseline before this cleanup commit:
  - `053172a` `Finalize live deployment handoff and port switchover`
  - `fa7bdc6` `Fix HA preview refresh and stream availability`
  - `f576a58` `fix: keep on-demand rtsp streams ready`

## Attribution Rules Used Here

- `idisposable`: credited for the fork baseline, release line, Home Assistant packaging lineage, and any unchanged inherited behavior we built on.
- `akeslo`: credited where the KVS/WebRTC architectural direction, signaling behavior, or camera-routing approach appears to align with the `akeslo/docker-wyze-bridge` branch, especially the move away from TUTK for newer cameras and use of `recipientClientId` in signaling.
- `Local work`: credited for all repo-specific adaptation, HA-local packaging, V4 trial toggles, WHEP proxy integration, deployment tooling, regression tests, UI changes, docs, and reliability fixes developed in this repo.
- If a file reflects multiple sources, credit is written as `base from idisposable`, `adapted from akeslo`, or `original local extension` rather than pretending it came from one source only.

## Exact Changed File Inventory by Area

### 1. Local bridge reliability and timeout work

Credit: base from `idisposable`, original local fixes and tests.

- Core runtime files: `app/run`, `app/wyze_bridge.py`, `app/wyzebridge/mtx_server.py`, `app/wyzebridge/stream_manager.py`, `app/wyzebridge/wyze_api.py`, `app/wyzebridge/wyze_stream.py`, `app/wyzecam/iotc.py`, `app/wyzecam/tutk/tutk_protocol.py`
- Regression and investigation files: `test_audio_session_lifetime.py`, `test_connect_watchdog_timeout.py`, `test_fixes.py`, `test_v4_auth_protocol.py`, `test_v4_timeout_fix.py`, `test_before_fix.sh`, `test_after_fix.sh`, `docs/maintainer/proposedPRs.md`

### 2. Web UI and stream-availability work

Credit: base from `idisposable`, original local UI and availability logic.

- Main app UI files: `app/frontend.py`, `app/static/site.css`, `app/static/site.js`, `app/templates/index.html`, `app/wyzebridge/web_ui.py`
- UI tests: `test_frontend_kvs_config_auth.py`, `test_web_ui_preview_streams.py`
- Planning and handoff docs: `docs/maintainer/LIVE-DEPLOYMENT.md`, `docs/maintainer/plans/2026-03-10-ha-preview-stream-fixes.md`, `docs/maintainer/plans/2026-03-11-on-demand-rtsp-readiness.md`

### 3. HA-local patched add-on packaging

Credit: base packaging lineage from `idisposable`, original local patched-add-on mirror and deployment workflow.

- Add-on metadata and docs: `.ha_live_addon/CHANGELOG.md`, `.ha_live_addon/DOCS.md`, `.ha_live_addon/README.md`, `.ha_live_addon/config.yaml`, `.ha_live_addon/config.yml`, `.ha_live_addon/Dockerfile`, `.ha_live_addon/icon.png`, `.ha_live_addon/translations/en.yml`
- Add-on app shell: `.ha_live_addon/app/requirements.txt`, `.ha_live_addon/app/run`, `.ha_live_addon/app/threads.py`, `.ha_live_addon/app/wyze_bridge.py`
- Add-on static and templates: `.ha_live_addon/app/static/bulma-toast.js`, `.ha_live_addon/app/static/bulma.css`, `.ha_live_addon/app/static/loading.svg`, `.ha_live_addon/app/static/notavailable.svg`, `.ha_live_addon/app/static/site.css`, `.ha_live_addon/app/static/site.js`, `.ha_live_addon/app/static/webrtc.js`, `.ha_live_addon/app/templates/base.html`, `.ha_live_addon/app/templates/index.html`, `.ha_live_addon/app/templates/login.html`, `.ha_live_addon/app/templates/m3u8.html`, `.ha_live_addon/app/templates/webrtc.html`
- Add-on bridge modules: `.ha_live_addon/app/frontend.py`, `.ha_live_addon/app/wyzebridge/__init__.py`, `.ha_live_addon/app/wyzebridge/auth.py`, `.ha_live_addon/app/wyzebridge/bridge_utils.py`, `.ha_live_addon/app/wyzebridge/bridge_utils_sunset.py`, `.ha_live_addon/app/wyzebridge/build_config.py`, `.ha_live_addon/app/wyzebridge/config.py`, `.ha_live_addon/app/wyzebridge/ffmpeg.py`, `.ha_live_addon/app/wyzebridge/hass.py`, `.ha_live_addon/app/wyzebridge/logging.py`, `.ha_live_addon/app/wyzebridge/mqtt.py`, `.ha_live_addon/app/wyzebridge/mtx_event.py`, `.ha_live_addon/app/wyzebridge/mtx_server.py`, `.ha_live_addon/app/wyzebridge/stream.py`, `.ha_live_addon/app/wyzebridge/stream_manager.py`, `.ha_live_addon/app/wyzebridge/web_ui.py`, `.ha_live_addon/app/wyzebridge/webhooks.py`, `.ha_live_addon/app/wyzebridge/wyze_api.py`, `.ha_live_addon/app/wyzebridge/wyze_commands.py`, `.ha_live_addon/app/wyzebridge/wyze_control.py`, `.ha_live_addon/app/wyzebridge/wyze_events.py`, `.ha_live_addon/app/wyzebridge/wyze_stream.py`, `.ha_live_addon/app/wyzebridge/wyze_stream_options.py`
- Add-on bundled runtime trees: `.ha_live_addon/app/lib/lib.amd64`, `.ha_live_addon/app/lib/lib.arm`, `.ha_live_addon/app/lib/lib.arm64`, `.ha_live_addon/app/wyzecam/__init__.py`, `.ha_live_addon/app/wyzecam/api.py`, `.ha_live_addon/app/wyzecam/api_models.py`, `.ha_live_addon/app/wyzecam/iotc.py`, `.ha_live_addon/app/wyzecam/py.typed`, `.ha_live_addon/app/wyzecam/kinesis/`, `.ha_live_addon/app/wyzecam/tutk/`, `.ha_live_addon/docker/.dockerignore`, `.ha_live_addon/docker/Dockerfile`, `.ha_live_addon/docker/Dockerfile.hwaccel`, `.ha_live_addon/docker/Dockerfile.multiarch`
- Deployment helpers: `scripts/deploy_ha_local_addon.sh`, `scripts/ha_ssh.sh`

### 4. V4 bypass, RTC routing, and WHEP proxy path

Credit: base from `idisposable`, KVS/WebRTC direction likely influenced by or adapted from `akeslo`, implementation and HA integration done locally.

- Proxy and signaling implementation: `.ha_live_addon/whep_proxy/go.mod`, `.ha_live_addon/whep_proxy/go.sum`, `.ha_live_addon/whep_proxy/main.go`, `.ha_live_addon/whep_proxy/main_test.go`
- V4/RTC add-on configuration and samples: `.ha_live_addon/options_payload.json`, `.ha_live_addon/options_payload2.json`, `.ha_live_addon/options_payload_garage.json`, `.ha_live_addon/options_payload_northyard.json`, `.orig_addon_options_payload.json`, `.orig_addon_options_payload_timeout.json`, `.v4lab_options_payload.json`, `scripts/run_v4kvs_trial.sh`
- Routing and regression tests: `test_all_rtc_proxy_config.py`, `test_live_v4_kvs_routing.py`, `test_v4_kvs_trial_routing.py`
- Evidence for `akeslo` credit:
  - `akeslo/docker-wyze-bridge` explicitly moved newer cameras such as V4 away from the legacy TUTK/LAN path toward cloud-signaled KVS WebRTC.
  - `akeslo` includes KVS signaling code that sends `recipientClientId` on `SDP_OFFER` and `ICE_CANDIDATE`.
  - Our local implementation does not directly mirror `akeslo` file-for-file, so the safest phrasing is influence/adaptation at the architectural level rather than a claim of direct file import.

### 5. Operational notes, changelog support, and process tracking

Credit: original local documentation and release-tracking work.

- Process tracking: `tasks/todo.md`, `lessons.md`
- Additional planning and runbook docs: `docs/maintainer/plans/2026-03-10-downstream-switchover.md`, `docs/maintainer/plans/2026-03-10-ha-stream-menu-copy-plan.md`, `docs/runbooks/wyze-scrypted-mqtt-runbook.md`
- This provenance record: `docs/maintainer/provenance-2026-03-11-local-patched-attribution.md`

## Excluded Local-Only Artifacts

These were intentionally not part of the safe commit because they are machine-local or raw debugging output rather than durable product history:

- `.live_local_info.json`, `.orig_addon_info.json`
- `.ha_live_addon/tasks_todo_snapshot.md`
- `console-errors-header-auth.txt`, `console-errors.txt`, `network-requests.txt`, `playwright-console-errors.txt`, `playwright-network-requests.txt`
- `garage-stream-menu.png`, `live-direct-wyzebridge-copy-buttons.png`, `wyzebridge-garage-menu-open.png`, `wyzebridge-live-ui.png`
- `deploy_to_ha.sh`, `lima-bridged.yaml`, `lima-simple.yaml`, `utm-vm/`

## Notes on Confidence

- Confidence is high for `idisposable` as the base fork and release line.
- Confidence is medium that the KVS/WebRTC migration direction and signaling shape should credit `akeslo` as an influence or adaptation source.
- Confidence is high that the HA-local packaging, WHEP proxy integration, V4/RTC toggles, deployment scripts, UI changes, tests, and operational docs are local work from this repo.
