import contextlib
import json
import time
from typing import Callable, Optional

from wyzebridge.wyze_api import WyzeApi
from wyzebridge.stream import Stream
from wyzebridge.config import IMG_PATH, IMG_TYPE, MOTION, MQTT_DISCOVERY, SNAPSHOT_TYPE
from wyzebridge.logging import logger
from wyzebridge.mqtt import bridge_status, cam_control, publish_topic, update_preview
from wyzebridge.mtx_event import RtspEvent
from wyzebridge.wyze_events import WyzeEvents
from wyzebridge.snapshot import SnapshotManager


class StreamManager:
    __slots__ = (
        "api",
        "stop_flag",
        "streams",
        "_snapshots",
    )

    def __init__(self, api: WyzeApi):
        self.api: WyzeApi = api
        self.stop_flag: bool = False
        self.streams: dict[str, Stream] = {}
        self._snapshots: SnapshotManager = SnapshotManager(
            streams=self.streams,
            api=self.api,
            stop_flag=lambda: self.stop_flag,
            enabled_streams=self.enabled_streams,
            active_streams=self.active_streams,
        )

    # --- Backward-compatible snapshot attribute access ---

    @property
    def rtsp_snapshots(self) -> dict:
        return self._snapshots.rtsp_snapshots

    @property
    def native_preloads(self) -> set:
        return self._snapshots.native_preloads

    @property
    def last_snap(self) -> float:
        return self._snapshots.last_snap

    @property
    def monitor_snapshots_thread(self) -> Optional[object]:
        return self._snapshots.monitor_snapshots_thread

    # --- Stream lifecycle ---

    @property
    def total(self):
        return len(self.streams)

    @property
    def active(self):
        return len([s for s in self.streams.values() if s.enabled])

    def add(self, stream: Stream) -> str:
        uri = stream.uri
        self.streams[uri] = stream
        return uri

    def get(self, uri: str) -> Optional[Stream]:
        return self.streams.get(uri)

    def get_info(self, uri: str) -> dict:
        return stream.get_info() if (stream := self.get(uri)) else {}

    def get_all_cam_info(self) -> dict:
        return {uri: s.get_info() for uri, s in list(self.streams.items())}

    def stop_all(self) -> None:
        logger.info(f"[STREAM] Stopping {self.total} stream{'s'[: self.total ^ 1]}")
        self.stop_flag = True

        for stream in self.streams.values():
            stream.stop()

        self._snapshots.stop_monitoring()

    def monitor_streams(self, mtx_health: Callable) -> None:
        self.stop_flag = False

        if MQTT_DISCOVERY:
            self._snapshots.monitor_snapshots()

        mqtt = cam_control(self.streams, self.send_cmd)
        logger.info(f"🎬 {self.total} stream{'s'[: self.total ^ 1]} enabled")
        event = RtspEvent(self.streams)
        events = WyzeEvents(self.streams) if MOTION and self.streams else None

        while not self.stop_flag:
            event.read(timeout=1)
            self._snapshots.snap_all(self.active_streams())
            for stream in list(self.streams.values()):
                _ = stream.motion

            if events:
                events.check_motion()

            if int(time.time()) % 15 == 0:
                mtx_health()
                bridge_status(mqtt)

        if mqtt:
            logger.info("[STREAM] Stopping mqtt loop")
            mqtt.loop_stop()
            mqtt = None

        logger.info("[STREAM] Stream monitoring stopped")

    def active_streams(self) -> list[str]:
        """
        Health check on all streams and return a list of enabled
        streams that are NOT battery powered.

        Returns:
        - list(str): uri-friendly name of streams that are enabled.
        """
        if self.stop_flag:
            return []
        return [cam for cam, s in list(self.streams.items()) if s.health_check() > 0]

    def enabled_streams(self) -> list[str]:
        if self.stop_flag:
            return []
        return [cam for cam, s in list(self.streams.items()) if getattr(s, "enabled", False)]

    def get_sse_status(self) -> dict:
        return {
            uri: {"status": cam.status(), "motion": cam.motion}
            for uri, cam in list(self.streams.items())
        }

    def send_cmd(
        self, cam_name: str, cmd: str, payload: str | list | dict = ""
    ) -> dict:
        """
        Send a command directly to the camera and wait for a response.

        Parameters:
        - cam_name (str): uri-friendly name of the camera.
        - cmd (str): The camera/tutk command to send.
        - payload (str): value for the tutk command.

        Returns:
        - dictionary: Results that can be converted to JSON.
        """
        resp = {"status": "error", "command": cmd, "payload": payload}

        if cam_name == "all" and cmd == "update_snapshot":
            self.snap_all(force=True)
            return resp | {"status": "success"}

        stream = self.get(cam_name)
        if cmd == "update_snapshot" and not stream:
            if not self.api.get_camera(cam_name):
                return resp | {"response": "Camera not found"}

            snapshot = self.refresh_preview(cam_name)["ok"]
            publish_topic(f"{cam_name}/{cmd}", int(time.time()) if snapshot else 0)
            return dict(resp, status="success", value=snapshot, response=snapshot)

        if not stream:
            return resp | {"response": "Camera not found"}

        if cam_resp := stream.send_cmd(cmd, payload):
            status = cam_resp.get("value") if cam_resp.get("status") == "success" else 0

            if isinstance(status, dict):
                status = json.dumps(status)

            if "update_snapshot" in cam_resp:
                demand_opened = not stream.connected
                snap = self.get_snapshot(cam_name)["ok"]
                if demand_opened:
                    stream.stop()

                publish_topic(f"{cam_name}/{cmd}", int(time.time()) if snap else 0)
                return dict(resp, status="success", value=snap, response=snap)

            publish_topic(f"{cam_name}/{cmd}", status)

        return cam_resp if "status" in cam_resp else resp | cam_resp

    # --- Snapshot public API ---
    # These delegate to SnapshotManager. They are the public snapshot interface
    # used by frontend.py (wb.streams.get_snapshot, wb.streams.refresh_preview)
    # and by tests (manager.get_snapshot, manager.get_rtsp_snap, etc.).
    # Internal callers in send_cmd use self.X so test patches on StreamManager
    # still intercept. Do not remove without updating all external callers.

    def snap_all(self, cams: Optional[list[str]] = None, force: bool = False):
        return self._snapshots.snap_all(cams, force)

    def get_snapshot(self, cam_name: str) -> dict:
        return self._snapshots.get_snapshot(cam_name)

    def refresh_preview(self, cam_name: str) -> dict:
        return self._snapshots.refresh_preview(cam_name)

    def rtsp_snap_popen(self, cam_name: str, interval: bool = False):
        return self._snapshots.rtsp_snap_popen(cam_name, interval)

    def get_rtsp_snap(self, cam_name: str) -> bool:
        return self._snapshots.get_rtsp_snap(cam_name)

    def monitor_snapshots(self) -> None:
        return self._snapshots.monitor_snapshots()

    def stop_subprocess(self, cam: str):
        return self._snapshots.stop_subprocess(cam)

    def remove_from_rtsp_snapshots(self, cam: str):
        return self._snapshots.remove_from_rtsp_snapshots(cam)
