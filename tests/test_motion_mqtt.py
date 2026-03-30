#!/usr/bin/env python3

import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

requests_stub = types.ModuleType("requests")
requests_exceptions = types.ModuleType("requests.exceptions")
requests_stub.RequestException = Exception
requests_stub.ConnectionError = Exception
requests_stub.HTTPError = Exception
requests_stub.PreparedRequest = object
requests_stub.Response = object
requests_stub.Session = Mock
requests_stub.get = Mock()
requests_stub.post = Mock()
requests_stub.put = Mock()
requests_exceptions.ConnectionError = Exception
requests_exceptions.HTTPError = Exception
requests_exceptions.RequestException = Exception
requests_stub.exceptions = requests_exceptions
sys.modules.setdefault("requests", requests_stub)
sys.modules.setdefault("requests.exceptions", requests_exceptions)

sys.modules.setdefault(
    "dotenv",
    SimpleNamespace(load_dotenv=lambda *args, **kwargs: False),
)
sys.modules.setdefault("xxtea", types.ModuleType("xxtea"))

fake_paho = types.ModuleType("paho")
fake_paho_mqtt = types.ModuleType("paho.mqtt")
fake_paho_mqtt_client = types.ModuleType("paho.mqtt.client")
fake_paho_mqtt_publish = types.ModuleType("paho.mqtt.publish")
fake_paho.mqtt = fake_paho_mqtt
fake_paho_mqtt.client = fake_paho_mqtt_client
fake_paho_mqtt.publish = fake_paho_mqtt_publish
sys.modules.setdefault("paho", fake_paho)
sys.modules.setdefault("paho.mqtt", fake_paho_mqtt)
sys.modules.setdefault("paho.mqtt.client", fake_paho_mqtt_client)
sys.modules.setdefault("paho.mqtt.publish", fake_paho_mqtt_publish)

fake_wyzecam_iotc = types.ModuleType("wyzecam.iotc")
fake_wyzecam_iotc.WyzeIOTC = object
fake_wyzecam_iotc.WyzeIOTCSession = object
sys.modules.setdefault("wyzecam.iotc", fake_wyzecam_iotc)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from wyzebridge.stream_manager import StreamManager
from wyzebridge.wyze_control import motion_alarm
from wyzebridge.wyze_events import WyzeEvents
from wyzebridge.wyze_stream import StreamStatus, WyzeStream


class DummyApi:
    def __init__(self, events=None):
        self.events = events or []

    def get_events(self, *_args, **_kwargs):
        return 0, self.events


def make_camera(name_uri="south-yard", mac="001122334455"):
    return SimpleNamespace(
        name_uri=name_uri,
        nickname="South Yard",
        product_model="HL_BC",
        model_name="Wyze Bulb Cam",
        ip="192.168.1.10",
        is_gwell=False,
        is_battery=False,
        is_floodlight=False,
        is_2k=False,
        is_kvs=True,
        can_substream=False,
        bridge_can_substream=False,
        rtsp_fw=False,
        camera_info=None,
        webrtc_support=True,
        mac=mac,
        p2p_id="p2p-id",
        dtls=1,
        parent_dtls=0,
        enr="enr",
        thumbnail=None,
        model_dump=lambda exclude=None: {"product_model": "HL_BC"},
    )


def make_options(substream=False):
    return SimpleNamespace(
        quality="sd30",
        audio=True,
        record=False,
        reconnect=True,
        substream=substream,
        frame_size=1,
        bitrate=30,
        update_quality=lambda hq: None,
    )


class TestMotionMQTT(unittest.TestCase):
    def test_boa_motion_alarm_publishes_camera_relative_topic_and_numeric_payload(self):
        cam = {
            "uri": "south-yard",
            "ip": "192.168.1.10",
            "img_dir": ".runtime/img/",
            "last_alarm": (None, None),
            "last_photo": ("new_alarm.jpg", "2026-03-29T12:00:00"),
            "cooldown": None,
        }

        with (
            patch("wyzebridge.wyze_control.pull_last_image"),
            patch("wyzebridge.wyze_control.publish_topic") as mock_publish_topic,
        ):
            motion_alarm(cam)

        mock_publish_topic.assert_called_once_with("south-yard/motion", 1, True)

    def test_boa_motion_alarm_publishes_off_payload_when_alarm_does_not_change(self):
        cam = {
            "uri": "south-yard",
            "ip": "192.168.1.10",
            "img_dir": ".runtime/img/",
            "last_alarm": ("same_alarm.jpg", "2026-03-29T12:00:00"),
            "last_photo": ("same_alarm.jpg", "2026-03-29T12:00:00"),
            "cooldown": None,
        }

        with (
            patch("wyzebridge.wyze_control.pull_last_image"),
            patch("wyzebridge.wyze_control.publish_topic") as mock_publish_topic,
        ):
            motion_alarm(cam)

        mock_publish_topic.assert_called_once_with("south-yard/motion", 2, True)

    def test_event_motion_uses_receipt_time_for_latch_window(self):
        camera = make_camera()
        stream = SimpleNamespace(
            api=DummyApi(),
            camera=camera,
            options=SimpleNamespace(substream=False),
            motion=None,
            uri="south-yard",
            start=Mock(),
        )
        events = WyzeEvents({"south-yard": stream})
        events.last_ts = 100

        with patch("wyzebridge.wyze_events.time.time", return_value=500):
            events.set_motion(camera.mac, [])

        self.assertEqual(stream.motion, 500)

    def test_motion_expiry_is_checked_during_monitor_loop_without_status_polling(self):
        class ExpiringStream:
            def __init__(self):
                self.enabled = True
                self.motion_reads = 0

            @property
            def motion(self):
                self.motion_reads += 1
                return False

        expiring_stream = ExpiringStream()
        manager = StreamManager(DummyApi())
        manager.streams["south-yard"] = expiring_stream

        class StopAfterFirstRead:
            def read(self, timeout=1):
                manager.stop_flag = True

        with (
            patch("wyzebridge.stream_manager.cam_control", return_value=None),
            patch("wyzebridge.stream_manager.RtspEvent", return_value=StopAfterFirstRead()),
            patch("wyzebridge.stream_manager.WyzeEvents", return_value=None),
            patch("wyzebridge.stream_manager.StreamManager.snap_all"),
            patch("wyzebridge.stream_manager.StreamManager.active_streams", return_value=[]),
        ):
            manager.monitor_streams(Mock())

        self.assertGreaterEqual(expiring_stream.motion_reads, 1)

    def test_motion_property_publishes_off_after_expiry(self):
        user = SimpleNamespace()
        camera = make_camera(name_uri="deck", mac="aa1122334455")

        with patch("wyzebridge.wyze_stream.publish_discovery"):
            stream = WyzeStream(user, DummyApi(), camera, make_options())

        stream.state = StreamStatus.CONNECTED
        stream._motion = True
        stream.motion_ts = 10

        with (
            patch("wyzebridge.wyze_stream.publish_messages") as mock_publish_messages,
            patch("wyzebridge.wyze_stream.time", return_value=31),
        ):
            self.assertFalse(stream.motion)

        mock_publish_messages.assert_called_once_with(
            [("wyzebridge/deck/motion", 2, 0, True)]
        )


if __name__ == "__main__":
    unittest.main()
