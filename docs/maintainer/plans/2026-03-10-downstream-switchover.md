# Downstream Switchover Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the repo-tracked downstream switchover to the promoted live Home Assistant ports without touching Frigate or external host-managed consumers.

**Architecture:** Update only repo-tracked handoff and Home Assistant live-addon consumer references that are meant to describe or parameterize the promoted deployment. Leave product-default ports and non-live sample configs alone unless they are explicitly part of the live handoff path. Validate with content searches plus live stream probing against the promoted endpoint.

**Tech Stack:** Markdown, YAML, JSON, shell verification with `rg`, `ffprobe`, and `git`

---

### Task 1: Identify repo-tracked live consumer references

**Files:**
- Modify: `LIVE-DEPLOYMENT.md`
- Modify: `tasks/todo.md`
- Modify: `.ha_live_addon/DOCS.md`
- Modify: `.ha_live_addon/options_payload.json`

**Step 1: Search for old and new live-port references**

Run: `rg -n "homeassistant\.local:(5000|8554|8889|58554|58889)|WB_RTSP_URL|WB_WEBRTC_URL" LIVE-DEPLOYMENT.md tasks/todo.md .ha_live_addon`
Expected: find the repo-tracked live-handoff files that still describe the old ports.

**Step 2: Confirm the files are live-consumer related, not generic product defaults**

Run: `git diff -- LIVE-DEPLOYMENT.md tasks/todo.md .ha_live_addon/DOCS.md .ha_live_addon/options_payload.json`
Expected: reviewable scope with no Frigate files involved.

### Task 2: Update the live consumer references

**Files:**
- Modify: `LIVE-DEPLOYMENT.md`
- Modify: `tasks/todo.md`
- Modify: `.ha_live_addon/DOCS.md`

**Step 1: Replace live handoff references that still point at old ports**

Apply minimal edits so the live add-on docs/examples point at `58554` and `58889` where they are describing the promoted deployment.

**Step 2: Mark the switchover tasks complete in tracking docs**

Apply minimal edits so `tasks/todo.md` and `LIVE-DEPLOYMENT.md` show commit and repo-tracked consumer switchover work as completed.

### Task 3: Verify content and live behavior

**Files:**
- Test: `LIVE-DEPLOYMENT.md`
- Test: `tasks/todo.md`
- Test: `.ha_live_addon/DOCS.md`

**Step 1: Re-run targeted searches**

Run: `rg -n "homeassistant\.local:(5000|8554|8889)|58554|58889|\[pending\]" LIVE-DEPLOYMENT.md tasks/todo.md .ha_live_addon/DOCS.md`
Expected: only intentional remaining old-port references, and no pending lines for the finished commit/switchover items.

**Step 2: Probe live RTSP endpoint**

Run: `ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_name,codec_type -of json 'rtsp://homeassistant.local:58554/garage'`
Expected: JSON output containing `h264` video and `pcm_mulaw` audio streams.

**Step 3: Check git diff and status**

Run: `git status --short && git diff -- LIVE-DEPLOYMENT.md tasks/todo.md .ha_live_addon/DOCS.md docs/plans/2026-03-10-downstream-switchover.md`
Expected: only intended tracked changes for the repo-only switchover and planning doc.

### Task 4: Commit the finished repo-tracked switchover

**Files:**
- Modify: `LIVE-DEPLOYMENT.md`
- Modify: `tasks/todo.md`
- Modify: `.ha_live_addon/DOCS.md`
- Create: `docs/plans/2026-03-10-downstream-switchover.md`

**Step 1: Stage the intended files**

Run: `git add LIVE-DEPLOYMENT.md tasks/todo.md .ha_live_addon/DOCS.md docs/plans/2026-03-10-downstream-switchover.md`
Expected: staged handoff, checklist, live-doc, and plan updates only.

**Step 2: Commit**

Run: `git commit -m "docs: finish live deployment handoff"`
Expected: successful commit describing the why of closing out the live switchover tracking.

**Step 3: Confirm clean post-commit state for these files**

Run: `git status --short -- LIVE-DEPLOYMENT.md tasks/todo.md .ha_live_addon/DOCS.md docs/plans/2026-03-10-downstream-switchover.md`
Expected: no remaining changes in the committed files.
