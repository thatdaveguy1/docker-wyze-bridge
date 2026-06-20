"""Tests for scripts/ffmpeg_helpers.py shared command-building module."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"


def _load_helpers():
    spec = importlib.util.spec_from_file_location(
        "ffmpeg_helpers", SCRIPTS_DIR / "ffmpeg_helpers.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPERS = _load_helpers()


class TestFfmpegHelpers(unittest.TestCase):
    def test_detect_timeout_flag_finds_rw_timeout(self):
        fake_result = MagicMock(stdout="-rw_timeout 500000\n-other-flag")
        with patch("subprocess.run", return_value=fake_result):
            self.assertEqual(HELPERS.detect_timeout_flag("/usr/bin/ffmpeg"), "rw_timeout")

    def test_detect_timeout_flag_falls_back_to_timeout(self):
        fake_result = MagicMock(stdout="-timeout 5\n-no-other-flags")
        with patch("subprocess.run", return_value=fake_result):
            self.assertEqual(HELPERS.detect_timeout_flag("/usr/bin/ffprobe"), "timeout")

    def test_detect_timeout_flag_returns_none_when_no_match(self):
        fake_result = MagicMock(stdout="-version 1.0\n-help\n-verbose")
        with patch("subprocess.run", return_value=fake_result):
            self.assertIsNone(HELPERS.detect_timeout_flag("/usr/bin/ffmpeg"))

    def test_ensure_binary_resolves_via_which(self):
        with patch("shutil.which", return_value="/usr/local/bin/ffmpeg"):
            with patch.object(Path, "exists", return_value=True):
                result = HELPERS.ensure_binary(None, "ffmpeg")
                self.assertEqual(result, "/usr/local/bin/ffmpeg")

    def test_ensure_binary_uses_provided_path(self):
        with patch.object(Path, "exists", return_value=True):
            result = HELPERS.ensure_binary("/custom/ffmpeg", "ffmpeg")
            self.assertEqual(result, "/custom/ffmpeg")

    def test_ensure_binary_raises_when_not_found(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(SystemExit):
                HELPERS.ensure_binary(None, "nonexistent_binary")

    def test_ensure_binary_raises_when_path_does_not_exist(self):
        with patch.object(Path, "exists", return_value=False):
            with self.assertRaises(SystemExit):
                HELPERS.ensure_binary("/fake/path/ffmpeg", "ffmpeg")

    def test_build_ffprobe_cmd_basic(self):
        cmd = HELPERS.build_ffprobe_cmd(
            "/usr/bin/ffprobe", "rtsp://127.0.0.1:8554/cam", "tcp"
        )
        self.assertEqual(cmd[0], "/usr/bin/ffprobe")
        self.assertIn("-hide_banner", cmd)
        self.assertIn("-loglevel", cmd)
        self.assertIn("error", cmd)
        self.assertIn("-rtsp_transport", cmd)
        self.assertIn("tcp", cmd)
        self.assertIn("-of", cmd)
        self.assertIn("json", cmd)
        self.assertEqual(cmd[-1], "rtsp://127.0.0.1:8554/cam")

    def test_build_ffprobe_cmd_with_timeout(self):
        cmd = HELPERS.build_ffprobe_cmd(
            "/usr/bin/ffprobe",
            "rtsp://127.0.0.1:8554/cam",
            "udp",
            timeout_flag="rw_timeout",
            timeout_us=3000000,
        )
        self.assertIn("-rw_timeout", cmd)
        self.assertIn("3000000", cmd)

    def test_build_ffprobe_cmd_custom_entries(self):
        cmd = HELPERS.build_ffprobe_cmd(
            "/usr/bin/ffprobe",
            "rtsp://127.0.0.1:8554/cam",
            "tcp",
            entries="stream=codec_name:format=format_name",
        )
        self.assertIn("stream=codec_name:format=format_name", cmd)

    def test_build_ffmpeg_rtsp_cmd_basic(self):
        cmd = HELPERS.build_ffmpeg_rtsp_cmd(
            "/usr/bin/ffmpeg", "rtsp://127.0.0.1:8554/cam", "tcp", 10.0
        )
        self.assertEqual(cmd[0], "/usr/bin/ffmpeg")
        self.assertIn("-hide_banner", cmd)
        self.assertIn("-nostats", cmd)
        self.assertIn("-nostdin", cmd)
        self.assertIn("-loglevel", cmd)
        self.assertIn("warning", cmd)
        self.assertIn("-rtsp_transport", cmd)
        self.assertIn("tcp", cmd)
        self.assertIn("-i", cmd)
        self.assertIn("rtsp://127.0.0.1:8554/cam", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("10.0", cmd)
        self.assertIn("-f", cmd)
        self.assertIn("null", cmd)
        self.assertIn("/dev/null", cmd)

    def test_build_ffmpeg_rtsp_cmd_with_progress_pipe(self):
        cmd = HELPERS.build_ffmpeg_rtsp_cmd(
            "/usr/bin/ffmpeg",
            "rtsp://127.0.0.1:8554/cam",
            "tcp",
            5.0,
            progress_pipe=1,
        )
        self.assertIn("-progress", cmd)
        self.assertIn("pipe:1", cmd)

    def test_build_ffmpeg_rtsp_cmd_with_extra_args(self):
        cmd = HELPERS.build_ffmpeg_rtsp_cmd(
            "/usr/bin/ffmpeg",
            "rtsp://127.0.0.1:8554/cam",
            "tcp",
            5.0,
            extra_input_args=["-thread_queue_size", "500"],
            extra_output_args=["-map", "0:v:0", "-an", "-c", "copy"],
            output_format="mpegts",
            output_target="pipe:1",
            progress_pipe=2,
        )
        self.assertIn("-thread_queue_size", cmd)
        self.assertIn("500", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("0:v:0", cmd)
        self.assertIn("-an", cmd)
        self.assertIn("-c", cmd)
        self.assertIn("copy", cmd)
        self.assertIn("mpegts", cmd)
        self.assertIn("pipe:1", cmd)
        self.assertIn("pipe:2", cmd)

    def test_build_ffmpeg_rtsp_cmd_without_nostats_nostdin(self):
        cmd = HELPERS.build_ffmpeg_rtsp_cmd(
            "/usr/bin/ffmpeg",
            "rtsp://127.0.0.1:8554/cam",
            "tcp",
            5.0,
            nostats=False,
            nostdin=False,
            loglevel="error",
        )
        self.assertNotIn("-nostats", cmd)
        self.assertNotIn("-nostdin", cmd)
        self.assertIn("error", cmd)

    def test_build_ffmpeg_rtsp_cmd_duration_as_string(self):
        cmd = HELPERS.build_ffmpeg_rtsp_cmd(
            "/usr/bin/ffmpeg", "rtsp://127.0.0.1:8554/cam", "tcp", "3.500"
        )
        self.assertIn("-t", cmd)
        self.assertIn("3.500", cmd)


if __name__ == "__main__":
    unittest.main()
