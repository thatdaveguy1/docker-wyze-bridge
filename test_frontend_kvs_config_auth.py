#!/usr/bin/env python3

import pathlib
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "app"))


class FakeApi:
    def __init__(self):
        self.auth = True
        self.total_cams = 1

    def get_kvs_proxy_config(self, cam_name):
        return {"signaling_url": f"wss://example.test/{cam_name}"}


class FakeStreams:
    def get(self, cam_name):
        return {"name": cam_name}


class FakeBridge:
    def __init__(self):
        self.api = FakeApi()
        self.streams = FakeStreams()

    def start(self):
        return None


fake_wyze_bridge = types.ModuleType("wyze_bridge")
fake_wyze_bridge.WyzeBridge = FakeBridge
sys.modules.setdefault("wyze_bridge", fake_wyze_bridge)

import frontend


class TestFrontendKVSConfigAuth(unittest.TestCase):
    def create_client(self):
        app = frontend.create_app()
        app.testing = True
        return app.test_client()

    def test_kvs_config_allows_loopback_without_auth(self):
        client = self.create_client()

        response = client.get(
            "/kvs-config/dog-run",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(), {"signaling_url": "wss://example.test/dog-run"}
        )


if __name__ == "__main__":
    unittest.main()
