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
requests_stub.PreparedRequest = Mock
requests_stub.Response = Mock
requests_stub.get = Mock()
requests_stub.put = Mock()
requests_stub.post = Mock()
requests_exceptions.ConnectionError = Exception
requests_exceptions.HTTPError = Exception
requests_exceptions.RequestException = Exception
requests_stub.exceptions = requests_exceptions
sys.modules["requests"] = requests_stub
sys.modules["requests.exceptions"] = requests_exceptions

# Stub native-only modules that can't be installed outside Docker
_paho = types.ModuleType("paho")
_paho_mqtt = types.ModuleType("paho.mqtt")
_paho_mqtt_client = types.ModuleType("paho.mqtt.client")
_paho_mqtt_publish = types.ModuleType("paho.mqtt.publish")
_paho.mqtt = _paho_mqtt
_paho_mqtt.client = _paho_mqtt_client
_paho_mqtt.publish = _paho_mqtt_publish
for _mod_name, _mod in [
    ("paho", _paho),
    ("paho.mqtt", _paho_mqtt),
    ("paho.mqtt.client", _paho_mqtt_client),
    ("paho.mqtt.publish", _paho_mqtt_publish),
]:
    sys.modules.setdefault(_mod_name, _mod)

for _mod in ("xxtea",):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))
# Stub wyzecam.iotc and tutk sub-hierarchy to avoid loading native .so files.
# Do NOT stub 'wyzecam' itself — the real package must be importable for api_models etc.
_wyzecam_iotc = types.ModuleType("wyzecam.iotc")
_wyzecam_iotc.WyzeIOTC = Mock
_wyzecam_iotc.WyzeIOTCSession = Mock
for _name, _mod in [
    ("wyzecam.iotc", _wyzecam_iotc),
    ("wyzecam.tutk", types.ModuleType("wyzecam.tutk")),
    ("wyzecam.tutk.tutk", types.ModuleType("wyzecam.tutk.tutk")),
    ("wyzecam.tutk.tutk_ioctl_mux", types.ModuleType("wyzecam.tutk.tutk_ioctl_mux")),
    ("wyzecam.tutk.tutk_protocol", types.ModuleType("wyzecam.tutk.tutk_protocol")),
]:
    sys.modules.setdefault(_name, _mod)

for module_name in list(sys.modules):
    if module_name == "wyzebridge" or module_name.startswith("wyzebridge."):
        del sys.modules[module_name]

# Re-apply wyzebridge internal stubs AFTER the clear so they aren't wiped out.
# stream_manager imports wyze_events → wyze_stream → wyzecam.tutk native .so,
# which cannot load outside Docker. Stub those two at the wyzebridge level.
_wyzebridge_wyze_stream = types.ModuleType("wyzebridge.wyze_stream")
_wyzebridge_wyze_stream.WyzeStream = Mock
_wyzebridge_wyze_events = types.ModuleType("wyzebridge.wyze_events")
_wyzebridge_wyze_events.WyzeEvents = Mock
sys.modules["wyzebridge.wyze_stream"] = _wyzebridge_wyze_stream
sys.modules["wyzebridge.wyze_events"] = _wyzebridge_wyze_events

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from wyzebridge.bridge_diagnostics import collect_bridge_diagnostics
from wyzebridge.stream_manager import StreamManager


class DummyApi:
    pass


class TestGo2RtcSnapshotAndDiagnostics(unittest.TestCase):
    def test_talkback_codec_probe_requests_microphone_media(self):
        from wyzebridge import go2rtc

        response = Mock()
        response.json.return_value = {"producers": []}
        requests_stub.get.return_value = response

        go2rtc._go2rtc_stream_details("north-yard")

        requests_stub.get.assert_called_with(
            f"{go2rtc.go2rtc_api_base()}/api/streams",
            params={"src": "north-yard", "microphone": "any"},
            timeout=2.0,
        )

    @patch("wyzebridge.go2rtc.time.sleep")
    @patch("wyzebridge.go2rtc.preload_native_stream")
    @patch("wyzebridge.go2rtc._talkback_ffmpeg_codec")
    def test_talkback_codec_resolution_preloads_and_retries(
        self, mock_codec, mock_preload, _mock_sleep
    ):
        from wyzebridge import go2rtc

        mock_codec.side_effect = [None, "aac/16000"]

        codec = go2rtc._resolve_talkback_ffmpeg_codec("north-yard")

        self.assertEqual(codec, "aac/16000")
        mock_preload.assert_called_once_with("north-yard", timeout=2.0)
        self.assertEqual(mock_codec.call_count, 2)

    @patch("wyzebridge.go2rtc._resolve_talkback_ffmpeg_codec")
    @patch("wyzebridge.go2rtc._go2rtc_stream_request")
    def test_send_native_talkback_uses_resolved_codec_for_audio_url(
        self, mock_stream_request, mock_resolve_codec
    ):
        from wyzebridge import go2rtc

        mock_resolve_codec.return_value = "aac/16000"
        mock_stream_request.return_value = {"status": "success"}

        result = go2rtc.send_native_talkback(
            {"audio_url": "http://127.0.0.1:55000/api/talkback-file/test.wav"},
            "north-yard",
        )

        self.assertEqual(result, {"status": "success"})
        mock_stream_request.assert_called_once_with(
            "north-yard",
            "ffmpeg:http://127.0.0.1:55000/api/talkback-file/test.wav#audio=aac/16000#input=file",
            mode="url",
            timeout=20.0,
        )

    @patch("wyzebridge.stream_manager.preload_native_stream")
    @patch("wyzebridge.stream_manager.write_native_snapshot")
    def test_stream_manager_prefers_native_snapshot_for_selected_camera(
        self, mock_write_native_snapshot, mock_preload
    ):
        manager = StreamManager(DummyApi())
        manager.streams["north-yard"] = SimpleNamespace(
            get_info=lambda: {
                "native_selected": True,
                "native_alias": "north-yard",
            }
        )
        mock_preload.return_value = {"ok": True}
        mock_write_native_snapshot.return_value = True

        result = manager.get_snapshot("north-yard")

        self.assertEqual(result, {"ok": True, "source": "go2rtc"})
        mock_preload.assert_called_once_with("north-yard")
        mock_write_native_snapshot.assert_called_once_with("north-yard", "north-yard")

    @patch("wyzebridge.go2rtc._resolve_talkback_ffmpeg_codec")
    @patch("wyzebridge.go2rtc._go2rtc_stream_request")
    @patch("wyzebridge.go2rtc._cleanup_stale_talkback_files")
    @patch("wyzebridge.go2rtc._talkback_temp_dir")
    def test_send_native_talkback_audio_b64_writes_temp_file_and_calls_stream_request(
        self, mock_temp_dir, mock_cleanup, mock_stream_request, mock_resolve_codec
    ):
        import base64
        import tempfile
        from pathlib import Path

        from wyzebridge import go2rtc

        mock_resolve_codec.return_value = "aac/16000"
        mock_stream_request.return_value = {"status": "success"}

        # Use a real temp dir so NamedTemporaryFile can actually create the file
        with tempfile.TemporaryDirectory() as real_tmp:
            mock_temp_dir.return_value = Path(real_tmp)

            audio_bytes = b"RIFF\x00\x00\x00\x00WAVEfmt "
            audio_b64 = base64.b64encode(audio_bytes).decode()

            result = go2rtc.send_native_talkback(
                {"audio_b64": audio_b64, "file_ext": "wav"},
                "north-yard",
            )

        self.assertEqual(result, {"status": "success"})
        mock_resolve_codec.assert_called_once()
        mock_cleanup.assert_called_once()
        call_args = mock_stream_request.call_args
        self.assertEqual(call_args[0][0], "north-yard")
        self.assertIn("aac/16000", call_args[0][1])
        self.assertIn("#input=file", call_args[0][1])
        self.assertEqual(call_args[1]["mode"], "file")

    def test_send_native_talkback_rejects_both_audio_b64_and_audio_url(self):
        import base64

        from wyzebridge import go2rtc

        audio_b64 = base64.b64encode(b"fake audio").decode()
        result = go2rtc.send_native_talkback(
            {"audio_b64": audio_b64, "audio_url": "http://example.com/audio.wav"},
            "north-yard",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("audio_b64", result["response"])
        self.assertIn("audio_url", result["response"])

    @patch("wyzebridge.bridge_diagnostics.go2rtc_probe")
    def test_collect_bridge_diagnostics_includes_go2rtc_selection(self, mock_probe):
        mock_probe.return_value = {
            "api": {"reachable": True},
            "aliases": ["north-yard", "north-yard-sd"],
        }

        details = collect_bridge_diagnostics(
            "north-yard",
            {
                "native_supported": True,
                "native_selected": True,
                "native_reason": "HL_CAM4 validated on native go2rtc",
                "native_preload": True,
                "snapshot_source": "go2rtc",
                "native_alias": "north-yard",
                "talkback_supported": True,
                "talkback_reason": "API-first talkback is available through the native go2rtc alias",
                "talkback_source": "go2rtc",
                "talkback_alias": "north-yard",
            },
        )

        self.assertTrue(details["go2rtc"]["selection"]["selected"])
        self.assertTrue(details["go2rtc"]["selection"]["talkback_supported"])
        self.assertEqual(details["go2rtc"]["alias"], {"name": "north-yard", "exists": True})
        self.assertEqual(
            details["go2rtc"]["talkback_alias"], {"name": "north-yard", "exists": True}
        )


if __name__ == "__main__":
    unittest.main()
