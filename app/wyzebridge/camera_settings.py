import json
from pathlib import Path

from wyzebridge.bridge_utils import clean_cam_name
from wyzebridge.logging import logger

SETTINGS_PATH = Path("/config/wyze_camera_settings.json")
VALID_STREAM_MODES = {"main", "sub", "both"}


def _normalize_cam_name(cam_name: str) -> str:
    return clean_cam_name(cam_name or "")


def load_camera_settings() -> dict[str, dict[str, str]]:
    try:
        if not SETTINGS_PATH.is_file():
            return {}
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        logger.warning(f"[SETTINGS] Unable to load camera settings: {type(ex).__name__}: {ex}")
        return {}

    if not isinstance(data, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for cam_name, config in data.items():
        slug = _normalize_cam_name(cam_name)
        if not slug or not isinstance(config, dict):
            continue
        stream = str(config.get("stream", "")).strip().lower()
        if stream in VALID_STREAM_MODES:
            normalized[slug] = {"stream": stream}
    return normalized


def save_camera_settings(settings: dict[str, dict[str, str]]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def get_camera_setting(cam_name: str, key: str, default: str = "") -> str:
    slug = _normalize_cam_name(cam_name)
    if not slug:
        return default
    return load_camera_settings().get(slug, {}).get(key, default)


def set_camera_stream_mode(cam_name: str, stream_mode: str) -> str:
    slug = _normalize_cam_name(cam_name)
    mode = str(stream_mode or "").strip().lower()
    if not slug or mode not in VALID_STREAM_MODES:
        raise ValueError("Invalid camera stream mode")

    settings = load_camera_settings()
    entry = settings.setdefault(slug, {})
    entry["stream"] = mode
    save_camera_settings(settings)
    return mode
