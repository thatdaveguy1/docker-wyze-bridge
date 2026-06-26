#!/usr/bin/env python3
"""Tests for app/babysitter/helpers.py — HTTP clients, JPEG validation, redaction."""

from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from babysitter.helpers import (  # noqa: E402
    FrigateClient,
    ScryptedClient,
    is_valid_jpeg,
    redact_token,
    redact_url,
    sha256_hex,
    tcp_reachable,
)


class TestRedaction(unittest.TestCase):
    def test_redact_url_with_credentials(self):
        url = "rtsp://admin:REDACTED@192.168.1.69:554/stream"
        redacted = redact_url(url)
        self.assertIn("<redacted>", redacted)
        self.assertNotIn("admin", redacted)
        self.assertNotIn("pass123", redacted)

    def test_redact_url_without_credentials(self):
        url = "rtsp://192.168.1.69:554/stream"
        redacted = redact_url(url)
        self.assertEqual(url, redacted)

    def test_redact_token_long(self):
        token = "abcdef1234567890"
        redacted = redact_token(token)
        self.assertIn("...", redacted)
        self.assertNotIn("1234567890", redacted)

    def test_redact_token_short(self):
        self.assertEqual("<redacted>", redact_token("abc"))

    def test_redact_token_empty(self):
        self.assertEqual("<empty>", redact_token(""))


class TestJpegValidation(unittest.TestCase):
    def _make_jpeg(self, size: int = 5000) -> bytes:
        """Create a minimal valid JPEG-like byte sequence."""
        return b"\xff\xd8" + b"\x00" * (size - 4) + b"\xff\xd9"

    def test_valid_jpeg(self):
        data = self._make_jpeg(5000)
        self.assertTrue(is_valid_jpeg(data))

    def test_too_small(self):
        data = self._make_jpeg(100)
        self.assertFalse(is_valid_jpeg(data))

    def test_empty(self):
        self.assertFalse(is_valid_jpeg(b""))

    def test_wrong_magic(self):
        data = b"\x89PNG" + b"\x00" * 5000 + b"\xff\xd9"
        self.assertFalse(is_valid_jpeg(data))

    def test_no_end_marker(self):
        data = b"\xff\xd8" + b"\x00" * 5000
        self.assertFalse(is_valid_jpeg(data))

    def test_custom_min_size(self):
        data = self._make_jpeg(3000)
        self.assertTrue(is_valid_jpeg(data, min_size=2048))
        self.assertFalse(is_valid_jpeg(data, min_size=4096))


class TestSha256(unittest.TestCase):
    def test_known_hash(self):
        import hashlib

        data = b"hello"
        expected = hashlib.sha256(b"hello").hexdigest()
        self.assertEqual(sha256_hex(data), expected)

    def test_empty(self):
        import hashlib

        self.assertEqual(sha256_hex(b""), hashlib.sha256(b"").hexdigest())


class TestTcpReachable(unittest.TestCase):
    @patch("babysitter.helpers.socket.create_connection")
    def test_reachable(self, mock_conn):
        mock_conn.return_value.__enter__.return_value = MagicMock()
        self.assertTrue(tcp_reachable("192.168.1.69", 554, timeout=1))

    @patch("babysitter.helpers.socket.create_connection", side_effect=OSError)
    def test_unreachable(self, mock_conn):
        self.assertFalse(tcp_reachable("192.168.1.99", 554, timeout=1))


class TestScryptedClient(unittest.TestCase):
    def setUp(self):
        self.client = ScryptedClient(
            host="https://192.168.1.244:10443",
            username="scrypted",
            password="secret",
        )

    @patch("babysitter.helpers.requests.post")
    def test_login_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "test-token-12345"},
        )
        token = self.client.login()
        self.assertEqual(token, "test-token-12345")
        self.assertEqual(self.client.token, "test-token-12345")

    @patch("babysitter.helpers.requests.post")
    def test_login_success_authorization_field(self, mock_post):
        """Real Scrypted returns ``authorization`` with a ``Bearer `` prefix."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "authorization": "Bearer abc123#{\"u\":\"scrypted\"}",
                "username": "scrypted",
            },
        )
        token = self.client.login()
        self.assertEqual(token, 'abc123#{"u":"scrypted"}')
        self.assertEqual(self.client.token, 'abc123#{"u":"scrypted"}')
        # Verify password was sent in the JSON body, not as basic auth.
        _, kwargs = mock_post.call_args
        self.assertIsNone(kwargs.get("auth"))
        self.assertEqual(kwargs["json"]["password"], "secret")
        self.assertEqual(kwargs["json"]["username"], "scrypted")

    @patch("babysitter.helpers.requests.post")
    def test_login_no_token(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {},
        )
        with self.assertRaises(ValueError):
            self.client.login()

    @patch("babysitter.helpers.requests.get")
    @patch("babysitter.helpers.requests.post")
    def test_snapshot_success(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "tok"},
        )
        jpeg_data = b"\xff\xd8" + b"\x00" * 5000 + b"\xff\xd9"
        mock_get.return_value = MagicMock(
            status_code=200,
            content=jpeg_data,
        )
        data = self.client.snapshot("54")
        self.assertEqual(data, jpeg_data)

    @patch("babysitter.helpers.requests.get")
    @patch("babysitter.helpers.requests.post")
    def test_snapshot_probe_valid(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "tok"},
        )
        jpeg_data = b"\xff\xd8" + b"\x00" * 5000 + b"\xff\xd9"
        mock_get.return_value = MagicMock(
            status_code=200,
            content=jpeg_data,
        )
        probe = self.client.snapshot_probe("54")
        self.assertTrue(probe["jpeg_valid"])
        self.assertEqual(probe["http_status"], 200)
        self.assertGreater(probe["content_length"], 0)
        self.assertTrue(probe["sha256"])

    @patch("babysitter.helpers.requests.get", side_effect=requests.RequestException("timeout"))
    @patch("babysitter.helpers.requests.post")
    def test_snapshot_probe_failure(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"token": "tok"},
        )
        probe = self.client.snapshot_probe("99")
        self.assertFalse(probe["jpeg_valid"])
        self.assertIn("error", probe)


class TestFrigateClient(unittest.TestCase):
    def setUp(self):
        self.client = FrigateClient(host="http://frigate:5000")

    @patch("babysitter.helpers.requests.get")
    def test_stats(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "doorbell": {"camera_fps": 5.0, "process_fps": 5.0, "skipped_fps": 0},
                "service": {"uptime": 100},
            },
        )
        stats = self.client.stats()
        self.assertIn("doorbell", stats)

    @patch("babysitter.helpers.requests.get")
    def test_camera_fps(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "doorbell": {"camera_fps": 5.0, "process_fps": 4.8, "skipped_fps": 0},
            },
        )
        fps = self.client.camera_fps("doorbell")
        self.assertEqual(fps["camera_fps"], 5.0)
        self.assertEqual(fps["process_fps"], 4.8)
        self.assertEqual(fps["skipped_fps"], 0.0)

    @patch("babysitter.helpers.requests.get")
    def test_camera_fps_missing(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {},
        )
        fps = self.client.camera_fps("nonexistent")
        self.assertEqual(fps["camera_fps"], 0.0)

    @patch("babysitter.helpers.requests.get")
    def test_rtsp_inputs(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "cameras": {
                    "doorbell": {
                        "ffmpeg": {
                            "inputs": [
                                {"path": "rtsp://admin:REDACTED@192.168.1.69:554/stream", "roles": ["record"]},
                            ]
                        }
                    }
                }
            },
        )
        inputs = self.client.rtsp_inputs()
        self.assertIn("doorbell", inputs)
        self.assertIn("rtsp://", inputs["doorbell"])

    @patch("babysitter.helpers.requests.get")
    def test_ffprobe(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"streams": [{"codec_type": "video"}]},
        )
        result = self.client.ffprobe("rtsp://test/stream")
        self.assertIn("streams", result)


if __name__ == "__main__":
    unittest.main()
