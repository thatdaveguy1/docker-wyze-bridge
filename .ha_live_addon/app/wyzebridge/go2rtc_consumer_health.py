#!/usr/bin/env python3
"""Consumer-facing health checks for the native go2rtc sidecar.

The existing sidecar monitor primarily observes producer state and receiver-byte
progress. Those signals can remain healthy while a new RTSP consumer cannot
negotiate a usable H.264 stream. This module probes both the RTSP SDP exposed
to consumers and go2rtc's decoded-frame endpoint, then performs bounded recovery.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import signal
import socket
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError


def _log(message: str) -> None:
    print(f"[GO2RTC_CONSUMER] {message}", file=sys.stderr, flush=True)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _h264_sdp_has_parameter_sets(sdp: str) -> bool:
    """Require a video H.264 payload with decodable SPS and PPS in its FMTP."""
    lines = [line.strip() for line in sdp.splitlines() if line.strip()]
    video_payloads: set[str] = set()
    for line in lines:
        if not line.lower().startswith("m=video "):
            continue
        fields = line.split()
        if len(fields) >= 4:
            video_payloads.update(fields[3:])

    h264_payloads: set[str] = set()
    for line in lines:
        if not line.lower().startswith("a=rtpmap:"):
            continue
        payload, separator, codec = line[len("a=rtpmap:") :].partition(" ")
        if separator and payload in video_payloads and codec.strip().lower().startswith("h264/90000"):
            h264_payloads.add(payload)

    if not h264_payloads:
        return False

    for line in lines:
        if not line.lower().startswith("a=fmtp:"):
            continue
        payload, separator, params = line[len("a=fmtp:") :].partition(" ")
        if not separator or payload not in h264_payloads:
            continue
        parsed: dict[str, str] = {}
        for token in params.split(";"):
            key, equals, value = token.strip().partition("=")
            if equals:
                parsed[key.strip().lower()] = value.strip()
        encoded_sets = [item.strip() for item in parsed.get("sprop-parameter-sets", "").split(",") if item.strip()]
        if len(encoded_sets) < 2:
            continue
        nal_types: set[int] = set()
        try:
            for encoded in encoded_sets:
                nal = base64.b64decode(encoded, validate=True)
                if nal:
                    nal_types.add(nal[0] & 0x1F)
        except (binascii.Error, ValueError):
            continue
        if 7 in nal_types and 8 in nal_types:
            return True
    return False


def rtsp_sdp_has_h264_video(response: bytes | str) -> bool:
    """Return True when DESCRIBE advertises H.264 with usable SPS/PPS metadata."""
    if isinstance(response, bytes):
        text = response.decode("utf-8", errors="replace")
    else:
        text = response
    header, _, sdp = text.partition("\r\n\r\n")
    first_line = header.splitlines()[0] if header else ""
    if " 200 " not in f" {first_line} ":
        return False
    return _h264_sdp_has_parameter_sets(sdp)


def jpeg_is_decodable_candidate(payload: bytes, minimum_bytes: int = 2048) -> bool:
    """Decode a non-trivial JPEG and require positive dimensions."""
    if len(payload) < minimum_bytes:
        return False
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "JPEG" or image.width <= 0 or image.height <= 0:
                return False
            image.load()
    except (OSError, UnidentifiedImageError, ValueError):
        return False
    return True


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
    """DESCRIBE a local RTSP alias and require usable H.264 SPS/PPS metadata."""
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
        return False, f"rtsp_invalid_h264_metadata={first_line or 'empty'}"
    return True, "rtsp_h264_sps_pps_ok"


def _http_get(url: str, timeout: float = 5.0) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def probe_decoded_frame(alias: str, api_base: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Require a genuinely decodable JPEG from go2rtc's frame endpoint."""
    url = f"{api_base}/api/frame.jpeg?{urllib.parse.urlencode({'src': alias})}"
    try:
        payload = _http_get(url, timeout=timeout)
    except requests.RequestException as exc:
        return False, f"frame_error={type(exc).__name__}"
    if not jpeg_is_decodable_candidate(payload):
        return False, f"frame_invalid_bytes={len(payload)}"
    return True, f"frame_ok_bytes={len(payload)}"


def active_aliases(api_base: str, timeout: float = 5.0) -> list[str]:
    """Return aliases that currently have at least one producer."""
    payload = _http_get(f"{api_base}/api/streams", timeout=timeout)
    data = json.loads(payload)
    if not isinstance(data, dict):
        return []
    return sorted(
        name
        for name, details in data.items()
        if isinstance(name, str) and isinstance(details, dict) and details.get("producers")
    )


def restart_go2rtc_child(proc_root: Path = Path("/proc")) -> int:
    """Signal exact go2rtc children; the sidecar wrapper restarts them cleanly."""
    killed = 0
    try:
        entries = proc_root.iterdir()
    except OSError:
        return 0
    for entry in entries:
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
    """Bounded failure state for individual aliases and shared RTSP egress."""

    failure_threshold: int = 2
    process_threshold: int = 3
    failures: dict[str, int] = field(default_factory=dict)
    shared_failure_cycles: int = 0

    def record_cycle(self, results: dict[str, bool]) -> tuple[list[str], bool]:
        """Return aliases requiring recovery and whether all-stream failure escalated."""
        active = set(results)
        for alias in list(self.failures):
            if alias not in active:
                self.failures.pop(alias, None)

        failed_aliases: list[str] = []
        for alias, healthy in results.items():
            if healthy:
                self.failures[alias] = 0
                continue
            count = self.failures.get(alias, 0) + 1
            self.failures[alias] = count
            if count >= self.failure_threshold:
                failed_aliases.append(alias)
                self.failures[alias] = 0

        if results and not any(results.values()):
            self.shared_failure_cycles += 1
        else:
            self.shared_failure_cycles = 0

        shared_failure = self.shared_failure_cycles >= self.process_threshold
        if shared_failure:
            self.shared_failure_cycles = 0
            for alias in results:
                self.failures[alias] = 0
        return failed_aliases, shared_failure


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
        except (OSError, ValueError, requests.RequestException) as exc:
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

        failed_aliases, shared_failure = state.record_cycle(results)
        if failed_aliases or shared_failure:
            killed = restart_go2rtc_child()
            if shared_failure:
                reason = "all active consumer probes failed repeatedly"
            else:
                reason = f"repeated consumer failure aliases={','.join(failed_aliases)}"
            _log(f"{reason}; signalled go2rtc child count={killed}")
            time.sleep(max(interval, 5))
            continue

        time.sleep(interval)


if __name__ == "__main__":
    run()
