#!/usr/bin/env python3

import importlib
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))


class FakeApi:
    def __init__(self, bridge_can_substream=True):
        self.auth = True
        self.total_cams = 1
        self._bridge_can_substream = bridge_can_substream

    def get_camera(self, cam_name, existing=False):
        if cam_name != "north-yard":
            return None
        return SimpleNamespace(name_uri="north-yard", bridge_can_substream=self._bridge_can_substream)


class FakeStreams:
    def get_info(self, cam_name):
        if cam_name == "north-yard":
            return {"name_uri": "north-yard"}
        return {}


class FakeBridge:
    stream_mode = "main"
    bridge_can_substream = True

    def __init__(self):
        self.api = FakeApi(self.bridge_can_substream)
        self.streams = FakeStreams()

    def start(self):
        return None

    def camera_stream_mode(self, camera):
        return self.stream_mode

    def camera_substream_enabled(self, camera):
        return self.stream_mode in {"both", "sub"} and camera.bridge_can_substream

    def refresh_cams(self):
        return None


fake_wyze_bridge = types.ModuleType("wyze_bridge")
fake_wyze_bridge.WyzeBridge = FakeBridge
sys.modules["wyze_bridge"] = fake_wyze_bridge

import frontend


class TestFrontendStreamMode(unittest.TestCase):
    def create_client(self):
        sys.modules["wyze_bridge"] = fake_wyze_bridge
        importlib.reload(frontend)
        app = frontend.create_app()
        app.testing = True
        return app.test_client()

    def test_stream_mode_reports_current_mode(self):
        FakeBridge.stream_mode = "both"
        FakeBridge.bridge_can_substream = True
        client = self.create_client()

        response = client.get("/api/north-yard/stream-mode")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["mode"], "both")
        self.assertTrue(response.get_json()["supports_substream"])

    def test_stream_mode_rejects_sub_when_camera_does_not_support_it(self):
        FakeBridge.stream_mode = "main"
        FakeBridge.bridge_can_substream = False
        client = self.create_client()

        response = client.post("/api/north-yard/stream-mode", json={"mode": "sub"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("not available", response.get_json()["response"])
