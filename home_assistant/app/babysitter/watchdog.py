"""Core watchdog module for the Reolink camera babysitter.

Monitors 3 Reolink cameras (doorbell, south_driveway, north_driveway)
integrated through Scrypted → Frigate → HomeKit. Detects camera wedges
and reboots them via the ONVIF SystemReboot command.

Convergence rules (see AGENTS.md plan):

* **Video-down** (triggers reboot):
  1. TCP/RTSP reachability TRUE (wifi-up guard)
  2. camera_fps == 0 AND process_fps == 0 for > VIDEO_DOWN_THRESHOLD seconds
  3. skipped_fps == 0 (if > 0, decoder backlog — disqualify)
  4. Scrypted snapshot structurally fails (SNAPSHOT_SAMPLES consecutive bad)
  5. Frigate ffprobe on the camera's RTSP input returns no valid stream info
  6. → ONVIF reboot

* **Snapshot-down only** (no reboot):
  1. TCP reachable
  2. Frigate FPS healthy (camera_fps > 0, process_fps > 0, skipped_fps == 0)
  3. Scrypted snapshot structurally fails
  4. → Log + MQTT notify only

* **Stale-hash warning** (no reboot, ever):
  1. Frigate FPS healthy
  2. Valid JPEGs but same SHA-256 for > SNAPSHOT_STALE_WINDOW
  3. → Log + MQTT warning only

* **Camera unreachable** (no action):
  1. TCP reachability FALSE
  2. → Log + MQTT notify only
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from babysitter.config import BabysitterConfig, CameraEntry
from babysitter.helpers import (
    FrigateClient,
    OnvifRebootClient,
    ScryptedClient,
    is_valid_jpeg,
    redact_url,
    sha256_hex,
    tcp_reachable,
)
from babysitter.state import (
    BabysitterState,
    RebootEvent,
    can_reboot,
    cooldown_remaining,
    daily_reboot_count,
    record_reboot,
)

logger = logging.getLogger(__name__)

# State labels (kept in sync with CameraState.current_state).
STATE_ONLINE = "online"
STATE_VIDEO_DOWN = "video_down"
STATE_SNAPSHOT_DOWN = "snapshot_down"
STATE_WIFI_DOWN = "wifi_down"
STATE_RECOVERING = "recovering"


# ---------------------------------------------------------------------------
# CameraStatus dataclass
# ---------------------------------------------------------------------------


@dataclass
class CameraStatus:
    """Snapshot of a single camera's health at poll time."""

    name: str
    state: str
    camera_fps: float = 0.0
    process_fps: float = 0.0
    skipped_fps: float = 0.0
    snapshot_valid: bool = False
    snapshot_hash: str = ""
    tcp_reachable: bool = False
    cgi_reachable: bool = False
    stale_hash_warning: bool = False
    last_reboot: float = 0.0
    reboots_today: int = 0
    cooldown_remaining: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# MQTT publisher (optional)
# ---------------------------------------------------------------------------


class MqttPublisher:
    """Thin paho-mqtt wrapper that fails gracefully.

    Only instantiated when ``config.mqtt_broker`` is set. Connection is
    lazy (opened on first publish). All publish failures are logged and
    swallowed so the watchdog never crashes on MQTT issues.
    """

    def __init__(self, config: BabysitterConfig) -> None:
        self.config = config
        self._client: Any = None
        self._connected = False

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import paho.mqtt.client as mqtt  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - paho is in requirements
            logger.warning("paho-mqtt not installed; MQTT publishing disabled")
            return None
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"babysitter-{int(time.time())}",
        )
        if self.config.mqtt_username:
            client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)
        self._client = client
        return client

    def _connect(self) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        if self._connected:
            return True
        try:
            client.connect(self.config.mqtt_broker, self.config.mqtt_port, keepalive=30)
            client.loop_start()
            self._connected = True
            return True
        except Exception as exc:  # noqa: BLE001 - never crash on MQTT
            logger.warning("MQTT connect to %s:%s failed: %s",
                           self.config.mqtt_broker, self.config.mqtt_port, exc)
            self._connected = False
            return False

    def publish(self, topic: str, payload: str) -> None:
        if not self.config.mqtt_broker:
            return
        if not self._connect():
            return
        try:
            self._client.publish(topic, payload, qos=0, retain=False)
        except Exception as exc:  # noqa: BLE001 - never crash on MQTT
            logger.warning("MQTT publish to %s failed: %s", topic, exc)
            self._connected = False

    def publish_status(self, camera_name: str, status_dict: dict[str, Any]) -> None:
        import json
        topic = f"babysitter/{camera_name}/status"
        self.publish(topic, json.dumps(status_dict, default=str))

    def publish_reboot_event(self, camera_name: str, event_dict: dict[str, Any]) -> None:
        import json
        topic = f"babysitter/{camera_name}/reboot"
        self.publish(topic, json.dumps(event_dict, default=str))

    def close(self) -> None:
        if self._client is not None and self._connected:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._connected = False


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


class Watchdog:
    """Camera watchdog that polls Frigate/Scrypted and reboots wedged cameras."""

    def __init__(self, config: BabysitterConfig, state: BabysitterState) -> None:
        self.config = config
        self.state = state
        self.scrypted = ScryptedClient(
            host=config.scrypted_host,
            username=config.scrypted_username,
            password=config.scrypted_password,
        )
        self.frigate = FrigateClient(host=config.frigate_host)
        # Per-camera ONVIF reboot clients, keyed by friendly_name.
        self.onvif: dict[str, OnvifRebootClient] = {
            cam.friendly_name: OnvifRebootClient(
                ip=cam.ip,
                username=config.reolink_username,
                password=config.reolink_password,
                port=config.onvif_port,
            )
            for cam in config.cameras
        }
        self.mqtt = MqttPublisher(config) if config.mqtt_broker else None
        # Cache of last poll results, keyed by friendly_name.
        self._last_status: dict[str, CameraStatus] = {}
        # Background thread controls.
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _camera_entry(self, camera_name: str) -> CameraEntry | None:
        for cam in self.config.cameras:
            if cam.friendly_name == camera_name:
                return cam
        return None

    def _is_dry_run(self, camera_name: str) -> bool:
        if self.config.per_camera_dry_run.get(camera_name, False):
            return True
        return self.config.dry_run

    def _go2rtc_producer_alive(self, alias: str) -> bool:
        """Check if a go2rtc stream has an active producer (for non-RTSP cameras)."""
        try:
            import requests
            host = os.environ.get("GO2RTC_API_BASE", "http://127.0.0.1:11984")
            resp = requests.get(f"{host}/api/streams?src={alias}", timeout=5)
            data = resp.json()
            producers = data.get("producers") or []
            return len(producers) > 0
        except Exception:
            return False

    def _ffprobe_returns_stream(self, rtsp_url: str) -> bool:
        """Return True if Frigate ffprobe reports a valid stream."""
        try:
            result = self.frigate.ffprobe(rtsp_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ffprobe for %s failed: %s", redact_url(rtsp_url), exc)
            return False
        # A valid ffprobe response has a non-empty "streams" list.
        streams = result.get("streams", []) if isinstance(result, dict) else []
        return bool(streams)

    # ------------------------------------------------------------------
    # poll_once
    # ------------------------------------------------------------------

    def poll_once(self) -> dict[str, CameraStatus]:
        """Poll all configured cameras and return their statuses."""
        statuses: dict[str, CameraStatus] = {}
        for entry in self.config.cameras:
            try:
                status = self._poll_camera(entry)
            except Exception as exc:  # noqa: BLE001 - never crash the poll loop
                logger.exception("Polling %s failed: %s", entry.friendly_name, exc)
                cam_state = self.state.get_camera(entry.friendly_name)
                status = CameraStatus(
                    name=entry.friendly_name,
                    state=cam_state.current_state or STATE_RECOVERING,
                    last_reboot=cam_state.last_reboot,
                    reboots_today=daily_reboot_count(cam_state),
                    cooldown_remaining=cooldown_remaining(cam_state, self.config.cooldown),
                )
            statuses[entry.friendly_name] = status
            self._last_status[entry.friendly_name] = status
        return statuses

    def _poll_camera(self, entry: CameraEntry) -> CameraStatus:
        cam_state = self.state.get_camera(entry.friendly_name)
        now = time.time()

        # 1. TCP reachability (wifi-up guard).
        # Cameras without RTSP port 554 (e.g. Wyze DTLS) use go2rtc stream
        # status as the reachability check instead.
        has_frigate = bool(entry.frigate_name)
        if has_frigate:
            tcp_ok = tcp_reachable(entry.ip, 554)
        else:
            # Non-RTSP camera: check go2rtc producer status instead of TCP.
            tcp_ok = self._go2rtc_producer_alive(entry.friendly_name)
        # Check ONVIF reboot path reachability.
        onvif = self.onvif.get(entry.friendly_name)
        onvif_ok = bool(onvif and tcp_reachable(entry.ip, onvif.port))
        cgi_reachable = onvif_ok

        if not tcp_ok:
            cam_state.current_state = STATE_WIFI_DOWN
            logger.warning(
                "%s: TCP 554 unreachable — wifi_down, no action", entry.friendly_name,
            )
            if self.mqtt:
                self.mqtt.publish_status(entry.friendly_name, {
                    "state": STATE_WIFI_DOWN, "tcp_reachable": False,
                })
            return CameraStatus(
                name=entry.friendly_name,
                state=STATE_WIFI_DOWN,
                tcp_reachable=False,
                cgi_reachable=cgi_reachable,
                last_reboot=cam_state.last_reboot,
                reboots_today=daily_reboot_count(cam_state),
                cooldown_remaining=cooldown_remaining(cam_state, self.config.cooldown),
            )

        # 2. Frigate FPS (skip for cameras not in Frigate — e.g. Wyze).
        if has_frigate:
            try:
                fps = self.frigate.camera_fps(entry.frigate_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s: Frigate stats failed: %s", entry.friendly_name, exc)
                fps = {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0}
        else:
            fps = {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0}

        camera_fps = float(fps.get("camera_fps", 0.0))
        process_fps = float(fps.get("process_fps", 0.0))
        skipped_fps = float(fps.get("skipped_fps", 0.0))

        # Track FPS-zero duration.
        if camera_fps == 0 and process_fps == 0:
            if cam_state.fps_zero_since == 0:
                cam_state.fps_zero_since = now
        else:
            cam_state.fps_zero_since = 0.0

        fps_zero_duration = (now - cam_state.fps_zero_since) if cam_state.fps_zero_since else 0.0

        # 3. Scrypted snapshot.
        snapshot_valid = False
        snapshot_hash = ""
        try:
            data = self.scrypted.snapshot(entry.scrypted_id)
            snapshot_valid = is_valid_jpeg(data)
            if snapshot_valid:
                snapshot_hash = sha256_hex(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s: Scrypted snapshot fetch failed: %s", entry.friendly_name, exc)

        # Track consecutive bad snapshots (structural failures only).
        if snapshot_valid:
            cam_state.consecutive_bad_snapshots = 0
        else:
            cam_state.consecutive_bad_snapshots += 1

        # Track stale hash (valid JPEGs only).
        stale_hash_warning = False
        if snapshot_valid:
            if cam_state.last_snapshot_hash and snapshot_hash == cam_state.last_snapshot_hash:
                if cam_state.stale_hash_since == 0:
                    cam_state.stale_hash_since = cam_state.last_snapshot_time or now
                stale_duration = now - cam_state.stale_hash_since
                if stale_duration > self.config.snapshot_stale_window:
                    stale_hash_warning = True
            else:
                cam_state.stale_hash_since = 0.0
            cam_state.last_snapshot_hash = snapshot_hash
            cam_state.last_snapshot_time = now

        # 4. Determine state via convergence rules.
        fps_healthy = camera_fps > 0 and process_fps > 0 and skipped_fps == 0
        fps_all_zero = camera_fps == 0 and process_fps == 0
        bad_snapshot_confirmed = (
            cam_state.consecutive_bad_snapshots >= self.config.snapshot_samples
        )

        new_state = STATE_ONLINE
        if has_frigate and fps_all_zero and skipped_fps == 0 and bad_snapshot_confirmed:
            # Suspect video-down; confirm with ffprobe + duration threshold.
            if fps_zero_duration > self.config.video_down_threshold:
                rtsp_url = ""
                try:
                    rtsp_inputs = self.frigate.rtsp_inputs()
                    rtsp_url = rtsp_inputs.get(entry.frigate_name, "")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s: Frigate rtsp_inputs failed: %s", entry.friendly_name, exc)
                stream_ok = False
                if rtsp_url:
                    stream_ok = self._ffprobe_returns_stream(rtsp_url)
                    logger.info(
                        "%s: ffprobe on %s → stream_ok=%s",
                        entry.friendly_name, redact_url(rtsp_url), stream_ok,
                    )
                if not stream_ok:
                    new_state = STATE_VIDEO_DOWN
                else:
                    # ffprobe says stream is fine even though FPS is zero — recovering.
                    new_state = STATE_RECOVERING
            else:
                new_state = STATE_RECOVERING
        elif fps_healthy and bad_snapshot_confirmed:
            new_state = STATE_SNAPSHOT_DOWN
        elif not has_frigate and bad_snapshot_confirmed:
            # Non-Frigate camera (e.g. Wyze) with bad snapshots — snapshot_down.
            # The go2rtc health monitor handles stream recovery for these cameras.
            new_state = STATE_SNAPSHOT_DOWN
        elif stale_hash_warning:
            new_state = STATE_ONLINE  # stale hash is a warning, not a down state
        elif fps_all_zero and skipped_fps > 0:
            # Decoder backlog — not a camera wedge.
            new_state = STATE_RECOVERING

        prev_state = cam_state.current_state
        cam_state.current_state = new_state

        if new_state == STATE_SNAPSHOT_DOWN:
            logger.warning(
                "%s: snapshot_down (FPS healthy=%s, bad_snapshots=%d) — no reboot",
                entry.friendly_name, fps_healthy, cam_state.consecutive_bad_snapshots,
            )
        if stale_hash_warning:
            logger.warning(
                "%s: stale hash %s for >%ds — warning only",
                entry.friendly_name, snapshot_hash[:12],
                self.config.snapshot_stale_window,
            )
        if prev_state != new_state and self.mqtt:
            self.mqtt.publish_status(entry.friendly_name, {
                "state": new_state, "previous": prev_state,
                "camera_fps": camera_fps, "process_fps": process_fps,
                "skipped_fps": skipped_fps,
            })

        return CameraStatus(
            name=entry.friendly_name,
            state=new_state,
            camera_fps=camera_fps,
            process_fps=process_fps,
            skipped_fps=skipped_fps,
            snapshot_valid=snapshot_valid,
            snapshot_hash=snapshot_hash,
            tcp_reachable=True,
            cgi_reachable=cgi_reachable,
            stale_hash_warning=stale_hash_warning,
            last_reboot=cam_state.last_reboot,
            reboots_today=daily_reboot_count(cam_state),
            cooldown_remaining=cooldown_remaining(cam_state, self.config.cooldown),
        )

    # ------------------------------------------------------------------
    # check_and_recover
    # ------------------------------------------------------------------

    def check_and_recover(self, camera_name: str) -> RebootEvent | None:
        """Check a single camera and reboot if video-down + all guards pass."""
        status = self._last_status.get(camera_name)
        if status is None:
            logger.warning("check_and_recover: no status for %s", camera_name)
            return None

        if status.state != STATE_VIDEO_DOWN:
            logger.debug("%s: state=%s, no recovery needed", camera_name, status.state)
            return None

        entry = self._camera_entry(camera_name)
        if entry is None:
            logger.warning("check_and_recover: no config entry for %s", camera_name)
            return None

        # wifi-up guard.
        if not status.tcp_reachable:
            logger.warning("%s: video_down but TCP unreachable — skipping reboot", camera_name)
            return None

        # ONVIF reachability guard.
        if not status.cgi_reachable:
            logger.error(
                "%s: video_down but ONVIF port unreachable — cannot reboot.",
                camera_name,
            )
            return None

        # Approval guard.
        if camera_name not in self.config.approved_cameras:
            logger.warning(
                "%s: video_down but not in approved_cameras — skipping (unapproved)",
                camera_name,
            )
            return None

        # Dry-run guard (global or per-camera).
        dry_run = self._is_dry_run(camera_name)

        # Cooldown + daily limit.
        cam_state = self.state.get_camera(camera_name)
        ok, reason = can_reboot(cam_state, self.config.cooldown, self.config.max_daily)
        if not ok:
            logger.info("%s: video_down but %s — skipping reboot", camera_name, reason)
            return None

        if dry_run:
            logger.info(
                "%s: DRY-RUN — would reboot via ONVIF %s (reason=video_down)",
                camera_name, entry.ip,
            )
            if self.mqtt:
                self.mqtt.publish_reboot_event(camera_name, {
                    "action": "skipped", "reason": "dry_run",
                    "camera": camera_name, "outcome": "skipped",
                })
            return None

        # Perform the reboot via ONVIF.
        onvif = self.onvif.get(camera_name)
        if not onvif:
            logger.error("%s: no ONVIF reboot client available", camera_name)
            return None

        action = "onvif"
        logger.info("%s: initiating ONVIF reboot (reason=video_down)", camera_name)
        start = time.time()
        try:
            success = onvif.reboot_with_retry()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s: ONVIF reboot failed: %s", camera_name, exc)
            record_reboot(
                self.state, camera_name, "onvif", "video_down", "failed",
                duration=time.time() - start,
            )
            if self.mqtt:
                self.mqtt.publish_reboot_event(camera_name, {
                    "action": "onvif", "reason": "video_down",
                    "outcome": "failed", "error": str(exc),
                })
            return self.state.history[0]

        if not success:
            logger.error("%s: reboot API returned failure", camera_name)
            record_reboot(
                self.state, camera_name, action, "video_down", "failed",
                duration=time.time() - start,
            )
            if self.mqtt:
                self.mqtt.publish_reboot_event(camera_name, {
                    "action": action, "reason": "video_down",
                    "outcome": "failed",
                })
            return self.state.history[0]

        # Record success.
        recovery_duration = self._wait_for_recovery(camera_name, entry)
        total_duration = time.time() - start
        record_reboot(
            self.state, camera_name, action, "video_down", "success",
            duration=total_duration,
        )
        logger.info(
            "%s: reboot succeeded (%s), recovered in %.1fs",
            camera_name, action, total_duration,
        )
        if self.mqtt:
            self.mqtt.publish_reboot_event(camera_name, {
                "action": action, "reason": "video_down",
                "outcome": "success", "duration": total_duration,
                "recovery_wait": recovery_duration,
            })
        return self.state.history[0]

    def _wait_for_recovery(self, camera_name: str, entry: CameraEntry) -> float:
        """Poll Frigate FPS until > 0 or timeout. Returns seconds waited."""
        deadline = time.time() + self.config.recovery_wait
        poll_interval = 5.0
        waited = 0.0
        while time.time() < deadline:
            try:
                fps = self.frigate.camera_fps(entry.frigate_name)
                if float(fps.get("camera_fps", 0)) > 0:
                    break
            except Exception as exc:  # noqa: BLE001
                logger.debug("%s: recovery poll failed: %s", camera_name, exc)
            time.sleep(poll_interval)
            waited += poll_interval
        return waited

    # ------------------------------------------------------------------
    # run_cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> dict[str, Any]:
        """Run one full poll + recover cycle. Returns a summary dict."""
        statuses = self.poll_once()
        actions: list[dict[str, Any]] = []

        # Process video-down cameras sequentially (never parallel).
        for name, status in statuses.items():
            if status.state != STATE_VIDEO_DOWN:
                continue
            event = self.check_and_recover(name)
            if event is not None:
                actions.append({
                    "camera": name,
                    "event": asdict(event),
                })

        # Save state after processing.
        try:
            from babysitter.state import save_state
            save_state(self.state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to save state: %s", exc)

        return {
            "statuses": {name: s.to_dict() for name, s in statuses.items()},
            "actions": actions,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def start_background(self, interval: int | None = None) -> None:
        """Run run_cycle() in a daemon thread on a fixed interval."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Background thread already running")
            return
        self._stop_event.clear()
        period = interval if interval is not None else self.config.interval

        def _loop() -> None:
            logger.info("Babysitter background loop started (interval=%ss)", period)
            while not self._stop_event.is_set():
                try:
                    self.run_cycle()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Background run_cycle failed: %s", exc)
                self._stop_event.wait(period)
            logger.info("Babysitter background loop stopped")

        self._thread = threading.Thread(target=_loop, daemon=True, name="babysitter-watchdog")
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        if self.mqtt is not None:
            self.mqtt.close()
