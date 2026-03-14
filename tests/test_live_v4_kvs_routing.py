#!/usr/bin/env python3

import pathlib
import sys
import unittest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / ".ha_live_addon" / "app")
)

from wyzecam.api_models import WyzeCamera


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


class TestLiveV4KVSRouting(unittest.TestCase):
    def test_hl_cam4_is_kvs_by_default(self):
        self.assertTrue(make_camera("HL_CAM4").is_kvs)

    def test_non_webrtc_camera_is_not_kvs(self):
        self.assertFalse(make_camera("WYZEC1").is_kvs)


if __name__ == "__main__":
    unittest.main()
