#!/usr/bin/env python3

import pathlib
import sys
import unittest
import importlib
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / ".ha_live_addon" / "app")
)

def make_camera(model: str = "HL_CAM4", nickname: str = "North Yard"):
    return SimpleNamespace(
        name_uri="north-yard",
        nickname=nickname,
        product_model=model,
        is_kvs=True,
        bridge_can_substream=True,
    )


class FakeStream:
    def __init__(self, user, api, camera, options):
        self.user = user
        self.api = api
        self.camera = camera
        self.options = options
        self.uri = camera.name_uri + ("-sub" if options.substream else "")
        self.uses_kvs_source = not options.substream


class FakeMtx:
    def __init__(self):
        self.paths = []

    def add_path(self, uri, on_demand, is_kvs):
        self.paths.append((uri, on_demand, is_kvs))

    def record(self, uri):
        raise AssertionError("record() should not be called in this test")


class FakeStreams:
    def __init__(self):
        self.added = []

    def add(self, stream):
        self.added.append(stream)


class TestBridgeKVSStartupBehavior(unittest.TestCase):
    def test_setup_streams_does_not_eagerly_start_kvs_proxy(self):
        sys.modules.pop("wyze_bridge", None)
        with patch("os.makedirs"):
            WyzeBridge = importlib.import_module("wyze_bridge").WyzeBridge

        bridge = WyzeBridge.__new__(WyzeBridge)
        bridge.api = SimpleNamespace(
            get_user=lambda: SimpleNamespace(email="test@example.com"),
            filtered_cams=lambda: [make_camera()],
            setup_mtx_proxy=lambda uri: (_ for _ in ()).throw(AssertionError("setup_mtx_proxy should not run during setup_streams")),
        )
        bridge.streams = FakeStreams()
        bridge.mtx = FakeMtx()
        bridge.add_substream = lambda user, api, cam, options: None

        with (
            patch("wyze_bridge.WyzeStream", FakeStream),
            patch("wyze_bridge.env_cam", side_effect=lambda key, name_uri, default=None: default),
            patch("wyze_bridge.is_livestream", return_value=False),
            patch("wyze_bridge.ON_DEMAND", True),
        ):
            bridge.setup_streams(SimpleNamespace(email="test@example.com"))

        self.assertEqual(len(bridge.streams.added), 1)
        self.assertEqual(bridge.streams.added[0].uri, "north-yard")
        self.assertEqual(bridge.mtx.paths, [("north-yard", True, True)])

    def test_setup_streams_honors_sub_only_mode(self):
        sys.modules.pop("wyze_bridge", None)
        with patch("os.makedirs"):
            wyze_bridge = importlib.import_module("wyze_bridge")
            WyzeBridge = wyze_bridge.WyzeBridge

        bridge = WyzeBridge.__new__(WyzeBridge)
        bridge.api = SimpleNamespace(
            get_user=lambda: SimpleNamespace(email="test@example.com"),
            filtered_cams=lambda: [make_camera()],
        )
        bridge.streams = FakeStreams()
        bridge.mtx = FakeMtx()

        with (
            patch("wyze_bridge.WyzeStream", FakeStream),
            patch("wyze_bridge.get_camera_setting", return_value="sub"),
            patch("wyze_bridge.env_cam", side_effect=lambda key, name_uri, default=None: default),
            patch("wyze_bridge.is_livestream", return_value=False),
            patch("wyze_bridge.ON_DEMAND", True),
        ):
            bridge.setup_streams(SimpleNamespace(email="test@example.com"))

        self.assertEqual([stream.uri for stream in bridge.streams.added], ["north-yard-sub"])
        self.assertEqual(bridge.mtx.paths, [("north-yard-sub", True, False)])

    def test_setup_streams_honors_both_mode(self):
        sys.modules.pop("wyze_bridge", None)
        with patch("os.makedirs"):
            wyze_bridge = importlib.import_module("wyze_bridge")
            WyzeBridge = wyze_bridge.WyzeBridge

        bridge = WyzeBridge.__new__(WyzeBridge)
        bridge.api = SimpleNamespace(
            get_user=lambda: SimpleNamespace(email="test@example.com"),
            filtered_cams=lambda: [make_camera()],
        )
        bridge.streams = FakeStreams()
        bridge.mtx = FakeMtx()

        with (
            patch("wyze_bridge.WyzeStream", FakeStream),
            patch("wyze_bridge.get_camera_setting", return_value="both"),
            patch("wyze_bridge.env_cam", side_effect=lambda key, name_uri, default=None: default),
            patch("wyze_bridge.is_livestream", return_value=False),
            patch("wyze_bridge.ON_DEMAND", True),
        ):
            bridge.setup_streams(SimpleNamespace(email="test@example.com"))

        self.assertEqual(
            [stream.uri for stream in bridge.streams.added],
            ["north-yard", "north-yard-sub"],
        )

    def test_camera_substream_enabled_rejects_sub_mode_on_unsupported_camera(self):
        sys.modules.pop("wyze_bridge", None)
        with patch("os.makedirs"):
            WyzeBridge = importlib.import_module("wyze_bridge").WyzeBridge

        bridge = WyzeBridge.__new__(WyzeBridge)
        camera = make_camera()
        camera.bridge_can_substream = False

        self.assertFalse(bridge.camera_substream_enabled(camera, "sub"))

    def test_add_substream_registers_tutk_source_for_kvs_camera(self):
        sys.modules.pop("wyze_bridge", None)
        with patch("os.makedirs"):
            wyze_bridge = importlib.import_module("wyze_bridge")
            WyzeBridge = wyze_bridge.WyzeBridge

        bridge = WyzeBridge.__new__(WyzeBridge)
        bridge.streams = FakeStreams()
        bridge.mtx = FakeMtx()

        with (
            patch("wyze_bridge.WyzeStream", FakeStream),
            patch("wyze_bridge.env_bool", side_effect=lambda key, default="": key == "SUBSTREAM"),
            patch("wyze_bridge.env_cam", side_effect=lambda key, name_uri, default=None: default),
        ):
            bridge.add_substream(
                SimpleNamespace(email="test@example.com"),
                SimpleNamespace(),
                make_camera(),
                wyze_bridge.WyzeStreamOptions(reconnect=False),
            )

        self.assertEqual(len(bridge.streams.added), 1)
        self.assertEqual(bridge.streams.added[0].uri, "north-yard-sub")
        self.assertEqual(bridge.mtx.paths, [("north-yard-sub", True, False)])


if __name__ == "__main__":
    unittest.main()
