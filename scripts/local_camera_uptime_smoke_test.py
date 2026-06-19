#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffmpeg_helpers import (
    build_ffmpeg_rtsp_cmd,
    build_ffprobe_cmd,
    detect_timeout_flag,
    ensure_binary,
)


REOLINK_RTSP_SUBSTREAM_PATH = "h264Preview_01_sub"
DEFAULT_DURATION_SECONDS = 3600
DEFAULT_CHECK_INTERVAL_SECONDS = 60
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_RTSP_SAMPLE_SECONDS = 8
DEFAULT_RTSP_TIMEOUT_US = 3_000_000
DEFAULT_RTSP_TRANSPORT = "tcp"
DEFAULT_WYZE_BRIDGE_HOST = "192.168.1.244"
DEFAULT_WYZE_BRIDGE_API_PORT = 5000


@dataclass(frozen=True)
class CameraTarget:
    kind: str
    label: str
    hostname: str
    ip: str
    bridge_camera: str | None = None


@dataclass
class CameraState:
    kind: str
    label: str
    hostname: str
    ip: str
    attempts: int = 0
    successes: int = 0
    last_ok: bool | None = None
    last_error: str = ""
    last_latency_s: float | None = None
    last_success_at: str | None = None
    last_checked_at: str | None = None
    last_frames: int | None = None
    last_http_status: int | None = None
    last_bytes: int | None = None
    last_connected: bool | None = None
    last_hash_prefix: str | None = None
    last_hash_changed: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


REOLINK_CAMERAS: tuple[CameraTarget, ...] = (
    CameraTarget("reolink", "south_driveway", "south-driveway-cx", "192.168.1.228"),
    CameraTarget("reolink", "north_driveway", "reolink-northdriveway-e1", "192.168.1.235"),
    CameraTarget("reolink", "doorbell", "reolink-doorbell", "192.168.1.69"),
)

WYZE_CAMERAS: tuple[CameraTarget, ...] = (
    CameraTarget("wyze", "garage", "wyze-v4-garage", "192.168.1.141", bridge_camera="garage"),
    CameraTarget("wyze", "deck", "wyze-deck-v4", "192.168.1.74", bridge_camera="deck"),
    CameraTarget("wyze", "back_yard", "wyze-backyard-v3", "192.168.1.195", bridge_camera="back-yard"),
    CameraTarget("wyze", "north_yard", "wyze-northyard-v4", "192.168.1.179", bridge_camera="north-yard"),
    CameraTarget("wyze", "south_yard", "wyze-southyard-bulb", "192.168.1.193", bridge_camera="south-yard"),
    CameraTarget("wyze", "hamster", "wyze-hamster-v4", "192.168.1.80", bridge_camera="hamster"),
)


def now_local() -> datetime:
    return datetime.now().astimezone()


def derive_wyze_api_key(email: str) -> str:
    digest = hashlib.sha256(email.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:40]


def redact_rtsp_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    prefix, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    _, tail = rest.split("@", 1)
    return f"{prefix}://<redacted>@{tail}"


def build_reolink_rtsp_url(username: str, password: str, ip: str) -> str:
    user = quote(username, safe="")
    secret = quote(password, safe="")
    return f"rtsp://{user}:{secret}@{ip}:554/{REOLINK_RTSP_SUBSTREAM_PATH}"


def parse_ffmpeg_progress_frame_count(text: str) -> int:
    frames = 0
    for line in text.splitlines():
        if not line.startswith("frame="):
            continue
        try:
            frames = int(line.split("=", 1)[1].strip())
        except ValueError:
            continue
    return frames


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a local camera smoke test from this Mac only. "
            "Reolink cameras use direct RTSP substream checks with ffmpeg/ffprobe. "
            "Wyze cameras use local bridge API plus forced image fetches."
        )
    )
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--check-interval-seconds", type=int, default=DEFAULT_CHECK_INTERVAL_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--artifact-root", default="tmp")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    parser.add_argument("--rtsp-transport", choices=["tcp", "udp"], default=DEFAULT_RTSP_TRANSPORT)
    parser.add_argument("--rtsp-sample-seconds", type=int, default=DEFAULT_RTSP_SAMPLE_SECONDS)
    parser.add_argument("--rtsp-timeout-us", type=int, default=DEFAULT_RTSP_TIMEOUT_US)
    parser.add_argument("--reolink-username", default=os.environ.get("REOLINK_USERNAME"))
    parser.add_argument("--reolink-password", default=os.environ.get("REOLINK_PASSWORD"))
    parser.add_argument("--wyze-bridge-host", default=os.environ.get("WYZE_BRIDGE_HOST", DEFAULT_WYZE_BRIDGE_HOST))
    parser.add_argument("--wyze-bridge-api-port", type=int, default=int(os.environ.get("WYZE_BRIDGE_API_PORT", DEFAULT_WYZE_BRIDGE_API_PORT)))
    parser.add_argument("--wyze-api-key", default=os.environ.get("WB_API") or os.environ.get("WYZE_API_KEY"))
    parser.add_argument("--wyze-email", default=os.environ.get("WYZE_EMAIL"))
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    numeric_fields = {
        "--duration-seconds": args.duration_seconds,
        "--check-interval-seconds": args.check_interval_seconds,
        "--heartbeat-seconds": args.heartbeat_seconds,
        "--rtsp-sample-seconds": args.rtsp_sample_seconds,
        "--rtsp-timeout-us": args.rtsp_timeout_us,
        "--wyze-bridge-api-port": args.wyze_bridge_api_port,
    }
    for name, value in numeric_fields.items():
        if value <= 0:
            raise SystemExit(f"{name} must be a positive integer.")
    if not args.reolink_username or not args.reolink_password:
        raise SystemExit(
            "Reolink credentials are required. Set REOLINK_USERNAME and REOLINK_PASSWORD "
            "or pass --reolink-username and --reolink-password."
        )
    if not args.wyze_api_key:
        if args.wyze_email:
            args.wyze_api_key = derive_wyze_api_key(args.wyze_email)
        else:
            raise SystemExit(
                "A Wyze bridge API key is required. Set WB_API or WYZE_API_KEY, "
                "or provide WYZE_EMAIL so the script can derive the local bridge token."
            )


def artifact_dir(root: str) -> Path:
    stamp = now_local().strftime("%Y%m%d_%H%M%S")
    path = Path(root) / f"local_camera_smoke_{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def ffprobe_metadata(
    ffprobe_path: str,
    timeout_flag: str | None,
    url: str,
    transport: str,
    timeout_us: int,
) -> dict[str, Any]:
    command = build_ffprobe_cmd(
        ffprobe_path, url, transport, timeout_flag=timeout_flag, timeout_us=timeout_us
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
        return {"ok": False, "error": f"ffprobe timed out after {timeout_seconds:.1f}s"}
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or "").strip(), "returncode": result.returncode}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"ffprobe JSON parse failed: {exc}"}
    video_stream = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
        {},
    )
    return {
        "ok": True,
        "video_codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "avg_frame_rate": video_stream.get("avg_frame_rate"),
        "r_frame_rate": video_stream.get("r_frame_rate"),
        "format": payload.get("format", {}).get("format_name"),
    }


def run_reolink_check(
    target: CameraTarget,
    args: argparse.Namespace,
    ffmpeg_timeout_flag: str | None,
) -> dict[str, Any]:
    url = build_reolink_rtsp_url(args.reolink_username, args.reolink_password, target.ip)
    command = build_ffmpeg_rtsp_cmd(
        args.ffmpeg,
        url,
        args.rtsp_transport,
        str(args.rtsp_sample_seconds),
        loglevel="error",
        extra_output_args=["-map", "0:v:0", "-an"],
        output_format="null",
        output_target="-",
        progress_pipe=1,
    )
    # Keep ffmpeg invocation minimal and let subprocess timeout police the wall clock.
    # The local macOS ffmpeg build reported rw_timeout support in help text but rejected it at runtime.
    started = time.monotonic()
    timeout_seconds = max(float(args.rtsp_sample_seconds) + 10.0, args.rtsp_timeout_us / 1_000_000 + 10.0)
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
            "latency_s": round(time.monotonic() - started, 3),
            "frames": 0,
            "error": f"ffmpeg timed out after {timeout_seconds:.1f}s",
            "redacted_url": redact_rtsp_url(url),
        }
    frames = parse_ffmpeg_progress_frame_count(result.stdout or "")
    ok = result.returncode == 0 and frames > 0
    error = ""
    if not ok:
        stderr = (result.stderr or "").strip()
        error = stderr or f"ffmpeg returncode={result.returncode} frames={frames}"
    return {
        "ok": ok,
        "latency_s": round(time.monotonic() - started, 3),
        "frames": frames,
        "error": error,
        "redacted_url": redact_rtsp_url(url),
    }


def http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return getattr(response, "status", 200), response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def run_wyze_check(target: CameraTarget, args: argparse.Namespace) -> dict[str, Any]:
    assert target.bridge_camera is not None
    headers = {"api": args.wyze_api_key}
    base = f"http://{args.wyze_bridge_host}:{args.wyze_bridge_api_port}"
    api_url = f"{base}/api/{quote(target.bridge_camera, safe='')}"
    image_url = f"{base}/img/{quote(target.bridge_camera, safe='')}.jpg?exp=0"
    started = time.monotonic()
    try:
        api_status, api_body = http_get(api_url, headers=headers, timeout=8.0)
        payload = json.loads(api_body.decode("utf-8", errors="replace")) if api_body else {}
    except Exception as exc:
        return {
            "ok": False,
            "latency_s": round(time.monotonic() - started, 3),
            "connected": False,
            "http_status": None,
            "bytes": 0,
            "hash_prefix": None,
            "error": f"api fetch failed: {exc}",
        }
    connected = bool(payload.get("connected"))
    try:
        image_status, image_bytes = http_get(image_url, headers=headers, timeout=12.0)
    except Exception as exc:
        return {
            "ok": False,
            "latency_s": round(time.monotonic() - started, 3),
            "connected": connected,
            "http_status": None,
            "bytes": 0,
            "hash_prefix": None,
            "error": f"image fetch failed: {exc}",
        }
    is_jpeg = len(image_bytes) > 2 and image_bytes[:2] == b"\xff\xd8"
    hash_prefix = hashlib.sha256(image_bytes).hexdigest()[:12] if image_bytes else None
    ok = api_status == 200 and connected and image_status == 200 and len(image_bytes) > 2048 and is_jpeg
    error = ""
    if not ok:
        error = (
            f"api_status={api_status} connected={connected} image_status={image_status} "
            f"bytes={len(image_bytes)} jpeg={is_jpeg}"
        )
    return {
        "ok": ok,
        "latency_s": round(time.monotonic() - started, 3),
        "connected": connected,
        "http_status": image_status,
        "bytes": len(image_bytes),
        "hash_prefix": hash_prefix,
        "error": error,
    }


def heartbeat_summary(
    states: dict[str, CameraState],
    start_monotonic: float,
    run_seconds: int,
) -> str:
    elapsed = int(time.monotonic() - start_monotonic)
    lines = [
        f"[heartbeat] elapsed={elapsed}s remaining={max(0, run_seconds - elapsed)}s",
    ]
    for key in sorted(states):
        state = states[key]
        pct = (state.successes / state.attempts * 100.0) if state.attempts else 0.0
        if state.kind == "reolink":
            lines.append(
                f"  {state.hostname}: {state.successes}/{state.attempts} ok ({pct:.1f}%) "
                f"last_ok={state.last_ok} frames={state.last_frames or 0} "
                f"latency={state.last_latency_s if state.last_latency_s is not None else 'n/a'} "
                f"err={state.last_error or 'none'}"
            )
        else:
            lines.append(
                f"  {state.hostname}: {state.successes}/{state.attempts} ok ({pct:.1f}%) "
                f"last_ok={state.last_ok} connected={state.last_connected} "
                f"bytes={state.last_bytes or 0} hash={state.last_hash_prefix or 'none'} "
                f"err={state.last_error or 'none'}"
            )
    return "\n".join(lines)


def write_event(log_path: Path, text: str) -> None:
    stamped = f"{now_local().isoformat()} {text}"
    print(stamped, flush=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(stamped + "\n")


def summarize_states(
    states: dict[str, CameraState],
    interrupted: bool,
    signal_name: str | None,
    started_at: str,
    ended_at: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    cameras = []
    for key in sorted(states):
        state = states[key]
        payload = asdict(state)
        payload["success_pct"] = round((state.successes / state.attempts * 100.0), 1) if state.attempts else 0.0
        cameras.append(payload)
    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "interrupted": interrupted,
        "interrupted_signal": signal_name,
        "duration_seconds": args.duration_seconds,
        "check_interval_seconds": args.check_interval_seconds,
        "heartbeat_seconds": args.heartbeat_seconds,
        "wyze_bridge_host": args.wyze_bridge_host,
        "reolink_rtsp_path": REOLINK_RTSP_SUBSTREAM_PATH,
        "cameras": cameras,
    }


def main() -> int:
    args = parse_args()
    validate_args(args)

    args.ffmpeg = ensure_binary(args.ffmpeg, "ffmpeg")
    args.ffprobe = ensure_binary(args.ffprobe, "ffprobe")

    art_dir = artifact_dir(args.artifact_root)
    log_path = art_dir / "events.log"
    summary_path = art_dir / "summary.json"

    reolink_timeout_flag = detect_timeout_flag(args.ffmpeg)
    ffprobe_timeout_flag = detect_timeout_flag(args.ffprobe)

    states: dict[str, CameraState] = {}
    for target in REOLINK_CAMERAS + WYZE_CAMERAS:
        states[target.label] = CameraState(
            kind=target.kind,
            label=target.label,
            hostname=target.hostname,
            ip=target.ip,
        )

    started_at = now_local().isoformat()
    start_monotonic = time.monotonic()
    stop_event = threading.Event()
    state_lock = threading.Lock()
    interrupted_signal: dict[str, str | None] = {"name": None}

    def request_stop(sig: int, _frame: Any) -> None:
        interrupted_signal["name"] = signal.Signals(sig).name
        stop_event.set()

    previous_handlers = {
        signal.SIGINT: signal.signal(signal.SIGINT, request_stop),
        signal.SIGTERM: signal.signal(signal.SIGTERM, request_stop),
    }

    for target in REOLINK_CAMERAS:
        url = build_reolink_rtsp_url(args.reolink_username, args.reolink_password, target.ip)
        metadata = ffprobe_metadata(
            ffprobe_path=args.ffprobe,
            timeout_flag=ffprobe_timeout_flag,
            url=url,
            transport=args.rtsp_transport,
            timeout_us=args.rtsp_timeout_us,
        )
        states[target.label].metadata = metadata | {"redacted_url": redact_rtsp_url(url)}

    write_event(log_path, f"artifact_dir={art_dir}")
    write_event(
        log_path,
        (
            "starting local smoke test "
            f"duration={args.duration_seconds}s interval={args.check_interval_seconds}s "
            f"heartbeat={args.heartbeat_seconds}s"
        ),
    )

    def heartbeat_worker() -> None:
        while not stop_event.wait(args.heartbeat_seconds):
            with state_lock:
                text = heartbeat_summary(states, start_monotonic, args.duration_seconds)
            write_event(log_path, "\n" + text)

    heartbeat_thread = threading.Thread(target=heartbeat_worker, daemon=True)
    heartbeat_thread.start()

    try:
        while not stop_event.is_set():
            cycle_start = time.monotonic()
            if cycle_start - start_monotonic >= args.duration_seconds:
                break

            def run_target(target: CameraTarget) -> tuple[CameraTarget, dict[str, Any]]:
                if target.kind == "reolink":
                    return target, run_reolink_check(target, args, reolink_timeout_flag)
                return target, run_wyze_check(target, args)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(REOLINK_CAMERAS) + len(WYZE_CAMERAS)
            ) as pool:
                futures = [pool.submit(run_target, target) for target in (REOLINK_CAMERAS + WYZE_CAMERAS)]
                for future in concurrent.futures.as_completed(futures):
                    target, result = future.result()
                    with state_lock:
                        state = states[target.label]
                        state.attempts += 1
                        state.last_ok = bool(result.get("ok"))
                        state.last_error = str(result.get("error") or "")
                        state.last_latency_s = result.get("latency_s")
                        state.last_checked_at = now_local().isoformat()
                        if result.get("ok"):
                            state.successes += 1
                            state.last_success_at = state.last_checked_at
                        if target.kind == "reolink":
                            state.last_frames = int(result.get("frames") or 0)
                        else:
                            state.last_connected = bool(result.get("connected"))
                            state.last_http_status = result.get("http_status")
                            state.last_bytes = int(result.get("bytes") or 0)
                            new_hash = result.get("hash_prefix")
                            state.last_hash_changed = bool(new_hash and new_hash != state.last_hash_prefix)
                            if new_hash:
                                state.last_hash_prefix = str(new_hash)

            with state_lock:
                cycle_text = heartbeat_summary(states, start_monotonic, args.duration_seconds)
            write_event(log_path, "\n" + cycle_text)

            remaining = args.check_interval_seconds - (time.monotonic() - cycle_start)
            if remaining > 0 and not stop_event.wait(remaining):
                continue
    finally:
        stop_event.set()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    heartbeat_thread.join(timeout=1.0)

    ended_at = now_local().isoformat()
    payload = summarize_states(
        states=states,
        interrupted=interrupted_signal["name"] is not None,
        signal_name=interrupted_signal["name"],
        started_at=started_at,
        ended_at=ended_at,
        args=args,
    )
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_event(log_path, f"summary_json={summary_path}")
    for key in sorted(states):
        state = states[key]
        pct = (state.successes / state.attempts * 100.0) if state.attempts else 0.0
        write_event(
            log_path,
            f"final {state.hostname}: {state.successes}/{state.attempts} ok ({pct:.1f}%)",
        )

    return 130 if interrupted_signal["name"] else 0


if __name__ == "__main__":
    sys.exit(main())
