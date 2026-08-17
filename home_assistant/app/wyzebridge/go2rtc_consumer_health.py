#!/usr/bin/env python3
"""Consumer-facing health checks for the native go2rtc sidecar.

The existing sidecar monitor primarily observes producer state and receiver-byte
progress. Those signals can remain healthy while a new RTSP consumer cannot
negotiate a usable H.264 stream. This module probes both the RTSP SDP exposed
to consumers and go2rtc's decoded-frame endpoint, then performs bounded recovery.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


def _log(message: str) -> None:
    print(f"[GO2RTC_CONSUMER] {message}", file=sys.stderr, flush=True)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def rtsp_sdp_has_h264_video(response: bytes | str) -> bool:
    """Return True when a DESCRIBE response advertises an H.264 video track."""
    if isinstance(response, bytes):
        text = response.decode("utf-8", errors="replace")
    else:
        text = response
    header, _, sdp = text.partition("\r\n\r\n")
    first_line = header.splitlines()[0] if header else ""
    if " 200 " not in f" {first_line} ":
        return False
    if not any(line.lower().startswith("m=video ") for line in sdp.splitlines()):
        return False
    return any(
        "h264/90000" in line.lower()
        for line in sdp.splitlines()
        if line.lower().startswith("a=rtpmap:")
    )


def jpeg_is_decodable_candidate(payload: bytes, minimum_bytes: int = 2048) -> bool:
    """Cheaply validate that go2rtc returned a non-trivial complete JPEG frame."""
    return len(payload) >= minimum_bytes and payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9")


def _read_rtsp_response(sock: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data and len(data) < 64 * 1024:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    header, marker, body = bytes(data).partition(b"\r\n\r\n")
    if not marker:
        return bytes(data)
    content_length = 0
    for raw_line in header.split(b"\r\n")[1:]:
        key, sep, value = raw_line.partition(b":")
        if sep and key.strip().lower() == b"content-length":
            try:
                content_length = int(value.strip())
            except ValueError:
                content_length = 0
            break
    while len(body) < content_length and len(body) < 256 * 1024:
        chunk = sock.recv(min(4096, content_length - len(body)))
        if not chunk:
            break
        body += chunk
    return header + marker + body


def probe_rtsp_sdp(alias: str, rtsp_port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """DESCRIBE a local RTSP alias and require an advertised H.264 video track."""
    request = (
        f"DESCRIBE rtsp://127.0.0.1:{rtsp_port}/{alias} RTSP/1.0\r\n"
        "CSeq: 1\r\n"
        "Accept: application/sdp\r\n"
        "User-Agent: wyze-bridge-consumer-health\r\n\r\n"
    ).encode()
    try:
        with socket.create_connection(("127.0.0.1", rtsp_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            response = _read_rtsp_response(sock)
    except OSError as exc:
        return False, f"rtsp_error={type(exc).__name__}"
    if not rtsp_sdp_has_h264_video(response):
        first_line = response.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        return False, f"rtsp_invalid_sdp={first_line or 'empty'}"
    return True, "rtsp_h264_ok"


def _urlopen(request: urllib.request.Request | str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def probe_decoded_frame(alias: str, api_base: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Require a complete JPEG from go2rtc, proving the source can decode a frame."""
    url = f"{api_base}/api/frame.jpeg?{urllib.parse.urlencode({'src': alias})}"
    try:
        payload = _urlopen(url, timeout=timeout)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        return False, f"frame_error={type(exc).__name__}"
    if not jpeg_is_decodable_candidate(payload):
        return False, f"frame_invalid_bytes={len(payload)}"
    return True, f"frame_ok_bytes={len(payload)}"


def active_aliases(api_base: str, timeout: float = 5.0) -> list[str]:
    """Return aliases that currently have at least one producer."""
    payload = _urlopen(f"{api_base}/api/streams", timeout=timeout)
    data = json.loads(payload)
    if not isinstance(data, dict):
        return []
    return sorted(
        name
        for name, details in data.items()
        if isinstance(name, str) and isinstance(details, dict) and details.get("producers")
    )


def restart_alias(alias: str, api_base: str, timeout: float = 5.0) -> None:
    """Recreate one go2rtc alias and immediately preload it."""
    restart_url = f"{api_base}/api/streams?{urllib.parse.urlencode({'src': '', 'dst': alias})}"
    _urlopen(urllib.request.Request(restart_url, method="POST"), timeout=timeout)
    time.sleep(2)
    preload_url = f"{api_base}/api/preload?{urllib.parse.urlencode({'src': alias})}"
    _urlopen(urllib.request.Request(preload_url, method="PUT"), timeout=timeout)


def restart_go2rtc_child() -> int:
    """Signal go2rtc child processes; the sidecar wrapper restarts them cleanly."""
    killed = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text(encoding="utf-8").strip() != "go2rtc":
                continue
            os.kill(int(entry.name), signal.SIGTERM)
            killed += 1
        except (OSError, ValueError):
            continue
    return killed


@dataclass
class ConsumerHealthState:
    """Bounded failure state for per-alias and shared-egress recovery."""

    failure_threshold: int = 2
    process_threshold: int = 3
    failures: dict[str, int] = field(default_factory=dict)
    shared_failure_cycles: int = 0

    def record_cycle(self, results: dict[str, bool]) -> tuple[list[str], bool]:
        """Return aliases to restart and whether the whole go2rtc child should restart."""
        active = set(results)
        for alias in list(self.failures):
            if alias not in active:
                self.failures.pop(alias, None)

        restart_aliases: list[str] = []
        for alias, healthy in results.items():
            if healthy:
                self.failures[alias] = 0
                continue
            count = self.failures.get(alias, 0) + 1
            self.failures[alias] = count
            if count >= self.failure_threshold:
                restart_aliases.append(alias)
                self.failures[alias] = 0

        if results and not any(results.values()):
            self.shared_failure_cycles += 1
        else:
            self.shared_failure_cycles = 0

        restart_process = self.shared_failure_cycles >= self.process_threshold
        if restart_process:
            self.shared_failure_cycles = 0
            for alias in results:
                self.failures[alias] = 0
        return restart_aliases, restart_process


def run() -> None:
    if not _truthy("GO2RTC_CONSUMER_HEALTH", True):
        _log("disabled by GO2RTC_CONSUMER_HEALTH")
        return

    api_port = int(os.environ.get("GO2RTC_API_PORT", "11984"))
    rtsp_port = int(os.environ.get("GO2RTC_RTSP_PORT", "19554"))
    interval = max(10, int(os.environ.get("GO2RTC_CONSUMER_HEALTH_INTERVAL", "30")))
    initial_delay = max(0, int(os.environ.get("GO2RTC_CONSUMER_HEALTH_INITIAL_DELAY", "20")))
    api_base = f"http://127.0.0.1:{api_port}"
    state = ConsumerHealthState()
    _log(f"watchdog started interval={interval}s rtsp=:{rtsp_port} api=:{api_port}")
    time.sleep(initial_delay)

    while True:
        try:
            aliases = active_aliases(api_base)
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            _log(f"stream table unavailable: {type(exc).__name__}")
            time.sleep(interval)
            continue

        results: dict[str, bool] = {}
        details: dict[str, str] = {}
        for alias in aliases:
            rtsp_ok, rtsp_detail = probe_rtsp_sdp(alias, rtsp_port)
            frame_ok, frame_detail = probe_decoded_frame(alias, api_base)
            results[alias] = rtsp_ok and frame_ok
            details[alias] = f"{rtsp_detail} {frame_detail}"
            if not results[alias]:
                _log(f"{alias}: consumer probe failed ({details[alias]})")

        aliases_to_restart, restart_process = state.record_cycle(results)
        if restart_process:
            killed = restart_go2rtc_child()
            _log(f"all active consumer probes failed repeatedly; signalled go2rtc child count={killed}")
            time.sleep(max(interval, 5))
            continue

        for alias in aliases_to_restart:
            try:
                _log(f"{alias}: repeated consumer failure; recreating alias")
                restart_alias(alias, api_base)
            except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                _log(f"{alias}: alias restart failed: {type(exc).__name__}")

        time.sleep(interval)


if __name__ == "__main__":
    run()
