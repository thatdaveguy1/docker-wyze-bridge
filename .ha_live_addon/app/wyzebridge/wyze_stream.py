import contextlib
import json
import multiprocessing as mp
import os
import zoneinfo
from collections import namedtuple
from ctypes import c_int
from datetime import datetime
from enum import IntEnum
from queue import Empty, Full
from threading import Thread
from time import sleep, time
from typing import Optional

from wyzecam.iotc import WyzeIOTC, WyzeIOTCSession
from wyzecam.tutk import tutk
from wyzecam.tutk.tutk import TutkError
from wyzecam.api_models import WyzeAccount, WyzeCamera
from wyzebridge.wyze_stream_options import WyzeStreamOptions
from wyzebridge.stream import Stream
from wyzebridge.bridge_utils import env_bool, env_cam
from wyzebridge.config import CONNECT_TIMEOUT, COOLDOWN, DISABLE_CONTROL, MQTT_TOPIC
from wyzebridge.go2rtc import native_stream_info
from wyzebridge.logging import logger, isDebugEnabled
from wyzebridge.mqtt import publish_discovery, publish_messages, update_mqtt_state
from wyzebridge.webhooks import send_webhook
from wyzebridge.wyze_api import WyzeApi
from wyzebridge.wyze_commands import GET_CMDS, PARAMS, SET_CMDS
from wyzebridge.tutk_session import (
    start_tutk_stream,
    is_timedout,
)

NET_MODE = {0: "P2P", 1: "RELAY", 2: "LAN"}

StreamTuple = namedtuple("stream", ["user", "camera", "options"])
QueueTuple = namedtuple("queue", ["cam_resp", "cam_cmd"])
KVS_ONLY_CMDS = {
    "state",
    "power",
    "notifications",
    "update_snapshot",
    "motion",
    "motion_ts",
}

FRAME_SIZE_LABELS = {
    tutk.FRAME_SIZE_2K: "2560x1440",
    tutk.FRAME_SIZE_1080P: "1920x1080",
    tutk.FRAME_SIZE_360P: "640x360",
    tutk.FRAME_SIZE_DOORBELL_HD: "1296x1728",
    tutk.FRAME_SIZE_DOORBELL_SD: "480x640",
}


def frame_size_to_resolution(frame_size: int | None) -> str | None:
    if frame_size is None:
        return None
    return FRAME_SIZE_LABELS.get(frame_size, str(frame_size))

HL_CAM4_MAIN_PROBE_MODES = {"kvs", "tutk_dtls", "tutk_parallel"}


def hl_cam4_main_probe_mode() -> str:
    mode = os.getenv("HL_CAM4_MAIN_PROBE_MODE", "kvs").strip().lower()
    return mode if mode in HL_CAM4_MAIN_PROBE_MODES else "kvs"


def connect_watchdog_timeout() -> int:
    retries = max(int(os.getenv("CONNECT_RETRIES", 3)), 1)
    retry_delay = max(float(os.getenv("CONNECT_RETRY_DELAY", 2.0)), 0.0)
    return int(CONNECT_TIMEOUT * retries + retry_delay * max(retries - 1, 0) + 6)


class StreamStatus(IntEnum):
    OFFLINE = -90
    INITIALIZING = -2
    STOPPING = -1
    DISABLED = 0
    STOPPED = 1
    CONNECTING = 2
    CONNECTED = 3


class WyzeStream(Stream):
    __slots__ = (
        "api",
        "cam_cmd",
        "cam_resp",
        "camera",
        "motion_ts",
        "options",
        "rtsp_fw_enabled",
        "start_time",
        "tutk_stream_process",
        "uri",
        "user",
        "_motion",
        "_state",
    )

    def __init__(
        self,
        user: WyzeAccount,
        api: WyzeApi,
        camera: WyzeCamera,
        options: WyzeStreamOptions,
    ) -> None:
        self.api: WyzeApi = api
        self.cam_cmd: mp.Queue
        self.cam_resp: mp.Queue
        self.camera: WyzeCamera = camera
        self.motion_ts: float = 0
        self.options: WyzeStreamOptions = options
        self.rtsp_fw_enabled: bool = False
        self.start_time: float = 0
        self.tutk_stream_process: Optional[mp.Process] = None
        self.uri: str = camera.name_uri + ("-sub" if options.substream else "")
        self.user: WyzeAccount = user
        self._motion: bool = False
        self._state: c_int = mp.Value("i", StreamStatus.STOPPED, lock=False)

        self.setup()

    def setup(self):
        if self.camera.ip is None or self.camera.ip == "":
            logger.warning(
                f"⚠︎ [{self.camera.product_model}] {self.camera.nickname} has no IP"
            )
            self.state = StreamStatus.OFFLINE
            return

        if self.camera.is_gwell or self.camera.product_model == "LD_CFP":
            logger.info(
                f"⚠︎ [{self.camera.product_model}] {self.camera.nickname} may not be supported"
            )
            self.state = StreamStatus.DISABLED

        if (
            self.options.substream
            and not self.camera.bridge_can_substream
            and self.camera.product_model != "HL_BC"
        ):
            logger.error(f"❗ {self.camera.nickname} may not support multiple streams!")
            self.state = StreamStatus.DISABLED
        elif self.uses_tutk_source:
            logger.info(
                f"[TUTK] Using mixed-protocol substream path for {self.camera.nickname}"
            )
        elif self.camera.product_model == "HL_CAM4" and not self.options.substream:
            logger.info(
                f"[HL_CAM4] {self.camera.nickname} main probe mode={hl_cam4_main_probe_mode()} source={'tutk' if self.uses_tutk_source else 'kvs'}"
            )

        hq_size = 4 if self.camera.is_floodlight else 3 if self.camera.is_2k else 0

        self.options.update_quality(hq_size)
        publish_discovery(self.uri, self.camera)

    @property
    def state(self) -> int:
        return self._state.value

    @state.setter
    def state(self, value) -> None:
        value = value.value if isinstance(value, StreamStatus) else value
        if self._state.value != value:
            self._state.value = value
            update_mqtt_state(self.uri, self.status())

    @property
    def motion(self) -> bool:
        state = time() - self.motion_ts < 20
        if self._motion and not state:
            self._motion = state
            publish_messages([(f"{MQTT_TOPIC}/{self.uri}/motion", 2, 0, True)])
        return state

    @motion.setter
    def motion(self, value: float):
        self._motion = True
        self.motion_ts = value
        publish_messages(
            [
                (f"{MQTT_TOPIC}/{self.uri}/motion", 1, 0, True),
                (f"{MQTT_TOPIC}/{self.uri}/motion_ts", value, 0, True),
            ]
        )

    @property
    def connected(self) -> bool:
        return self.state == StreamStatus.CONNECTED

    @property
    def enabled(self) -> bool:
        return self.state != StreamStatus.DISABLED

    @property
    def uses_kvs_source(self) -> bool:
        return not self.uses_tutk_source

    @property
    def uses_tutk_source(self) -> bool:
        if self.options.substream:
            return self.camera.product_model == "HL_CAM3P" or (
                self.camera.product_model == "HL_CAM4" and self.camera.is_kvs
            )

        if not (self.camera.product_model == "HL_CAM4" and self.camera.is_kvs):
            return False

        return hl_cam4_main_probe_mode() in {"tutk_dtls", "tutk_parallel"}

    def init(self) -> bool:
        self.state = StreamStatus.INITIALIZING
        logger.info(
            f"🪄 MediaMTX Initializing WyzeCam {self.camera.model_name} - {self.camera.nickname} on {self.camera.ip}"
        )
        self.state = StreamStatus.STOPPED
        return True

    def start(self) -> bool:
        if self.health_check(False) != StreamStatus.STOPPED:
            return False
        if self.uses_tutk_source and self.camera.ip is None:
            logger.warning(
                f"Skipping {self.camera.nickname}: no IP available for TUTK substream."
            )
            self.state = StreamStatus.DISABLED
            return False
        self.start_time = time()
        self.state = StreamStatus.CONNECTING
        if self.uses_kvs_source:
            if not self.api.setup_mtx_proxy(self.uri):
                self.state = StreamStatus.STOPPED
                self.start_time = 0
                return False
            self.state = StreamStatus.CONNECTED
            return True

        logger.info(
            f"🎉 Connecting to WyzeCam {self.camera.model_name} - {self.camera.nickname} on {self.camera.ip} via TUTK"
        )
        self.cam_resp = mp.Queue(1)
        self.cam_cmd = mp.Queue(1)
        self.tutk_stream_process = mp.Process(
            target=start_tutk_stream,
            args=(
                self.uri,
                StreamTuple(self.user, self.camera, self.options),
                QueueTuple(self.cam_resp, self.cam_cmd),
                self._state,
            ),
            name=self.uri,
        )
        self.tutk_stream_process.start()
        if not self.tutk_stream_process.is_alive():
            self.state = StreamStatus.STOPPED
            self.start_time = 0
            return False
        return True

    def stop(self) -> bool:
        self._clear_mp_queue()
        self.start_time = 0
        if self.uses_tutk_source:
            self.state = StreamStatus.STOPPING
            if self.tutk_stream_process and self.tutk_stream_process.is_alive():
                with contextlib.suppress(ValueError, AttributeError, RuntimeError):
                    if self.tutk_stream_process.is_alive():
                        self.tutk_stream_process.terminate()
                        self.tutk_stream_process.join(5)
            self.tutk_stream_process = None
        self.state = StreamStatus.STOPPED
        return True

    def enable(self) -> bool:
        if self.state == StreamStatus.DISABLED:
            logger.info(f"🔓 Enabling {self.uri}")
            self.state = StreamStatus.STOPPED

        return self.state > StreamStatus.DISABLED

    def disable(self) -> bool:
        if self.state != StreamStatus.DISABLED:
            logger.info(f"🔒 Disabling {self.uri}")
            if self.state != StreamStatus.STOPPED:
                self.stop()

            self.state = StreamStatus.DISABLED
        return True

    def health_check(self, should_start: bool = True) -> int:
        if self.state == StreamStatus.OFFLINE:
            if env_bool("IGNORE_OFFLINE"):
                logger.info(f"🪦 {self.uri} is offline. WILL ignore.")
                self.disable()
                return self.state
            logger.info(f"👻 {self.camera.nickname} is offline.")
        if self.uses_tutk_source and self.state in {-13, -19, -68}:
            self.refresh_camera()
        elif self.state < StreamStatus.DISABLED:
            state = self.state
            self.stop()
            if state < StreamStatus.STOPPING:
                self.start_time = time() + COOLDOWN
                logger.info(f"🌬️ {self.camera.nickname} will cooldown for {COOLDOWN}s.")
        elif (
            self.state == StreamStatus.STOPPED
            and self.options.reconnect
            and should_start
        ):
            self.start()
        elif self.state == StreamStatus.CONNECTING and is_timedout(
            self.start_time, connect_watchdog_timeout()
        ):
            logger.warning(f"⏰ Timed out connecting to {self.camera.nickname}.")
            self.stop()

        if (
            should_start
            and self.camera.is_battery
            and self.state == StreamStatus.STOPPED
        ):
            return StreamStatus.DISABLED

        return self.state if self.start_time < time() else StreamStatus.DISABLED

    def refresh_camera(self):
        self.stop()
        if not (cam := self.api.get_camera(self.camera.name_uri)):
            return False
        self.camera = cam
        return True

    def status(self) -> str:
        try:
            return StreamStatus(self._state.value).name.lower()
        except ValueError:
            return "error"

    def get_info(self, item: Optional[str] = None) -> dict:
        if item == "boa_info":
            return self.boa_info()
        data = {
            "name_uri": self.uri,
            "camera_uri": self.camera.name_uri,
            "source": "kvs" if self.uses_kvs_source else "tutk",
            "status": self.state,
            "connected": self.connected,
            "enabled": self.enabled,
            "motion": self.motion,
            "motion_ts": self.motion_ts,
            "on_demand": not self.options.reconnect,
            "audio": self.options.audio,
            "record": self.options.record,
            "substream": self.options.substream,
            "model_name": self.camera.model_name,
            "is_2k": self.camera.is_2k,
            "rtsp_fw": self.camera.rtsp_fw,
            "rtsp_fw_enabled": self.rtsp_fw_enabled,
            "is_battery": self.camera.is_battery,
            "webrtc": self.camera.webrtc_support,
            "start_time": self.start_time,
            "req_frame_size": self.options.frame_size,
            "req_bitrate": self.options.bitrate,
            "actual_resolution": frame_size_to_resolution(self.options.frame_size),
            "bridge_can_substream": self.camera.bridge_can_substream,
            "camera_can_substream": self.camera.can_substream,
        }
        if self.connected and not self.camera.camera_info:
            self.update_cam_info()
        if self.camera.camera_info and "boa_info" in self.camera.camera_info:
            data["boa_url"] = f"http://{self.camera.ip}/cgi-bin/hello.cgi?name=/"
        native_info = native_stream_info(self.camera, self.options.substream)
        sd_only_bridge_feed = (
            env_bool("SD_ONLY", style="bool")
            and str(self.options.quality or "").lower().startswith("sd")
        )
        if sd_only_bridge_feed:
            native_info = native_info | {
                "native_selected": False,
                "native_preload": False,
                "snapshot_source": "rtsp",
                "talkback_supported": False,
                "talkback_source": None,
            }

        return data | native_info | self.camera.model_dump(exclude={"p2p_id", "enr", "parent_enr"})

    def update_cam_info(self) -> None:
        if not self.connected:
            return
        if not hasattr(self, "cam_cmd") or not hasattr(self, "cam_resp"):
            return

        if (resp := self.send_cmd("caminfo")) and ("response" not in resp):
            self.camera.set_camera_info(resp)

    def boa_info(self) -> dict:
        self.update_cam_info()
        if not self.camera.camera_info:
            return {}
        return self.camera.camera_info.get("boa_info", {})

    def state_control(self, payload) -> dict:
        if payload in {"start", "stop", "disable", "enable"}:
            logger.info(f"[CONTROL] SET {self.uri} state={payload}")
            response = getattr(self, payload)()
            return {
                "status": "success" if response else "error",
                "response": payload if response else self.status(),
                "value": payload,
            }
        logger.info(f"[CONTROL] GET {self.uri} state")
        return {"status": "success", "response": self.status()}

    def power_control(self, payload: str) -> dict:
        if payload not in {"on", "off", "restart"}:
            resp = self.api.get_device_info(self.camera, "P3")
            resp["value"] = "on" if resp["value"] == "1" else "off"
            return resp
        run_cmd = payload if payload == "restart" else f"power_{payload}"

        return dict(
            self.api.run_action(self.camera, run_cmd),
            value="on" if payload == "restart" else payload,
        )

    def notification_control(self, payload: str) -> dict:
        if payload not in {"on", "off", "1", "2", "true", "false"}:
            return self.api.get_device_info(self.camera, "P1")

        pvalue = "1" if payload in {"on", "1", "true"} else "2"
        resp = self.api.set_property(self.camera, "P1", pvalue)
        value = None if resp.get("status") == "error" else pvalue

        return dict(resp, value=value)

    def tz_control(self, payload: str) -> dict:
        try:
            zone = zoneinfo.ZoneInfo(payload)
            offset = datetime.now(zone).utcoffset()
            assert offset is not None
        except (zoneinfo.ZoneInfoNotFoundError, AssertionError):
            return {"response": "invalid time zone"}

        return dict(
            self.api.set_device_info(self.camera, {"device_timezone_city": zone.key}),
            value=int(offset.total_seconds() / 3600),
        )

    def send_cmd(self, cmd: str, payload: str | list | dict = "") -> dict:
        if cmd in {"state", "start", "stop", "disable", "enable"}:
            return self.state_control(payload or cmd)

        if cmd == "device_info":
            return self.api.get_device_info(self.camera)
        if cmd == "device_setting":
            return self.api.get_device_info(self.camera, cmd="device_setting")

        if cmd == "battery":
            return self.api.get_device_info(self.camera, "P8")

        if cmd == "power":
            return self.power_control(str(payload).lower())

        if cmd == "notifications":
            return self.notification_control(str(payload).lower())

        if cmd in {"motion", "motion_ts"}:
            return {
                "status": "success",
                "response": {"motion": self.motion, "motion_ts": self.motion_ts},
                "value": self.motion if cmd == "motion" else self.motion_ts,
            }

        if self.state < StreamStatus.STOPPED:
            return {"response": self.status()}

        if DISABLE_CONTROL:
            return {"response": "control disabled"}

        if self.uses_kvs_source and cmd not in KVS_ONLY_CMDS:
            return {"response": f"{cmd} unsupported in KVS-only mode"}

        if cmd == "time_zone" and payload and isinstance(payload, str):
            return self.tz_control(payload)

        if cmd == "bitrate" and isinstance(payload, (str, int)) and payload.isdigit():
            self.options.bitrate = int(payload)

        if cmd == "update_snapshot":
            return {"update_snapshot": True}

        if cmd == "cruise_point" and payload == "-":
            return {"status": "success", "value": "-"}

        if cmd not in GET_CMDS | SET_CMDS | PARAMS and cmd not in {"caminfo"}:
            return {"response": "invalid command"}

        if on_demand := not self.connected:
            logger.info(f"🖇 [CONTROL] Connecting to {self.uri}")
            self.start()
            while not self.connected and time() - self.start_time < 10:
                sleep(0.1)
        self._clear_mp_queue()
        try:
            self.cam_cmd.put_nowait((cmd, payload))
            cam_resp = self.cam_resp.get(timeout=10)
        except Full:
            return {"response": "camera busy"}
        except Empty:
            return {"response": "timed out"}
        finally:
            if on_demand:
                logger.info(f"⛓️‍💥 [CONTROL] Disconnecting from {self.uri}")
                self.stop()

        return cam_resp.pop(cmd, None) or {"response": "could not get result"}

    def check_rtsp_fw(self, force: bool = False) -> Optional[str]:
        """Check and add rtsp."""
        if not self.camera.rtsp_fw:
            return
        logger.info(f"🛃 Checking {self.camera.nickname} for firmware RTSP")
        try:
            with (
                WyzeIOTC() as iotc,
                WyzeIOTCSession(
                    iotc.tutk_platform_lib, self.user, self.camera
                ) as session,
            ):
                if (
                    session.session_check().mode != 2
                ):  # 0: P2P mode, 1: Relay mode, 2: LAN mode
                    logger.warning(
                        f"⚠️ [{self.camera.nickname}] Camera is not on same LAN"
                    )
                    return
                return session.check_native_rtsp(start_rtsp=force)
        except TutkError:
            return

    def _clear_mp_queue(self):
        with contextlib.suppress(Empty, AttributeError):
            self.cam_cmd.get_nowait()
        with contextlib.suppress(Empty, AttributeError):
            self.cam_resp.get_nowait()
