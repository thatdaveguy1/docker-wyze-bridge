"""Native alias readiness, selection, and snapshot — extracted from go2rtc.py.

Architecture review candidate #9: one module owns the native alias lifecycle
(ready/cache/probe/snapshot) behind a narrow seam. go2rtc.py keeps the core
API/probe helpers; this module owns alias-specific logic. Talkback (two-way
audio) lives in wyzebridge.native_talkback.
"""

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from wyzebridge.config import IMG_PATH, IMG_TYPE

# --- Port helpers (canonical source: wyzebridge.go2rtc) ---
from wyzebridge.go2rtc import (  # noqa: E402
    _go2rtc_api_port,
    go2rtc_api_base,
    go2rtc_rtsp_base,
)
from wyzebridge.logging import logger
from wyzebridge.preview_validation import (
    preview_bytes_are_valid_image,
    preview_payload_matches_existing,
    record_preview_hash,
)

# --- Caches and constants ---

_NATIVE_ALIAS_READY_CACHE_TTL = 10.0
_NATIVE_ALIAS_READY_CACHE: dict[str, tuple[float, bool]] = {}
_NATIVE_ALIAS_STATUS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_KEYFRAME_CONSUMER_PILEUP_THRESHOLD = 16
_GO2RTC_API_REACHABLE_CACHE_TTL = 5.0
_GO2RTC_API_REACHABLE_CACHE: dict[int, tuple[float, bool]] = {}


# Per-camera recovery aliases tried when the primary and substream aliases fail.
# The HD recovery lane is opt-in via GO2RTC_HD_RECOVERY_CAMERAS (comma-separated
# camera/uri names, default empty = feature off) so no household camera is baked in.
def recovery_aliases(cam_name: str) -> list[str]:
    cameras = {c.strip() for c in os.environ.get("GO2RTC_HD_RECOVERY_CAMERAS", "").split(",") if c.strip()}
    if cam_name in cameras:
        return [f"{cam_name}-v4-hd-recovery"]
    return []


_VALIDATED_NATIVE_MODELS = {
    "HL_CAM3P": {
        "reason": "HL_CAM3P validated on native go2rtc for the SD feed while the main alias remains unproven on this host",
        "selected": False,
        "sub_selected": True,
    },
    "HL_CAM4": {
        "reason": "HL_CAM4 validated on native go2rtc with higher-resolution main stream",
        "selected": True,
        "sub_selected": True,
    },
    "HL_BC": {
        "reason": "HL_BC native go2rtc SD remains diagnostic-only until it produces non-empty frames on this host",
        "selected": False,
        "sub_selected": False,
    },
}


def native_alias(name_uri: str, substream: bool = False) -> str:
    if substream or name_uri.endswith("-sub"):
        base_name = name_uri[:-4] if name_uri.endswith("-sub") else name_uri
        return f"{base_name}-sd"
    return name_uri


def native_snapshot_path(cam_name: str) -> Path:
    return Path(f"{IMG_PATH}{cam_name}.{IMG_TYPE}")


def _validated_native_model(camera) -> dict[str, Any] | None:
    return _VALIDATED_NATIVE_MODELS.get(getattr(camera, "product_model", ""))


def _go2rtc_api_reachable(timeout: float = 0.75) -> bool:
    now = time.monotonic()
    port = _go2rtc_api_port()
    cached = _GO2RTC_API_REACHABLE_CACHE.get(port)
    if cached and now - cached[0] < _GO2RTC_API_REACHABLE_CACHE_TTL:
        return cached[1]

    reachable = False
    try:
        response = requests.get(f"{go2rtc_api_base()}/api", timeout=timeout)
        response.raise_for_status()
        reachable = True
    except requests.RequestException:
        reachable = False
    if reachable:
        _GO2RTC_API_REACHABLE_CACHE[port] = (now, True)
    else:
        _GO2RTC_API_REACHABLE_CACHE.pop(port, None)
    return reachable


def _go2rtc_keyframe_consumer_count(details: dict[str, Any]) -> int:
    consumers = details.get("consumers") if isinstance(details, dict) else None
    if not isinstance(consumers, list):
        return 0
    return sum(1 for consumer in consumers if isinstance(consumer, dict) and consumer.get("format_name") == "keyframe")


def _go2rtc_receiver_child_count(details: dict[str, Any]) -> int:
    producers = details.get("producers") if isinstance(details, dict) else None
    if not isinstance(producers, list):
        return 0
    child_count = 0
    for producer in producers:
        receivers = producer.get("receivers") if isinstance(producer, dict) else None
        if not isinstance(receivers, list):
            continue
        for receiver in receivers:
            if not isinstance(receiver, dict):
                continue
            codec = receiver.get("codec")
            codec_name = codec.get("codec_name") if isinstance(codec, dict) else None
            if str(codec_name).lower() != "h264":
                continue
            childs = receiver.get("childs")
            if isinstance(childs, list):
                child_count += len(childs)
    return child_count


def _native_alias_status_from_details(details: dict[str, Any]) -> dict[str, Any]:
    producers = details.get("producers") if isinstance(details, dict) else None
    producer_count = len(producers) if isinstance(producers, list) else 0
    keyframe_consumers = _go2rtc_keyframe_consumer_count(details)
    receiver_children = _go2rtc_receiver_child_count(details)
    wedged = max(keyframe_consumers, receiver_children) >= _KEYFRAME_CONSUMER_PILEUP_THRESHOLD
    return {
        "producer_count": producer_count,
        "keyframe_consumers": keyframe_consumers,
        "receiver_children": receiver_children,
        "wedged": wedged,
        "ready": bool(producer_count and not wedged),
    }


def _native_alias_details_are_ready(alias: str, details: dict[str, Any]) -> bool:
    status = _native_alias_status_from_details(details)
    if not status["producer_count"]:
        return False

    if status["wedged"]:
        logger.debug(
            f"[{alias}] Native alias not ready: keyframe consumers appear wedged "
            f"(consumers={status['keyframe_consumers']}, "
            f"receiver_children={status['receiver_children']})"
        )
        return False
    return True


def _native_alias_status(alias: str, timeout: float = 0.25, use_cache: bool = True) -> dict[str, Any]:
    now = time.monotonic()
    cached = _NATIVE_ALIAS_STATUS_CACHE.get(alias)
    if use_cache and cached and now - cached[0] < _NATIVE_ALIAS_READY_CACHE_TTL:
        return dict(cached[1])

    details = _go2rtc_stream_details(alias, timeout=timeout)
    status = _native_alias_status_from_details(details)
    _NATIVE_ALIAS_STATUS_CACHE[alias] = (now, dict(status))
    _NATIVE_ALIAS_READY_CACHE[alias] = (now, status["ready"])
    return status


def _native_alias_is_ready(alias: str, timeout: float = 0.25) -> bool:
    return bool(_native_alias_status(alias, timeout=timeout)["ready"])


def clear_native_alias_status_cache(alias: str) -> None:
    _NATIVE_ALIAS_READY_CACHE.pop(alias, None)
    _NATIVE_ALIAS_STATUS_CACHE.pop(alias, None)


def native_stream_info(camera, substream: bool = False) -> dict[str, Any]:
    alias = native_alias(camera.name_uri, substream)
    primary_alias = native_alias(camera.name_uri, False)
    model_support = _validated_native_model(camera)
    api_reachable = _go2rtc_api_reachable()
    supported = bool(model_support and not getattr(camera, "is_gwell", False))
    selected_flag = (
        (model_support.get("sub_selected") if substream else model_support.get("selected")) if model_support else False
    )
    selected = bool(supported and selected_flag and api_reachable)
    alias_status = (
        _native_alias_status(alias)
        if selected and api_reachable
        else {
            "producer_count": 0,
            "keyframe_consumers": 0,
            "receiver_children": 0,
            "wedged": False,
            "ready": False,
        }
    )
    alias_ready = bool(selected and alias_status["ready"])

    if getattr(camera, "is_gwell", False):
        reason = "GW_* remains blocked until a real Gwell model validates end-to-end"
    elif model_support:
        reason = model_support["reason"]
        if selected_flag and not api_reachable:
            reason = f"{reason}; go2rtc sidecar is not reachable"
    else:
        reason = "bridge remains the default until native go2rtc is validated for this model"

    if substream:
        talkback_supported = False
        talkback_reason = "talkback is only exposed on the primary native alias"
    elif selected:
        talkback_supported = True
        talkback_reason = "API-first talkback is available through the native go2rtc alias"
    elif supported and model_support and selected_flag and not api_reachable:
        talkback_supported = False
        talkback_reason = "talkback requires a reachable go2rtc sidecar"
    elif supported:
        talkback_supported = False
        talkback_reason = "talkback is limited to native-selected cameras in 4.2"
    else:
        talkback_supported = False
        talkback_reason = "talkback is unavailable until native go2rtc is validated for this model"

    return {
        "native_supported": supported,
        "native_selected": selected,
        "native_reason": reason,
        "native_alias": alias,
        "native_rtsp_url": f"{go2rtc_rtsp_base()}/{alias}",
        "native_preload": selected,
        "native_api_reachable": api_reachable,
        "native_alias_ready": alias_ready,
        "native_producer_count": alias_status["producer_count"],
        "native_keyframe_consumers": alias_status["keyframe_consumers"],
        "native_receiver_children": alias_status["receiver_children"],
        "native_alias_wedged": alias_status["wedged"],
        "snapshot_source": "go2rtc" if selected else "rtsp",
        "talkback_supported": talkback_supported,
        "talkback_reason": talkback_reason,
        "talkback_alias": primary_alias,
        "talkback_source": "go2rtc" if talkback_supported else None,
    }


def preload_native_stream(alias: str, timeout: float = 2.0) -> dict[str, Any]:
    result = {"alias": alias, "requested": False, "ok": False}
    try:
        response = requests.put(
            f"{go2rtc_api_base()}/api/preload",
            params={"src": alias},
            timeout=timeout,
        )
        result["requested"] = True
        result["status_code"] = response.status_code
        response.raise_for_status()
        result["ok"] = True
    except requests.RequestException as ex:
        if ex.response is not None:
            result["status_code"] = ex.response.status_code
        result["error"] = str(ex)
    return result


def write_native_snapshot(
    alias: str,
    cam_name: str,
    timeout: float = 15.0,
    warn_on_failure: bool = True,
) -> bool:
    output_path = native_snapshot_path(cam_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    last_error = "no frame returned"
    max_attempts = 3
    attempts = 0
    while time.monotonic() < deadline and attempts < max_attempts:
        attempts += 1
        try:
            response = requests.get(
                f"{go2rtc_api_base()}/api/frame.jpeg?src={quote(alias, safe='')}&cache=10s",
                timeout=max(0.5, min(5.0, deadline - time.monotonic())),
            )
            if response.status_code == 503:
                logger.debug(f"[{cam_name}] Native snapshot from {alias} not ready: status=503; falling back")
                return False
            if response.status_code == 404:
                log = logger.warning if warn_on_failure else logger.debug
                log(f"❗ [{cam_name}] Native snapshot from {alias} failed: status=404")
                return False
            response.raise_for_status()
            if not response.content:
                status = _native_alias_status(alias, timeout=0.25, use_cache=False)
                if status["producer_count"]:
                    log = logger.warning if warn_on_failure else logger.debug
                    log(
                        f"[{cam_name}] Native snapshot from {alias} returned an empty frame "
                        f"despite active producers "
                        f"(consumers={status['keyframe_consumers']}, "
                        f"receiver_children={status['receiver_children']})"
                    )
                    return False
                last_error = "empty response"
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                continue
            if not preview_bytes_are_valid_image(response.content):
                log = logger.warning if warn_on_failure else logger.debug
                log(f"❗ [{cam_name}] Native snapshot from {alias} was not a valid image")
                return False
            if preview_payload_matches_existing(output_path, response.content):
                last_error = "matched existing preview"
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                continue
            output_path.write_bytes(response.content)
            record_preview_hash(
                output_path,
                response.content,
                camera=cam_name,
                source=f"go2rtc:{alias}",
            )
            return output_path.stat().st_size > 0
        except (requests.RequestException, OSError) as ex:
            last_error = f"{type(ex).__name__}: {ex}"
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    logger.debug(
        f"[{cam_name}] Native snapshot from {alias} did not produce a fresh frame "
        f"after {attempts} attempt(s) before fallback: {last_error}"
    )
    return False


def _go2rtc_stream_details(alias: str, timeout: float = 2.0) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{go2rtc_api_base()}/api/streams",
            params={"src": alias, "microphone": "any"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}
    except (requests.RequestException, ValueError):
        pass
    try:
        response = requests.get(
            f"{go2rtc_api_base()}/api/streams",
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            details = data.get(alias)
            return details if isinstance(details, dict) else {}
    except (requests.RequestException, ValueError):
        return {}
    return {}
