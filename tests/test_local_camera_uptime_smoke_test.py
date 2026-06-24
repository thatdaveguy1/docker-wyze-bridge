#!/usr/bin/env python3

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "local_camera_uptime_smoke_test.py"
SPEC = importlib.util.spec_from_file_location("local_camera_uptime_smoke_test", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TestLocalCameraUptimeSmokeTest(unittest.TestCase):
    def test_build_reolink_rtsp_url_uses_substream_path(self):
        url = MODULE.build_reolink_rtsp_url("user", "p@ss word", "192.168.1.228")

        self.assertTrue(url.startswith("rtsp://user:p%40ss%20word@192.168.1.228:554/"))
        self.assertTrue(url.endswith("/h264Preview_01_sub"))

    def test_validate_args_derives_wyze_api_key_from_email(self):
        args = argparse.Namespace(
            duration_seconds=3600,
            check_interval_seconds=60,
            heartbeat_seconds=30,
            rtsp_sample_seconds=8,
            rtsp_timeout_us=3_000_000,
            wyze_bridge_api_port=5000,
            reolink_username="demo",
            reolink_password="secret",
            wyze_api_key=None,
            wyze_email="user@example.com",
        )

        MODULE.validate_args(args)

        self.assertEqual(args.wyze_api_key, MODULE.derive_wyze_api_key("user@example.com"))

    def test_parse_ffmpeg_progress_frame_count_uses_latest_frame_value(self):
        progress = "\n".join(
            [
                "frame=12",
                "fps=9.5",
                "progress=continue",
                "frame=48",
                "progress=end",
            ]
        )

        self.assertEqual(MODULE.parse_ffmpeg_progress_frame_count(progress), 48)

    def test_run_reolink_check_does_not_pass_detected_timeout_flag_to_ffmpeg(self):
        args = argparse.Namespace(
            ffmpeg="/usr/bin/ffmpeg",
            rtsp_transport="tcp",
            rtsp_timeout_us=3_000_000,
            rtsp_sample_seconds=5,
            reolink_username="demo",
            reolink_password="secret",
        )
        target = MODULE.REOLINK_CAMERAS[0]
        captured_command: list[str] = []

        def fake_run(command, **_kwargs):
            captured_command.extend(command)
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="frame=75\nprogress=end\n",
                stderr="",
            )

        with patch.object(MODULE.subprocess, "run", side_effect=fake_run):
            result = MODULE.run_reolink_check(target, args, "rw_timeout")

        self.assertTrue(result["ok"])
        self.assertEqual(result["frames"], 75)
        self.assertNotIn("-rw_timeout", captured_command)

    def test_main_writes_summary_for_successful_single_cycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = argparse.Namespace(
                duration_seconds=1,
                check_interval_seconds=1,
                heartbeat_seconds=60,
                artifact_root=temp_dir,
                ffmpeg="/usr/bin/ffmpeg",
                ffprobe="/usr/bin/ffprobe",
                rtsp_transport="tcp",
                rtsp_sample_seconds=2,
                rtsp_timeout_us=3_000_000,
                reolink_username="demo",
                reolink_password="secret",
                wyze_bridge_host="192.168.1.244",
                wyze_bridge_api_port=5000,
                wyze_api_key="bridge-key",
                wyze_email=None,
            )

            def fake_reolink(_target, _args, _timeout_flag):
                return {
                    "ok": True,
                    "latency_s": 0.25,
                    "frames": 50,
                    "error": "",
                    "redacted_url": "rtsp://<redacted>@camera",
                }

            def fake_wyze(_target, _args):
                return {
                    "ok": True,
                    "latency_s": 0.1,
                    "connected": True,
                    "http_status": 200,
                    "bytes": 4096,
                    "hash_prefix": "abc123def456",
                    "error": "",
                }

            with (
                patch.object(MODULE, "parse_args", return_value=args),
                patch.object(MODULE, "ensure_binary", side_effect=lambda path, _name: path),
                patch.object(MODULE, "detect_timeout_flag", return_value=None),
                patch.object(MODULE, "ffprobe_metadata", return_value={"ok": True, "width": 640, "height": 360}),
                patch.object(MODULE, "run_reolink_check", side_effect=fake_reolink),
                patch.object(MODULE, "run_wyze_check", side_effect=fake_wyze),
            ):
                exit_code = MODULE.main()

            self.assertEqual(exit_code, 0)

            artifact_dirs = list(pathlib.Path(temp_dir).glob("local_camera_smoke_*"))
            self.assertEqual(len(artifact_dirs), 1)
            summary_path = artifact_dirs[0] / "summary.json"
            self.assertTrue(summary_path.exists())

            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertFalse(payload["interrupted"])
            self.assertEqual(len(payload["cameras"]), len(MODULE.REOLINK_CAMERAS) + len(MODULE.WYZE_CAMERAS))
            self.assertTrue(all(camera["attempts"] >= 1 for camera in payload["cameras"]))


if __name__ == "__main__":
    unittest.main()
