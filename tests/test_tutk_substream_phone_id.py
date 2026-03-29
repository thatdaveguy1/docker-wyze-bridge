#!/usr/bin/env python3

import pathlib
import sys
import unittest
from ctypes import c_int
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from wyzecam.api_models import WyzeAccount, WyzeCamera
from wyzecam.iotc import WyzeIOTC


class FakeTutkLib:
    def TUTK_SDK_Set_License_Key(self, *_args, **_kwargs):
        return 0

    def TUTK_SDK_Set_Region(self, *_args, **_kwargs):
        return 0

    def IOTC_Set_Log_Attr(self, *_args, **_kwargs):
        return 0


def make_account() -> WyzeAccount:
    return WyzeAccount(
        phone_id="abcd1234-efgh-5678",
        logo="",
        nickname="tester",
        email="test@example.com",
        user_code="user-code",
        user_center_id="user-center",
        open_user_id="open-user",
    )


def make_camera() -> WyzeCamera:
    return WyzeCamera(
        p2p_id="P2P-ID",
        p2p_type=1,
        ip="192.168.1.176",
        enr="ENR-VALUE",
        mac="001122334455",
        product_model="HL_CAM4",
        nickname="North Yard",
        timezone_name="America/Edmonton",
        firmware_ver="4.52.9.5332",
        dtls=1,
        parent_dtls=0,
        parent_enr=None,
        parent_mac=None,
        thumbnail=None,
    )


class TestTutkSubstreamPhoneId(unittest.TestCase):
    def test_substream_session_does_not_mutate_user_phone_id(self):
        iotc = WyzeIOTC(tutk_platform_lib=FakeTutkLib(), sdk_key="x")
        user = make_account()
        stream = SimpleNamespace(
            user=user,
            camera=make_camera(),
            options=SimpleNamespace(
                frame_size=1,
                bitrate=30,
                audio=True,
                substream=True,
            ),
        )

        with patch("wyzecam.iotc.CONNECT_TIMEOUT", 60):
            session = iotc.session(stream, c_int(0))

        self.assertEqual(user.phone_id, "abcd1234-efgh-5678")
        self.assertEqual(session.account.phone_id, "abcd1234-efgh-5678")


if __name__ == "__main__":
    unittest.main()
