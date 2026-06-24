#!/usr/bin/env python3

import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "wyze_cam_smoke_test.py"
SPEC = importlib.util.spec_from_file_location("wyze_cam_smoke_test", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeStopFlag:
    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


class FakeNow:
    def __init__(self) -> None:
        self.start = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.start
        self.start += timedelta(seconds=1)
        return value


class TestWyzeCamSmokeTest(unittest.TestCase):
    def test_resolve_camera_specs_uses_defaults_and_alias_override(self):
        args = argparse.Namespace(
            cameras=["north-yard", "custom-cam"],
            camera_alias=["custom-cam=custom-cam-sd"],
        )

        specs = MODULE.resolve_camera_specs(args)

        self.assertEqual(
            specs,
            [
                MODULE.CameraSpec(name="north-yard", frame_alias="north-yard-sd"),
                MODULE.CameraSpec(name="custom-cam", frame_alias="custom-cam-sd"),
            ],
        )

    def test_ensure_api_key_requires_value(self):
        with self.assertRaises(SystemExit) as ctx:
            MODULE.ensure_api_key(None)

        self.assertIn("WYZE_BRIDGE_API_KEY", str(ctx.exception))

    def test_run_probe_writes_summary_with_mocked_fetchers(self):
        args = argparse.Namespace(
            duration=30,
            sample_interval=10.0,
            status_interval=15.0,
            bridge_base="http://bridge",
            go2rtc_base="http://go2rtc",
            bridge_api_key="secret",
        )
        camera_specs = [
            MODULE.CameraSpec(name="north-yard", frame_alias="north-yard-sd"),
            MODULE.CameraSpec(name="garage", frame_alias="garage-sd"),
        ]
        bridge_calls = []
        frame_calls = []

        def fake_bridge_fetcher(base: str, api_key: str, camera_name: str) -> dict:
            bridge_calls.append((base, api_key, camera_name))
            return {
                "ok": camera_name != "garage",
                "status_code": 200,
                "connected": camera_name != "garage",
                "error": None if camera_name != "garage" else "connected=false",
            }

        def fake_frame_fetcher(base: str, frame_alias: str) -> dict:
            frame_calls.append((base, frame_alias))
            return {
                "ok": frame_alias == "north-yard-sd",
                "status_code": 200 if frame_alias == "north-yard-sd" else 503,
                "byte_count": 12345 if frame_alias == "north-yard-sd" else 0,
                "error": None if frame_alias == "north-yard-sd" else "HTTP 503",
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_dir = pathlib.Path(temp_dir)
            clock = FakeClock()
            now_fn = FakeNow()
            exit_code, summary = MODULE.run_probe(
                args=args,
                camera_specs=camera_specs,
                artifact_dir=artifact_dir,
                stop_event=FakeStopFlag(),
                bridge_fetcher=fake_bridge_fetcher,
                frame_fetcher=fake_frame_fetcher,
                monotonic=clock.monotonic,
                sleep_fn=clock.sleep,
                now_fn=now_fn,
            )
            MODULE.write_summary_files(summary, artifact_dir)

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(bridge_calls), 6)
            self.assertEqual(len(frame_calls), 6)

            summary_json = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(len(summary_json["cameras"]), 2)
            north_yard = next(item for item in summary_json["cameras"] if item["name"] == "north-yard")
            garage = next(item for item in summary_json["cameras"] if item["name"] == "garage")
            self.assertEqual(north_yard["api_connected_pct"], 100.0)
            self.assertEqual(north_yard["frame_pct"], 100.0)
            self.assertEqual(garage["api_connected_pct"], 0.0)
            self.assertEqual(garage["frame_pct"], 0.0)
            self.assertEqual(garage["last_api_error"], "connected=false")
            self.assertEqual(garage["last_frame_error"], "HTTP 503")
            self.assertTrue((artifact_dir / "samples.csv").exists())


if __name__ == "__main__":
    unittest.main()
