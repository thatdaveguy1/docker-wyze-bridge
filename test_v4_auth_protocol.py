#!/usr/bin/env python3

import pathlib
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "app"))

from wyzecam.tutk.tutk_protocol import (
    K10002ConnectAuth,
    K10008ConnectUserAuth,
    respond_to_ioctrl_10001,
)


class TestV4AuthProtocol(unittest.TestCase):
    def test_hl_cam4_prefers_10008_when_supported(self):
        payload = struct.pack("<B16s", 1, b"0123456789ABCDEF")

        with (
            patch(
                "wyzecam.tutk.tutk_protocol.generate_challenge_response",
                return_value=b"R" * 16,
            ),
            patch(
                "wyzecam.tutk.tutk_protocol.supports",
                side_effect=lambda _m, _p, c: c == 10008,
            ),
        ):
            msg = respond_to_ioctrl_10001(
                payload,
                protocol=30,
                enr="A" * 32,
                product_model="HL_CAM4",
                mac="001122334455",
                phone_id="phone",
                open_userid="user",
            )

        self.assertIsInstance(msg, K10008ConnectUserAuth)

    def test_hl_cam4_falls_back_to_10002_when_10008_unsupported(self):
        payload = struct.pack("<B16s", 1, b"0123456789ABCDEF")

        with (
            patch(
                "wyzecam.tutk.tutk_protocol.generate_challenge_response",
                return_value=b"R" * 16,
            ),
            patch("wyzecam.tutk.tutk_protocol.supports", return_value=False),
        ):
            msg = respond_to_ioctrl_10001(
                payload,
                protocol=30,
                enr="A" * 32,
                product_model="HL_CAM4",
                mac="001122334455",
                phone_id="phone",
                open_userid="user",
            )

        self.assertIsInstance(msg, K10002ConnectAuth)


if __name__ == "__main__":
    unittest.main()
