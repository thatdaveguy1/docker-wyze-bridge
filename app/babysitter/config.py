"""Babysitter configuration: env defaults + JSON file override.

Env vars provide defaults on first load. After that, the JSON config file
at ``/config/babysitter_config.json`` is the source of truth for runtime
changes made via the settings page.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/config/babysitter_config.json")


@dataclass
class CameraEntry:
    """A single camera mapping entry."""

    friendly_name: str
    scrypted_id: str
    ip: str
    frigate_name: str
    reolink_port: int = 0  # 0 = use global reolink_port
    reolink_use_https: bool | None = None  # None = use global setting


@dataclass
class BabysitterConfig:
    """Full babysitter configuration."""

    # Scrypted (detection only)
    scrypted_host: str = ""
    scrypted_username: str = ""
    scrypted_password: str = ""

    # Frigate
    frigate_host: str = ""

    # MQTT (optional)
    mqtt_broker: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""

    # Reolink CGI (reboot)
    reolink_username: str = ""
    reolink_password: str = ""
    reolink_port: int = 80
    reolink_use_https: bool = False
    # ONVIF (fallback reboot for cameras without web UI, e.g. E1 Pro)
    onvif_port: int = 8000

    # Watchdog config
    dry_run: bool = True
    cooldown: int = 900
    max_daily: int = 3
    video_down_threshold: int = 120
    snapshot_samples: int = 3
    snapshot_stale_window: int = 600
    reboot_timeout: int = 60
    recovery_wait: int = 180
    interval: int = 60

    # Per-camera settings
    cameras: list[CameraEntry] = field(default_factory=list)
    approved_cameras: set[str] = field(default_factory=set)
    per_camera_dry_run: dict[str, bool] = field(default_factory=dict)

    def to_dict(self, mask_secrets: bool = True) -> dict[str, Any]:
        """Serialize to dict. If *mask_secrets*, replace passwords with '****'."""
        d = asdict(self)
        d["approved_cameras"] = sorted(self.approved_cameras)
        if mask_secrets:
            for key in ("scrypted_password", "mqtt_password", "reolink_password"):
                if d.get(key):
                    d[key] = "****"
        return d

    def to_json(self, mask_secrets: bool = True) -> str:
        return json.dumps(self.to_dict(mask_secrets=mask_secrets), indent=2)


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "")
    return val.lower() in ("1", "true", "yes", "on") if val else default


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key, "")
    return int(val) if val.isdigit() else default


def _parse_cameras(env_val: str) -> list[CameraEntry]:
    """Parse BABYSIT_CAMERAS env var.

    Format: friendly_name:scrypted_id:ip:frigate_name[:port[:https]]
    Port and https are optional; 0/None means use global defaults.
    """
    if not env_val:
        return []
    cameras: list[CameraEntry] = []
    for entry in env_val.split(","):
        parts = entry.strip().split(":")
        if len(parts) >= 4:
            port = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            use_https = parts[5].lower() in ("1", "true", "yes") if len(parts) > 5 else None
            cameras.append(CameraEntry(
                friendly_name=parts[0],
                scrypted_id=parts[1],
                ip=parts[2],
                frigate_name=parts[3],
                reolink_port=port,
                reolink_use_https=use_https,
            ))
    return cameras


def from_env() -> BabysitterConfig:
    """Build a BabysitterConfig from environment variables."""
    cfg = BabysitterConfig(
        scrypted_host=os.environ.get("SCRYPTED_HOST", ""),
        scrypted_username=os.environ.get("SCRYPTED_USERNAME", ""),
        scrypted_password=os.environ.get("SCRYPTED_PASSWORD", ""),
        frigate_host=os.environ.get("FRIGATE_HOST", ""),
        mqtt_broker=os.environ.get("MQTT_BROKER", ""),
        mqtt_port=_env_int("MQTT_PORT", 1883),
        mqtt_username=os.environ.get("MQTT_USERNAME", ""),
        mqtt_password=os.environ.get("MQTT_PASSWORD", ""),
        reolink_username=os.environ.get("REOLINK_USERNAME", ""),
        reolink_password=os.environ.get("REOLINK_PASSWORD", ""),
        reolink_port=_env_int("REOLINK_PORT", 80),
        reolink_use_https=_env_bool("REOLINK_USE_HTTPS", False),
        onvif_port=_env_int("ONVIF_PORT", 8000),
        dry_run=_env_bool("BABYSIT_DRY_RUN", True),
        cooldown=_env_int("BABYSIT_COOLDOWN", 900),
        max_daily=_env_int("BABYSIT_MAX_DAILY", 3),
        video_down_threshold=_env_int("BABYSIT_VIDEO_DOWN_THRESHOLD", 120),
        snapshot_samples=_env_int("BABYSIT_SNAPSHOT_SAMPLES", 3),
        snapshot_stale_window=_env_int("BABYSIT_SNAPSHOT_STALE_WINDOW", 600),
        reboot_timeout=_env_int("BABYSIT_REBOOT_TIMEOUT", 60),
        recovery_wait=_env_int("BABYSIT_RECOVERY_WAIT", 180),
        interval=_env_int("BABYSIT_INTERVAL", 60),
        cameras=_parse_cameras(os.environ.get("BABYSIT_CAMERAS", "")),
    )
    approved = os.environ.get("BABYSIT_APPROVED_CAMERAS", "")
    cfg.approved_cameras = {c.strip() for c in approved.split(",") if c.strip()}
    return cfg


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> BabysitterConfig:
    """Load config from JSON file, falling back to env defaults.

    If the JSON file exists, it overrides env defaults. If not, env defaults
    are used and written to the file for future editing.
    """
    env_cfg = from_env()
    if not path.exists():
        logger.debug("Config file %s does not exist, using env defaults", path)
        return env_cfg

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load config file %s: %s, using env defaults", path, exc)
        return env_cfg

    # Merge: JSON file takes precedence over env for non-empty values
    cfg = env_cfg
    for key, value in data.items():
        if key == "cameras":
            cfg.cameras = [CameraEntry(**c) for c in value]
        elif key == "approved_cameras":
            cfg.approved_cameras = set(value)
        elif key == "per_camera_dry_run":
            cfg.per_camera_dry_run = dict(value)
        elif hasattr(cfg, key) and value is not None:
            # Only override if the JSON value is non-empty/non-default
            if isinstance(value, str) and value:
                setattr(cfg, key, value)
            elif not isinstance(value, str):
                setattr(cfg, key, value)
    return cfg


def save_config(cfg: BabysitterConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Save config to JSON file (with real passwords, not masked)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.to_json(mask_secrets=False))
    logger.debug("Saved config to %s", path)


def update_config(
    updates: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH
) -> BabysitterConfig:
    """Apply partial updates to the config file and return the new config.

    Passwords are only updated if the new value is not '****' (masked).
    """
    cfg = load_config(path)
    for key, value in updates.items():
        if key == "cameras":
            cfg.cameras = [CameraEntry(**c) for c in value]
        elif key == "approved_cameras":
            cfg.approved_cameras = set(value)
        elif key == "per_camera_dry_run":
            cfg.per_camera_dry_run = dict(value)
        elif hasattr(cfg, key):
            # Don't overwrite passwords with masked values
            if key.endswith("_password") and value == "****":
                continue
            setattr(cfg, key, value)
    save_config(cfg, path)
    return cfg
