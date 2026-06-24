#!/usr/bin/env python3

import importlib
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

# Stub native-only modules that can't be installed outside Docker
import types as _types

_requests_stub = _types.ModuleType("requests")
_requests_ex = _types.ModuleType("requests.exceptions")
_requests_stub.RequestException = Exception
_requests_stub.PreparedRequest = object
_requests_stub.Response = object
_requests_stub.Session = object
_requests_stub.get = lambda *a, **kw: None
_requests_stub.put = lambda *a, **kw: None
_requests_stub.post = lambda *a, **kw: None
_requests_ex.ConnectionError = Exception
_requests_ex.HTTPError = Exception
_requests_ex.RequestException = Exception
_requests_stub.exceptions = _requests_ex
_saved_requests = sys.modules.get("requests")
_saved_requests_ex = sys.modules.get("requests.exceptions")
sys.modules["requests"] = _requests_stub
sys.modules["requests.exceptions"] = _requests_ex

_paho = _types.ModuleType("paho")
_paho_mqtt = _types.ModuleType("paho.mqtt")
_paho_mqtt.client = _types.ModuleType("paho.mqtt.client")
_paho_mqtt.publish = _types.ModuleType("paho.mqtt.publish")
_paho.mqtt = _paho_mqtt
_paho_mqtt.client.Client = object
_paho_mqtt.client.CallbackAPIVersion = object
_paho_mqtt.client.MQTTv311 = 3
_paho_mqtt.client.MQTTv5 = 5
_paho_mqtt.publish.single = lambda *a, **kw: None

# Save original modules so we can restore them after the frontend import
_saved_modules = {}
for _n, _m in [
    ("paho", _paho),
    ("paho.mqtt", _paho_mqtt),
    ("paho.mqtt.client", _paho_mqtt.client),
    ("paho.mqtt.publish", _paho_mqtt.publish),
    ("xxtea", _types.ModuleType("xxtea")),
    ("wyzecam.iotc", _types.ModuleType("wyzecam.iotc")),
    ("wyzecam.tutk", _types.ModuleType("wyzecam.tutk")),
    ("wyzecam.tutk.tutk", _types.ModuleType("wyzecam.tutk.tutk")),
    ("wyzecam.tutk.tutk_ioctl_mux", _types.ModuleType("wyzecam.tutk.tutk_ioctl_mux")),
    ("wyzecam.tutk.tutk_protocol", _types.ModuleType("wyzecam.tutk.tutk_protocol")),
]:
    _saved_modules[_n] = sys.modules.get(_n)
    sys.modules.setdefault(_n, _m)

# Stub wyzebridge internals that transitively require native TUTK .so
_wb_wyze_stream = _types.ModuleType("wyzebridge.wyze_stream")
_wb_wyze_stream.WyzeStream = object
_wb_wyze_events = _types.ModuleType("wyzebridge.wyze_events")
_wb_wyze_events.WyzeEvents = object
sys.modules["wyzebridge.wyze_stream"] = _wb_wyze_stream
sys.modules["wyzebridge.wyze_events"] = _wb_wyze_events


class FakeApi:
    def __init__(self):
        self.auth = True
        self.total_cams = 1


class FakeStream:
    def get_info(self):
        return dict(FakeBridge.stream_info)


class FakeStreams:
    def get(self, cam_name):
        if cam_name != "north-yard":
            return None
        return FakeStream()


class FakeBridge:
    stream_info = {
        "talkback_supported": True,
        "talkback_reason": "API-first talkback is available through the native go2rtc alias",
        "talkback_alias": "north-yard",
        "native_alias": "north-yard",
    }

    def __init__(self):
        self.api = FakeApi()
        self.streams = FakeStreams()

    def start(self):
        return None


fake_wyze_bridge = types.ModuleType("wyze_bridge")
fake_wyze_bridge.WyzeBridge = FakeBridge
sys.modules["wyze_bridge"] = fake_wyze_bridge

import frontend

# The frontend import needed the lightweight wyzebridge stubs above, but other
# tests in the same pytest process need the real module later. Drop the stubs
# after import so future imports resolve the actual implementation.
sys.modules.pop("wyzebridge.wyze_stream", None)
sys.modules.pop("wyzebridge.wyze_events", None)
# Restore the original requests module so other tests get the real one
if _saved_requests is not None:
    sys.modules["requests"] = _saved_requests
else:
    sys.modules.pop("requests", None)
if _saved_requests_ex is not None:
    sys.modules["requests.exceptions"] = _saved_requests_ex
else:
    sys.modules.pop("requests.exceptions", None)
# Drop cached wyzebridge modules that use requests directly, so future tests
# re-import them with the real requests module
for _mod_name in (
    "wyzebridge.native_alias",
    "wyzebridge.wyze_api",
    "wyzebridge.wyze_api_helpers",
    "wyzebridge.wyze_control",
    "wyzebridge.webhooks",
    "wyzebridge.hass",
    "wyzebridge.snapshot",
):
    sys.modules.pop(_mod_name, None)
# Restore original wyzecam/paho/xxtea modules so other tests get the real ones
for _mod_name, _saved in _saved_modules.items():
    if _saved is not None:
        sys.modules[_mod_name] = _saved
    else:
        sys.modules.pop(_mod_name, None)


class TestFrontendTalkback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Re-install stubs needed for frontend import
        sys.modules.setdefault("paho", _paho)
        sys.modules.setdefault("paho.mqtt", _paho_mqtt)
        sys.modules.setdefault("paho.mqtt.client", _paho_mqtt.client)
        sys.modules.setdefault("paho.mqtt.publish", _paho_mqtt.publish)
        sys.modules.setdefault("xxtea", _types.ModuleType("xxtea"))
        sys.modules.setdefault("wyzecam.iotc", _types.ModuleType("wyzecam.iotc"))
        sys.modules.setdefault("wyzecam.tutk", _types.ModuleType("wyzecam.tutk"))
        sys.modules.setdefault("wyzecam.tutk.tutk", _types.ModuleType("wyzecam.tutk.tutk"))
        sys.modules.setdefault("wyzecam.tutk.tutk_ioctl_mux", _types.ModuleType("wyzecam.tutk.tutk_ioctl_mux"))
        sys.modules.setdefault("wyzecam.tutk.tutk_protocol", _types.ModuleType("wyzecam.tutk.tutk_protocol"))
        _paho_mqtt.client.Client = object
        _paho_mqtt.client.CallbackAPIVersion = object
        _paho_mqtt.client.MQTTv311 = 3
        _paho_mqtt.client.MQTTv5 = 5
        _paho_mqtt.publish.single = lambda *a, **kw: None

    @classmethod
    def tearDownClass(cls):
        # Clean up stubs so other tests get real modules
        for _mod_name, _saved in _saved_modules.items():
            if _saved is not None:
                sys.modules[_mod_name] = _saved
            else:
                sys.modules.pop(_mod_name, None)
        for _mod_name in (
            "wyzebridge.native_alias",
            "wyzebridge.wyze_api",
            "wyzebridge.wyze_api_helpers",
            "wyzebridge.wyze_control",
            "wyzebridge.webhooks",
            "wyzebridge.hass",
            "wyzebridge.snapshot",
        ):
            sys.modules.pop(_mod_name, None)

    def create_client(self):
        sys.modules["wyze_bridge"] = fake_wyze_bridge
        importlib.reload(frontend)
        app = frontend.create_app()
        app.testing = True
        # Disable HTTP Basic auth so tests don't need real credentials
        from wyzebridge.web_ui import auth as _wb_auth

        _wb_auth.verify_password(lambda u, p: True)
        import wyzebridge.web_ui as _web_ui

        _web_ui.WbAuth.enabled = False
        return app.test_client()

    def test_talkback_requires_json_payload(self):
        client = self.create_client()

        response = client.post("/api/north-yard/talkback", data="hello")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["response"], "Talkback requires a JSON object payload")

    def test_talkback_returns_conflict_when_camera_not_supported(self):
        client = self.create_client()
        FakeBridge.stream_info = {
            "talkback_supported": False,
            "talkback_reason": "talkback is limited to native-selected cameras in 4.2",
            "talkback_alias": "north-yard",
            "native_alias": "north-yard",
        }

        response = client.post("/api/north-yard/talkback", json={"text": "hello"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("native-selected cameras", response.get_json()["response"])

    def test_talkback_forwards_text_payload_to_go2rtc_helper(self):
        client = self.create_client()
        FakeBridge.stream_info = {
            "talkback_supported": True,
            "talkback_reason": "API-first talkback is available through the native go2rtc alias",
            "talkback_alias": "north-yard",
            "native_alias": "north-yard",
        }

        with patch.object(frontend, "send_native_talkback") as mock_send:
            mock_send.return_value = {"status": "success", "source": "go2rtc"}
            response = client.post("/api/north-yard/talkback", json={"text": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        mock_send.assert_called_once_with({"text": "hello"}, "north-yard")

    def test_talkback_converts_audio_b64_to_loopback_url(self):
        client = self.create_client()
        FakeBridge.stream_info = {
            "talkback_supported": True,
            "talkback_reason": "API-first talkback is available through the native go2rtc alias",
            "talkback_alias": "north-yard",
            "native_alias": "north-yard",
        }

        with (
            patch.object(frontend.tempfile, "_get_candidate_names", return_value=iter(["clip"])),
            patch.object(frontend, "send_native_talkback") as mock_send,
        ):
            mock_send.return_value = {"status": "success", "source": "go2rtc"}
            response = client.post(
                "/api/north-yard/talkback",
                json={"audio_b64": "aGVsbG8=", "file_ext": "wav"},
            )

        self.assertEqual(response.status_code, 200)
        forwarded_payload = mock_send.call_args.args[0]
        self.assertNotIn("audio_b64", forwarded_payload)
        self.assertEqual(
            forwarded_payload["audio_url"],
            "http://127.0.0.1:5000/api/talkback-file/clip.wav",
        )
        staged_path = pathlib.Path(tempfile.gettempdir()) / "wyze-talkback-http" / "clip.wav"
        try:
            self.assertTrue(staged_path.is_file())
            self.assertEqual(staged_path.read_text(encoding="ascii"), "aGVsbG8=")
        finally:
            if staged_path.exists():
                staged_path.unlink()


if __name__ == "__main__":
    unittest.main()
