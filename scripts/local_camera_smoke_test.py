#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse


def now_local() -> datetime:
    return datetime.now().astimezone()


def slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local one-hour camera smoke test from this Mac by launching the "
            "existing Reolink direct RTSP probe and the Wyze bridge/frame probe together."
        )
    )
    parser.add_argument("--duration", type=int, default=3600, help="Run length in seconds. Default: 3600.")
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=60.0,
        help="Seconds between wrapper heartbeat lines. Default: 60.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=30.0,
        help="Status interval forwarded to child probes. Default: 30.",
    )
    parser.add_argument(
        "--wyze-sample-interval",
        type=float,
        default=15.0,
        help="Seconds between Wyze sample loops. Default: 15.",
    )
    parser.add_argument("--artifact-root", default="tmp", help="Root artifact directory. Default: tmp.")
    parser.add_argument("--test-name", default="", help="Optional short label for the run.")
    parser.add_argument(
        "--bridge-base",
        default=os.environ.get("WYZE_BRIDGE_BASE", "http://192.0.2.10:5000"),
        help="Wyze bridge base URL. Defaults to WYZE_BRIDGE_BASE or an example value.",
    )
    parser.add_argument(
        "--go2rtc-base",
        default=os.environ.get("WYZE_GO2RTC_BASE", "http://192.0.2.10:1984"),
        help="Wyze go2rtc base URL. Defaults to WYZE_GO2RTC_BASE or an example value.",
    )
    parser.add_argument(
        "--bridge-api-key",
        default=os.environ.get("WYZE_BRIDGE_API_KEY"),
        help="Wyze bridge API key. Defaults to WYZE_BRIDGE_API_KEY.",
    )
    parser.add_argument("--reolink-camera", action="append", default=[], help="Repeat to limit Reolink cameras.")
    parser.add_argument(
        "--reolink-camera-url",
        action="append",
        default=[],
        metavar="LABEL=RTSP_URL",
        help="Repeat to override one Reolink camera RTSP URL.",
    )
    parser.add_argument(
        "--reolink-camera-ip",
        action="append",
        default=[],
        metavar="LABEL=IP",
        help="Repeat to override one Reolink camera IP.",
    )
    parser.add_argument("--wyze-camera", action="append", default=[], help="Repeat to limit Wyze cameras.")
    parser.add_argument(
        "--wyze-camera-alias",
        action="append",
        default=[],
        metavar="CAMERA=FRAME_ALIAS",
        help="Repeat to override one Wyze go2rtc frame alias.",
    )
    return parser.parse_args()


def ensure_positive(name: str, value: float) -> None:
    if value <= 0:
        raise SystemExit(f"{name} must be greater than zero.")


def validate_simple_url(name: str, value: str) -> str:
    parsed = parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SystemExit(f"{name} must be a simple http(s) base URL.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise SystemExit(f"{name} must be a simple http(s) base URL without a path or query.")
    return value.rstrip("/")


@dataclass
class ChildState:
    name: str
    command: list[str]
    redacted_command: list[str]
    process: subprocess.Popen[str] | None = None
    output_lines: list[str] = field(default_factory=list)
    artifact_dir: str | None = None
    summary_json: str | None = None
    summary_txt: str | None = None
    returncode: int | None = None


def script_path(name: str) -> str:
    return str(Path(__file__).resolve().with_name(name))


def build_reolink_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        script_path("reolink_direct_stability_probe.py"),
        "--duration",
        str(args.duration),
        "--status-interval",
        str(args.status_interval),
        "--artifact-root",
        args.artifact_root,
    ]
    if args.test_name.strip():
        command.extend(["--test-name", args.test_name])
    for camera in args.reolink_camera:
        command.extend(["--camera", camera])
    for value in args.reolink_camera_url:
        command.extend(["--camera-url", value])
    for value in args.reolink_camera_ip:
        command.extend(["--camera-ip", value])
    return command


def build_reolink_redacted_command(command: list[str]) -> list[str]:
    redacted = command[:]
    for index, value in enumerate(redacted[:-1]):
        if value == "--reolink-camera-url" or value == "--camera-url":
            raw_value = redacted[index + 1]
            if "=" in raw_value:
                label, url = raw_value.split("=", 1)
                redacted[index + 1] = f"{label}={redact_rtsp_url(url)}"
            else:
                redacted[index + 1] = "<redacted>"
    return redacted


def build_wyze_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        script_path("wyze_cam_smoke_test.py"),
        "--duration",
        str(args.duration),
        "--sample-interval",
        str(args.wyze_sample_interval),
        "--status-interval",
        str(args.status_interval),
        "--artifact-root",
        args.artifact_root,
        "--bridge-base",
        args.bridge_base,
        "--go2rtc-base",
        args.go2rtc_base,
    ]
    if args.test_name.strip():
        command.extend(["--test-name", args.test_name])
    for camera in args.wyze_camera:
        command.extend(["--camera", camera])
    for value in args.wyze_camera_alias:
        command.extend(["--camera-alias", value])
    return command


def redact_rtsp_url(url: str) -> str:
    parsed = parse.urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"<redacted>@{host}{port}" if (parsed.username or parsed.password) else parsed.netloc
    return parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def write_summary_files(run_summary: dict[str, Any], artifact_dir: Path) -> None:
    summary_json = artifact_dir / "summary.json"
    summary_txt = artifact_dir / "summary.txt"
    summary_json.write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Local Camera Smoke Test",
        f"started_at={run_summary['started_at']}",
        f"ended_at={run_summary['ended_at']}",
        f"duration_s={run_summary['duration_s']}",
        f"actual_duration_s={run_summary['actual_duration_s']}",
        f"heartbeat_interval_s={run_summary['heartbeat_interval_s']}",
        f"interrupted={run_summary['interrupted']}",
        f"interrupted_signal={run_summary['interrupted_signal']}",
        f"artifact_dir={artifact_dir}",
    ]
    for child in run_summary["children"]:
        lines.extend(
            [
                "",
                f"## {child['name']}",
                f"returncode={child['returncode']}",
                f"artifact_dir={child['artifact_dir']}",
                f"summary_json={child['summary_json']}",
                f"summary_txt={child['summary_txt']}",
                f"command={' '.join(child['command_redacted'])}",
            ]
        )
    summary_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stream_child_output(child: ChildState) -> None:
    assert child.process is not None
    assert child.process.stdout is not None
    for raw_line in child.process.stdout:
        line = raw_line.rstrip("\n")
        child.output_lines.append(line)
        if line.startswith("artifact_dir="):
            child.artifact_dir = line.split("=", 1)[1].strip()
        elif line.startswith("summary_json="):
            child.summary_json = line.split("=", 1)[1].strip()
        elif line.startswith("summary_txt="):
            child.summary_txt = line.split("=", 1)[1].strip()
        print(f"[{child.name}] {line}", flush=True)
    child.process.stdout.close()


def run_children(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    run_started_at = now_local()
    timestamp = now_local().strftime("%Y%m%d_%H%M%S")
    test_slug = f"_{slugify(args.test_name)}" if args.test_name.strip() else ""
    artifact_dir = Path(args.artifact_root) / f"local_camera_smoke_test{test_slug}_{timestamp}"
    artifact_dir.mkdir(parents=True, exist_ok=False)

    children = [
        ChildState(
            name="reolink",
            command=build_reolink_command(args),
            redacted_command=build_reolink_redacted_command(build_reolink_command(args)),
        ),
        ChildState(
            name="wyze",
            command=build_wyze_command(args),
            redacted_command=build_wyze_command(args),
        ),
    ]

    env = os.environ.copy()
    if args.bridge_api_key:
        env["WYZE_BRIDGE_API_KEY"] = args.bridge_api_key

    stop_requested = threading.Event()
    interrupted_signal: list[int] = []

    def request_stop(signum: int, _frame: Any) -> None:
        if stop_requested.is_set():
            return
        interrupted_signal.append(signum)
        stop_requested.set()
        signal_name = signal.Signals(signum).name if signum in signal.Signals._value2member_map_ else str(signum)
        print(f"INTERRUPT signal={signal_name} forwarding SIGTERM to child probes", file=sys.stderr, flush=True)
        for child in children:
            if child.process is not None and child.process.poll() is None:
                child.process.terminate()

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    readers: list[threading.Thread] = []
    try:
        for child in children:
            child.process = subprocess.Popen(
                child.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            reader = threading.Thread(target=stream_child_output, args=(child,), daemon=True)
            reader.start()
            readers.append(reader)

        started_mono = time.monotonic()
        last_heartbeat = started_mono
        while True:
            running = [child.name for child in children if child.process is not None and child.process.poll() is None]
            if not running:
                break
            now_mono = time.monotonic()
            if args.heartbeat_interval > 0 and now_mono - last_heartbeat >= args.heartbeat_interval:
                print(
                    f"[wrapper] HEARTBEAT elapsed_s={int(round(now_mono - started_mono))} "
                    f"running={','.join(running)}",
                    flush=True,
                )
                last_heartbeat = now_mono
            time.sleep(1.0)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    for child, reader in zip(children, readers):
        if child.process is not None:
            child.returncode = child.process.wait()
        reader.join(timeout=2.0)

    run_ended_at = now_local()
    run_summary = {
        "started_at": run_started_at.isoformat(),
        "ended_at": run_ended_at.isoformat(),
        "duration_s": args.duration,
        "actual_duration_s": round(max(0.0, (run_ended_at - run_started_at).total_seconds()), 3),
        "heartbeat_interval_s": args.heartbeat_interval,
        "interrupted": bool(interrupted_signal),
        "interrupted_signal": None
        if not interrupted_signal
        else (
            signal.Signals(interrupted_signal[0]).name
            if interrupted_signal[0] in signal.Signals._value2member_map_
            else str(interrupted_signal[0])
        ),
        "artifact_dir": str(artifact_dir),
        "children": [
            {
                "name": child.name,
                "returncode": child.returncode,
                "artifact_dir": child.artifact_dir,
                "summary_json": child.summary_json,
                "summary_txt": child.summary_txt,
                "command_redacted": child.redacted_command,
            }
            for child in children
        ],
    }
    write_summary_files(run_summary, artifact_dir)

    print(f"artifact_dir={artifact_dir}")
    for child in children:
        print(
            f"CHILD name={child.name} returncode={child.returncode} artifact_dir={child.artifact_dir}",
            flush=True,
        )
    print(f"summary_json={artifact_dir / 'summary.json'}")
    print(f"summary_txt={artifact_dir / 'summary.txt'}")

    exit_code = 0
    for child in children:
        if child.returncode not in (0, None):
            exit_code = child.returncode or 1
            break
    if interrupted_signal and exit_code == 0:
        exit_code = 130
    return exit_code, run_summary


def main() -> int:
    args = parse_args()
    ensure_positive("--duration", float(args.duration))
    ensure_positive("--heartbeat-interval", float(args.heartbeat_interval))
    ensure_positive("--status-interval", float(args.status_interval))
    ensure_positive("--wyze-sample-interval", float(args.wyze_sample_interval))
    args.bridge_base = validate_simple_url("--bridge-base", args.bridge_base)
    args.go2rtc_base = validate_simple_url("--go2rtc-base", args.go2rtc_base)
    exit_code, _run_summary = run_children(args)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
