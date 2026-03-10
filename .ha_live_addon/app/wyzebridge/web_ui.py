import json
import os
from time import sleep
from typing import Callable, Generator, Optional
from urllib.parse import urlparse

from flask import request
from flask import url_for as _url_for
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash

from wyzebridge.config import (
    BRIDGE_IP,
    HASS_TOKEN,
    IMG_PATH,
    IMG_TYPE,
    LLHLS,
    RTMP_URL,
    RTSP_URL,
    SNAPSHOT_TYPE,
    TOKEN_PATH,
    WEBRTC_URL,
    HLS_URL,
)
from wyzebridge.auth import WbAuth
from wyzebridge.bridge_utils import env_bool
from wyzebridge.logging import logger
from wyzebridge.stream import Stream
from wyzebridge.stream_manager import StreamManager

auth = HTTPBasicAuth()

API_ENDPOINTS = "/api", "/img", "/snapshot", "/thumb", "/photo"


@auth.verify_password
def verify_password(username, password):
    if HASS_TOKEN and request.remote_addr == "172.30.32.2":
        return True
    if WbAuth.api in (request.args.get("api"), request.headers.get("api")):
        return request.path.startswith(API_ENDPOINTS)
    if username == WbAuth.username:
        return check_password_hash(WbAuth.hashed_password(), password)
    return not WbAuth.enabled


@auth.error_handler
def unauthorized():
    return {"error": "Unauthorized"}, 401


def url_for(endpoint, **values):
    proxy = (
        request.headers.get("X-Ingress-Path")
        or request.headers.get("X-Forwarded-Prefix")
        or ""
    ).rstrip("/")
    return proxy + _url_for(endpoint, **values)


def sse_generator(sse_status: Callable) -> Generator[str, str, str]:
    """Generator to return the status for enabled cameras."""
    cameras = {}
    while True:
        if cameras != (cameras := sse_status()):
            yield f"data: {json.dumps(cameras)}\n\n"
        sleep(1)


def mfa_generator(mfa_req: Callable) -> Generator[str, str, str]:
    if mfa_req():
        yield f"event: mfa\ndata: {mfa_req()}\n\n"
        while mfa_req():
            sleep(1)
    while True:
        yield "event: mfa\ndata: clear\n\n"
        sleep(30)


def set_mfa(mfa_code: str) -> bool:
    """Set MFA code from WebUI."""
    mfa_file = f"{TOKEN_PATH}mfa_token.txt"
    try:
        with open(mfa_file, "w") as f:
            f.write(mfa_code)
        while os.path.getsize(mfa_file) != 0:
            sleep(1)
        return True
    except Exception as ex:
        logger.error(ex)
        return False


def get_webrtc_signal(cam_name: str, api_key: str) -> dict:
    """Generate signaling for MediaMTX webrtc."""
    hostname = env_bool("DOMAIN", urlparse(request.root_url).hostname or "localhost")
    ssl = "s" if env_bool("MTX_WEBRTCENCRYPTION") else ""
    webrtc = WEBRTC_URL.lstrip("http") or f"{ssl}://{hostname}:8889"
    wep = {"result": "ok", "cam": cam_name, "whep": f"http{webrtc}/{cam_name}/whep"}

    if ice_server := validate_ice(env_bool("MTX_WEBRTCICESERVERS")):
        return wep | {"servers": ice_server}

    ice_server = {
        "credentialType": "password",
        "urls": ["stun:stun.l.google.com:19302"],
    }
    if api_key:
        ice_server |= {
            "username": "wb",
            "credential": api_key,
            "credentialType": "password",
        }
    return wep | {"servers": [ice_server]}


def validate_ice(data: str) -> Optional[list[dict]]:
    if not data:
        return
    try:
        json_data = json.loads(data)
        if "urls" in json_data:
            return [json_data]
    except ValueError:
        return


def preview_refresh_route(snapshot_type: str) -> str:
    return "thumb" if snapshot_type == "api" else "snapshot"


def build_stream_entries(camera: dict, data: dict) -> list[dict]:
    auth_required = bool(WbAuth.api)

    def entry(
        stream_id: str,
        label: str,
        url: Optional[str],
        available: bool,
        reason: str = "",
    ) -> dict:
        return {
            "id": stream_id,
            "label": label,
            "url": url,
            "available": bool(available and url),
            "reason": "" if available and url else reason,
            "auth_required": auth_required,
        }

    enabled = camera.get("enabled", False)
    connected = camera.get("connected", False)
    active_reason = "disabled" if not enabled else "offline"

    streams = [
        entry(
            "webrtc",
            "WebRTC",
            data.get("webrtc_url"),
            bool(camera.get("webrtc"))
            and bool(BRIDGE_IP)
            and bool(data.get("webrtc_url")),
            "not supported" if not camera.get("webrtc") else "not configured",
        ),
        entry(
            "hls",
            "HLS",
            data.get("hls_url"),
            enabled and bool(HLS_URL),
            active_reason if HLS_URL else "not configured",
        ),
        entry(
            "rtmp",
            "RTMP",
            data.get("rtmp_url"),
            enabled and bool(RTMP_URL),
            active_reason if RTMP_URL else "not configured",
        ),
        entry(
            "rtsp",
            "RTSP",
            data.get("rtsp_url"),
            enabled and bool(RTSP_URL),
            active_reason if RTSP_URL else "not configured",
        ),
        entry(
            "fw_rtsp",
            "FW_RTSP",
            f"{data['rtsp_url']}fw"
            if camera.get("rtsp_fw_enabled") and data.get("rtsp_url")
            else None,
            bool(camera.get("rtsp_fw_enabled")) and connected,
            "not supported" if not camera.get("rtsp_fw_enabled") else active_reason,
        ),
        entry(
            "sd_card",
            "SD Card",
            camera.get("boa_url"),
            bool(camera.get("boa_url")) and connected,
            "not supported" if not camera.get("boa_url") else active_reason,
        ),
        entry(
            "rtsp_snapshot",
            "RTSP Snapshot",
            data.get("snapshot_url"),
            SNAPSHOT_TYPE != "api" and enabled,
            "disabled in api mode" if SNAPSHOT_TYPE == "api" else active_reason,
        ),
        entry(
            "api_thumbnail",
            "API Thumbnail",
            data.get("thumbnail_url"),
            SNAPSHOT_TYPE == "api",
            "disabled" if SNAPSHOT_TYPE != "api" else "",
        ),
    ]
    return streams


def format_stream(name_uri: str) -> dict:
    """
    Format stream with hostname.

    Parameters:
    - name_uri (str): camera name.

    Returns:
    - dict: Can be merged with camera info.
    """
    hostname = env_bool("DOMAIN", urlparse(request.root_url).hostname or "localhost")
    img = f"{name_uri}.{IMG_TYPE}"
    try:
        img_time = int(os.path.getmtime(IMG_PATH + img) * 1000)
    except FileNotFoundError:
        img_time = None

    webrtc_url = (WEBRTC_URL or f"http://{hostname}:8889") + f"/{name_uri}/"
    preview_kind = "api" if SNAPSHOT_TYPE == "api" else "rtsp"
    preview_url = f"img/{img}"
    data = {
        "hls_url": (HLS_URL or f"http://{hostname}:8888") + f"/{name_uri}/",
        "webrtc_url": webrtc_url if BRIDGE_IP else None,
        "rtmp_url": (RTMP_URL or f"rtmp://{hostname}:1935") + f"/{name_uri}",
        "rtsp_url": (RTSP_URL or f"rtsp://{hostname}:8554") + f"/{name_uri}",
        "img_url": f"img/{img}" if img_time else None,
        "snapshot_url": f"snapshot/{img}",
        "thumbnail_url": f"thumb/{img}",
        "img_time": img_time,
        "preview_url": preview_url,
        "preview_kind": preview_kind,
        "preview_refresh_mode": preview_refresh_route(SNAPSHOT_TYPE),
    }
    if LLHLS:
        data["hls_url"] = data["hls_url"].replace("http:", "https:")
    return data


def format_streams(cams: dict) -> dict[str, dict]:
    """
    Format info for multiple streams with hostname.

    Parameters:
    - cams (dict): get_all_cam_info from StreamManager.

    Returns:
    - dict: cam info with hostname.
    """
    formatted = {}
    for uri, cam in cams.items():
        stream_data = format_stream(uri)
        formatted[uri] = cam | stream_data
        formatted[uri]["streams"] = build_stream_entries(formatted[uri], stream_data)
    return formatted


def all_cams(streams: StreamManager, total: int) -> dict:
    return {
        "total": total,
        "available": streams.total,
        "enabled": streams.active,
        "cameras": format_streams(streams.get_all_cam_info()),
    }


def boa_snapshot(stream: Stream) -> Optional[dict]:
    """Take photo."""
    stream.send_cmd("take_photo")
    if boa_info := stream.get_info("boa_info"):
        return boa_info.get("last_photo")
