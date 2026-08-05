#!/usr/bin/env python3
"""
Wyze Camera RTSP Smoke Test
Pulls full RTSP feed via ffmpeg, counts frames minute-by-minute,
and reports percentage of expected frames received.

Usage:
    python3 wyze_cam_rtsp_smoke_test.py

Requires ffmpeg + ffprobe (install via: brew install ffmpeg)
"""
import subprocess
import json
import time
import sys
import os
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffmpeg_helpers import build_ffmpeg_rtsp_cmd

# Camera display name -> RTSP URL (the path Scrypted/HomeKit actually uses).
# Override the go2rtc host/port with WYZE_RTSP_HOST / WYZE_RTSP_PORT.
WYZE_RTSP_HOST = os.environ.get("WYZE_RTSP_HOST", "192.0.2.10")
WYZE_RTSP_PORT = os.environ.get("WYZE_RTSP_PORT", "8554")
CAMERAS = [
    ("North Yard",  f"rtsp://{WYZE_RTSP_HOST}:{WYZE_RTSP_PORT}/north_yard_sd"),
    ("Garage",      f"rtsp://{WYZE_RTSP_HOST}:{WYZE_RTSP_PORT}/garage_sd"),
    ("Patio",       f"rtsp://{WYZE_RTSP_HOST}:{WYZE_RTSP_PORT}/cam_patio_sd"),
    ("South Yard",  f"rtsp://{WYZE_RTSP_HOST}:{WYZE_RTSP_PORT}/south_yard_sd"),
    ("Side Yard",   f"rtsp://{WYZE_RTSP_HOST}:{WYZE_RTSP_PORT}/side_yard_sd"),
]

TEST_MINUTES = 10
FFMPEG_TIMEOUT_PAD = 15   # seconds beyond the minute


def log(msg, logfile=None, to_stdout=True):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    if to_stdout:
        print(line)
    if logfile:
        with open(logfile, "a") as f:
            f.write(line + "\n")
            f.flush()


def check_ffmpeg():
    """Verify ffmpeg and ffprobe are available."""
    for binary in ["ffmpeg", "ffprobe"]:
        try:
            subprocess.run([binary, "-version"], capture_output=True, timeout=5)
        except FileNotFoundError:
            print(f"ERROR: {binary} not found. Install with: brew install ffmpeg")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR checking {binary}: {e}")
            sys.exit(1)


def probe_fps(url, timeout=15):
    """Use ffprobe to get the stream's declared frame rate."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "json", url],
            capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0:
            return None, r.stderr[:200]
        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        if not streams:
            return None, "no video stream found"
        fps_str = streams[0].get("r_frame_rate", "0/1")
        num, den = fps_str.split("/")
        return float(num) / float(den), None
    except subprocess.TimeoutExpired:
        return None, "ffprobe timeout"
    except Exception as e:
        return None, str(e)


def count_frames(url, duration_sec, progress_cb=None):
    """
    Run ffmpeg for `duration_sec` seconds, count received frames.
    Returns (frames_received, error_string_or_None).
    """
    cmd = build_ffmpeg_rtsp_cmd(
        "ffmpeg",
        url,
        "tcp",
        str(duration_sec),
        loglevel="error",
        nostats=False,
        nostdin=False,
        extra_output_args=["-pix_fmt", "yuv420p"],
        output_format="rawvideo",
        output_target="/dev/null",
        progress_pipe=1,
    )

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        last_frame = 0
        start_ts = time.time()

        # Stream progress lines from stdout
        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("frame="):
                    try:
                        last_frame = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
                    if progress_cb:
                        progress_cb(last_frame)

        # Wait for ffmpeg to finish (with padding for teardown)
        proc.wait(timeout=duration_sec + FFMPEG_TIMEOUT_PAD)

        if proc.returncode != 0:
            stderr_tail = proc.stderr.read()[-500:] if proc.stderr else ""
            return last_frame, f"ffmpeg exited {proc.returncode}: {stderr_tail}"

        return last_frame, None

    except subprocess.TimeoutExpired:
        proc.kill()
        return last_frame, "ffmpeg timeout"
    except Exception as e:
        return 0, str(e)


def test_camera(name, url, minutes, logfile):
    """Test one camera minute-by-minute. Returns result dict."""
    log(f"[{name}] Probing stream...", logfile)
    fps, err = probe_fps(url)
    if fps is None:
        log(f"[{name}] PROBE FAILED: {err}", logfile)
        return {
            "name": name,
            "url": url,
            "fps": None,
            "expected_per_minute": 0,
            "minutes": [],
            "error": err,
        }

    expected_per_minute = int(round(fps * 60))
    log(f"[{name}] Probed fps={fps:.2f}, expected={expected_per_minute}/min", logfile)

    minutes_data = []
    for minute in range(1, minutes + 1):
        log(f"[{name}] Minute {minute}/{minutes} starting...", logfile)
        frames, err = count_frames(url, 60)

        pct = (frames / expected_per_minute) * 100 if expected_per_minute > 0 else 0

        if err:
            status = "ERROR"
            log(f"[{name}] Minute {minute}: {frames}/{expected_per_minute} = {pct:.1f}%  ({err[:60]})", logfile)
        else:
            status = "OK" if pct >= 90 else "DEGRADED" if pct >= 50 else "FAIL"
            log(f"[{name}] Minute {minute}: {frames}/{expected_per_minute} = {pct:.1f}%  [{status}]", logfile)

        minutes_data.append({
            "minute": minute,
            "frames": frames,
            "expected": expected_per_minute,
            "pct": pct,
            "status": status,
            "error": err,
        })

    return {
        "name": name,
        "url": url,
        "fps": fps,
        "expected_per_minute": expected_per_minute,
        "minutes": minutes_data,
        "error": None,
    }


def print_summary(results, logfile):
    """Print final minute-by-minute summary table."""
    log("", logfile)
    log("=" * 80, logfile)
    log("FINAL SUMMARY — RTSP FRAME AVAILABILITY", logfile)
    log("=" * 80, logfile)
    log("", logfile)

    for res in results:
        name = res["name"]
        if res["error"] and res["fps"] is None:
            log(f"[{name}] STREAM UNREACHABLE — {res['error']}", logfile)
            continue

        total_frames = sum(m["frames"] for m in res["minutes"])
        total_expected = sum(m["expected"] for m in res["minutes"])
        avg_pct = (total_frames / total_expected) * 100 if total_expected > 0 else 0

        log(f"[{name}] Overall: {total_frames}/{total_expected} frames = {avg_pct:.1f}%", logfile)
        log(f"  Per-minute breakdown:", logfile)
        for m in res["minutes"]:
            marker = "✓" if m["status"] == "OK" else "~" if m["status"] == "DEGRADED" else "✗"
            log(f"    Min {m['minute']:2d}: {m['frames']:4d}/{m['expected']:4d} = {m['pct']:5.1f}% {marker} {m['status']}", logfile)
        log("", logfile)


def main():
    logfile = "/tmp/wyze_rtsp_smoke_test.log"
    # Clear old log
    with open(logfile, "w") as f:
        f.write("")

    check_ffmpeg()
    log(f"RTSP Smoke Test starting: {TEST_MINUTES} minutes per camera", logfile)
    log(f"Cameras: {', '.join(n for n, _ in CAMERAS)}", logfile)
    log(f"Log: {logfile}", logfile)
    log("-" * 60, logfile)

    results = []

    # Test cameras sequentially (ffmpeg is CPU-heavy; parallel might overload)
    for name, url in CAMERAS:
        log("", logfile)
        log(f"{'='*50}", logfile)
        log(f"Testing {name}: {url}", logfile)
        log(f"{'='*50}", logfile)
        res = test_camera(name, url, TEST_MINUTES, logfile)
        results.append(res)

    print_summary(results, logfile)
    log(f"Done. Full log: {logfile}", logfile)


if __name__ == "__main__":
    main()
