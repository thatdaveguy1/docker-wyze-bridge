#!/usr/bin/env python3

import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "reolink_direct_stability_probe.py"
SPEC = importlib.util.spec_from_file_location("reolink_direct_stability_probe", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_summary(camera, started_at: str, ended_at: str) -> dict:
    return {
        "label": camera.label,
        "url": camera.redacted_url,
        "url_source": camera.url_source,
        "metadata": {
            "ok": False,
            "returncode": 1,
            "stderr": "ffprobe failed",
        },
        "attempts": 1,
        "restarts": 0,
        "exit_codes": [1],
        "startup_latency_s": None,
        "elapsed_s": 0.0,
        "streaming_seconds": 0.0,
        "streaming_pct": 0.0,
        "coverage_pct": 0.0,
        "frames_total": 0,
        "bytes_total": 0,
        "wall_avg_fps": 0.0,
        "active_avg_fps": None,
        "wall_avg_kbps": 0.0,
        "active_avg_kbps": None,
        "window_fps": {"min": None, "median": None, "p95": None, "max": None},
        "window_kbps": {"min": None, "median": None, "p95": None, "max": None},
        "ffmpeg_reported_fps": {"median": None, "p95": None},
        "dropouts": {"count": 0, "total_s": 0.0, "longest_s": None, "events": []},
        "recent_errors": [],
        "artifacts": {
            "samples_csv": "samples.csv",
            "ffmpeg_log": "ffmpeg.log",
        },
        "started_at": started_at,
        "ended_at": ended_at,
    }


class TestReolinkDirectStabilityProbe(unittest.TestCase):
    def test_main_writes_summary_when_camera_worker_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = argparse.Namespace(
                ffmpeg="/usr/bin/ffmpeg",
                ffprobe="/usr/bin/ffprobe",
                artifact_root=temp_dir,
                test_name="worker-failure",
                duration=5,
                transport="tcp",
                dropout_threshold=3.0,
                status_interval=0.0,
                rw_timeout_us=1,
                restart_delay=0.1,
                cameras=None,
                camera_url=[],
                camera_ip=[],
                username=None,
                password=None,
            )
            cameras = [
                MODULE.CameraConfig(
                    label="south_driveway",
                    url="rtsp://south",
                    url_source="explicit_url",
                    redacted_url="rtsp://<redacted>@south",
                ),
                MODULE.CameraConfig(
                    label="north_driveway",
                    url="rtsp://north",
                    url_source="explicit_url",
                    redacted_url="rtsp://<redacted>@north",
                ),
            ]

            def fake_probe_camera(camera, args, ffprobe_timeout_flag, artifact_dir, stop_event):
                if camera.label == "north_driveway":
                    raise RuntimeError("simulated worker failure")
                return make_summary(
                    camera=camera,
                    started_at="2026-05-28T12:00:00+00:00",
                    ended_at="2026-05-28T12:00:05+00:00",
                )

            with (
                patch.object(MODULE, "parse_args", return_value=args),
                patch.object(MODULE, "ensure_binary", side_effect=lambda path, _name: path),
                patch.object(MODULE, "resolve_camera_configs", return_value=cameras),
                patch.object(MODULE, "detect_timeout_flag", return_value=None),
                patch.object(MODULE, "probe_camera", side_effect=fake_probe_camera),
            ):
                exit_code = MODULE.main()

            self.assertEqual(exit_code, 0)
            artifacts = list(pathlib.Path(temp_dir).glob("reolink_direct_stability_worker-failure_*"))
            self.assertEqual(len(artifacts), 1)
            artifact_dir = artifacts[0]

            summary_json = artifact_dir / "summary.json"
            summary_txt = artifact_dir / "summary.txt"
            self.assertTrue(summary_json.exists())
            self.assertTrue(summary_txt.exists())

            payload = json.loads(summary_json.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["cameras"]), 2)
            failed_camera = next(camera for camera in payload["cameras"] if camera["label"] == "north_driveway")
            self.assertEqual(failed_camera["fatal_error"], "RuntimeError: simulated worker failure")
            self.assertEqual(failed_camera["coverage_pct"], 0.0)

    def test_main_writes_partial_summary_when_signal_requests_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = argparse.Namespace(
                ffmpeg="/usr/bin/ffmpeg",
                ffprobe="/usr/bin/ffprobe",
                artifact_root=temp_dir,
                test_name="interrupted-run",
                duration=3600,
                transport="tcp",
                dropout_threshold=3.0,
                status_interval=0.0,
                rw_timeout_us=1,
                restart_delay=0.1,
                cameras=None,
                camera_url=[],
                camera_ip=[],
                username=None,
                password=None,
            )
            cameras = [
                MODULE.CameraConfig(
                    label="south_driveway",
                    url="rtsp://south",
                    url_source="explicit_url",
                    redacted_url="rtsp://<redacted>@south",
                ),
                MODULE.CameraConfig(
                    label="north_driveway",
                    url="rtsp://north",
                    url_source="explicit_url",
                    redacted_url="rtsp://<redacted>@north",
                ),
            ]
            handlers = {}

            def fake_signal(sig, handler):
                previous = handlers.get(sig, 0)
                handlers[sig] = handler
                return previous

            def fake_probe_camera(camera, args, ffprobe_timeout_flag, artifact_dir, stop_event):
                if camera.label == "south_driveway":
                    handlers[MODULE.signal.SIGTERM](MODULE.signal.SIGTERM, None)
                return make_summary(
                    camera=camera,
                    started_at="2026-05-28T12:00:00+00:00",
                    ended_at="2026-05-28T12:09:00+00:00",
                )

            with (
                patch.object(MODULE, "parse_args", return_value=args),
                patch.object(MODULE, "ensure_binary", side_effect=lambda path, _name: path),
                patch.object(MODULE, "resolve_camera_configs", return_value=cameras),
                patch.object(MODULE, "detect_timeout_flag", return_value=None),
                patch.object(MODULE, "probe_camera", side_effect=fake_probe_camera),
                patch.object(MODULE.signal, "signal", side_effect=fake_signal),
            ):
                exit_code = MODULE.main()

            self.assertEqual(exit_code, 130)
            artifacts = list(pathlib.Path(temp_dir).glob("reolink_direct_stability_interrupted-run_*"))
            self.assertEqual(len(artifacts), 1)
            payload = json.loads((artifacts[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["interrupted"])
            self.assertEqual(payload["interrupted_signal"], "SIGTERM")
            self.assertEqual(len(payload["cameras"]), 2)


if __name__ == "__main__":
    unittest.main()
