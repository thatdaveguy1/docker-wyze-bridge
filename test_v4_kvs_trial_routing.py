#!/usr/bin/env python3

import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent / ".ha_v4kvs_trial_addon" / "app")
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


class TestV4KVSTrialRouting(unittest.TestCase):
    def test_hl_cam4_is_not_kvs_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertFalse(make_camera("HL_CAM4").is_kvs)

    def test_hl_cam4_can_enable_kvs_trial_with_env(self):
        with patch.dict("os.environ", {"ENABLE_V4_KVS_TRIAL": "true"}, clear=False):
            self.assertTrue(make_camera("HL_CAM4").is_kvs)

    def test_existing_kvs_camera_stays_kvs_without_env(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(make_camera("LD_CFP").is_kvs)


if __name__ == "__main__":
    unittest.main()
