#!/usr/bin/env python3

import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "local_camera_smoke_test.py"
SPEC = importlib.util.spec_from_file_location("local_camera_smoke_test", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TestLocalCameraSmokeTest(unittest.TestCase):
    def test_build_reolink_command_includes_requested_overrides(self):
        args = argparse.Namespace(
            duration=3600,
            status_interval=45.0,
            artifact_root="tmp",
            test_name="deco-a",
            reolink_camera=["doorbell"],
            reolink_camera_url=["doorbell=rtsp://user:pass@example/stream"],
            reolink_camera_ip=["doorbell=192.0.2.69"],
        )

        command = MODULE.build_reolink_command(args)
        redacted = MODULE.build_reolink_redacted_command(command)

        self.assertIn("--duration", command)
        self.assertIn("3600", command)
        self.assertIn("--camera", command)
        self.assertIn("doorbell", command)
        joined = " ".join(redacted)
        self.assertIn("rtsp://<redacted>@example/stream", joined)
        self.assertNotIn("user:pass", joined)

    def test_build_wyze_command_uses_local_probe_and_no_api_key_cli_arg(self):
        args = argparse.Namespace(
            duration=1800,
            wyze_sample_interval=20.0,
            status_interval=30.0,
            artifact_root="tmp",
            bridge_base="http://192.0.2.10:5000",
            go2rtc_base="http://192.0.2.10:1984",
            test_name="asus-b",
            wyze_camera=["north-yard"],
            wyze_camera_alias=["north-yard=north-yard-sd"],
        )

        command = MODULE.build_wyze_command(args)

        self.assertIn("wyze_cam_smoke_test.py", " ".join(command))
        self.assertIn("--camera", command)
        self.assertIn("--camera-alias", command)
        self.assertNotIn("--bridge-api-key", command)

    def test_write_summary_files_points_to_child_artifacts(self):
        run_summary = {
            "started_at": "2026-06-08T12:00:00+00:00",
            "ended_at": "2026-06-08T13:00:00+00:00",
            "duration_s": 3600,
            "actual_duration_s": 3600.0,
            "heartbeat_interval_s": 60.0,
            "interrupted": False,
            "interrupted_signal": None,
            "children": [
                {
                    "name": "reolink",
                    "returncode": 0,
                    "artifact_dir": "tmp/reolink_run",
                    "summary_json": "tmp/reolink_run/summary.json",
                    "summary_txt": "tmp/reolink_run/summary.txt",
                    "command_redacted": ["python3", "scripts/reolink_direct_stability_probe.py"],
                },
                {
                    "name": "wyze",
                    "returncode": 0,
                    "artifact_dir": "tmp/wyze_run",
                    "summary_json": "tmp/wyze_run/summary.json",
                    "summary_txt": "tmp/wyze_run/summary.txt",
                    "command_redacted": ["python3", "scripts/wyze_cam_smoke_test.py"],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_dir = pathlib.Path(temp_dir)
            MODULE.write_summary_files(run_summary, artifact_dir)

            payload = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["children"][0]["artifact_dir"], "tmp/reolink_run")
            self.assertEqual(payload["children"][1]["artifact_dir"], "tmp/wyze_run")
            summary_txt = (artifact_dir / "summary.txt").read_text(encoding="utf-8")
            self.assertIn("summary_json=tmp/reolink_run/summary.json", summary_txt)
            self.assertIn("summary_json=tmp/wyze_run/summary.json", summary_txt)


if __name__ == "__main__":
    unittest.main()
