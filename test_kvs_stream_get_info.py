#!/usr/bin/env python3

import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent / ".ha_live_addon" / "app")
)

from wyzebridge.wyze_stream import StreamStatus, WyzeStream


class DummyApi:
    pass


def make_camera():
    return SimpleNamespace(
        name_uri="garage",
        nickname="Garage",
        product_model="WYZE_CAKP2JFUS",
        model_name="Wyze Cam v3",
        ip="192.168.1.10",
        is_gwell=False,
        is_battery=False,
        is_floodlight=False,
        is_2k=False,
        can_substream=False,
        rtsp_fw=False,
        camera_info=None,
        webrtc_support=True,
        is_kvs=True,
        mac="001122334455",
        p2p_id="p2p-id",
        dtls=1,
        parent_dtls=0,
        enr="enr",
        model_dump=lambda exclude=None: {"product_model": "WYZE_CAKP2JFUS"},
    )


class TestKVSStreamGetInfo(unittest.TestCase):
    def test_connected_kvs_stream_skips_tutk_caminfo_when_control_queue_missing(self):
        user = SimpleNamespace()
        camera = make_camera()

        with patch("wyzebridge.wyze_stream.publish_discovery"):
            stream = WyzeStream(
                user,
                DummyApi(),
                camera,
                SimpleNamespace(
                    quality="hd180",
                    audio=True,
                    record=False,
                    reconnect=True,
                    substream=False,
                    frame_size=0,
                    bitrate=180,
                    update_quality=lambda hq: None,
                ),
            )

        stream.state = StreamStatus.CONNECTED

        info = stream.get_info()

        self.assertTrue(info["connected"])
        self.assertEqual(info["product_model"], "WYZE_CAKP2JFUS")


if __name__ == "__main__":
    unittest.main()
