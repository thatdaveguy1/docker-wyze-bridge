"""Per-camera snapshot health tracking for the DWB snapshot pipeline.

Records consecutive snapshot failures, stale-hash duration, and per-camera
state transitions (``online`` → ``snapshot_down`` → ``stale_snapshot``).
``SnapshotManager`` calls into the tracker after each snapshot attempt so
the bridge can self-heal and publish MQTT health without relying on the
external babysitter process.

State labels (kept compatible with the babysitter's ``CameraState``):

* ``online``         — last snapshot succeeded and hash is fresh.
* ``snapshot_down``  — ``failure_threshold`` consecutive snapshots failed.
* ``stale_snapshot`` — snapshots succeed but the hash has not changed for
  longer than ``stale_window`` seconds (frozen frame).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

STATE_ONLINE = "online"
STATE_SNAPSHOT_DOWN = "snapshot_down"
STATE_STALE_SNAPSHOT = "stale_snapshot"


@dataclass
class CameraSnapshotHealth:
    """Mutable per-camera health record."""

    state: str = STATE_ONLINE
    consecutive_failures: int = 0
    last_success_time: float = 0.0
    last_hash: str = ""
    stale_hash_since: float = 0.0
    last_restart_time: float = 0.0
    previous_state: str = STATE_ONLINE

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "last_success_time": self.last_success_time,
            "stale": self.state == STATE_STALE_SNAPSHOT,
            "last_restart_time": self.last_restart_time,
        }


class SnapshotHealthTracker:
    """Thread-safe per-camera snapshot health tracker.

    Parameters
    ----------
    failure_threshold:
        Number of consecutive snapshot failures before declaring
        ``snapshot_down``. Default 3 (matches the babysitter's
        ``SNAPSHOT_SAMPLES``).
    stale_window:
        Seconds that a snapshot hash may remain unchanged before
        declaring ``stale_snapshot``. Default 600 (10 minutes).
    restart_cooldown:
        Minimum seconds between proactive stream restarts triggered by
        the tracker. Prevents restart loops. Default 60.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        stale_window: float = 600.0,
        restart_cooldown: float = 60.0,
    ) -> None:
        self._cameras: dict[str, CameraSnapshotHealth] = {}
        self._failure_threshold = failure_threshold
        self._stale_window = stale_window
        self._restart_cooldown = restart_cooldown
        self._lock = threading.Lock()

    def get(self, cam_name: str) -> CameraSnapshotHealth:
        with self._lock:
            if cam_name not in self._cameras:
                self._cameras[cam_name] = CameraSnapshotHealth()
            return self._cameras[cam_name]

    def record_success(self, cam_name: str, payload_hash: str = "") -> str:
        """Record a successful snapshot and return the new state label."""
        with self._lock:
            cam = self._cameras.setdefault(cam_name, CameraSnapshotHealth())
            cam.previous_state = cam.state
            cam.consecutive_failures = 0
            now = time.time()
            prev_success_time = cam.last_success_time
            cam.last_success_time = now

            # Stale-hash tracking (only when a hash is provided).
            if payload_hash:
                if cam.last_hash and payload_hash == cam.last_hash:
                    if cam.stale_hash_since == 0.0:
                        # Mark stale from the PREVIOUS success, not this one.
                        cam.stale_hash_since = prev_success_time or now
                else:
                    cam.stale_hash_since = 0.0
                cam.last_hash = payload_hash

            # Determine state: stale only if hash unchanged beyond window.
            if cam.stale_hash_since and (now - cam.stale_hash_since) > self._stale_window:
                cam.state = STATE_STALE_SNAPSHOT
            else:
                cam.state = STATE_ONLINE
            return cam.state

    def record_failure(self, cam_name: str) -> str:
        """Record a failed snapshot and return the new state label."""
        with self._lock:
            cam = self._cameras.setdefault(cam_name, CameraSnapshotHealth())
            cam.previous_state = cam.state
            cam.consecutive_failures += 1
            if cam.consecutive_failures >= self._failure_threshold:
                cam.state = STATE_SNAPSHOT_DOWN
            return cam.state

    def should_restart(self, cam_name: str) -> bool:
        """Return True if a proactive stream restart is advisable.

        A restart is advised when the camera is ``snapshot_down`` or
        ``stale_snapshot`` AND no restart has been triggered within the
        cooldown window.
        """
        with self._lock:
            cam = self._cameras.get(cam_name)
            if cam is None:
                return False
            if cam.state not in (STATE_SNAPSHOT_DOWN, STATE_STALE_SNAPSHOT):
                return False
            now = time.time()
            if cam.last_restart_time and (now - cam.last_restart_time) < self._restart_cooldown:
                return False
            return True

    def mark_restarted(self, cam_name: str) -> None:
        """Record that a proactive restart was triggered for this camera."""
        with self._lock:
            cam = self._cameras.setdefault(cam_name, CameraSnapshotHealth())
            cam.last_restart_time = time.time()
            # Reset failure counter so we give the restart a fresh chance.
            cam.consecutive_failures = 0
            # If stale, reset stale tracking so the new frames get a fair window.
            cam.stale_hash_since = 0.0
            cam.state = STATE_ONLINE

    def state_changed(self, cam_name: str) -> bool:
        """Return True if the most recent record_* call changed the state."""
        with self._lock:
            cam = self._cameras.get(cam_name)
            if cam is None:
                return False
            return cam.state != cam.previous_state

    def all_health(self) -> dict[str, dict]:
        """Return a snapshot of all camera health as a JSON-serialisable dict."""
        with self._lock:
            return {name: cam.to_dict() for name, cam in self._cameras.items()}

    def health_for(self, cam_name: str) -> dict:
        """Return health dict for a single camera (empty dict if unknown)."""
        with self._lock:
            cam = self._cameras.get(cam_name)
            return cam.to_dict() if cam else {}
