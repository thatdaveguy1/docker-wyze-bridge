"""go2rtc core API helpers — probe, port resolution, and stream management.

Architecture review candidate #9: native alias readiness, selection,
snapshot, and talkback logic moved to native_alias.py. This module
keeps the core go2rtc API helpers (ports, probe, stream request).
"""

import os
import socket
from typing import Any

import requests

DEFAULT_GO2RTC_API_PORT = 11984
DEFAULT_GO2RTC_RTSP_PORT = 19554


def _go2rtc_api_port() -> int:
    return int(os.getenv("GO2RTC_API_PORT", str(DEFAULT_GO2RTC_API_PORT)))


def _go2rtc_rtsp_port() -> int:
    return int(os.getenv("GO2RTC_RTSP_PORT", str(DEFAULT_GO2RTC_RTSP_PORT)))


def go2rtc_api_base() -> str:
    return f"http://127.0.0.1:{_go2rtc_api_port()}"


def go2rtc_rtsp_base() -> str:
    return f"rtsp://127.0.0.1:{_go2rtc_rtsp_port()}"


def _socket_probe(port: int, timeout: float = 0.5) -> dict[str, Any]:
    result = {
        "host": "127.0.0.1",
        "port": port,
        "reachable": False,
    }
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            result["reachable"] = True
    except OSError as ex:
        result["error"] = f"{type(ex).__name__}: {ex}"
    return result


def go2rtc_probe(timeout: float = 1.0, include_streams: bool = False) -> dict[str, Any]:
    result = {
        "api": {
            "listener": go2rtc_api_base(),
            "reachable": False,
        },
        "rtsp": _socket_probe(_go2rtc_rtsp_port()),
    }
    try:
        response = requests.get(f"{go2rtc_api_base()}/api", timeout=timeout)
        result["api"]["status_code"] = response.status_code
        response.raise_for_status()
        result["api"]["reachable"] = True
        if include_streams:
            streams_response = requests.get(f"{go2rtc_api_base()}/api/streams", timeout=timeout)
            streams_response.raise_for_status()
            data = streams_response.json()
            result["aliases"] = sorted(data.keys()) if isinstance(data, dict) else []
    except requests.RequestException as ex:
        if ex.response is not None:
            result["api"]["status_code"] = ex.response.status_code
        result["api"]["error"] = str(ex)
    return result
