#!/usr/bin/env python3
"""
Wyze RTSP Stability Probe
Adapted from reolink_direct_stability_probe.py for Wyze cameras via go2rtc.
Reports both coverage % (active streaming time) and frame availability %
(actual frames / expected frames per minute).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import queue
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffmpeg_helpers import (
    build_ffmpeg_rtsp_cmd,
    build_ffprobe_cmd,
    detect_timeout_flag,
    ensure_binary,
)

DEFAULT_RTSP_HOST = os.environ.get("WYZE_RTSP_HOST", "192.0.2.10")
DEFAULT_RTSP_PORT = os.environ.get("WYZE_RTSP_PORT", "8554")


def _rtsp_url(path: str) -> str:
    return f"rtsp://{DEFAULT_RTSP_HOST}:{DEFAULT_RTSP_PORT}/{path}"


DEFAULT_CAMERAS = [
    ("north_yard", _rtsp_url("north_yard_sd")),
    ("garage", _rtsp_url("garage_sd")),
    ("patio", _rtsp_url("cam_patio_sd")),
    ("south_yard", _rtsp_url("south_yard_sd")),
    ("side_yard", _rtsp_url("side_yard_sd")),
]

PROGRESS_WINDOW = 10.0
DEFAULT_RW_TIMEOUT_US = 3_000_000
DEFAULT_STATUS_INTERVAL = 30.0
END_GRACE = 5.0


@dataclass
class CameraConfig:
    label: str
    url: str


@dataclass
class AttemptState:
    byte_count: int = 0
    byte_lock: threading.Lock = field(default_factory=threading.Lock)

    def add_bytes(self, count: int) -> None:
        with self.byte_lock:
            self.byte_count += count

    def bytes_total(self) -> int:
        with self.byte_lock:
            return self.byte_count


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wyze RTSP stability probe")
    parser.add_argument("--camera", action="append", dest="cameras", help="Camera label to probe")
    parser.add_argument("--duration", type=int, default=600, help="Probe duration in seconds. Default: 600")
    parser.add_argument("--dropout-threshold", type=float, default=3.0, help="Dropout gap threshold. Default: 3.0")
    parser.add_argument("--restart-delay", type=float, default=1.0, help="Restart delay. Default: 1.0")
    parser.add_argument("--rw-timeout-us", type=int, default=DEFAULT_RW_TIMEOUT_US)
    parser.add_argument("--transport", choices=["tcp", "udp"], default="tcp")
    parser.add_argument("--status-interval", type=float, default=DEFAULT_STATUS_INTERVAL)
    parser.add_argument("--artifact-root", default="tmp")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    parser.add_argument("--debug", action="store_true", help="Verbose ffmpeg output for debugging")
    return parser.parse_args()


def ffprobe_metadata(ffprobe_path: str, timeout_flag: str | None, camera: CameraConfig, transport: str, timeout_us: int) -> dict[str, Any]:
    command = build_ffprobe_cmd(ffprobe_path, camera.url, transport, timeout_flag=timeout_flag, timeout_us=timeout_us)
    timeout_seconds = max(10.0, timeout_us / 1_000_000 + 5.0)
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stderr": f"probe timeout after {timeout_seconds:.1f}s"}
    metadata: dict[str, Any] = {"ok": result.returncode == 0, "returncode": result.returncode, "stderr": (result.stderr or "").strip()}
    if result.returncode != 0:
        return metadata
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        metadata["ok"] = False
        metadata["stderr"] = f"JSON parse failed: {exc}"
        return metadata
    video_streams = [s for s in payload.get("streams", []) if s.get("codec_type") == "video"]
    audio_streams = [s for s in payload.get("streams", []) if s.get("codec_type") == "audio"]
    video = video_streams[0] if video_streams else {}
    fps_str = video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/1"
    num, den = fps_str.split("/")
    metadata.update({"video_codec": video.get("codec_name"), "width": video.get("width"), "height": video.get("height"), "fps_str": fps_str, "fps": float(num) / float(den), "audio_streams": len(audio_streams), "format": payload.get("format", {}).get("format_name")})
    return metadata


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def iso_ts(run_start_wall: datetime, run_start_mono: float, moment_mono: float) -> str:
    return (run_start_wall + timedelta(seconds=moment_mono - run_start_mono)).isoformat()


def start_dropout(dropouts: list, run_start_wall: datetime, run_start_mono: float, start_mono: float) -> None:
    dropouts.append({"start": iso_ts(run_start_wall, run_start_mono, start_mono), "end": None, "duration_s": None})


def close_dropout(dropouts: list, run_start_wall: datetime, run_start_mono: float, start_mono: float, end_mono: float) -> None:
    if not dropouts:
        return
    latest = dropouts[-1]
    latest["end"] = iso_ts(run_start_wall, run_start_mono, end_mono)
    latest["duration_s"] = round(max(0.0, end_mono - start_mono), 3)


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def drain_stdout(stdout_pipe: Any, attempt_state: AttemptState) -> None:
    try:
        while True:
            chunk = stdout_pipe.read(65536)
            if not chunk:
                break
            attempt_state.add_bytes(len(chunk))
    finally:
        stdout_pipe.close()


def read_progress(stderr_pipe: Any, event_queue: queue.Queue[tuple[str, Any]]) -> None:
    progress_block: dict[str, str] = {}
    text_stream = io.TextIOWrapper(stderr_pipe, encoding="utf-8", errors="replace")
    try:
        for raw_line in text_stream:
            line = raw_line.strip()
            if not line:
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                if key.replace("_", "").isalnum() and key.lower() == key:
                    progress_block[key] = value
                    if key == "progress":
                        event_queue.put(("progress", progress_block.copy()))
                        progress_block.clear()
                    continue
            event_queue.put(("log", line))
    finally:
        if progress_block:
            event_queue.put(("progress", progress_block.copy()))
        text_stream.close()


def probe_camera(camera: CameraConfig, args: argparse.Namespace, ffprobe_timeout_flag: str | None, artifact_dir: Path, stop_event: threading.Event) -> dict[str, Any]:
    run_start_wall = now_local()
    run_start_mono = time.monotonic()
    run_end_mono = run_start_mono + args.duration
    samples_path = artifact_dir / "samples" / f"{camera.label}.csv"
    logs_path = artifact_dir / "logs" / f"{camera.label}.ffmpeg.log"
    metadata = ffprobe_metadata(args.ffprobe, ffprobe_timeout_flag, camera, args.transport, args.rw_timeout_us)

    declared_fps = metadata.get("fps", 0.0) if metadata.get("ok") else 0.0
    expected_per_minute = int(round(declared_fps * 60)) if declared_fps > 0 else 0
    minute_buckets: list[dict[str, Any]] = []
    current_minute = 1
    frames_this_minute = 0
    minute_start_mono = run_start_mono

    sample_history: deque[tuple[float, int, int]] = deque()
    fps_windows: list[float] = []
    kbps_windows: list[float] = []
    progress_fps_values: list[float] = []
    dropouts: list[dict[str, Any]] = []
    recent_errors: list[str] = []
    exit_codes: list[int] = []
    restart_gap = max(args.dropout_threshold * 2.0, args.dropout_threshold + 1.0)

    total_frames = 0
    total_bytes = 0
    attempts = 0
    first_frame_mono: float | None = None
    last_frame_mono: float | None = None
    open_dropout_mono: float | None = None
    last_status_mono = run_start_mono

    with samples_path.open("w", newline="", encoding="utf-8") as samples_file, logs_path.open("w", encoding="utf-8") as logs_file:
        writer = csv.DictWriter(samples_file, fieldnames=["timestamp", "attempt", "total_frames", "total_bytes", "window_fps", "window_kbps", "stall_seconds", "ffmpeg_reported_fps", "progress_state"])
        writer.writeheader()

        while time.monotonic() < run_end_mono and not stop_event.is_set():
            attempts += 1
            attempt_frames_base = total_frames
            attempt_bytes_base = total_bytes
            attempt_state = AttemptState()
            remaining = run_end_mono - time.monotonic()
            attempt_start_mono = time.monotonic()

            loglevel = "info" if args.debug else "warning"
            command = build_ffmpeg_rtsp_cmd(
                args.ffmpeg,
                camera.url,
                args.transport,
                f"{max(1.0, remaining):.3f}",
                loglevel=loglevel,
                extra_output_args=["-c", "copy"],
                output_format="null",
                output_target="/dev/null",
                progress_pipe=2,
            )

            logs_file.write(f"\n[{now_local().isoformat()}] attempt={attempts} start\n")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
            threading.Thread(target=drain_stdout, args=(process.stdout, attempt_state), daemon=True).start()
            threading.Thread(target=read_progress, args=(process.stderr, event_queue), daemon=True).start()

            attempt_frames = 0
            requested_stop = False
            forced_restart = False

            while True:
                now_mono = time.monotonic()
                if stop_event.is_set() and not requested_stop:
                    requested_stop = True
                    terminate_process(process)
                if now_mono >= run_end_mono + END_GRACE:
                    requested_stop = True
                    terminate_process(process)

                event_kind: str | None = None
                payload: Any = None
                try:
                    event_kind, payload = event_queue.get(timeout=0.5)
                except queue.Empty:
                    pass

                now_mono = time.monotonic()
                if event_kind == "log":
                    log_line = str(payload)
                    logs_file.write(log_line + "\n")
                    logs_file.flush()
                    if len(recent_errors) < 50:
                        recent_errors.append(log_line)
                elif event_kind == "progress":
                    progress = payload
                    new_attempt_frames = attempt_frames
                    if progress.get("frame", "").isdigit():
                        new_attempt_frames = int(progress["frame"])
                    if new_attempt_frames > attempt_frames:
                        attempt_frames = new_attempt_frames
                        total_frames = attempt_frames_base + attempt_frames
                        total_bytes = attempt_bytes_base + attempt_state.bytes_total()
                        if first_frame_mono is None:
                            first_frame_mono = now_mono
                        if open_dropout_mono is not None:
                            close_dropout(dropouts, run_start_wall, run_start_mono, open_dropout_mono, now_mono)
                            open_dropout_mono = None
                        last_frame_mono = now_mono
                    else:
                        total_bytes = attempt_bytes_base + attempt_state.bytes_total()

                    elapsed_minute = now_mono - minute_start_mono
                    if elapsed_minute >= 60.0:
                        minute_buckets.append({"minute": current_minute, "frames": frames_this_minute, "expected": expected_per_minute})
                        current_minute += 1
                        frames_this_minute = 0
                        minute_start_mono = now_mono
                    frames_this_minute = max(frames_this_minute, attempt_frames)

                    sample_history.append((now_mono, total_frames, total_bytes))
                    while sample_history and now_mono - sample_history[0][0] > PROGRESS_WINDOW:
                        sample_history.popleft()

                    window_fps: float | None = None
                    window_kbps: float | None = None
                    if len(sample_history) >= 2:
                        oldest_time, oldest_frames, oldest_bytes = sample_history[0]
                        elapsed = now_mono - oldest_time
                        if elapsed > 0:
                            window_fps = (total_frames - oldest_frames) / elapsed
                            window_kbps = ((total_bytes - oldest_bytes) * 8.0 / 1000.0) / elapsed
                            fps_windows.append(window_fps)
                            kbps_windows.append(window_kbps)

                    ffmpeg_reported_fps: float | None = None
                    reported_fps = progress.get("fps")
                    if reported_fps and reported_fps != "N/A":
                        try:
                            ffmpeg_reported_fps = float(reported_fps)
                            progress_fps_values.append(ffmpeg_reported_fps)
                        except ValueError:
                            pass

                    stall_seconds = now_mono - run_start_mono if last_frame_mono is None else max(0.0, now_mono - last_frame_mono)

                    writer.writerow({
                        "timestamp": iso_ts(run_start_wall, run_start_mono, now_mono),
                        "attempt": attempts,
                        "total_frames": total_frames,
                        "total_bytes": total_bytes,
                        "window_fps": "" if window_fps is None else f"{window_fps:.3f}",
                        "window_kbps": "" if window_kbps is None else f"{window_kbps:.3f}",
                        "stall_seconds": f"{stall_seconds:.3f}",
                        "ffmpeg_reported_fps": "" if ffmpeg_reported_fps is None else f"{ffmpeg_reported_fps:.3f}",
                        "progress_state": progress.get("progress", "continue"),
                    })
                    samples_file.flush()

                if (first_frame_mono is None and open_dropout_mono is None
                        and now_mono - run_start_mono >= args.dropout_threshold):
                    open_dropout_mono = run_start_mono
                    start_dropout(dropouts, run_start_wall, run_start_mono, open_dropout_mono)

                if args.status_interval > 0 and now_mono - last_status_mono >= args.status_interval:
                    elapsed = min(args.duration, max(0.0, now_mono - run_start_mono))
                    startup_display = f"{max(0.0, first_frame_mono - run_start_mono):.2f}" if first_frame_mono is not None else "pending"
                    stall_seconds = elapsed if last_frame_mono is None else max(0.0, now_mono - last_frame_mono)
                    partial_expected = int(round(declared_fps * elapsed)) if declared_fps > 0 else 0
                    fa_pct = (total_frames / partial_expected * 100) if partial_expected > 0 else 0.0
                    print(f"STATUS camera={camera.label} elapsed_s={elapsed:.1f}/{args.duration} attempt={attempts} frames={total_frames} startup_latency_s={startup_display} stall_s={stall_seconds:.2f} frame_availability_pct={fa_pct:.1f}%", flush=True)
                    last_status_mono = now_mono

                if (last_frame_mono is not None and open_dropout_mono is None
                        and now_mono - last_frame_mono >= args.dropout_threshold):
                    open_dropout_mono = last_frame_mono
                    start_dropout(dropouts, run_start_wall, run_start_mono, open_dropout_mono)

                if not requested_stop and process.poll() is None:
                    if last_frame_mono is None and now_mono - attempt_start_mono >= 20.0:
                        forced_restart = True
                        logs_file.write(f"[{now_local().isoformat()}] startup_stall_restart\n")
                        logs_file.flush()
                        terminate_process(process)
                    elif last_frame_mono is not None and now_mono - last_frame_mono >= restart_gap:
                        forced_restart = True
                        logs_file.write(f"[{now_local().isoformat()}] frame_stall_restart\n")
                        logs_file.flush()
                        terminate_process(process)

                if process.poll() is not None and event_queue.empty():
                    break

            total_frames = attempt_frames_base + attempt_frames
            total_bytes = attempt_bytes_base + attempt_state.bytes_total()
            exit_code = process.wait(timeout=3)
            exit_codes.append(exit_code)
            logs_file.write(f"[{now_local().isoformat()}] attempt={attempts} exit_code={exit_code}\n")
            logs_file.flush()

            if requested_stop or time.monotonic() >= run_end_mono:
                break
            if forced_restart and first_frame_mono is not None and open_dropout_mono is None:
                open_dropout_mono = time.monotonic()
                start_dropout(dropouts, run_start_wall, run_start_mono, open_dropout_mono)
            time.sleep(min(args.restart_delay, max(0.0, run_end_mono - time.monotonic())))

    run_end_wall = now_local()
    final_end_mono = min(time.monotonic(), run_end_mono)
    if open_dropout_mono is not None:
        close_dropout(dropouts, run_start_wall, run_start_mono, open_dropout_mono, final_end_mono)

    if frames_this_minute > 0 or current_minute <= (args.duration // 60):
        minute_buckets.append({"minute": current_minute, "frames": frames_this_minute, "expected": expected_per_minute})

    total_dropout_seconds = sum(item["duration_s"] or 0.0 for item in dropouts)
    startup_latency_seconds = None
    if first_frame_mono is not None:
        startup_latency_seconds = max(0.0, first_frame_mono - run_start_mono)

    active_seconds = 0.0
    if startup_latency_seconds is not None:
        active_seconds = max(0.0, args.duration - startup_latency_seconds - total_dropout_seconds)
    coverage_pct = (active_seconds / args.duration * 100.0) if args.duration > 0 else 0.0

    total_expected_frames = sum(b["expected"] for b in minute_buckets)
    total_received_frames = sum(b["frames"] for b in minute_buckets)
    frame_availability_pct = (total_received_frames / total_expected_frames * 100.0) if total_expected_frames > 0 else 0.0

    wall_avg_fps = (total_frames / args.duration) if args.duration > 0 else 0.0
    active_avg_fps = (total_frames / active_seconds) if active_seconds > 0 else None
    wall_avg_kbps = (total_bytes * 8.0 / 1000.0 / args.duration) if args.duration > 0 else 0.0
    active_avg_kbps = (total_bytes * 8.0 / 1000.0 / active_seconds) if active_seconds > 0 else None

    return {
        "label": camera.label,
        "url": camera.url,
        "metadata": metadata,
        "attempts": attempts,
        "restarts": max(0, attempts - 1),
        "exit_codes": exit_codes,
        "startup_latency_s": None if startup_latency_seconds is None else round(startup_latency_seconds, 3),
        "elapsed_s": round(min(args.duration, max(0.0, final_end_mono - run_start_mono)), 3),
        "streaming_seconds": round(active_seconds, 3),
        "coverage_pct": round(coverage_pct, 3),
        "frame_availability_pct": round(frame_availability_pct, 3),
        "frames_total": total_frames,
        "expected_total": total_expected_frames,
        "bytes_total": total_bytes,
        "wall_avg_fps": round(wall_avg_fps, 3),
        "active_avg_fps": None if active_avg_fps is None else round(active_avg_fps, 3),
        "wall_avg_kbps": round(wall_avg_kbps, 3),
        "active_avg_kbps": None if active_avg_kbps is None else round(active_avg_kbps, 3),
        "window_fps": {"min": None if not fps_windows else round(min(fps_windows), 3), "median": None if not fps_windows else round(statistics.median(fps_windows), 3), "p95": None if not fps_windows else round(percentile(fps_windows, 0.95) or 0.0, 3), "max": None if not fps_windows else round(max(fps_windows), 3)},
        "window_kbps": {"min": None if not kbps_windows else round(min(kbps_windows), 3), "median": None if not kbps_windows else round(statistics.median(kbps_windows), 3), "p95": None if not kbps_windows else round(percentile(kbps_windows, 0.95) or 0.0, 3), "max": None if not kbps_windows else round(max(kbps_windows), 3)},
        "ffmpeg_reported_fps": {"median": None if not progress_fps_values else round(statistics.median(progress_fps_values), 3), "p95": None if not progress_fps_values else round(percentile(progress_fps_values, 0.95) or 0.0, 3)},
        "dropouts": {"count": len(dropouts), "total_s": round(total_dropout_seconds, 3), "longest_s": None if not dropouts else round(max((item["duration_s"] or 0.0) for item in dropouts), 3), "events": dropouts},
        "minute_buckets": minute_buckets,
        "recent_errors": recent_errors[-10:],
        "artifacts": {"samples_csv": str(samples_path), "ffmpeg_log": str(logs_path)},
        "started_at": run_start_wall.isoformat(),
        "ended_at": run_end_wall.isoformat(),
    }


def write_summary_files(run_summary: dict[str, Any], artifact_dir: Path) -> None:
    summary_json_path = artifact_dir / "summary.json"
    summary_txt_path = artifact_dir / "summary.txt"
    summary_json_path.write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append("# Wyze RTSP Stability Probe")
    lines.append(f"started_at={run_summary['started_at']}")
    lines.append(f"ended_at={run_summary['ended_at']}")
    lines.append(f"duration_s={run_summary['duration_s']}")
    lines.append(f"actual_duration_s={run_summary['actual_duration_s']}")
    lines.append(f"interrupted={run_summary['interrupted']}")
    lines.append(f"artifact_dir={artifact_dir}")

    for camera in run_summary["cameras"]:
        lines.append("")
        lines.append(f"## {camera['label']}")
        lines.append(f"url={camera['url']}")
        meta = camera["metadata"]
        if meta.get("ok"):
            lines.append(f"metadata={meta.get('video_codec')} {meta.get('width')}x{meta.get('height')} @{meta.get('fps')}fps")
        else:
            lines.append(f"metadata=probe_failed: {meta.get('stderr', 'unknown')}")
        lines.append(f"coverage_pct={camera['coverage_pct']}%  frame_availability_pct={camera['frame_availability_pct']}%  frames={camera['frames_total']}/{camera['expected_total']}")
        lines.append(f"streaming_seconds={camera['streaming_seconds']}  startup_latency_s={camera.get('startup_latency_s', 'pending')}  dropouts={camera['dropouts']['count']} total_s={camera['dropouts']['total_s']}")
        lines.append(f"restarts={camera['restarts']} attempts={camera['attempts']} exit_codes={camera['exit_codes']}")
        lines.append(f"wall_avg_fps={camera['wall_avg_fps']} active_avg_fps={camera['active_avg_fps']} wall_avg_kbps={camera['wall_avg_kbps']}")
        if camera["minute_buckets"]:
            lines.append("minute_by_minute=")
            for b in camera["minute_buckets"]:
                pct = (b["frames"] / b["expected"] * 100) if b["expected"] > 0 else 0
                marker = "OK" if pct >= 90 else "DEGRADED" if pct >= 50 else "FAIL"
                lines.append(f"  Min {b['minute']:2d}: {b['frames']:4d}/{b['expected']:4d} = {pct:5.1f}% [{marker}]")
        if camera["dropouts"]["events"]:
            lines.append("dropout_events=")
            for event in camera["dropouts"]["events"]:
                lines.append(f"  start={event['start']} end={event['end']} duration_s={event['duration_s']}")
        if camera["recent_errors"]:
            lines.append("recent_errors=")
            for error in camera["recent_errors"]:
                lines.append(f"  {error}")

    summary_txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def failed_camera_summary(camera: CameraConfig, artifact_dir: Path, started_at: datetime, ended_at: datetime, error: Exception) -> dict[str, Any]:
    error_text = f"{type(error).__name__}: {error}"
    return {
        "label": camera.label, "url": camera.url,
        "metadata": {"ok": False, "stderr": error_text},
        "attempts": 0, "restarts": 0, "exit_codes": [],
        "startup_latency_s": None, "elapsed_s": 0.0, "streaming_seconds": 0.0,
        "coverage_pct": 0.0, "frame_availability_pct": 0.0,
        "frames_total": 0, "expected_total": 0, "bytes_total": 0,
        "wall_avg_fps": 0.0, "active_avg_fps": None,
        "wall_avg_kbps": 0.0, "active_avg_kbps": None,
        "window_fps": {"min": None, "median": None, "p95": None, "max": None},
        "window_kbps": {"min": None, "median": None, "p95": None, "max": None},
        "ffmpeg_reported_fps": {"median": None, "p95": None},
        "dropouts": {"count": 0, "total_s": 0.0, "longest_s": None, "events": []},
        "minute_buckets": [],
        "recent_errors": [error_text],
        "artifacts": {"samples_csv": str(artifact_dir / "samples" / f"{camera.label}.csv"), "ffmpeg_log": str(artifact_dir / "logs" / f"{camera.label}.ffmpeg.log")},
        "started_at": started_at.isoformat(), "ended_at": ended_at.isoformat(),
        "fatal_error": error_text,
    }


def main() -> int:
    args = parse_args()
    args.ffmpeg = ensure_binary(args.ffmpeg, "ffmpeg")
    args.ffprobe = ensure_binary(args.ffprobe, "ffprobe")

    camera_labels = args.cameras or [label for label, _ in DEFAULT_CAMERAS]
    camera_map = dict(DEFAULT_CAMERAS)
    camera_configs = [CameraConfig(label=label, url=camera_map[label]) for label in camera_labels if label in camera_map]
    if not camera_configs:
        raise SystemExit("No valid cameras specified.")

    run_started_at = now_local()
    ffprobe_timeout_flag = detect_timeout_flag(args.ffprobe)
    stop_event = threading.Event()
    interrupted_signal: list[int] = []

    def request_stop(signum: int, _frame: Any) -> None:
        if not stop_event.is_set():
            interrupted_signal.append(signum)
            stop_event.set()
            signal_name = signal.Signals(signum).name if signum in signal.Signals._value2member_map_ else str(signum)
            print(f"INTERRUPT signal={signal_name} writing partial summary after workers stop", file=sys.stderr, flush=True)

    timestamp = now_local().strftime("%Y%m%d_%H%M%S")
    artifact_dir = Path(args.artifact_root) / f"wyze_rtsp_probe_{timestamp}"
    (artifact_dir / "samples").mkdir(parents=True, exist_ok=False)
    (artifact_dir / "logs").mkdir(parents=True, exist_ok=False)

    camera_summaries: list[dict[str, Any] | None] = [None] * len(camera_configs)
    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        with ThreadPoolExecutor(max_workers=len(camera_configs)) as executor:
            futures = {
                executor.submit(probe_camera, camera=config, args=args, ffprobe_timeout_flag=ffprobe_timeout_flag, artifact_dir=artifact_dir, stop_event=stop_event): index
                for index, config in enumerate(camera_configs)
            }
            for future, index in futures.items():
                try:
                    camera_summaries[index] = future.result()
                except Exception as exc:
                    camera_summaries[index] = failed_camera_summary(
                        camera=camera_configs[index], artifact_dir=artifact_dir,
                        started_at=run_started_at, ended_at=now_local(), error=exc,
                    )
                    print(f"ERROR camera={camera_configs[index].label} failure={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    camera_summaries = [camera for camera in camera_summaries if camera is not None]
    run_ended_at = now_local()

    if not camera_summaries:
        raise SystemExit("No camera summaries were collected.")

    run_summary = {
        "started_at": min(camera["started_at"] for camera in camera_summaries),
        "ended_at": max([run_ended_at.isoformat(), *(camera["ended_at"] for camera in camera_summaries)]),
        "duration_s": args.duration,
        "actual_duration_s": round(max(0.0, (run_ended_at - run_started_at).total_seconds()), 3),
        "interrupted": bool(interrupted_signal),
        "interrupted_signal": None if not interrupted_signal else (signal.Signals(interrupted_signal[0]).name if interrupted_signal[0] in signal.Signals._value2member_map_ else str(interrupted_signal[0])),
        "transport": args.transport,
        "dropout_threshold_s": args.dropout_threshold,
        "status_interval_s": args.status_interval,
        "rw_timeout_us": args.rw_timeout_us,
        "ffmpeg": args.ffmpeg,
        "ffprobe": args.ffprobe,
        "timeout_flag": {"ffmpeg": None, "ffprobe": ffprobe_timeout_flag},
        "artifact_dir": str(artifact_dir),
        "cameras": camera_summaries,
    }
    write_summary_files(run_summary, artifact_dir)

    print(f"\nartifact_dir={artifact_dir}")
    for camera in camera_summaries:
        print(f"CAMERA {camera['label']} coverage_pct={camera['coverage_pct']}% frame_availability_pct={camera['frame_availability_pct']}% frames={camera['frames_total']}/{camera['expected_total']} dropouts={camera['dropouts']['count']} longest_dropout_s={camera['dropouts']['longest_s']}")
    print(f"summary_json={artifact_dir / 'summary.json'}")
    print(f"summary_txt={artifact_dir / 'summary.txt'}")
    return 130 if interrupted_signal else 0


if __name__ == "__main__":
    raise SystemExit(main())
