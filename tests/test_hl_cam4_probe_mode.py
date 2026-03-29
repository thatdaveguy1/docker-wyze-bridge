#!/usr/bin/env python3

import json
import os
import pathlib
import sys
import threading
import time
import unittest
from ctypes import c_uint32
from unittest.mock import patch

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / ".ha_live_addon" / "app")
)

from wyzecam.api_models import WyzeAccount, WyzeCamera
from wyzecam import iotc as iotc_module
from wyzecam.iotc import WyzeIOTC, WyzeIOTCSession


def make_account() -> WyzeAccount:
    return WyzeAccount(
        phone_id="phone-id",
        logo="",
        nickname="tester",
        email="test@example.com",
        user_code="user-code",
        user_center_id="user-center",
        open_user_id="open-user",
    )


def make_camera(model: str = "HL_CAM4") -> WyzeCamera:
    return WyzeCamera(
        p2p_id="P2P-ID",
        p2p_type=1,
        ip="192.168.1.176",
        enr="ENR-VALUE",
        mac="001122334455",
        product_model=model,
        nickname="North Yard",
        timezone_name="America/Edmonton",
        firmware_ver="4.52.9.5332",
        dtls=1,
        parent_dtls=0,
        parent_enr=None,
        parent_mac=None,
        thumbnail=None,
    )


class FakeTutkLib:
    def TUTK_SDK_Set_License_Key(self, *_args, **_kwargs):
        return 0

    def TUTK_SDK_Set_Region(self, *_args, **_kwargs):
        return 0

    def avClientSetMaxBufSize(self, *_args, **_kwargs):
        return None


class TestHlCam4ProbeMode(unittest.TestCase):
    def test_wyze_iotc_can_enable_native_tutk_log(self):
        tutk_lib = FakeTutkLib()

        with (
            patch.dict(
                os.environ,
                {
                    "TUTK_NATIVE_LOG": "1",
                    "TUTK_NATIVE_LOG_PATH": "/tmp/tutk-native.log",
                    "TUTK_NATIVE_LOG_LEVEL": "0",
                },
                clear=False,
            ),
            patch("wyzecam.iotc.tutk.iotc_set_log_attr", return_value=0) as set_log_attr,
            patch("builtins.print"),
        ):
            WyzeIOTC(tutk_platform_lib=tutk_lib)

        self.assertEqual(set_log_attr.call_count, 1)
        self.assertIs(set_log_attr.call_args.args[0], tutk_lib)
        self.assertEqual(set_log_attr.call_args.args[1], "/tmp/tutk-native.log")
        self.assertIsInstance(set_log_attr.call_args.args[2], c_uint32)
        self.assertEqual(set_log_attr.call_args.args[2].value, 0)

    def run_connect_in_thread(self, session: WyzeIOTCSession):
        errors = []

        def target():
            try:
                session._connect()
            except Exception as exc:  # pragma: no cover - assertion target
                errors.append(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread, errors

    def tutk_trace_events(self, log_info):
        events = []
        for call in log_info.call_args_list:
            trace = call.args[0]
            if not trace.startswith("[TUTK_TRACE] "):
                continue
            events.append(json.loads(trace.split(" ", 1)[1]))
        return events

    def test_main_probe_mode_uses_dtls_connect(self):
        session = WyzeIOTCSession(FakeTutkLib(), make_account(), make_camera("HL_CAM4"))

        with (
            patch("wyzecam.iotc.tutk.iotc_get_session_id", return_value=0),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_by_uid_parallel", return_value=0
            ) as connect_parallel,
            patch(
                "wyzecam.iotc.tutk.iotc_connect_by_uid_ex", return_value=0
            ) as connect_ex,
            patch("wyzecam.iotc.tutk.av_client_start", return_value=0),
            patch("wyzecam.iotc.tutk.av_client_set_recv_buf_size", return_value=None),
            patch.object(session, "session_check") as session_check,
            patch.dict(os.environ, {"HL_CAM4_MAIN_PROBE_MODE": "tutk_dtls"}, clear=False),
        ):
            session_check.return_value.mode = 2
            session._connect()

        connect_ex.assert_called_once()
        connect_parallel.assert_not_called()

    def test_main_probe_mode_can_force_parallel_connect(self):
        session = WyzeIOTCSession(FakeTutkLib(), make_account(), make_camera("HL_CAM4"))

        with (
            patch("wyzecam.iotc.tutk.iotc_get_session_id", return_value=0),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_by_uid_parallel", return_value=0
            ) as connect_parallel,
            patch(
                "wyzecam.iotc.tutk.iotc_connect_by_uid_ex", return_value=0
            ) as connect_ex,
            patch("wyzecam.iotc.tutk.av_client_start", return_value=0),
            patch("wyzecam.iotc.tutk.av_client_set_recv_buf_size", return_value=None),
            patch.object(session, "session_check") as session_check,
            patch.dict(os.environ, {"HL_CAM4_MAIN_PROBE_MODE": "tutk_parallel"}, clear=False),
        ):
            session_check.return_value.mode = 2
            session._connect()

        connect_parallel.assert_called_once()
        connect_ex.assert_not_called()

    def test_connect_watchdog_stops_wedged_dtls_connect(self):
        session = WyzeIOTCSession(FakeTutkLib(), make_account(), make_camera("HL_CAM4"))
        release_connect = threading.Event()

        def block_until_stopped(*_args, **_kwargs):
            release_connect.wait(timeout=1)
            return -13

        def stop_connect(*_args, **_kwargs):
            release_connect.set()
            return 0

        with (
            patch("wyzecam.iotc.tutk.iotc_get_session_id", return_value=17),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_by_uid_ex",
                side_effect=block_until_stopped,
            ),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_stop_by_session_id",
                side_effect=stop_connect,
            ) as stop_by_sid,
            patch.object(session, "_disconnect", return_value=None),
            patch.dict(
                os.environ,
                {
                    "HL_CAM4_MAIN_PROBE_MODE": "tutk_dtls",
                    "HL_CAM4_CONNECT_WATCHDOG_SECS": "0.05",
                    "CONNECT_RETRIES": "1",
                },
                clear=False,
            ),
        ):
            thread, errors = self.run_connect_in_thread(session)
            thread.join(timeout=0.5)
            if thread.is_alive():
                release_connect.set()
                thread.join(timeout=1)

        self.assertFalse(thread.is_alive(), "watchdog should release wedged DTLS connect")
        self.assertEqual(stop_by_sid.call_count, 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], iotc_module.tutk.TutkError)
        self.assertEqual(errors[0].code, -13)

    def test_connect_watchdog_stops_wedged_parallel_connect(self):
        session = WyzeIOTCSession(FakeTutkLib(), make_account(), make_camera("HL_CAM4"))
        release_connect = threading.Event()

        def block_until_stopped(*_args, **_kwargs):
            release_connect.wait(timeout=1)
            return -13

        def stop_connect(*_args, **_kwargs):
            release_connect.set()
            return 0

        with (
            patch("wyzecam.iotc.tutk.iotc_get_session_id", return_value=23),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_by_uid_parallel",
                side_effect=block_until_stopped,
            ),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_stop_by_session_id",
                side_effect=stop_connect,
            ) as stop_by_sid,
            patch.object(session, "_disconnect", return_value=None),
            patch.dict(
                os.environ,
                {
                    "HL_CAM4_MAIN_PROBE_MODE": "tutk_parallel",
                    "HL_CAM4_CONNECT_WATCHDOG_SECS": "0.05",
                    "CONNECT_RETRIES": "1",
                },
                clear=False,
            ),
        ):
            thread, errors = self.run_connect_in_thread(session)
            thread.join(timeout=0.5)
            if thread.is_alive():
                release_connect.set()
                thread.join(timeout=1)

        self.assertFalse(
            thread.is_alive(), "watchdog should release wedged parallel connect"
        )
        self.assertEqual(stop_by_sid.call_count, 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], iotc_module.tutk.TutkError)
        self.assertEqual(errors[0].code, -13)

    def test_connect_watchdog_does_not_stop_fast_connect(self):
        session = WyzeIOTCSession(FakeTutkLib(), make_account(), make_camera("HL_CAM4"))

        with (
            patch("wyzecam.iotc.tutk.iotc_get_session_id", return_value=0),
            patch("wyzecam.iotc.tutk.iotc_connect_by_uid_ex", return_value=0),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_stop_by_session_id", return_value=0
            ) as stop_by_sid,
            patch("wyzecam.iotc.tutk.av_client_start", return_value=0),
            patch("wyzecam.iotc.tutk.av_client_set_recv_buf_size", return_value=None),
            patch.object(session, "session_check") as session_check,
            patch.object(iotc_module.logger, "info") as log_info,
            patch("builtins.print"),
            patch.dict(
                os.environ,
                {
                    "CONNECT_RETRIES": "1",
                    "HL_CAM4_MAIN_PROBE_MODE": "tutk_dtls",
                    "HL_CAM4_CONNECT_WATCHDOG_SECS": "0.05",
                    "TUTK_TRACE_STREAM": "north-yard",
                },
                clear=False,
            ),
        ):
            session_check.return_value.mode = 2
            session._connect()
            time.sleep(0.1)

        stop_by_sid.assert_not_called()
        result_events = [
            event
            for event in self.tutk_trace_events(log_info)
            if event["event"] == "connect_result"
        ]
        self.assertEqual(len(result_events), 1)
        self.assertEqual(result_events[0]["attempt_no"], 1)
        self.assertEqual(result_events[0]["connect_mode"], "dtls_ex")
        self.assertEqual(result_events[0]["max_retries"], 1)
        self.assertEqual(result_events[0]["session_id"], 0)
        self.assertFalse(result_events[0]["watchdog_fired"])

    def test_watchdog_induced_fail_connect_search_is_retried(self):
        session = WyzeIOTCSession(FakeTutkLib(), make_account(), make_camera("HL_CAM4"))
        attempts = {"count": 0}
        release_connect = {"event": None}

        def fake_connect(*_args, **_kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                release_connect["event"] = threading.Event()
                release_connect["event"].wait(timeout=1)
                return -27
            return 0

        def stop_connect(*_args, **_kwargs):
            if release_connect["event"] is not None:
                release_connect["event"].set()
            return 0

        with (
            patch("wyzecam.iotc.tutk.iotc_get_session_id", return_value=0),
            patch("wyzecam.iotc.tutk.iotc_connect_by_uid_ex", side_effect=fake_connect),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_stop_by_session_id",
                side_effect=stop_connect,
            ) as stop_by_sid,
            patch("wyzecam.iotc.tutk.av_client_start", return_value=0),
            patch("wyzecam.iotc.tutk.av_client_set_recv_buf_size", return_value=None),
            patch.object(session, "session_check") as session_check,
            patch.object(session, "_disconnect", return_value=None),
            patch.object(iotc_module.logger, "info") as log_info,
            patch("builtins.print"),
            patch("wyzecam.iotc.time.sleep", return_value=None),
            patch.dict(
                os.environ,
                {
                    "HL_CAM4_MAIN_PROBE_MODE": "tutk_dtls",
                    "HL_CAM4_CONNECT_WATCHDOG_SECS": "0.05",
                    "CONNECT_RETRIES": "3",
                    "CONNECT_RETRY_DELAY": "0",
                    "TUTK_TRACE_STREAM": "north-yard",
                },
                clear=False,
            ),
        ):
            session_check.return_value.mode = 2
            session._connect()

        self.assertEqual(attempts["count"], 3)
        self.assertGreaterEqual(stop_by_sid.call_count, 2)
        result_events = [
            event
            for event in self.tutk_trace_events(log_info)
            if event["event"] == "connect_result"
        ]
        self.assertEqual([event["attempt_no"] for event in result_events], [1, 2, 3])
        self.assertEqual([event["max_retries"] for event in result_events], [3, 3, 3])
        self.assertEqual([event["session_id"] for event in result_events], [-27, -27, 0])
        self.assertEqual(
            [event["watchdog_fired"] for event in result_events], [True, True, False]
        )

    def test_tutk_trace_is_gated_by_stream_name(self):
        camera = make_camera("HL_CAM4")

        with (
            patch.dict(os.environ, {"TUTK_TRACE_STREAM": ""}, clear=False),
            patch.object(iotc_module.logger, "info") as log_info,
            patch("builtins.print") as print_mock,
        ):
            iotc_module._log_tutk_trace(camera, "connect_start", substream=False)

        log_info.assert_not_called()
        print_mock.assert_called_once()

        with (
            patch.dict(os.environ, {"TUTK_TRACE_STREAM": "north-yard"}, clear=False),
            patch.object(iotc_module.logger, "info") as log_info,
            patch("builtins.print") as print_mock,
        ):
            iotc_module._log_tutk_trace(
                camera,
                "connect_start",
                connect_mode="dtls_ex",
                substream=False,
            )

        log_info.assert_called_once()
        trace = log_info.call_args[0][0]
        self.assertIn("[TUTK_TRACE]", trace)
        self.assertIn('"camera": "north-yard"', trace)
        self.assertIn('"event": "connect_start"', trace)
        self.assertEqual(print_mock.call_count, 2)
        self.assertIn("[TUTK_TRACE_GATE]", print_mock.call_args_list[0].args[0])
        self.assertEqual(print_mock.call_args_list[1].args[0], trace)


if __name__ == "__main__":
    unittest.main()
