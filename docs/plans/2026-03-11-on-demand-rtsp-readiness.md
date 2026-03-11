# On-Demand RTSP Readiness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make RTSP appear usable in the API/menu when a camera can start on demand, instead of showing a disabled `offline` tag.

**Architecture:** Keep the change inside stream-entry formatting so templates keep working. Add a focused test for the new on-demand-ready state, then update the backend to mark RTSP and RTMP available for enabled cameras with valid local URLs even when no reader is attached, while still preserving hard blockers like disabled cameras.

**Tech Stack:** Python, Flask, pytest/unittest, existing Home Assistant mirrored addon source

---

### Task 1: Add failing readiness tests

**Files:**
- Modify: `test_web_ui_preview_streams.py`

**Step 1: Write the failing test**
- Add a test that asserts an enabled camera with `connected=False` and local RTSP/RTMP URLs still returns `available=True` for `rtsp` and `rtmp`.

**Step 2: Run test to verify it fails**
Run: `python3 -m pytest test_web_ui_preview_streams.py -q`
Expected: FAIL because current code still marks on-demand streams unavailable when `connected=False`.

**Step 3: Write minimal implementation**
- Update stream availability logic to distinguish on-demand-ready RTSP/RTMP from truly unavailable streams.

**Step 4: Run test to verify it passes**
Run: `python3 -m pytest test_web_ui_preview_streams.py -q`
Expected: PASS.

**Step 5: Commit**
Run:
```bash
git commit -m "fix: show on-demand rtsp streams as ready"
```

### Task 2: Mirror behavior into Home Assistant addon backend

**Files:**
- Modify: `.ha_live_addon/app/wyzebridge/web_ui.py`

**Step 1: Apply mirrored backend change**
- Copy the same on-demand stream availability logic into the HA addon mirror.

**Step 2: Verify targeted tests still pass**
Run: `python3 -m pytest test_web_ui_preview_streams.py -q`
Expected: PASS.

### Task 3: Deploy and live-verify

**Files:**
- Modify if needed: `tasks/todo.md`, `lessons.md`

**Step 1: Deploy**
Run: `bash scripts/deploy_ha_local_addon.sh`

**Step 2: Verify live API/menu**
- Check `/api` for `dog-run` / `garage` RTSP availability.
- Check the browser stream menu no longer shows a disabled `offline` tag for on-demand RTSP.

**Step 3: Commit final scope**
- Commit the stream-menu readiness and KVS auth fixes together.
