#!/usr/bin/env python3

import pathlib
import sys
import types
import unittest
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

fake_wyzecam_tutk = sys.modules.setdefault(
    "wyzecam.tutk", types.ModuleType("wyzecam.tutk")
)
fake_wyzecam_tutk_tutk = sys.modules.setdefault(
    "wyzecam.tutk.tutk", types.ModuleType("wyzecam.tutk.tutk")
)
fake_wyzecam_tutk_protocol = sys.modules.setdefault(
    "wyzecam.tutk.tutk_protocol", types.ModuleType("wyzecam.tutk.tutk_protocol")
)


class FakeTutkProtocolMessage:
    def __init__(self, *args, **kwargs):
        pass


class FakeTutkWyzeProtocolError(Exception):
    pass


class FakeTutkError(Exception):
    pass


fake_wyzecam_tutk.tutk = fake_wyzecam_tutk_tutk
fake_wyzecam_tutk.tutk_protocol = fake_wyzecam_tutk_protocol
if not hasattr(fake_wyzecam_tutk_tutk, "TutkError"):
    fake_wyzecam_tutk_tutk.TutkError = FakeTutkError
for _name, _value in {
    "FRAME_SIZE_2K": 0,
    "FRAME_SIZE_1080P": 1,
    "FRAME_SIZE_360P": 2,
    "FRAME_SIZE_DOORBELL_HD": 3,
    "FRAME_SIZE_DOORBELL_SD": 4,
}.items():
    if not hasattr(fake_wyzecam_tutk_tutk, _name):
        setattr(fake_wyzecam_tutk_tutk, _name, _value)


for _name in (
    "K10058TakePhoto",
    "K10148StartBoa",
    "K11010GetCruisePoints",
    "K11018SetPTZPosition",
):
    if not hasattr(fake_wyzecam_tutk_protocol, _name):
        setattr(fake_wyzecam_tutk_protocol, _name, FakeTutkProtocolMessage)
if not hasattr(fake_wyzecam_tutk_protocol, "TutkWyzeProtocolError"):
    fake_wyzecam_tutk_protocol.TutkWyzeProtocolError = FakeTutkWyzeProtocolError

from wyzebridge import web_ui


class TestWebUIPreviewStreams(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_format_stream_uses_api_preview_metadata(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", "http://homeassistant.local:58889"),
                patch.object(web_ui, "RTSP_URL", "rtsp://homeassistant.local:58554"),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", "rtmp://homeassistant.local:51935"),
                patch.object(web_ui, "BRIDGE_IP", "192.168.1.10"),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
            ):
                data = web_ui.format_stream("garage")

        self.assertEqual(data["preview_url"], "img/garage.jpg")
        self.assertEqual(data["preview_kind"], "api")
        self.assertEqual(data["preview_refresh_mode"], "thumb")
        self.assertEqual(data["img_url"], "img/garage.jpg")

    def test_format_stream_marks_disabled_streams_with_reasons(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", ""),
                patch.object(web_ui, "RTSP_URL", "rtsp://homeassistant.local:58554"),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", ""),
                patch.object(web_ui, "BRIDGE_IP", ""),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
            ):
                data = web_ui.format_streams(
                    {
                        "garage": {
                            "enabled": True,
                            "connected": True,
                            "rtsp_fw_enabled": False,
                            "boa_url": None,
                            "webrtc": False,
                        }
                    }
                )["garage"]

        streams = {item["id"]: item for item in data["streams"]}
        self.assertFalse(streams["webrtc"]["available"])
        self.assertEqual(streams["webrtc"]["reason"], "not supported")
        self.assertTrue(streams["rtmp"]["available"])
        self.assertEqual(streams["rtmp"]["reason"], "")
        self.assertTrue(streams["api_thumbnail"]["available"])
        self.assertFalse(streams["rtsp_snapshot"]["available"])
        self.assertEqual(streams["rtsp_snapshot"]["reason"], "disabled in api mode")

    def test_active_camera_keeps_rtsp_and_rtmp_available_without_explicit_config(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", ""),
                patch.object(web_ui, "RTSP_URL", ""),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", ""),
                patch.object(web_ui, "BRIDGE_IP", ""),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
            ):
                data = web_ui.format_streams(
                    {
                        "garage": {
                            "enabled": True,
                            "connected": True,
                            "rtsp_fw_enabled": True,
                            "boa_url": None,
                            "webrtc": False,
                        }
                    }
                )["garage"]

        streams = {item["id"]: item for item in data["streams"]}
        self.assertTrue(streams["rtsp"]["available"])
        self.assertTrue(streams["rtmp"]["available"])
        self.assertTrue(streams["fw_rtsp"]["available"])

    def test_rtsp_disables_for_disabled_camera_even_with_local_fallback(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", ""),
                patch.object(web_ui, "RTSP_URL", ""),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", ""),
                patch.object(web_ui, "BRIDGE_IP", ""),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
            ):
                data = web_ui.format_streams(
                    {
                        "garage": {
                            "enabled": False,
                            "connected": True,
                            "rtsp_fw_enabled": True,
                            "boa_url": None,
                            "webrtc": False,
                        }
                    }
                )["garage"]

        streams = {item["id"]: item for item in data["streams"]}
        self.assertFalse(streams["rtsp"]["available"])
        self.assertEqual(streams["rtsp"]["reason"], "disabled")

    def test_rtsp_stays_available_for_on_demand_camera_without_reader(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", ""),
                patch.object(web_ui, "RTSP_URL", ""),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", ""),
                patch.object(web_ui, "BRIDGE_IP", ""),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
            ):
                data = web_ui.format_streams(
                    {
                        "garage": {
                            "enabled": True,
                            "connected": False,
                            "rtsp_fw_enabled": True,
                            "boa_url": None,
                            "webrtc": False,
                        }
                    }
                )["garage"]

        streams = {item["id"]: item for item in data["streams"]}
        self.assertTrue(streams["rtsp"]["available"])
        self.assertEqual(streams["rtsp"]["reason"], "")

    def test_on_demand_rtsp_and_rtmp_are_available_without_disabled_tag(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", ""),
                patch.object(web_ui, "RTSP_URL", ""),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", ""),
                patch.object(web_ui, "BRIDGE_IP", ""),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
            ):
                data = web_ui.format_streams(
                    {
                        "dog-run": {
                            "enabled": True,
                            "connected": False,
                            "rtsp_fw_enabled": False,
                            "boa_url": None,
                            "webrtc": False,
                        }
                    }
                )["dog-run"]

        streams = {item["id"]: item for item in data["streams"]}
        self.assertTrue(streams["rtsp"]["available"])
        self.assertEqual(streams["rtsp"]["reason"], "")
        self.assertTrue(streams["rtmp"]["available"])
        self.assertEqual(streams["rtmp"]["reason"], "")

    def test_preview_refresh_route_uses_api_mode(self):
        self.assertEqual(web_ui.preview_refresh_route("api"), "thumb")
        self.assertEqual(web_ui.preview_refresh_route("rtsp"), "snapshot")

    def test_stream_metadata_never_embeds_basic_auth_in_urls(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", "http://homeassistant.local:58889"),
                patch.object(web_ui, "RTSP_URL", "rtsp://homeassistant.local:58554"),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", "rtmp://homeassistant.local:51935"),
                patch.object(web_ui, "BRIDGE_IP", "192.168.1.10"),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
                patch.object(web_ui.WbAuth, "api", "super-secret-api"),
            ):
                data = web_ui.format_streams(
                    {
                        "garage": {
                            "enabled": True,
                            "connected": True,
                            "rtsp_fw_enabled": True,
                            "boa_url": None,
                            "webrtc": True,
                        }
                    }
                )["garage"]

        for stream in data["streams"]:
            for field in ("url", "lan_url", "copy_text", "lan_copy_text"):
                value = stream.get(field)
                if value:
                    self.assertNotIn("super-secret-api", value)
                    self.assertNotIn("@", value)
        self.assertTrue(any(stream["auth_required"] for stream in data["streams"]))

    def test_camera_level_url_overrides_replace_default_bridge_urls(self):
        with self.app.test_request_context("/"):
            with (
                patch.object(web_ui, "SNAPSHOT_TYPE", "api", create=True),
                patch.object(web_ui, "IMG_TYPE", "jpg"),
                patch.object(web_ui, "IMG_PATH", "/tmp/"),
                patch.object(web_ui, "WEBRTC_URL", "http://homeassistant.local:58889"),
                patch.object(web_ui, "RTSP_URL", "rtsp://homeassistant.local:58554"),
                patch.object(web_ui, "HLS_URL", "http://homeassistant.local:58888"),
                patch.object(web_ui, "RTMP_URL", "rtmp://homeassistant.local:51935"),
                patch.object(web_ui, "BRIDGE_IP", "192.168.1.10"),
                patch.object(web_ui.os.path, "getmtime", return_value=1234),
            ):
                data = web_ui.format_streams(
                    {
                        "north-yard": {
                            "enabled": True,
                            "connected": True,
                            "rtsp_fw_enabled": False,
                            "boa_url": None,
                            "webrtc": False,
                            "native_selected": False,
                            "rtsp_url": "rtsp://127.0.0.1:19554/north-yard-sd",
                            "hls_url": None,
                            "rtmp_url": None,
                            "webrtc_url": None,
                        }
                    }
                )["north-yard"]

        streams = {item["id"]: item for item in data["streams"]}
        self.assertEqual(data["rtsp_url"], "rtsp://localhost:19554/north-yard-sd")
        self.assertEqual(streams["rtsp"]["url"], "rtsp://localhost:19554/north-yard-sd")
        self.assertFalse(streams["hls"]["available"])


if __name__ == "__main__":
    unittest.main()
