"""Helper functions extracted from iotc.py.

Architecture review candidate #11: the 1307-line iotc.py monolith contained
module-level env/config helpers and audio codec mapping logic that were
tightly coupled to the WyzeIOTCSession class only through `self.camera`
and `self.av_chan_id`. Extracting them here keeps iotc.py focused on
session lifecycle and connection/auth logic.
"""

import json
import logging
import os
import pathlib
from ctypes import CDLL, c_uint32

from wyzebridge.auth import redact_password as redact_password  # noqa: F401
from wyzebridge.bridge_utils import truthy
from wyzebridge.config import CONNECT_TIMEOUT
from wyzebridge.source_selector import hl_cam4_main_probe_mode as hl_cam4_main_probe_mode  # noqa: F401
from wyzecam.api_models import WyzeCamera
from wyzecam.tutk import tutk

logger = logging.getLogger(__name__)


# --- Environment / config helpers ---


def tutk_trace_enabled(camera: WyzeCamera) -> bool:
    raw = os.getenv("TUTK_TRACE_STREAM", "").strip().lower()
    if not raw:
        return False

    targets = {item.strip() for item in raw.split(",") if item.strip()}
    return "all" in targets or camera.name_uri in targets


def log_tutk_trace(camera: WyzeCamera, event: str, **fields) -> None:
    raw = os.getenv("TUTK_TRACE_STREAM", "").strip().lower()
    enabled = tutk_trace_enabled(camera)
    if event == "connect_start":
        logger.debug(f"TUTK_TRACE_GATE raw={raw!r} camera={camera.name_uri} enabled={enabled}")
    if not enabled:
        return

    payload = {"camera": camera.name_uri, "event": event} | fields
    trace = f"[TUTK_TRACE] {json.dumps(payload, sort_keys=True)}"
    logger.info(trace)


def hl_cam4_connect_watchdog_secs() -> float | None:
    raw = os.getenv("HL_CAM4_CONNECT_WATCHDOG_SECS", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return None
    if raw:
        try:
            return max(float(raw), 0.1)
        except ValueError:
            logger.warning("[IOTC] Ignoring invalid HL_CAM4_CONNECT_WATCHDOG_SECS=%r", raw)
            return None
    return float(CONNECT_TIMEOUT + 2)


def truthy_env(name: str) -> bool:
    return truthy(os.getenv(name))


def configure_tutk_native_log(tutk_platform_lib: CDLL) -> None:
    if not truthy_env("TUTK_NATIVE_LOG"):
        return

    log_path = os.getenv("TUTK_NATIVE_LOG_PATH", "/tmp/tutk_iotc.log").strip() or "/tmp/tutk_iotc.log"
    level_raw = os.getenv("TUTK_NATIVE_LOG_LEVEL", "0").strip()
    try:
        log_level = max(int(level_raw), 0)
    except ValueError:
        logger.warning("[TUTK] Ignoring invalid TUTK_NATIVE_LOG_LEVEL=%r", level_raw)
        log_level = 0

    try:
        pathlib.Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        logger.warning(f"TUTK_NATIVE_LOG mkdir_failed path={log_path} error={type(ex).__name__}: {ex}")

    errno = tutk.iotc_set_log_attr(
        tutk_platform_lib,
        log_path,
        c_uint32(log_level),
    )
    logger.info(f"TUTK_NATIVE_LOG path={log_path} level={log_level} errno={errno}")


# --- Audio codec mapping ---

AUDIO_CODEC_MAPPING = {
    137: ("mulaw", None),  # sample rate resolved at call time
    140: ("s16le", None),
    141: ("aac", None),
    143: ("alaw", None),
    144: ("aac", 16000),  # aac_eld
    146: ("opus", 16000),
}


def get_audio_sample_rate(camera: WyzeCamera) -> int:
    """Attempt to get the audio sample rate from camera info or default."""
    if camera.camera_info and "audioParm" in camera.camera_info:
        audio_param = camera.camera_info["audioParm"]
        return int(audio_param.get("sampleRate", camera.default_sample_rate))

    return camera.default_sample_rate


def resolve_audio_codec(codec_id: int, sample_rate: int) -> tuple[str, int]:
    """Map a TUTK codec_id to (codec_name, sample_rate)."""
    codec, mapped_rate = AUDIO_CODEC_MAPPING.get(codec_id, (None, None))

    if not codec:
        raise RuntimeError(f"\nUnknown audio codec {codec_id=}\n")

    rate = mapped_rate or sample_rate
    logger.info(f"[IOTC] Audio {codec=} {rate=} {codec_id=}")
    return codec, rate or 16000
