#!/usr/bin/env python3

import pathlib
import sys
import unittest
from ctypes import c_int
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from wyzebridge.config import CONNECT_TIMEOUT
from wyzebridge import mtx_server
from wyzecam.api_models import WyzeAccount, WyzeCamera
from wyzecam.iotc import WyzeIOTC, WyzeIOTCSession
from wyzecam.tutk import tutk


class DummyOptions:
    frame_size = 0
    bitrate = 120
    audio = True
    substream = False


class DummyStream:
    def __init__(self, user, camera):
        self.user = user
        self.camera = camera
        self.options = DummyOptions()


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

    def IOTC_Set_Log_Attr(self, *_args, **_kwargs):
        return 0

    def avClientSetMaxBufSize(self, *_args, **_kwargs):
        return None


class TestV4TimeoutFix(unittest.TestCase):
    def test_session_uses_configured_connect_timeout(self):
        iotc = WyzeIOTC(tutk_platform_lib=FakeTutkLib(), sdk_key="x")
        stream = DummyStream(make_account(), make_camera())

        session = WyzeIOTC.session(iotc, stream, c_int(0))

        self.assertEqual(session.connect_timeout, CONNECT_TIMEOUT)

    def test_connect_retries_timeout_errors(self):
        session = WyzeIOTCSession(FakeTutkLib(), make_account(), make_camera())
        attempts = {"count": 0}

        def fake_connect_by_uid_ex(*_args, **_kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                return -13
            return 0

        with (
            patch("wyzecam.iotc.tutk.iotc_get_session_id", return_value=0),
            patch(
                "wyzecam.iotc.tutk.iotc_connect_by_uid_ex",
                side_effect=fake_connect_by_uid_ex,
            ),
            patch("wyzecam.iotc.tutk.iotc_connect_by_uid_parallel", return_value=0),
            patch("wyzecam.iotc.tutk.av_client_start", return_value=0),
            patch("wyzecam.iotc.tutk.av_client_set_recv_buf_size", return_value=None),
            patch.object(session, "session_check") as session_check,
            patch.object(session, "_disconnect", return_value=None),
            patch("wyzecam.iotc.time.sleep", return_value=None),
            patch.dict(
                "os.environ",
                {
                    "HL_CAM4_MAIN_PROBE_MODE": "kvs",
                    "TUTK_TRACE_STREAM": "",
                    "HL_CAM4_CONNECT_WATCHDOG_SECS": "0",
                },
                clear=False,
            ),
        ):
            session_check.return_value.mode = 2
            session._connect()

        self.assertEqual(attempts["count"], 3)

    def test_hl_cam4_can_force_parallel_connect(self):
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
            patch.dict("os.environ", {"FORCE_V4_PARALLEL": "true"}, clear=False),
        ):
            session_check.return_value.mode = 2
            session._connect()

        connect_parallel.assert_called_once()
        connect_ex.assert_not_called()

    def test_hl_cam4_substream_uses_parallel_connect_without_env_flag(self):
        session = WyzeIOTCSession(
            FakeTutkLib(), make_account(), make_camera("HL_CAM4"), substream=True
        )

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
            patch.dict("os.environ", {"FORCE_V4_PARALLEL": "false"}, clear=False),
        ):
            session_check.return_value.mode = 2
            session._connect()

        connect_parallel.assert_called_once()
        connect_ex.assert_not_called()

    def test_mtx_run_on_demand_timeout_covers_connect_retries(self):
        writes = {}

        class FakeMtxInterface:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

            def set(self_inner, path, value):
                writes[path] = value

            def save_config(self_inner):
                return None

        with (
            patch("wyzebridge.mtx_server.MtxInterface", FakeMtxInterface),
            patch.dict(
                "os.environ",
                {"CONNECT_RETRIES": "3", "CONNECT_RETRY_DELAY": "2.0"},
                clear=False,
            ),
        ):
            mtx_server.MtxServer()

        self.assertEqual(writes["pathDefaults.runOnDemandStartTimeout"], "70s")


if __name__ == "__main__":
    unittest.main()
