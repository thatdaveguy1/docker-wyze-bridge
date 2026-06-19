# Architecture Refactor: Candidate #13 — FFmpeg Command Building

Last updated: 2026-06-19
Status: in-progress

## Objective

Extract shared ffmpeg/ffprobe command-building logic from 4 Python probe
scripts into one `scripts/ffmpeg_helpers.py` module. A change to the common
RTSP probe command flags (transport, timeout, progress pipe) propagates to
all scripts automatically.

## Success Criteria

1. `scripts/ffmpeg_helpers.py` exists and defines: `detect_timeout_flag`,
   `ensure_binary`, `build_ffprobe_cmd`, `build_ffmpeg_rtsp_cmd`.
2. Each migrated script imports from the shared module instead of
   duplicating the function bodies.
3. All existing tests pass (tests patch `MODULE.ffprobe_metadata` etc.,
   which still exist on each module via import).
4. New test `tests/test_ffmpeg_helpers.py` covers the shared module directly.
5. No script gains new subprocess calls or changes probe behavior.

## Duplication Surface (confirmed by reading all 4 scripts)

### Identical across 3 scripts
- `detect_timeout_flag(binary_path)` — runs `binary -h full`, checks for
  `rw_timeout`/`timeout`/`stimeout`. Identical in reolink, wyze_rtsp,
  local_camera_uptime.
- `ensure_binary(path, name)` — resolves/validates binary path. Slight
  variants: local_camera_uptime uses `shutil.which` fallback, reolink and
  wyze_rtsp check `Path.exists()`. Merge to use both.

### Nearly identical command construction across 3 scripts
- `ffprobe_metadata()` — all 3 scripts build the same ffprobe command:
  `[ffprobe, "-hide_banner", "-loglevel", "error", "-rtsp_transport",
  transport, ...timeout..., "-show_entries", "stream=...", "-of", "json",
  url]`. Response parsing differs per script (different fields extracted).
  Extract only the command builder; keep response parsing in each script.

### Inline ffmpeg command construction across 4 scripts
- `reolink_direct_stability_probe.py` — `[ffmpeg, "-hide_banner",
  "-nostats", "-loglevel", "warning", "-nostdin", "-rtsp_transport",
  transport, "-i", url, "-map", "0", "-c", "copy", "-t", duration, "-f",
  "mpegts", "-progress", "pipe:2", "pipe:1"]`
- `wyze_rtsp_stability_probe.py` — `[ffmpeg, "-hide_banner", "-nostats",
  "-loglevel", loglevel, "-nostdin", "-rtsp_transport", transport, "-i",
  url, "-t", duration, "-c", "copy", "-progress", "pipe:2", "-f", "null",
  "/dev/null"]`
- `local_camera_uptime_smoke_test.py` — `[ffmpeg, "-hide_banner",
  "-nostats", "-loglevel", "error", "-nostdin", "-rtsp_transport",
  transport, "-i", url, "-map", "0:v:0", "-an", "-t", duration, "-f",
  "null", "-", "-progress", "pipe:1"]`
- `wyze_cam_rtsp_smoke_test.py` — `["ffmpeg", "-hide_banner", "-loglevel",
  "error", "-progress", "pipe:1", "-rtsp_transport", "tcp", "-i", url,
  "-t", duration, "-f", "rawvideo", "-pix_fmt", "yuv420p", "/dev/null"]`

## Functions Extracted

### `detect_timeout_flag(binary_path: str) -> str | None`
Runs `binary -h full` and returns the first supported timeout flag name.

### `ensure_binary(path: str | None, name: str) -> str`
Resolves `path` or `shutil.which(name)`, validates existence, returns path.

### `build_ffprobe_cmd(ffprobe_path, url, transport, timeout_flag, timeout_us, entries) -> list[str]`
Returns the ffprobe command list. `entries` defaults to the standard
`stream=index,codec_name,codec_type,width,height,avg_frame_rate,r_frame_rate:format=format_name`.

### `build_ffmpeg_rtsp_cmd(ffmpeg_path, url, transport, duration, *, loglevel="warning", nostats=True, nostdin=True, output_format="null", output_target="/dev/null", progress_pipe=None, extra_input_args=None, extra_output_args=None) -> list[str]`
Returns the ffmpeg RTSP probe command list.

## Functions NOT Extracted (vary per script)

- `ffprobe_metadata()` response parsing — each script extracts different
  fields (fps vs avg_frame_rate, error key names differ)
- `probe_camera()` / `run_reolink_check()` — probe loop logic, output
  handling, and progress parsing are script-specific
- `count_frames()` in wyze_cam_rtsp_smoke_test — uses rawvideo output,
  different progress parsing

## Files Touched

### New
- `scripts/ffmpeg_helpers.py` — the shared module
- `tests/test_ffmpeg_helpers.py` — module tests

### Migrated (scripts)
- `scripts/reolink_direct_stability_probe.py` (tracer bullet)
- `scripts/wyze_rtsp_stability_probe.py`
- `scripts/local_camera_uptime_smoke_test.py`
- `scripts/wyze_cam_rtsp_smoke_test.py`

### Test impact
- `tests/test_reolink_direct_stability_probe.py` — patches
  `MODULE.ensure_binary`, `MODULE.detect_timeout_flag` — still works
  because imports create module-level references
- `tests/test_local_camera_uptime_smoke_test.py` — patches
  `MODULE.ffprobe_metadata`, `MODULE.ensure_binary`,
  `MODULE.detect_timeout_flag` — same pattern, no changes needed

## Tracer Bullet

`scripts/reolink_direct_stability_probe.py` is the tracer — it has all
duplicated patterns (`detect_timeout_flag`, `ensure_binary`,
`ffprobe_metadata`, inline ffmpeg cmd) and has test coverage. Migrate it
first, verify tests pass, then migrate the rest.
