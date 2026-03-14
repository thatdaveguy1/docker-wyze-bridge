# HA Stream Menu Availability and Copy Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure HA stream entries only show usable URLs, restore the copy buttons, deploy the updated code to `local_docker_wyze_bridge_local`, and validate everything against the live ingress page.

**Architecture:** Keep availability policy in `app/wyzebridge/web_ui.py` so the template simply renders `camera.streams`, and harden `app/static/site.js` so the copy handler is resilient within the dropdown. Deploy via the existing `scripts/deploy_ha_local_addon.sh` flow.

**Tech Stack:** Python, Flask/Jinja2, JavaScript, pytest, shell scripts, HA add-on rebuild & ingress testing.

---

### Task 1: Verify symptoms and existing logic

**Files:**
- Inspect: `app/wyzebridge/web_ui.py`, `app/templates/index.html`, `app/static/site.js`
- Inspect: `.ha_live_addon/...` copies of those files
- Inspect: `lessons.md`, `scripts/deploy_ha_local_addon.sh`

**Step 1:** Confirm `build_stream_entries` and copy handler exist locally.
**Step 2:** Compare repo files against `.ha_live_addon` to spot drift.
**Step 3:** Use the script/HA ingress to pull the live `static/site.js` and check for the copy handler.
**Step 4:** Reproduce the broken copy flow in the live UI, capture console logs, success/failure notifications, and clipboard access behavior.
**Step 5:** Inspect a camera stream row that should be disabled but isn’t, record the `camera` metadata (enabled/connected/webrtc/etc.) and the `stream` entry.

### Task 2: Add backend tests showing smart availability requirements

**Files:**
- Modify: `test_web_ui_preview_streams.py`
- Modify: `app/wyzebridge/web_ui.py`

**Step 1:** Add tests for disabled/disconnected cameras to assert `rtmp`/`rtsp` become unavailable with the correct `reason`.
**Step 2:** Add tests for missing `RTMP_URL`/`RTSP_URL` and for cameras without WebRTC support.
**Step 3:** Run `python3 -m pytest -q test_web_ui_preview_streams.py` to confirm the tests fail.
**Step 4:** Implement availability rules (see Task 4) and rerun the suite.

### Task 3: Add copy metadata regression coverage

**Files:**
- Modify: `test_web_ui_preview_streams.py`
- Modify: `app/wyzebridge/web_ui.py`

**Step 1:** Assert that `copy_text` retains the protocol/auth prefix for external URLs and `lan_copy_text` matches `lan_url` when different.
**Step 2:** Run `python3 -m pytest -q test_web_ui_preview_streams.py -k copy` to verify.

### Task 4: Implement backend availability policy

**Files:**
- Modify: `app/wyzebridge/web_ui.py`

**Step 1:** Refactor `build_stream_entries()` to evaluate these requirements per stream:
1. `webrtc`: camera supports WebRTC, bridge exposes WHEP, URL exists.
2. `hls`: keep disabled with the existing reason for now.
3. `rtmp`: camera `enabled`, `connected`, and `RTMP_URL` configured.
4. `rtsp`: camera `enabled`, `connected`, and `RTSP_URL` configured.
5. `fw_rtsp`: firmware flag and `connected` state.
6. `sd_card`: `boa_url` and `connected`.
7. `rtsp_snapshot`: non-API snapshot mode and camera enabled.
8. `api_thumbnail`: API snapshot mode.
**Step 2:** Encode reason precedence (`not supported` → `not configured` → `disabled` → `offline`).
**Step 3:** Ensure `_stream_entry()` still wraps URLs with credentials and fills `copy_text`/`lan_copy_text`.
**Step 4:** Run the tests from Task 2 and Task 3 to confirm the new logic.

### Task 5: Fix frontend copy interaction

**Files:**
- Modify: `app/static/site.js`
- Modify: `app/templates/index.html`
- Modify: `app/static/site.css` *only if necessary*

**Step 1:** Harden the click handler so it:
1. Delegates events (if necessary) to survive rendering inside the Streams dropdown.
2. Prevents dropdown navigation and justifies focusing on the button.
3. Retains notifications for success/failure and the manual prompt fallback.
4. Guards against missing `data-copy-text` values.
**Step 2:** Ensure each copy button in the template has `type="button"` and the appropriate dataset attributes.
**Step 3:** If needed, adjust CSS to keep the button clickable (e.g., `pointer-events` or `z-index`).
**Step 4:** Reproduce copy success/failure in the live UI after the JS change to confirm the fix works.

### Task 6: Deploy the updated code to the Home Assistant add-on

**Files:**
- Deploy via: `scripts/deploy_ha_local_addon.sh`

**Step 1:** Run `python3 -m pytest -q test_web_ui_preview_streams.py` to verify tests before deploy.
**Step 2:** Execute `scripts/deploy_ha_local_addon.sh` to copy files, rebuild `local_docker_wyze_bridge_local`, and check live `static/site.js`.
**Step 3:** Run `scripts/ha_ssh.sh curl -fsS "http://172.30.32.1:5000/static/site.js" | grep -n "copyToClipboard"` to ensure the deploy serves the new JS.

### Task 7: Validate in the live HA UI and document results

**Files:**
- Validate: live ingress page, browser console, `ffprobe` command

**Step 1:** Confirm disabled streams show reason tags and cannot be clicked.
**Step 2:** Confirm copy buttons copy/ prompt the expected URL for external and LAN rows.
**Step 3:** Ensure no console errors or failed network requests occur on the stream menu page.
**Step 4:** Run `ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_name,codec_type -of json "rtsp://homeassistant.local:58554/garage"` to prove streaming still works
**Step 5:** Update `tasks/todo.md` if the repo checklist needs reflecting.
**Step 6:** Summarize validations in the final answer with the commands/results from above.
