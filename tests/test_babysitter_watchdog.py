#!/usr/bin/env python3
"""Tests for app/babysitter/watchdog.py — convergence rules, recovery, MQTT."""

from __future__ import annotations

import pathlib
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from babysitter.config import BabysitterConfig, CameraEntry  # noqa: E402
from babysitter.state import BabysitterState  # noqa: E402
from babysitter.watchdog import (  # noqa: E402
    STATE_ONLINE,
    STATE_RECOVERING,
    STATE_SNAPSHOT_DOWN,
    STATE_VIDEO_DOWN,
    STATE_WIFI_DOWN,
    CameraStatus,
    Watchdog,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_jpeg(size: int = 5000, seed: int = 0) -> bytes:
    """Create a minimal valid JPEG-like byte sequence with varying content."""
    body = bytes([seed % 256]) * (size - 4)
    return b"\xff\xd8" + body + b"\xff\xd9"


def _make_config(
    dry_run: bool = False,
    approved: set[str] | None = None,
    video_down_threshold: int = 120,
    snapshot_samples: int = 3,
    snapshot_stale_window: int = 600,
    cooldown: int = 900,
    max_daily: int = 3,
    recovery_wait: int = 180,
    interval: int = 60,
    mqtt_broker: str = "",
    per_camera_dry_run: dict[str, bool] | None = None,
) -> BabysitterConfig:
    return BabysitterConfig(
        scrypted_host="https://scrypted.local",
        scrypted_username="admin",
        scrypted_password="secret",
        frigate_host="http://frigate.local",
        mqtt_broker=mqtt_broker,
        reolink_username="admin",
        reolink_password="pass",
        dry_run=dry_run,
        cooldown=cooldown,
        max_daily=max_daily,
        video_down_threshold=video_down_threshold,
        snapshot_samples=snapshot_samples,
        snapshot_stale_window=snapshot_stale_window,
        recovery_wait=recovery_wait,
        interval=interval,
        cameras=[
            CameraEntry(friendly_name="doorbell", scrypted_id="101", ip="192.168.1.71", frigate_name="doorbell"),
            CameraEntry(
                friendly_name="south_driveway", scrypted_id="102", ip="192.168.1.72", frigate_name="south_driveway"
            ),
            CameraEntry(
                friendly_name="north_driveway", scrypted_id="103", ip="192.168.1.73", frigate_name="north_driveway"
            ),
        ],
        approved_cameras=approved if approved is not None else {"doorbell", "south_driveway", "north_driveway"},
        per_camera_dry_run=per_camera_dry_run or {},
    )


def _make_watchdog(
    config: BabysitterConfig | None = None,
    state: BabysitterState | None = None,
) -> Watchdog:
    config = config or _make_config()
    state = state or BabysitterState()
    return Watchdog(config, state)


class _BasePollTest(unittest.TestCase):
    """Base class that patches all external calls for poll_once tests."""

    def setUp(self) -> None:
        self.config = _make_config()
        self.state = BabysitterState()
        self.wd = _make_watchdog(self.config, self.state)

        # Default mocks.
        self.tcp_patcher = patch("babysitter.watchdog.tcp_reachable", return_value=True)
        self.mock_tcp = self.tcp_patcher.start()
        self.addCleanup(self.tcp_patcher.stop)

        self.fps_patcher = patch.object(
            self.wd.frigate,
            "camera_fps",
            return_value={"camera_fps": 10.0, "process_fps": 10.0, "skipped_fps": 0.0, "detection_fps": 0.0},
        )
        self.mock_fps = self.fps_patcher.start()
        self.addCleanup(self.fps_patcher.stop)

        self.snap_patcher = patch.object(
            self.wd.scrypted,
            "snapshot",
            return_value=_make_jpeg(),
        )
        self.mock_snap = self.snap_patcher.start()
        self.addCleanup(self.snap_patcher.stop)

        self.rtsp_patcher = patch.object(
            self.wd.frigate,
            "rtsp_inputs",
            return_value={"doorbell": "rtsp://admin:REDACTED@192.168.1.71:554/stream"},
        )
        self.mock_rtsp = self.rtsp_patcher.start()
        self.addCleanup(self.rtsp_patcher.stop)

        self.ffprobe_patcher = patch.object(
            self.wd.frigate,
            "ffprobe",
            return_value={"streams": []},
        )
        self.mock_ffprobe = self.ffprobe_patcher.start()
        self.addCleanup(self.ffprobe_patcher.stop)


# ---------------------------------------------------------------------------
# CameraStatus dataclass
# ---------------------------------------------------------------------------


class TestCameraStatus(unittest.TestCase):
    def test_defaults(self):
        s = CameraStatus(name="doorbell", state=STATE_ONLINE)
        self.assertEqual(s.name, "doorbell")
        self.assertEqual(s.state, STATE_ONLINE)
        self.assertFalse(s.snapshot_valid)
        self.assertFalse(s.tcp_reachable)
        self.assertEqual(s.reboots_today, 0)

    def test_to_dict(self):
        s = CameraStatus(name="doorbell", state=STATE_VIDEO_DOWN, camera_fps=5.0)
        d = s.to_dict()
        self.assertEqual(d["name"], "doorbell")
        self.assertEqual(d["state"], STATE_VIDEO_DOWN)
        self.assertEqual(d["camera_fps"], 5.0)


# ---------------------------------------------------------------------------
# poll_once — convergence rules
# ---------------------------------------------------------------------------


class TestPollOnline(_BasePollTest):
    def test_healthy_camera_is_online(self):
        statuses = self.wd.poll_once()
        self.assertIn("doorbell", statuses)
        s = statuses["doorbell"]
        self.assertEqual(s.state, STATE_ONLINE)
        self.assertTrue(s.tcp_reachable)
        self.assertTrue(s.snapshot_valid)
        self.assertEqual(s.camera_fps, 10.0)
        self.assertEqual(s.process_fps, 10.0)
        self.assertEqual(s.skipped_fps, 0.0)


class TestPollWifiDown(_BasePollTest):
    def test_unreachable_camera_is_wifi_down(self):
        self.mock_tcp.return_value = False
        statuses = self.wd.poll_once()
        s = statuses["doorbell"]
        self.assertEqual(s.state, STATE_WIFI_DOWN)
        self.assertFalse(s.tcp_reachable)
        # Frigate/Scrypted should not be called when TCP is down.
        self.mock_fps.assert_not_called()
        self.mock_snap.assert_not_called()
        self.assertEqual(self.state.get_camera("doorbell").current_state, STATE_WIFI_DOWN)


class TestPollSnapshotDown(_BasePollTest):
    def test_fps_healthy_snapshot_fails_is_snapshot_down(self):
        # FPS healthy but snapshot structurally fails for SNAPSHOT_SAMPLES polls.
        self.mock_snap.return_value = b""  # empty → invalid JPEG
        # Need snapshot_samples (3) consecutive bad snapshots.
        for _ in range(3):
            self.wd.poll_once()
        s = self.wd._last_status["doorbell"]
        self.assertEqual(s.state, STATE_SNAPSHOT_DOWN)
        self.assertTrue(s.camera_fps > 0)
        self.assertFalse(s.snapshot_valid)


class TestPollStaleHash(_BasePollTest):
    def test_stale_hash_warning(self):
        # Same JPEG content → same hash. Make stale window tiny so it triggers.
        self.config.snapshot_stale_window = 0
        self.wd = _make_watchdog(self.config, self.state)
        # Re-patch since we recreated the watchdog.
        with (
            patch("babysitter.watchdog.tcp_reachable", return_value=True),
            patch.object(
                self.wd.frigate,
                "camera_fps",
                return_value={"camera_fps": 10.0, "process_fps": 10.0, "skipped_fps": 0.0, "detection_fps": 0.0},
            ),
            patch.object(self.wd.scrypted, "snapshot", return_value=_make_jpeg(seed=1)),
            patch.object(self.wd.frigate, "rtsp_inputs", return_value={}),
            patch.object(self.wd.frigate, "ffprobe", return_value={"streams": []}),
        ):
            self.wd.poll_once()
            self.wd.poll_once()
            s = self.wd._last_status["doorbell"]
            self.assertTrue(s.stale_hash_warning)
            # Stale hash is a warning, state stays online.
            self.assertEqual(s.state, STATE_ONLINE)


class TestPollVideoDownConvergence(_BasePollTest):
    def test_video_down_all_signals_met(self):
        # All 5 convergence signals: TCP ok, fps zero, skipped zero,
        # snapshot bad (3 samples), ffprobe no streams, duration > threshold.
        self.mock_fps.return_value = {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0}
        self.mock_snap.return_value = b""
        self.mock_ffprobe.return_value = {"streams": []}
        # Set fps_zero_since in the past to exceed threshold.
        cam = self.state.get_camera("doorbell")
        cam.fps_zero_since = time.time() - (self.config.video_down_threshold + 10)
        # Need snapshot_samples consecutive bad snapshots.
        for _ in range(3):
            self.wd.poll_once()
        s = self.wd._last_status["doorbell"]
        self.assertEqual(s.state, STATE_VIDEO_DOWN)
        self.assertEqual(s.skipped_fps, 0.0)

    def test_video_down_skipped_fps_disqualifies(self):
        # skipped_fps > 0 → decoder backlog, not a camera wedge.
        self.mock_fps.return_value = {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 5.0, "detection_fps": 0.0}
        self.mock_snap.return_value = b""
        cam = self.state.get_camera("doorbell")
        cam.fps_zero_since = time.time() - (self.config.video_down_threshold + 10)
        for _ in range(3):
            self.wd.poll_once()
        s = self.wd._last_status["doorbell"]
        self.assertNotEqual(s.state, STATE_VIDEO_DOWN)

    def test_video_down_ffprobe_stream_ok_is_recovering(self):
        # ffprobe says stream is fine → not video_down, recovering.
        self.mock_fps.return_value = {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0}
        self.mock_snap.return_value = b""
        self.mock_ffprobe.return_value = {"streams": [{"codec_type": "video"}]}
        cam = self.state.get_camera("doorbell")
        cam.fps_zero_since = time.time() - (self.config.video_down_threshold + 10)
        for _ in range(3):
            self.wd.poll_once()
        s = self.wd._last_status["doorbell"]
        self.assertEqual(s.state, STATE_RECOVERING)

    def test_video_down_threshold_not_met_is_recovering(self):
        # FPS zero but duration < threshold → recovering, not video_down.
        self.mock_fps.return_value = {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0}
        self.mock_snap.return_value = b""
        cam = self.state.get_camera("doorbell")
        cam.fps_zero_since = time.time() - 10  # well under 120s threshold
        for _ in range(3):
            self.wd.poll_once()
        s = self.wd._last_status["doorbell"]
        self.assertEqual(s.state, STATE_RECOVERING)


# ---------------------------------------------------------------------------
# check_and_recover — guards
# ---------------------------------------------------------------------------


class _BaseRecoverTest(unittest.TestCase):
    """Base class for check_and_recover tests — pre-seeds a video_down status."""

    def setUp(self) -> None:
        self.config = _make_config()
        self.state = BabysitterState()
        self.wd = _make_watchdog(self.config, self.state)
        # Seed a video_down status for "doorbell".
        self.wd._last_status["doorbell"] = CameraStatus(
            name="doorbell",
            state=STATE_VIDEO_DOWN,
            camera_fps=0.0,
            process_fps=0.0,
            skipped_fps=0.0,
            snapshot_valid=False,
            tcp_reachable=True,
            cgi_reachable=True,
        )

    def _patch_reboot(self, return_value: bool = True) -> MagicMock:
        mock = MagicMock(return_value=return_value)
        self.wd.onvif["doorbell"].reboot_with_retry = mock  # type: ignore[assignment]
        return mock


class TestCheckAndRecoverVideoDown(_BaseRecoverTest):
    @patch("babysitter.watchdog.tcp_reachable", return_value=True)
    @patch.object(Watchdog, "_wait_for_recovery", return_value=30.0)
    def test_all_signals_met_triggers_reboot(self, _mock_wait, _mock_tcp):
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNotNone(event)
        self.assertEqual(event.outcome, "success")  # type: ignore[union-attr]
        self.assertEqual(event.action, "onvif")  # type: ignore[union-attr]
        mock_reboot.assert_called_once()
        cam = self.state.get_camera("doorbell")
        self.assertGreater(cam.last_reboot, 0)
        self.assertEqual(len(cam.reboot_times), 1)


class TestCheckAndRecoverSkippedFps(_BaseRecoverTest):
    def test_skipped_fps_disqualifies(self):
        # Status already video_down but we simulate skipped_fps > 0 by
        # checking the guard logic: state must be video_down to proceed.
        # Since state is video_down, the skipped_fps guard is in poll_once,
        # not check_and_recover. Here we verify a non-video_down state skips.
        self.wd._last_status["doorbell"].state = STATE_RECOVERING
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()


class TestCheckAndRecoverTcpUnreachable(_BaseRecoverTest):
    def test_tcp_unreachable_skips_reboot(self):
        self.wd._last_status["doorbell"].tcp_reachable = False
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()


class TestCheckAndRecoverOnvifUnreachable(_BaseRecoverTest):
    def test_onvif_unreachable_skips_reboot(self):
        self.wd._last_status["doorbell"].cgi_reachable = False
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()


class TestCheckAndRecoverUnapproved(_BaseRecoverTest):
    def test_unapproved_camera_skips_reboot(self):
        self.config.approved_cameras = set()  # no cameras approved
        self.wd.config = self.config
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()


class TestCheckAndRecoverDryRun(_BaseRecoverTest):
    def test_global_dry_run_skips_reboot(self):
        self.config.dry_run = True
        self.wd.config = self.config
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()

    def test_per_camera_dry_run_skips_reboot(self):
        self.config.per_camera_dry_run = {"doorbell": True}
        self.wd.config = self.config
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()


class TestCheckAndRecoverCooldown(_BaseRecoverTest):
    def test_in_cooldown_skips_reboot(self):
        cam = self.state.get_camera("doorbell")
        cam.last_reboot = time.time()  # just rebooted → in cooldown
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()


class TestCheckAndRecoverMaxDaily(_BaseRecoverTest):
    def test_max_daily_reached_skips_reboot(self):
        cam = self.state.get_camera("doorbell")
        now = time.time()
        cam.reboot_times = [now - 100, now - 200, now - 300]  # 3 reboots today
        mock_reboot = self._patch_reboot(True)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNone(event)
        mock_reboot.assert_not_called()


class TestCheckAndRebootFailure(_BaseRecoverTest):
    @patch("babysitter.watchdog.tcp_reachable", return_value=True)
    def test_reboot_failure_records_failed_event(self, _mock_tcp):
        mock_reboot = self._patch_reboot(False)
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNotNone(event)
        self.assertEqual(event.outcome, "failed")  # type: ignore[union-attr]
        mock_reboot.assert_called_once()
        cam = self.state.get_camera("doorbell")
        # Failed reboots don't count toward daily limit.
        self.assertEqual(len(cam.reboot_times), 0)

    @patch("babysitter.watchdog.tcp_reachable", return_value=True)
    def test_reboot_exception_records_failed_event(self, _mock_tcp):
        mock_reboot = MagicMock(side_effect=RuntimeError("connection refused"))
        self.wd.onvif["doorbell"].reboot_with_retry = mock_reboot  # type: ignore[assignment]
        event = self.wd.check_and_recover("doorbell")
        self.assertIsNotNone(event)
        self.assertEqual(event.outcome, "failed")  # type: ignore[union-attr]


class TestRecoveryWait(unittest.TestCase):
    @patch("babysitter.watchdog.time.sleep")
    def test_recovery_wait_polls_until_fps_positive(self, _mock_sleep):
        config = _make_config(recovery_wait=180)
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        entry = config.cameras[0]
        # First two polls return 0, third returns > 0.
        wd.frigate.camera_fps = MagicMock(
            side_effect=[
                {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0},
                {"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0},
                {"camera_fps": 5.0, "process_fps": 5.0, "skipped_fps": 0.0, "detection_fps": 0.0},
            ]
        )
        waited = wd._wait_for_recovery("doorbell", entry)
        self.assertGreater(waited, 0)

    @patch("babysitter.watchdog.time.sleep")
    def test_recovery_wait_times_out(self, _mock_sleep):
        config = _make_config(recovery_wait=1)
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        entry = config.cameras[0]
        wd.frigate.camera_fps = MagicMock(
            return_value={"camera_fps": 0.0, "process_fps": 0.0, "skipped_fps": 0.0, "detection_fps": 0.0},
        )
        # Force time to advance past the deadline.
        with patch("babysitter.watchdog.time.time", side_effect=[1000.0, 1000.0, 2000.0, 2000.0]):
            wd._wait_for_recovery("doorbell", entry)
        # Timeout reached without reporting recovery.


# ---------------------------------------------------------------------------
# run_cycle — sequential processing
# ---------------------------------------------------------------------------


class TestRunCycle(unittest.TestCase):
    def test_sequential_processing_multiple_down(self):
        config = _make_config()
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        # Seed two cameras as video_down.
        wd._last_status["doorbell"] = CameraStatus(
            name="doorbell", state=STATE_VIDEO_DOWN, tcp_reachable=True, cgi_reachable=True
        )
        wd._last_status["south_driveway"] = CameraStatus(
            name="south_driveway", state=STATE_VIDEO_DOWN, tcp_reachable=True, cgi_reachable=True
        )

        reboot_calls: list[str] = []

        def _make_reboot(name: str):
            def _reboot():
                reboot_calls.append(name)
                return True

            return _reboot

        wd.onvif["doorbell"].reboot_with_retry = _make_reboot("doorbell")  # type: ignore[assignment]
        wd.onvif["south_driveway"].reboot_with_retry = _make_reboot("south_driveway")  # type: ignore[assignment]

        # Mock poll_once to return our seeded statuses.
        with (
            patch.object(wd, "poll_once", return_value=wd._last_status),
            patch.object(wd, "_wait_for_recovery", return_value=0.0),
            patch("babysitter.watchdog.tcp_reachable", return_value=True),
            patch("babysitter.state.save_state"),
        ):
            summary = wd.run_cycle()

        self.assertEqual(len(summary["actions"]), 2)
        # Both reboots happened sequentially.
        self.assertEqual(len(reboot_calls), 2)
        self.assertIn("doorbell", reboot_calls)
        self.assertIn("south_driveway", reboot_calls)

    def test_run_cycle_no_action_when_all_online(self):
        config = _make_config()
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        wd._last_status["doorbell"] = CameraStatus(name="doorbell", state=STATE_ONLINE)
        with patch.object(wd, "poll_once", return_value=wd._last_status), patch("babysitter.state.save_state"):
            summary = wd.run_cycle()
        self.assertEqual(len(summary["actions"]), 0)


# ---------------------------------------------------------------------------
# MQTT publishing
# ---------------------------------------------------------------------------


class TestMqttPublishing(unittest.TestCase):
    def test_mqtt_publish_status(self):
        config = _make_config(mqtt_broker="mqtt.local")
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        with patch.object(wd.mqtt, "_client") as mock_client, patch.object(wd.mqtt, "_connect", return_value=True):
            wd.mqtt.publish_status("doorbell", {"state": "online"})
            mock_client.publish.assert_called_once()
            topic = mock_client.publish.call_args[0][0]
            self.assertIn("doorbell", topic)
            self.assertIn("status", topic)

    def test_mqtt_publish_reboot_event(self):
        config = _make_config(mqtt_broker="mqtt.local")
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        with patch.object(wd.mqtt, "_client") as mock_client, patch.object(wd.mqtt, "_connect", return_value=True):
            wd.mqtt.publish_reboot_event("doorbell", {"outcome": "success"})
            mock_client.publish.assert_called_once()
            topic = mock_client.publish.call_args[0][0]
            self.assertIn("doorbell", topic)
            self.assertIn("reboot", topic)

    def test_mqtt_connection_failure_does_not_crash(self):
        config = _make_config(mqtt_broker="mqtt.local")
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        with patch.object(wd.mqtt, "_ensure_client", return_value=None):
            # Should not raise.
            wd.mqtt.publish_status("doorbell", {"state": "online"})

    def test_mqtt_publish_failure_does_not_crash(self):
        config = _make_config(mqtt_broker="mqtt.local")
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        mock_client = MagicMock()
        mock_client.publish.side_effect = RuntimeError("broker down")
        with (
            patch.object(wd.mqtt, "_ensure_client", return_value=mock_client),
            patch.object(wd.mqtt, "_connect", return_value=True),
        ):
            # Should not raise.
            wd.mqtt.publish_status("doorbell", {"state": "online"})

    def test_no_mqtt_when_broker_empty(self):
        config = _make_config(mqtt_broker="")
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        self.assertIsNone(wd.mqtt)


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


class TestBackgroundLoop(unittest.TestCase):
    def test_start_and_stop_background(self):
        config = _make_config(interval=1)
        state = BabysitterState()
        wd = _make_watchdog(config, state)
        with patch.object(wd, "run_cycle") as mock_cycle, patch("babysitter.state.save_state"):
            wd.start_background(interval=1)
            time.sleep(0.3)
            wd.stop()
        mock_cycle.assert_called()


if __name__ == "__main__":
    unittest.main()
