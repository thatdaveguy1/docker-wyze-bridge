"""Babysitter state: atomic persistence of cooldowns, daily counts, and history.

State is stored in ``/config/babysitter_state.json`` with atomic writes
(write to temp, fsync, rename). No secrets in this file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("/config/babysitter_state.json")
MAX_HISTORY = 20


@dataclass
class RebootEvent:
    """A single reboot event in history."""

    timestamp: float
    camera: str
    action: str  # "reolink_cgi" or "skipped"
    reason: str
    outcome: str  # "success" or "failed"
    duration: float = 0.0  # seconds from reboot to recovery


@dataclass
class CameraState:
    """Per-camera runtime state."""

    last_reboot: float = 0.0  # epoch timestamp of last reboot attempt
    reboot_times: list[float] = field(default_factory=list)  # rolling 24h window
    last_snapshot_hash: str = ""
    last_snapshot_time: float = 0.0
    consecutive_bad_snapshots: int = 0
    stale_hash_since: float = 0.0  # epoch when hash first became stale
    fps_zero_since: float = 0.0  # epoch when camera_fps/process_fps first hit 0
    current_state: str = "online"  # online/video_down/snapshot_down/wifi_down/recovering


@dataclass
class BabysitterState:
    """Full babysitter runtime state."""

    cameras: dict[str, CameraState] = field(default_factory=dict)
    history: list[RebootEvent] = field(default_factory=list)

    def get_camera(self, name: str) -> CameraState:
        """Get or create state for a camera."""
        if name not in self.cameras:
            self.cameras[name] = CameraState()
        return self.cameras[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cameras": {k: asdict(v) for k, v in self.cameras.items()},
            "history": [asdict(e) for e in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BabysitterState:
        state = cls()
        for name, cam_data in data.get("cameras", {}).items():
            state.cameras[name] = CameraState(**cam_data)
        for evt_data in data.get("history", []):
            state.history.append(RebootEvent(**evt_data))
        return state


# ---------------------------------------------------------------------------
# Cooldown logic
# ---------------------------------------------------------------------------


def is_in_cooldown(cam_state: CameraState, cooldown_seconds: int) -> bool:
    """Return True if the camera is still in cooldown."""
    if cam_state.last_reboot == 0:
        return False
    return (time.time() - cam_state.last_reboot) < cooldown_seconds


def cooldown_remaining(cam_state: CameraState, cooldown_seconds: int) -> int:
    """Return seconds remaining in cooldown (0 if expired)."""
    if cam_state.last_reboot == 0:
        return 0
    remaining = cooldown_seconds - (time.time() - cam_state.last_reboot)
    return max(0, int(remaining))


# ---------------------------------------------------------------------------
# Daily reboot count logic
# ---------------------------------------------------------------------------

DAY_SECONDS = 86400


def daily_reboot_count(cam_state: CameraState) -> int:
    """Count reboots in the last 24h (rolling window)."""
    cutoff = time.time() - DAY_SECONDS
    return sum(1 for t in cam_state.reboot_times if t > cutoff)


def prune_old_reboots(cam_state: CameraState) -> None:
    """Remove reboot timestamps older than 24h."""
    cutoff = time.time() - DAY_SECONDS
    cam_state.reboot_times = [t for t in cam_state.reboot_times if t > cutoff]


def can_reboot(
    cam_state: CameraState,
    cooldown_seconds: int,
    max_daily: int,
) -> tuple[bool, str]:
    """Check if a camera can be rebooted.

    Returns (can_reboot, reason_if_not).
    """
    if is_in_cooldown(cam_state, cooldown_seconds):
        remaining = cooldown_remaining(cam_state, cooldown_seconds)
        return False, f"in cooldown ({remaining}s remaining)"
    prune_old_reboots(cam_state)
    count = daily_reboot_count(cam_state)
    if count >= max_daily:
        return False, f"max daily reached ({count}/{max_daily})"
    return True, ""


def record_reboot(
    state: BabysitterState,
    camera: str,
    action: str,
    reason: str,
    outcome: str,
    duration: float = 0.0,
) -> None:
    """Record a reboot event in state and history."""
    cam = state.get_camera(camera)
    cam.last_reboot = time.time()
    if outcome == "success":
        cam.reboot_times.append(time.time())
        prune_old_reboots(cam)
    state.history.insert(
        0,
        RebootEvent(
            timestamp=time.time(),
            camera=camera,
            action=action,
            reason=reason,
            outcome=outcome,
            duration=duration,
        ),
    )
    # Trim history to MAX_HISTORY
    state.history = state.history[:MAX_HISTORY]


# ---------------------------------------------------------------------------
# Atomic persistence
# ---------------------------------------------------------------------------


def load_state(path: Path = DEFAULT_STATE_PATH) -> BabysitterState:
    """Load state from JSON file, or return empty state if file missing."""
    if not path.exists():
        logger.debug("State file %s does not exist, starting fresh", path)
        return BabysitterState()
    try:
        data = json.loads(path.read_text())
        return BabysitterState.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        logger.warning("Failed to load state file %s: %s, starting fresh", path, exc)
        return BabysitterState()


def save_state(state: BabysitterState, path: Path = DEFAULT_STATE_PATH) -> None:
    """Atomically save state to JSON file.

    Writes to a temp file, fsyncs, then renames to the target path.
    This prevents corruption if the process is killed mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(json.dumps(state.to_dict(), indent=2))
        # fsync the temp file
        with open(tmp_path) as f:
            os.fsync(f.fileno())
        tmp_path.rename(path)
        logger.debug("Saved state to %s", path)
    except OSError as exc:
        logger.error("Failed to save state to %s: %s", path, exc)
        # Clean up temp file if rename failed
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
