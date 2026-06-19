#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
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
from urllib.parse import quote, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ffmpeg_helpers
from ffmpeg_helpers import (
    build_ffmpeg_rtsp_cmd,
    build_ffprobe_cmd,
    detect_timeout_flag,
    ensure_binary,
)


DEFAULT_CAMERA_ORDER = ["south_driveway", "north_driveway", "doorbell"]
DEFAULT_CAMERA_IPS = {
    "south_driveway": "192.168.1.228",
    "north_driveway": "192.168.1.235",
    "doorbell": "192.168.1.69",
}
DEFAULT_RTSP_PATH = "h264Preview_01_main"
PROGRESS_WINDOW_SECONDS = 10.0
DEFAULT_RW_TIMEOUT_US = 3_000_000
DEFAULT_STATUS_INTERVAL_SECONDS = 30.0
END_GRACE_SECONDS = 5.0


@dataclass
class CameraConfig:
    label: str
    url: str
    url_source: str
    redacted_url: str


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
    parser = argparse.ArgumentParser(
        description=(
            "Run a direct Reolink RTSP stability soak and write human-readable, JSON, "
            "and CSV artifacts under tmp/."
        )
    )
    parser.add_argument(
        "--camera",
        action="append",
        dest="cameras",
        help="Camera label to probe. Repeat to limit the run to a subset. Defaults to south_driveway, north_driveway, and doorbell.",
    )
    parser.add_argument(
        "--camera-url",
        action="append",
        default=[],
        metavar="LABEL=RTSP_URL",
        help="Explicit RTSP URL override for a camera label.",
    )
    parser.add_argument(
        "--camera-ip",
        action="append",
        default=[],
        metavar="LABEL=IP",
        help="Override the default camera IP used when building direct URLs from shared credentials.",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("REOLINK_USERNAME"),
        help="Shared Reolink username. Defaults to REOLINK_USERNAME.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("REOLINK_PASSWORD"),
        help="Shared Reolink password. Defaults to REOLINK_PASSWORD.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=600,
        help="Wall-clock probe duration in seconds. Default: 600.",
    )
    parser.add_argument(
        "--test-name",
        default="",
        help="Optional label for this run, such as Deco or Asus. It is written into the summary and artifact directory name.",
    )
    parser.add_argument(
        "--dropout-threshold",
        type=float,
        default=3.0,
        help="Gap in seconds without new video frames that counts as a dropout. Default: 3.0.",
    )
    parser.add_argument(
        "--restart-delay",
        type=float,
        default=1.0,
        help="Delay before restarting ffmpeg after an unexpected disconnect. Default: 1.0.",
    )
    parser.add_argument(
        "--rw-timeout-us",
        type=int,
        default=DEFAULT_RW_TIMEOUT_US,
        help="Network read timeout passed to ffmpeg/ffprobe in microseconds. Default: 3000000.",
    )
    parser.add_argument(
        "--transport",
        choices=["tcp", "udp"],
        default="tcp",
        help="RTSP transport. Default: tcp.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=DEFAULT_STATUS_INTERVAL_SECONDS,
        help="How often to print per-camera progress updates to stdout. Use 0 to disable. Default: 30.",
    )
    parser.add_argument(
        "--artifact-root",
        default="tmp",
        help="Directory where the timestamped artifact folder is created. Default: tmp.",
    )
    parser.add_argument(
        "--ffmpeg",
        default=shutil.which("ffmpeg"),
        help="Path to ffmpeg. Defaults to the first ffmpeg on PATH.",
    )
    parser.add_argument(
        "--ffprobe",
        default=shutil.which("ffprobe"),
        help="Path to ffprobe. Defaults to the first ffprobe on PATH.",
    )
    return parser.parse_args()


def parse_label_map(values: list[str], option_name: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_value in values:
        if "=" not in raw_value:
            raise SystemExit(f"{option_name} expects LABEL=VALUE, got: {raw_value}")
        label, value = raw_value.split("=", 1)
        label = label.strip()
        value = value.strip()
        if not label or not value:
            raise SystemExit(f"{option_name} expects LABEL=VALUE, got: {raw_value}")
        parsed[label] = value
    return parsed


def redact_rtsp_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = host + port
    if parts.username or parts.password:
        netloc = f"<redacted>@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def build_direct_url(username: str, password: str, ip_address: str) -> str:
    encoded_user = quote(username, safe="")
    encoded_password = quote(password, safe="")
    return f"rtsp://{encoded_user}:{encoded_password}@{ip_address}:554/{DEFAULT_RTSP_PATH}"


def resolve_camera_configs(args: argparse.Namespace) -> list[CameraConfig]:
    explicit_urls = parse_label_map(args.camera_url, "--camera-url")
    camera_ips = DEFAULT_CAMERA_IPS.copy()
    camera_ips.update(parse_label_map(args.camera_ip, "--camera-ip"))

    ordered_labels = list(dict.fromkeys((args.cameras or DEFAULT_CAMERA_ORDER) + list(explicit_urls)))
    configs: list[CameraConfig] = []
    missing: list[str] = []

    for label in ordered_labels:
        if label in explicit_urls:
            url = explicit_urls[label]
            configs.append(
                CameraConfig(
                    label=label,
                    url=url,
                    url_source="explicit_url",
                    redacted_url=redact_rtsp_url(url),
                )
            )
            continue

        if not args.username or not args.password:
            missing.append(label)
            continue

        ip_address = camera_ips.get(label)
        if not ip_address:
            missing.append(label)
            continue

        url = build_direct_url(args.username, args.password, ip_address)
        configs.append(
            CameraConfig(
                label=label,
                url=url,
                url_source="shared_credentials+default_ip" if label in DEFAULT_CAMERA_IPS else "shared_credentials+override_ip",
                redacted_url=redact_rtsp_url(url),
            )
        )

    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(
            "Missing RTSP details for: "
            f"{missing_list}. Supply --camera-url LABEL=rtsp://... for each missing camera, "
            "or set REOLINK_USERNAME and REOLINK_PASSWORD and optionally override IPs with --camera-ip."
        )

    return configs


def ffprobe_metadata(
    ffprobe_path: str,
    timeout_flag: str | None,
    camera: CameraConfig,
    transport: str,
    timeout_us: int,
) -> dict[str, Any]:
    command = build_ffprobe_cmd(
        ffprobe_path,
        camera.url,
        transport,
        timeout_flag=timeout_flag,
        timeout_us=timeout_us,
    )
    timeout_seconds = max(10.0, timeout_us / 1_000_000 + 5.0)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": None,
            "stderr": f"metadata probe timed out after {timeout_seconds:.1f}s",
        }
    metadata: dict[str, Any] = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stderr": (result.stderr or "").strip(),
    }
    if result.returncode != 0:
        return metadata
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        metadata["ok"] = False
        metadata["stderr"] = f"metadata JSON parse failed: {exc}"
        return metadata

    video_streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"]
    video = video_streams[0] if video_streams else {}
    metadata.update(
        {
            "video_codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "avg_frame_rate": video.get("avg_frame_rate"),
            "r_frame_rate": video.get("r_frame_rate"),
            "audio_streams": len(audio_streams),
            "format": payload.get("format", {}).get("format_name"),
        }
    )
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
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    fraction = position - lower
    return lower_value + (upper_value - lower_value) * fraction


def seconds_to_iso(run_start_wall: datetime, run_start_monotonic: float, moment_monotonic: float) -> str:
    delta = moment_monotonic - run_start_monotonic
    return (run_start_wall + timedelta(seconds=delta)).isoformat()


def start_dropout(
    dropouts: list[dict[str, Any]],
    run_start_wall: datetime,
    run_start_monotonic: float,
    dropout_start_monotonic: float,
) -> None:
    dropouts.append(
        {
            "start": seconds_to_iso(run_start_wall, run_start_monotonic, dropout_start_monotonic),
            "end": None,
            "duration_s": None,
        }
    )


def close_dropout(
    dropouts: list[dict[str, Any]],
    run_start_wall: datetime,
    run_start_monotonic: float,
    dropout_start_monotonic: float,
    dropout_end_monotonic: float,
) -> None:
    if not dropouts:
        return
    latest = dropouts[-1]
    latest["end"] = seconds_to_iso(run_start_wall, run_start_monotonic, dropout_end_monotonic)
    latest["duration_s"] = round(max(0.0, dropout_end_monotonic - dropout_start_monotonic), 3)


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


def probe_camera(
    camera: CameraConfig,
    args: argparse.Namespace,
    ffprobe_timeout_flag: str | None,
    artifact_dir: Path,
    stop_event: threading.Event,
) -> dict[str, Any]:
    run_start_wall = now_local()
    run_start_monotonic = time.monotonic()
    run_end_monotonic = run_start_monotonic + args.duration
    samples_path = artifact_dir / "samples" / f"{camera.label}.csv"
    logs_path = artifact_dir / "logs" / f"{camera.label}.ffmpeg.log"
    metadata = ffprobe_metadata(
        ffprobe_path=args.ffprobe,
        timeout_flag=ffprobe_timeout_flag,
        camera=camera,
        transport=args.transport,
        timeout_us=args.rw_timeout_us,
    )

    sample_history: deque[tuple[float, int, int]] = deque()
    fps_windows: list[float] = []
    kbps_windows: list[float] = []
    progress_fps_values: list[float] = []
    dropouts: list[dict[str, Any]] = []
    recent_errors: list[str] = []
    exit_codes: list[int] = []
    restart_gap_seconds = max(args.dropout_threshold * 2.0, args.dropout_threshold + 1.0)

    total_frames = 0
    total_bytes = 0
    attempts = 0
    first_frame_monotonic: float | None = None
    last_frame_monotonic: float | None = None
    open_dropout_monotonic: float | None = None
    last_status_monotonic = run_start_monotonic

    with samples_path.open("w", newline="", encoding="utf-8") as samples_file, logs_path.open(
        "w", encoding="utf-8"
    ) as logs_file:
        writer = csv.DictWriter(
            samples_file,
            fieldnames=[
                "timestamp",
                "attempt",
                "total_frames",
                "total_bytes",
                "window_fps",
                "window_kbps",
                "stall_seconds",
                "ffmpeg_reported_fps",
                "progress_state",
            ],
        )
        writer.writeheader()

        while time.monotonic() < run_end_monotonic and not stop_event.is_set():
            attempts += 1
            attempt_frames_base = total_frames
            attempt_bytes_base = total_bytes
            attempt_state = AttemptState()
            remaining = run_end_monotonic - time.monotonic()
            attempt_start_monotonic = time.monotonic()
            command = build_ffmpeg_rtsp_cmd(
                args.ffmpeg,
                camera.url,
                args.transport,
                f"{max(1.0, remaining):.3f}",
                loglevel="warning",
                extra_output_args=["-map", "0", "-c", "copy"],
                output_format="mpegts",
                output_target="pipe:1",
                progress_pipe=2,
            )

            logs_file.write(f"\n[{now_local().isoformat()}] attempt={attempts} start\n")
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
            stdout_thread = threading.Thread(target=drain_stdout, args=(process.stdout, attempt_state), daemon=True)
            stderr_thread = threading.Thread(target=read_progress, args=(process.stderr, event_queue), daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            attempt_frames = 0
            requested_stop = False
            forced_restart = False

            while True:
                now_monotonic = time.monotonic()
                if stop_event.is_set() and not requested_stop:
                    requested_stop = True
                    terminate_process(process)
                if now_monotonic >= run_end_monotonic + END_GRACE_SECONDS:
                    requested_stop = True
                    terminate_process(process)

                event_kind: str | None = None
                payload: Any = None
                try:
                    event_kind, payload = event_queue.get(timeout=0.5)
                except queue.Empty:
                    pass

                now_monotonic = time.monotonic()
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
                        if first_frame_monotonic is None:
                            first_frame_monotonic = now_monotonic
                        if open_dropout_monotonic is not None:
                            close_dropout(
                                dropouts,
                                run_start_wall,
                                run_start_monotonic,
                                open_dropout_monotonic,
                                now_monotonic,
                            )
                            open_dropout_monotonic = None
                        last_frame_monotonic = now_monotonic
                    else:
                        total_bytes = attempt_bytes_base + attempt_state.bytes_total()

                    sample_history.append((now_monotonic, total_frames, total_bytes))
                    while sample_history and now_monotonic - sample_history[0][0] > PROGRESS_WINDOW_SECONDS:
                        sample_history.popleft()

                    window_fps: float | None = None
                    window_kbps: float | None = None
                    if len(sample_history) >= 2:
                        oldest_time, oldest_frames, oldest_bytes = sample_history[0]
                        elapsed = now_monotonic - oldest_time
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
                            ffmpeg_reported_fps = None

                    if last_frame_monotonic is None:
                        stall_seconds = now_monotonic - run_start_monotonic
                    else:
                        stall_seconds = max(0.0, now_monotonic - last_frame_monotonic)

                    writer.writerow(
                        {
                            "timestamp": seconds_to_iso(run_start_wall, run_start_monotonic, now_monotonic),
                            "attempt": attempts,
                            "total_frames": total_frames,
                            "total_bytes": total_bytes,
                            "window_fps": "" if window_fps is None else f"{window_fps:.3f}",
                            "window_kbps": "" if window_kbps is None else f"{window_kbps:.3f}",
                            "stall_seconds": f"{stall_seconds:.3f}",
                            "ffmpeg_reported_fps": ""
                            if ffmpeg_reported_fps is None
                            else f"{ffmpeg_reported_fps:.3f}",
                            "progress_state": progress.get("progress", "continue"),
                        }
                    )
                    samples_file.flush()

                if (
                    first_frame_monotonic is None
                    and open_dropout_monotonic is None
                    and now_monotonic - run_start_monotonic >= args.dropout_threshold
                ):
                    open_dropout_monotonic = run_start_monotonic
                    start_dropout(dropouts, run_start_wall, run_start_monotonic, open_dropout_monotonic)

                if args.status_interval > 0 and now_monotonic - last_status_monotonic >= args.status_interval:
                    elapsed = min(args.duration, max(0.0, now_monotonic - run_start_monotonic))
                    startup_latency_display = "pending"
                    if first_frame_monotonic is not None:
                        startup_latency_display = f"{max(0.0, first_frame_monotonic - run_start_monotonic):.2f}"
                    stall_seconds = elapsed if last_frame_monotonic is None else max(0.0, now_monotonic - last_frame_monotonic)
                    print(
                        "STATUS "
                        f"camera={camera.label} elapsed_s={elapsed:.1f}/{args.duration} "
                        f"attempt={attempts} frames={total_frames} bytes={total_bytes} "
                        f"startup_latency_s={startup_latency_display} stall_s={stall_seconds:.2f}",
                        flush=True,
                    )
                    last_status_monotonic = now_monotonic

                if (
                    last_frame_monotonic is not None
                    and open_dropout_monotonic is None
                    and now_monotonic - last_frame_monotonic >= args.dropout_threshold
                ):
                    open_dropout_monotonic = last_frame_monotonic
                    start_dropout(dropouts, run_start_wall, run_start_monotonic, open_dropout_monotonic)

                if not requested_stop and process.poll() is None:
                    if last_frame_monotonic is None and now_monotonic - attempt_start_monotonic >= restart_gap_seconds:
                        forced_restart = True
                        logs_file.write(
                            f"[{now_local().isoformat()}] attempt={attempts} startup_stall_restart_s={restart_gap_seconds}\n"
                        )
                        logs_file.flush()
                        terminate_process(process)
                    elif (
                        last_frame_monotonic is not None
                        and now_monotonic - last_frame_monotonic >= restart_gap_seconds
                    ):
                        forced_restart = True
                        logs_file.write(
                            f"[{now_local().isoformat()}] attempt={attempts} frame_stall_restart_s={restart_gap_seconds}\n"
                        )
                        logs_file.flush()
                        terminate_process(process)

                if process.poll() is not None and event_queue.empty():
                    break

            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            total_frames = attempt_frames_base + attempt_frames
            total_bytes = attempt_bytes_base + attempt_state.bytes_total()
            exit_code = process.wait(timeout=3)
            exit_codes.append(exit_code)
            logs_file.write(f"[{now_local().isoformat()}] attempt={attempts} exit_code={exit_code}\n")
            logs_file.flush()

            if requested_stop or time.monotonic() >= run_end_monotonic:
                break

            if forced_restart and first_frame_monotonic is not None and open_dropout_monotonic is None:
                open_dropout_monotonic = time.monotonic()
                start_dropout(dropouts, run_start_wall, run_start_monotonic, open_dropout_monotonic)

            time.sleep(min(args.restart_delay, max(0.0, run_end_monotonic - time.monotonic())))

    run_end_wall = now_local()
    final_end_monotonic = min(time.monotonic(), run_end_monotonic)
    elapsed_seconds = max(0.0, final_end_monotonic - run_start_monotonic)
    if open_dropout_monotonic is not None:
        close_dropout(
            dropouts,
            run_start_wall,
            run_start_monotonic,
            open_dropout_monotonic,
            final_end_monotonic,
        )

    total_dropout_seconds = sum(item["duration_s"] or 0.0 for item in dropouts)
    startup_latency_seconds = None
    if first_frame_monotonic is not None:
        startup_latency_seconds = max(0.0, first_frame_monotonic - run_start_monotonic)

    active_seconds = 0.0
    if startup_latency_seconds is not None:
        active_seconds = max(0.0, args.duration - startup_latency_seconds - total_dropout_seconds)
    coverage_pct = (active_seconds / args.duration * 100.0) if args.duration > 0 else 0.0
    wall_avg_kbps = (total_bytes * 8.0 / 1000.0 / args.duration) if args.duration > 0 else 0.0
    wall_avg_fps = (total_frames / args.duration) if args.duration > 0 else 0.0
    active_avg_kbps = (total_bytes * 8.0 / 1000.0 / active_seconds) if active_seconds > 0 else None
    active_avg_fps = (total_frames / active_seconds) if active_seconds > 0 else None

    summary = {
        "label": camera.label,
        "url": camera.redacted_url,
        "url_source": camera.url_source,
        "metadata": metadata,
        "attempts": attempts,
        "restarts": max(0, attempts - 1),
        "exit_codes": exit_codes,
        "startup_latency_s": None if startup_latency_seconds is None else round(startup_latency_seconds, 3),
        "elapsed_s": round(elapsed_seconds, 3),
        "streaming_seconds": round(active_seconds, 3),
        "streaming_pct": round(coverage_pct, 3),
        "coverage_pct": round(coverage_pct, 3),
        "frames_total": total_frames,
        "bytes_total": total_bytes,
        "wall_avg_fps": round(wall_avg_fps, 3),
        "active_avg_fps": None if active_avg_fps is None else round(active_avg_fps, 3),
        "wall_avg_kbps": round(wall_avg_kbps, 3),
        "active_avg_kbps": None if active_avg_kbps is None else round(active_avg_kbps, 3),
        "window_fps": {
            "min": None if not fps_windows else round(min(fps_windows), 3),
            "median": None if not fps_windows else round(statistics.median(fps_windows), 3),
            "p95": None if not fps_windows else round(percentile(fps_windows, 0.95) or 0.0, 3),
            "max": None if not fps_windows else round(max(fps_windows), 3),
        },
        "window_kbps": {
            "min": None if not kbps_windows else round(min(kbps_windows), 3),
            "median": None if not kbps_windows else round(statistics.median(kbps_windows), 3),
            "p95": None if not kbps_windows else round(percentile(kbps_windows, 0.95) or 0.0, 3),
            "max": None if not kbps_windows else round(max(kbps_windows), 3),
        },
        "ffmpeg_reported_fps": {
            "median": None if not progress_fps_values else round(statistics.median(progress_fps_values), 3),
            "p95": None if not progress_fps_values else round(percentile(progress_fps_values, 0.95) or 0.0, 3),
        },
        "dropouts": {
            "count": len(dropouts),
            "total_s": round(total_dropout_seconds, 3),
            "longest_s": None
            if not dropouts
            else round(max((item["duration_s"] or 0.0) for item in dropouts), 3),
            "events": dropouts,
        },
        "recent_errors": recent_errors[-10:],
        "artifacts": {
            "samples_csv": str(samples_path),
            "ffmpeg_log": str(logs_path),
        },
        "started_at": run_start_wall.isoformat(),
        "ended_at": run_end_wall.isoformat(),
    }
    return summary


def format_metadata(metadata: dict[str, Any]) -> str:
    if not metadata.get("ok"):
        error = metadata.get("stderr") or f"ffprobe returncode={metadata.get('returncode')}"
        return f"metadata probe failed: {error}"
    width = metadata.get("width")
    height = metadata.get("height")
    rate = metadata.get("avg_frame_rate") or metadata.get("r_frame_rate")
    codec = metadata.get("video_codec") or "unknown"
    audio_streams = metadata.get("audio_streams", 0)
    return f"{codec} {width}x{height} avg_frame_rate={rate} audio_streams={audio_streams}"


def write_summary_files(run_summary: dict[str, Any], artifact_dir: Path) -> None:
    summary_json_path = artifact_dir / "summary.json"
    summary_txt_path = artifact_dir / "summary.txt"
    summary_json_path.write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append("# Reolink Direct Stability Probe")
    lines.append(f"test_name={run_summary['test_name']}")
    lines.append(f"started_at={run_summary['started_at']}")
    lines.append(f"ended_at={run_summary['ended_at']}")
    lines.append(f"duration_s={run_summary['duration_s']}")
    lines.append(f"actual_duration_s={run_summary['actual_duration_s']}")
    lines.append(f"interrupted={run_summary['interrupted']}")
    lines.append(f"interrupted_signal={run_summary['interrupted_signal']}")
    lines.append(f"artifact_dir={artifact_dir}")
    lines.append(f"ffmpeg={run_summary['ffmpeg']}")
    lines.append(f"ffprobe={run_summary['ffprobe']}")
    lines.append(f"timeout_flag={run_summary['timeout_flag']}")

    for camera in run_summary["cameras"]:
        lines.append("")
        lines.append(f"## {camera['label']}")
        lines.append(f"url={camera['url']}")
        lines.append(f"url_source={camera['url_source']}")
        lines.append(f"metadata={format_metadata(camera['metadata'])}")
        lines.append(
            "summary="
            f"elapsed_s={camera.get('elapsed_s', camera['streaming_seconds'] + camera['dropouts']['total_s'])} streaming_seconds={camera['streaming_seconds']} streaming_pct={camera['streaming_pct']} "
            f"coverage_pct={camera['coverage_pct']} wall_avg_fps={camera['wall_avg_fps']} "
            f"active_avg_fps={camera['active_avg_fps']} wall_avg_kbps={camera['wall_avg_kbps']} "
            f"active_avg_kbps={camera['active_avg_kbps']}"
        )
        lines.append(
            "dropouts="
            f"count={camera['dropouts']['count']} total_s={camera['dropouts']['total_s']} "
            f"longest_s={camera['dropouts']['longest_s']}"
        )
        lines.append(
            "restarts="
            f"attempts={camera['attempts']} restarts={camera['restarts']} exit_codes={camera['exit_codes']}"
        )
        lines.append(
            "window_fps="
            f"min={camera['window_fps']['min']} median={camera['window_fps']['median']} "
            f"p95={camera['window_fps']['p95']} max={camera['window_fps']['max']}"
        )
        lines.append(
            "window_kbps="
            f"min={camera['window_kbps']['min']} median={camera['window_kbps']['median']} "
            f"p95={camera['window_kbps']['p95']} max={camera['window_kbps']['max']}"
        )
        if camera["dropouts"]["events"]:
            lines.append("dropout_events=")
            for event in camera["dropouts"]["events"]:
                lines.append(
                    f"  start={event['start']} end={event['end']} duration_s={event['duration_s']}"
                )
        if camera["recent_errors"]:
            lines.append("recent_errors=")
            for error in camera["recent_errors"]:
                lines.append(f"  {error}")

    summary_txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def failed_camera_summary(
    camera: CameraConfig,
    artifact_dir: Path,
    started_at: datetime,
    ended_at: datetime,
    error: Exception,
) -> dict[str, Any]:
    error_text = f"{type(error).__name__}: {error}"
    return {
        "label": camera.label,
        "url": camera.redacted_url,
        "url_source": camera.url_source,
        "metadata": {
            "ok": False,
            "returncode": None,
            "stderr": f"probe worker failed before summary generation: {error_text}",
        },
        "attempts": 0,
        "restarts": 0,
        "exit_codes": [],
        "startup_latency_s": None,
        "elapsed_s": 0.0,
        "streaming_seconds": 0.0,
        "streaming_pct": 0.0,
        "coverage_pct": 0.0,
        "frames_total": 0,
        "bytes_total": 0,
        "wall_avg_fps": 0.0,
        "active_avg_fps": None,
        "wall_avg_kbps": 0.0,
        "active_avg_kbps": None,
        "window_fps": {
            "min": None,
            "median": None,
            "p95": None,
            "max": None,
        },
        "window_kbps": {
            "min": None,
            "median": None,
            "p95": None,
            "max": None,
        },
        "ffmpeg_reported_fps": {
            "median": None,
            "p95": None,
        },
        "dropouts": {
            "count": 0,
            "total_s": 0.0,
            "longest_s": None,
            "events": [],
        },
        "recent_errors": [error_text],
        "artifacts": {
            "samples_csv": str(artifact_dir / "samples" / f"{camera.label}.csv"),
            "ffmpeg_log": str(artifact_dir / "logs" / f"{camera.label}.ffmpeg.log"),
        },
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "fatal_error": error_text,
    }


def main() -> int:
    args = parse_args()
    args.ffmpeg = ensure_binary(args.ffmpeg, "ffmpeg")
    args.ffprobe = ensure_binary(args.ffprobe, "ffprobe")
    camera_configs = resolve_camera_configs(args)
    run_started_at = now_local()
    test_name = args.test_name.strip()
    test_slug = ""
    if test_name:
        test_slug = "_" + "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in test_name)

    ffprobe_timeout_flag = detect_timeout_flag(args.ffprobe)
    stop_event = threading.Event()
    interrupted_signal: list[int] = []

    def request_stop(signum: int, _frame: Any) -> None:
        if not stop_event.is_set():
            interrupted_signal.append(signum)
            stop_event.set()
            signal_name = signal.Signals(signum).name if signum in signal.Signals._value2member_map_ else str(signum)
            print(
                f"INTERRUPT signal={signal_name} writing partial summary after workers stop",
                file=sys.stderr,
                flush=True,
            )

    timestamp = now_local().strftime("%Y%m%d_%H%M%S")
    artifact_dir = Path(args.artifact_root) / f"reolink_direct_stability{test_slug}_{timestamp}"
    (artifact_dir / "samples").mkdir(parents=True, exist_ok=False)
    (artifact_dir / "logs").mkdir(parents=True, exist_ok=False)

    camera_summaries: list[dict[str, Any] | None] = [None] * len(camera_configs)
    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        with ThreadPoolExecutor(max_workers=len(camera_configs)) as executor:
            futures = {
                executor.submit(
                    probe_camera,
                    camera=config,
                    args=args,
                    ffprobe_timeout_flag=ffprobe_timeout_flag,
                    artifact_dir=artifact_dir,
                    stop_event=stop_event,
                ): index
                for index, config in enumerate(camera_configs)
            }
            for future, index in futures.items():
                try:
                    camera_summaries[index] = future.result()
                except Exception as exc:
                    camera_summaries[index] = failed_camera_summary(
                        camera=camera_configs[index],
                        artifact_dir=artifact_dir,
                        started_at=run_started_at,
                        ended_at=now_local(),
                        error=exc,
                    )
                    print(
                        f"ERROR camera={camera_configs[index].label} failure={type(exc).__name__}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
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
        "test_name": test_name or None,
        "interrupted": bool(interrupted_signal),
        "interrupted_signal": None
        if not interrupted_signal
        else (
            signal.Signals(interrupted_signal[0]).name
            if interrupted_signal[0] in signal.Signals._value2member_map_
            else str(interrupted_signal[0])
        ),
        "transport": args.transport,
        "dropout_threshold_s": args.dropout_threshold,
        "status_interval_s": args.status_interval,
        "rw_timeout_us": args.rw_timeout_us,
        "ffmpeg": args.ffmpeg,
        "ffprobe": args.ffprobe,
        "timeout_flag": {
            "ffmpeg": None,
            "ffprobe": ffprobe_timeout_flag,
        },
        "artifact_dir": str(artifact_dir),
        "cameras": camera_summaries,
    }
    write_summary_files(run_summary, artifact_dir)

    print(f"artifact_dir={artifact_dir}")
    for camera in camera_summaries:
        print(
            f"CAMERA {camera['label']} coverage_pct={camera['coverage_pct']} wall_avg_fps={camera['wall_avg_fps']} "
            f"wall_avg_kbps={camera['wall_avg_kbps']} dropouts={camera['dropouts']['count']} "
            f"longest_dropout_s={camera['dropouts']['longest_s']}"
        )
    print(f"summary_json={artifact_dir / 'summary.json'}")
    print(f"summary_txt={artifact_dir / 'summary.txt'}")
    return 130 if interrupted_signal else 0


if __name__ == "__main__":
    raise SystemExit(main())