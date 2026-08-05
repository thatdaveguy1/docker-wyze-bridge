#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request


DEFAULT_BRIDGE_BASE = os.environ.get("WYZE_BRIDGE_BASE", "http://192.0.2.10:5000")
DEFAULT_GO2RTC_BASE = os.environ.get("WYZE_GO2RTC_BASE", "http://192.0.2.10:1984")
DEFAULT_CAMERA_ALIASES = {
    "north-yard": "north-yard-sd",
    "garage": "garage-sd",
    "patio": "cam-patio-sd",
    "south-yard": "south-yard-sd",
    "side-yard": "side-yard-sd",
}


@dataclass(frozen=True)
class CameraSpec:
    name: str
    frame_alias: str


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a local Wyze smoke test from this Mac using bridge /api/<camera> "
            "connected-state checks plus go2rtc frame.jpeg fetches."
        )
    )
    parser.add_argument(
        "--camera",
        action="append",
        dest="cameras",
        help="Camera name to probe. Repeat to limit the run to a subset.",
    )
    parser.add_argument(
        "--camera-alias",
        action="append",
        default=[],
        metavar="CAMERA=FRAME_ALIAS",
        help="Override the go2rtc frame alias for one camera.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Wall-clock run duration in seconds. Default: 3600.",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=15.0,
        help="Seconds between repeated camera checks. Default: 15.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=30.0,
        help="Seconds between aggregate heartbeat updates. Use 0 to disable. Default: 30.",
    )
    parser.add_argument(
        "--bridge-base",
        default=DEFAULT_BRIDGE_BASE,
        help=f"Bridge base URL. Default: {DEFAULT_BRIDGE_BASE}",
    )
    parser.add_argument(
        "--go2rtc-base",
        default=DEFAULT_GO2RTC_BASE,
        help=f"go2rtc base URL. Default: {DEFAULT_GO2RTC_BASE}",
    )
    parser.add_argument(
        "--bridge-api-key",
        default=os.environ.get("WYZE_BRIDGE_API_KEY"),
        help="Bridge API key. Defaults to WYZE_BRIDGE_API_KEY.",
    )
    parser.add_argument(
        "--artifact-root",
        default="tmp",
        help="Directory where the timestamped artifact folder is created. Default: tmp.",
    )
    parser.add_argument(
        "--test-name",
        default="",
        help="Optional short label for this run, such as deco-a or asus-b.",
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())


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


def validate_camera_token(name: str, value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if not value or any(ch not in allowed for ch in value):
        raise SystemExit(
            f"{name} must use only letters, numbers, '-', '_' or '.'. Got: {value}"
        )
    return value


def parse_alias_overrides(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_value in values:
        if "=" not in raw_value:
            raise SystemExit(f"--camera-alias expects CAMERA=FRAME_ALIAS, got: {raw_value}")
        camera_name, alias = raw_value.split("=", 1)
        parsed[validate_camera_token("camera name", camera_name.strip())] = validate_camera_token(
            "frame alias", alias.strip()
        )
    return parsed


def resolve_camera_specs(args: argparse.Namespace) -> list[CameraSpec]:
    alias_overrides = parse_alias_overrides(args.camera_alias)
    selected = args.cameras or list(DEFAULT_CAMERA_ALIASES)
    ordered_names = list(dict.fromkeys([*selected, *alias_overrides]))
    specs: list[CameraSpec] = []
    missing_aliases: list[str] = []

    for raw_name in ordered_names:
        camera_name = validate_camera_token("camera name", raw_name)
        frame_alias = alias_overrides.get(camera_name, DEFAULT_CAMERA_ALIASES.get(camera_name))
        if not frame_alias:
            missing_aliases.append(camera_name)
            continue
        specs.append(CameraSpec(name=camera_name, frame_alias=frame_alias))

    if missing_aliases:
        missing = ", ".join(missing_aliases)
        raise SystemExit(
            "Missing frame alias for: "
            f"{missing}. Supply repeated --camera-alias CAMERA=FRAME_ALIAS values."
        )
    return specs


def ensure_api_key(api_key: str | None) -> str:
    if not api_key:
        raise SystemExit(
            "Bridge API key is required. Set WYZE_BRIDGE_API_KEY or pass --bridge-api-key."
        )
    return api_key


def format_percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 1)


def fetch_bridge_camera_status(
    bridge_base: str,
    api_key: str,
    camera_name: str,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    target_url = f"{bridge_base}/api/{camera_name}"
    req = request.Request(target_url, headers={"api": api_key})
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read()
            status_code = getattr(response, "status", 200)
    except error.HTTPError as exc:
        payload = exc.read() if exc.fp is not None else b""
        return {
            "ok": False,
            "status_code": exc.code,
            "connected": False,
            "error": f"HTTP {exc.code}",
            "body_preview": payload[:200].decode("utf-8", errors="replace"),
        }
    except error.URLError as exc:
        return {
            "ok": False,
            "status_code": None,
            "connected": False,
            "error": str(exc.reason),
            "body_preview": "",
        }
    except TimeoutError:
        return {
            "ok": False,
            "status_code": None,
            "connected": False,
            "error": "timeout",
            "body_preview": "",
        }

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status_code": status_code,
            "connected": False,
            "error": f"invalid JSON: {exc}",
            "body_preview": body[:200].decode("utf-8", errors="replace"),
        }

    connected = bool(payload.get("connected"))
    return {
        "ok": status_code == 200 and connected,
        "status_code": status_code,
        "connected": connected,
        "error": None if connected else "connected=false",
        "body_preview": "",
    }


def fetch_go2rtc_frame(
    go2rtc_base: str,
    frame_alias: str,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    target_url = f"{go2rtc_base}/api/frame.jpeg?src={parse.quote(frame_alias, safe='')}"
    req = request.Request(target_url)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read()
            status_code = getattr(response, "status", 200)
    except error.HTTPError as exc:
        payload = exc.read() if exc.fp is not None else b""
        return {
            "ok": False,
            "status_code": exc.code,
            "byte_count": len(payload),
            "error": f"HTTP {exc.code}",
        }
    except error.URLError as exc:
        return {
            "ok": False,
            "status_code": None,
            "byte_count": 0,
            "error": str(exc.reason),
        }
    except TimeoutError:
        return {
            "ok": False,
            "status_code": None,
            "byte_count": 0,
            "error": "timeout",
        }

    is_jpeg = len(body) >= 2 and body[:2] == b"\xff\xd8"
    return {
        "ok": status_code == 200 and len(body) > 0 and is_jpeg,
        "status_code": status_code,
        "byte_count": len(body),
        "error": None if status_code == 200 and len(body) > 0 and is_jpeg else "invalid JPEG body",
    }


def print_heartbeat(sample_number: int, elapsed_s: float, camera_stats: dict[str, dict[str, Any]]) -> None:
    parts = [f"HEARTBEAT sample={sample_number} elapsed_s={int(round(elapsed_s))}"]
    for camera_name, stats in camera_stats.items():
        parts.append(
            f"{camera_name}:api={stats['api_connected_passes']}/{stats['samples']} "
            f"frame={stats['frame_passes']}/{stats['samples']}"
        )
    print(" ".join(parts), flush=True)


def write_summary_files(run_summary: dict[str, Any], artifact_dir: Path) -> None:
    summary_json = artifact_dir / "summary.json"
    summary_txt = artifact_dir / "summary.txt"
    summary_json.write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Wyze Camera Smoke Test",
        f"started_at={run_summary['started_at']}",
        f"ended_at={run_summary['ended_at']}",
        f"duration_s={run_summary['duration_s']}",
        f"actual_duration_s={run_summary['actual_duration_s']}",
        f"sample_interval_s={run_summary['sample_interval_s']}",
        f"status_interval_s={run_summary['status_interval_s']}",
        f"bridge_base={run_summary['bridge_base']}",
        f"go2rtc_base={run_summary['go2rtc_base']}",
        f"interrupted={run_summary['interrupted']}",
        f"interrupted_signal={run_summary['interrupted_signal']}",
        f"artifact_dir={artifact_dir}",
    ]

    for camera in run_summary["cameras"]:
        lines.extend(
            [
                "",
                f"## {camera['name']}",
                f"frame_alias={camera['frame_alias']}",
                f"samples={camera['samples']}",
                f"api_connected_passes={camera['api_connected_passes']}",
                f"frame_passes={camera['frame_passes']}",
                f"api_connected_pct={camera['api_connected_pct']}",
                f"frame_pct={camera['frame_pct']}",
                f"last_api_error={camera['last_api_error']}",
                f"last_frame_error={camera['last_frame_error']}",
            ]
        )

    summary_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_probe(
    args: argparse.Namespace,
    camera_specs: list[CameraSpec],
    artifact_dir: Path,
    stop_event: Any,
    bridge_fetcher: Callable[[str, str, str], dict[str, Any]] = fetch_bridge_camera_status,
    frame_fetcher: Callable[[str, str], dict[str, Any]] = fetch_go2rtc_frame,
    monotonic: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = now_local,
) -> tuple[int, dict[str, Any]]:
    run_started_at = now_fn()
    run_started_mono = monotonic()
    run_end_mono = run_started_mono + args.duration
    samples_path = artifact_dir / "samples.csv"
    last_heartbeat_mono = run_started_mono
    interrupted_signal: list[int] = []

    camera_stats: dict[str, dict[str, Any]] = {
        spec.name: {
            "name": spec.name,
            "frame_alias": spec.frame_alias,
            "samples": 0,
            "api_connected_passes": 0,
            "frame_passes": 0,
            "last_api_error": None,
            "last_frame_error": None,
        }
        for spec in camera_specs
    }

    def request_stop(signum: int, _frame: Any) -> None:
        if stop_event.is_set():
            return
        interrupted_signal.append(signum)
        stop_event.set()
        signal_name = signal.Signals(signum).name if signum in signal.Signals._value2member_map_ else str(signum)
        print(f"INTERRUPT signal={signal_name} writing partial summary", file=sys.stderr, flush=True)

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        sample_number = 0
        with samples_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "timestamp",
                    "sample",
                    "camera",
                    "frame_alias",
                    "api_status_code",
                    "api_connected",
                    "frame_status_code",
                    "frame_bytes",
                    "api_error",
                    "frame_error",
                ],
            )
            writer.writeheader()

            while monotonic() < run_end_mono and not stop_event.is_set():
                sample_number += 1
                sample_started_at = now_fn().isoformat()
                elapsed_s = monotonic() - run_started_mono
                print(
                    f"SAMPLE sample={sample_number} elapsed_s={int(round(elapsed_s))} "
                    f"camera_count={len(camera_specs)}",
                    flush=True,
                )

                for spec in camera_specs:
                    api_result = bridge_fetcher(args.bridge_base, args.bridge_api_key, spec.name)
                    frame_result = frame_fetcher(args.go2rtc_base, spec.frame_alias)
                    stats = camera_stats[spec.name]
                    stats["samples"] += 1
                    if api_result["ok"]:
                        stats["api_connected_passes"] += 1
                    else:
                        stats["last_api_error"] = api_result.get("error")
                    if frame_result["ok"]:
                        stats["frame_passes"] += 1
                    else:
                        stats["last_frame_error"] = frame_result.get("error")

                    writer.writerow(
                        {
                            "timestamp": sample_started_at,
                            "sample": sample_number,
                            "camera": spec.name,
                            "frame_alias": spec.frame_alias,
                            "api_status_code": api_result.get("status_code"),
                            "api_connected": api_result.get("connected"),
                            "frame_status_code": frame_result.get("status_code"),
                            "frame_bytes": frame_result.get("byte_count"),
                            "api_error": api_result.get("error"),
                            "frame_error": frame_result.get("error"),
                        }
                    )
                    handle.flush()
                    print(
                        f"CAMERA name={spec.name} alias={spec.frame_alias} "
                        f"api_ok={str(api_result['ok']).lower()} api_status={api_result.get('status_code')} "
                        f"frame_ok={str(frame_result['ok']).lower()} frame_status={frame_result.get('status_code')} "
                        f"frame_bytes={frame_result.get('byte_count')}",
                        flush=True,
                    )

                current_mono = monotonic()
                if args.status_interval > 0 and current_mono - last_heartbeat_mono >= args.status_interval:
                    print_heartbeat(
                        sample_number=sample_number,
                        elapsed_s=current_mono - run_started_mono,
                        camera_stats=camera_stats,
                    )
                    last_heartbeat_mono = current_mono

                if stop_event.is_set():
                    break
                remaining = run_end_mono - monotonic()
                if remaining <= 0:
                    break
                sleep_fn(min(args.sample_interval, remaining))
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    run_ended_at = now_fn()
    summary_cameras = []
    for spec in camera_specs:
        stats = camera_stats[spec.name]
        summary_cameras.append(
            {
                **stats,
                "api_connected_pct": format_percent(
                    stats["api_connected_passes"],
                    stats["samples"],
                ),
                "frame_pct": format_percent(
                    stats["frame_passes"],
                    stats["samples"],
                ),
            }
        )

    run_summary = {
        "started_at": run_started_at.isoformat(),
        "ended_at": run_ended_at.isoformat(),
        "duration_s": args.duration,
        "actual_duration_s": round(max(0.0, (run_ended_at - run_started_at).total_seconds()), 3),
        "sample_interval_s": args.sample_interval,
        "status_interval_s": args.status_interval,
        "bridge_base": args.bridge_base,
        "go2rtc_base": args.go2rtc_base,
        "artifact_dir": str(artifact_dir),
        "interrupted": bool(interrupted_signal),
        "interrupted_signal": None
        if not interrupted_signal
        else (
            signal.Signals(interrupted_signal[0]).name
            if interrupted_signal[0] in signal.Signals._value2member_map_
            else str(interrupted_signal[0])
        ),
        "cameras": summary_cameras,
    }
    exit_code = 130 if interrupted_signal else 0
    return exit_code, run_summary


def main() -> int:
    args = parse_args()
    ensure_positive("--duration", float(args.duration))
    ensure_positive("--sample-interval", float(args.sample_interval))
    if args.status_interval < 0:
        raise SystemExit("--status-interval cannot be negative.")
    args.bridge_base = validate_simple_url("--bridge-base", args.bridge_base)
    args.go2rtc_base = validate_simple_url("--go2rtc-base", args.go2rtc_base)
    args.bridge_api_key = ensure_api_key(args.bridge_api_key)
    camera_specs = resolve_camera_specs(args)

    test_slug = f"_{slugify(args.test_name)}" if args.test_name.strip() else ""
    timestamp = now_local().strftime("%Y%m%d_%H%M%S")
    artifact_dir = Path(args.artifact_root) / f"wyze_cam_smoke_test{test_slug}_{timestamp}"
    artifact_dir.mkdir(parents=True, exist_ok=False)

    class StopFlag:
        def __init__(self) -> None:
            self._set = False

        def is_set(self) -> bool:
            return self._set

        def set(self) -> None:
            self._set = True

    exit_code, run_summary = run_probe(
        args=args,
        camera_specs=camera_specs,
        artifact_dir=artifact_dir,
        stop_event=StopFlag(),
    )
    write_summary_files(run_summary, artifact_dir)

    print(f"artifact_dir={artifact_dir}")
    for camera in run_summary["cameras"]:
        print(
            f"CAMERA_SUMMARY name={camera['name']} api_connected_pct={camera['api_connected_pct']} "
            f"frame_pct={camera['frame_pct']} samples={camera['samples']}",
            flush=True,
        )
    print(f"summary_json={artifact_dir / 'summary.json'}")
    print(f"summary_txt={artifact_dir / 'summary.txt'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
