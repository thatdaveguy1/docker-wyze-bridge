#!/usr/bin/env python3

import pathlib
import sys
import types
import unittest
import importlib
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))


class FakeApi:
    def __init__(self):
        self.auth = True
        self.total_cams = 1


class FakeStreams:
    total = 1
    active = 1

    def get_all_cam_info(self):
        return {
            "deck": {
                "enabled": True,
                "connected": False,
                "camera_info": None,
                "nickname": "Deck",
                "name_uri": "deck",
                "on_demand": False,
                "substream": False,
                "audio": True,
                "record": False,
                "webrtc": True,
                "rtsp_fw_enabled": False,
                "boa_url": None,
                "img_url": "img/deck.jpg",
                "snapshot_url": "snapshot/deck.jpg",
                "thumbnail_url": "thumb/deck.jpg",
                "hls_url": "http://homeassistant.local:58888/deck/",
                "webrtc_url": "http://homeassistant.local:58889/deck/",
                "rtsp_url": "rtsp://homeassistant.local:58554/deck",
                "rtmp_url": "rtmp://homeassistant.local:51935/deck",
                "img_time": 0,
                "is_battery": False,
            }
        }

    def get_sse_status(self):
        return {"deck": {"motion": False, "status": "stopped"}}


class FakeBridge:
    def __init__(self):
        self.api = FakeApi()
        self.streams = FakeStreams()

    def start(self):
        return None


fake_wyze_bridge = types.ModuleType("wyze_bridge")
fake_wyze_bridge.WyzeBridge = FakeBridge
sys.modules["wyze_bridge"] = fake_wyze_bridge

import frontend


class TestFrontendIngressBaseHref(unittest.TestCase):
    def test_ingress_request_renders_base_href_with_trailing_slash(self):
        sys.modules["wyze_bridge"] = fake_wyze_bridge
        importlib.reload(frontend)
        app = frontend.create_app()
        app.testing = True
        client = app.test_client()

        with patch.object(
            frontend.web_ui.auth, "login_required", side_effect=lambda fn: fn
        ):
            response = client.get(
                "/",
                headers={"X-Ingress-Path": "/api/hassio_ingress/test-token"},
            )

        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<base href="/api/hassio_ingress/test-token/" />', html)


if __name__ == "__main__":
    unittest.main()
