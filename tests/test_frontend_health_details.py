#!/usr/bin/env python3

import importlib
import pathlib
import sys
import types
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))


class FakeApi:
    def __init__(self):
        self.auth = True
        self.total_cams = 1


class FakeStreams:
    pass


class FakeBridge:
    def __init__(self):
        self.api = FakeApi()
        self.streams = FakeStreams()

    def start(self):
        return None

    def health(self):
        return {"mtx_alive": True, "wyze_authed": True, "active_streams": 5}

    def health_details(self, stream_name=None):
        return {
            "mtx_alive": True,
            "wyze_authed": True,
            "active_streams": 5,
            "stream": stream_name,
            "whep_proxy": {"reachable": True},
            "mediamtx": {"reachable": True},
        }


fake_wyze_bridge = types.ModuleType("wyze_bridge")
fake_wyze_bridge.WyzeBridge = FakeBridge
sys.modules["wyze_bridge"] = fake_wyze_bridge

import frontend


class TestFrontendHealthDetails(unittest.TestCase):
    def create_client(self):
        sys.modules["wyze_bridge"] = fake_wyze_bridge
        importlib.reload(frontend)
        app = frontend.create_app()
        app.testing = True
        return app.test_client()

    def test_health_details_returns_stream_specific_diagnostics(self):
        client = self.create_client()

        response = client.get("/health/details?stream=dog-run")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stream"], "dog-run")
        self.assertTrue(response.get_json()["whep_proxy"]["reachable"])
        self.assertTrue(response.get_json()["mediamtx"]["reachable"])

    def test_health_details_can_include_network_snapshot(self):
        client = self.create_client()
        network = {
            "hostname": "wyze-bridge-dev",
            "outbound_ipv4": {"source_ip": "192.168.1.244"},
            "resolv_conf": {"nameservers": ["192.168.1.254"]},
            "dns": {"targets": []},
        }

        with patch.object(frontend, "network_snapshot", return_value=network):
            response = client.get("/health/details?stream=dog-run&network=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["network"], network)

    def test_network_snapshot_includes_dns_targets(self):
        with patch.object(
            frontend,
            "_candidate_dns_targets",
            return_value=["auth-prod.api.wyze.com", "homeassistant.local"],
        ):
            with patch.object(
                frontend,
                "_resolve_dns_target",
                side_effect=lambda host: {
                    "host": host,
                    "port": 443,
                    "reachable": True,
                    "elapsed_ms": 1.25,
                    "addresses": [{"family": "AF_INET", "address": "1.2.3.4"}],
                },
            ):
                with patch.object(
                    frontend, "_tutk_library_hosts", return_value=("m1.iotcplatform.com",)
                ):
                    snapshot = frontend.network_snapshot()

        self.assertEqual(
            snapshot["dns"]["targets"],
            [
                {
                    "host": "auth-prod.api.wyze.com",
                    "port": 443,
                    "reachable": True,
                    "elapsed_ms": 1.25,
                    "addresses": [{"family": "AF_INET", "address": "1.2.3.4"}],
                },
                {
                    "host": "homeassistant.local",
                    "port": 443,
                    "reachable": True,
                    "elapsed_ms": 1.25,
                    "addresses": [{"family": "AF_INET", "address": "1.2.3.4"}],
                },
            ],
        )
        self.assertEqual(snapshot["dns"]["tutk_library_hosts"], ["m1.iotcplatform.com"])

    def test_tutk_library_scan_filters_symbol_like_junk(self):
        frontend._tutk_library_hosts.cache_clear()
        fake_binary = b"iotcapis.o iotcplatform.com tutkssl.o kalayservice.com"

        with patch.object(frontend, "TUTK_HOST_SCAN_PATHS", ("/tmp/fake.so",)):
            with patch("builtins.open", mock_open(read_data=fake_binary)):
                hosts = frontend._tutk_library_hosts()

        frontend._tutk_library_hosts.cache_clear()
        self.assertEqual(hosts, ("iotcplatform.com", "kalayservice.com"))

    def test_create_app_logs_network_snapshot_when_enabled(self):
        sys.modules["wyze_bridge"] = fake_wyze_bridge
        importlib.reload(frontend)

        with patch.dict(frontend.os.environ, {"NETWORK_TRACE": "1"}, clear=False):
            with patch.object(
                frontend,
                "network_snapshot",
                return_value={"hostname": "wyze-bridge-dev"},
            ):
                with patch.object(frontend, "print") as mock_print:
                    app = frontend.create_app()
                    app.testing = True
                    app.test_client()

        mock_print.assert_any_call(
            '[NETWORK_TRACE] {"hostname": "wyze-bridge-dev"}', flush=True
        )


if __name__ == "__main__":
    unittest.main()
