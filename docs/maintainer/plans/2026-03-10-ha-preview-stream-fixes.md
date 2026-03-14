# HA Preview And Stream Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the Home Assistant Wyze Bridge page so card previews refresh from API snapshots saved locally and the Streams menu accurately reflects live deployment availability.

**Architecture:** Move preview and stream availability policy into backend metadata produced by `web_ui.py`, then render the UI from that metadata instead of hard-coded assumptions. Keep API snapshots as the only preview refresh source for the HA page, while preserving explicit RTSP snapshot routes for manual use and clearly labeling unavailable stream types.

**Tech Stack:** Python, Jinja2, JavaScript, pytest, Home Assistant add-on config

---

### Task 1: Add backend behavior tests

**Files:**
- Create: `test_web_ui_preview_streams.py`
- Modify: `app/wyzebridge/web_ui.py`

**Step 1: Write failing tests**

Add tests covering:
- `format_stream()` returns API preview metadata when snapshot mode is `api`
- `format_stream()` marks disabled/unavailable stream entries with reasons
- explicit configured URLs override old fallback ports

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q test_web_ui_preview_streams.py`
Expected: FAIL because the metadata does not exist yet.

**Step 3: Write minimal implementation**

Add backend metadata generation for preview and streams in `app/wyzebridge/web_ui.py`.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q test_web_ui_preview_streams.py`
Expected: PASS.

### Task 2: Fix preview refresh path

**Files:**
- Modify: `app/frontend.py`
- Modify: `app/static/site.js`
- Modify: `app/templates/index.html`

**Step 1: Write failing test**

Extend `test_web_ui_preview_streams.py` or add one focused test proving API snapshot mode routes `/img/...` refreshes through API thumbnails instead of RTSP snapshots.

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q test_web_ui_preview_streams.py -k preview`
Expected: FAIL because API mode still falls back to RTSP snapshots.

**Step 3: Write minimal implementation**

Change backend and JS so preview refresh stays API-only in API mode and card preview elements receive the correct mode metadata.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q test_web_ui_preview_streams.py -k preview`
Expected: PASS.

### Task 3: Render accurate Streams menu

**Files:**
- Modify: `app/templates/index.html`
- Modify: `app/static/site.js`

**Step 1: Implement template rendering from backend entries**

Render available entries as links and unavailable ones as disabled items with reasons.

**Step 2: Mirror the same change into the live add-on copy**

Update `.ha_live_addon/app/...` files to match the source behavior.

**Step 3: Run targeted tests**

Run: `python3 -m pytest -q test_web_ui_preview_streams.py`
Expected: PASS.

### Task 4: Update live add-on runtime config

**Files:**
- Modify: `.ha_live_addon/options_payload.json`

**Step 1: Set API snapshots and explicit public URLs**

Set `SNAPSHOT` to `api15` and add explicit `WB_HLS_URL` and `WB_RTMP_URL` values alongside existing RTSP/WebRTC values.

**Step 2: Verify config contents**

Run: `python3 - <<'PY'
import json
data=json.load(open('.ha_live_addon/options_payload.json'))['options']
print(data['SNAPSHOT'], data['WB_RTSP_URL'], data['WB_WEBRTC_URL'], data['WB_HLS_URL'], data['WB_RTMP_URL'])
PY`
Expected: `api15` and promoted live URLs.

### Task 5: Validate end to end

**Files:**
- Modify: `.ha_live_addon/app/wyzebridge/web_ui.py`
- Modify: `.ha_live_addon/app/frontend.py`
- Modify: `.ha_live_addon/app/templates/index.html`
- Modify: `.ha_live_addon/app/static/site.js`

**Step 1: Run full tests**

Run: `python3 -m pytest -q`
Expected: PASS.

**Step 2: Validate live endpoints**

Run: `ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_name,codec_type -of json "rtsp://homeassistant.local:58554/garage"`
Expected: `h264` video and `pcm_mulaw` audio.

**Step 3: Validate UI in browser**

Use Playwright against the HA page to confirm:
- previews load with fresh non-placeholder images
- stream entries show correct enabled/disabled states
- no critical console errors
- no failed critical network requests for the camera card page
