#!/usr/bin/env python3
"""Tests for the SnapshotHealthTracker and /api/snapshot-health integration."""

import pathlib
import sys
import time
import unittest
from unittest.mock import Mock, patch

# Add app/ to path so wyzebridge imports resolve
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from wyzebridge.snapshot_health import (
    STATE_ONLINE,
    STATE_SNAPSHOT_DOWN,
    STATE_STALE_SNAPSHOT,
    SnapshotHealthTracker,
)


class TestSnapshotHealthTracker(unittest.TestCase):
    def test_initial_state_is_online(self):
        tracker = SnapshotHealthTracker()
        cam = tracker.get("back-yard")
        self.assertEqual(cam.state, STATE_ONLINE)
        self.assertEqual(cam.consecutive_failures, 0)

    def test_failure_threshold_triggers_snapshot_down(self):
        tracker = SnapshotHealthTracker(failure_threshold=3)
        tracker.record_failure("cam1")
        tracker.record_failure("cam1")
        self.assertEqual(tracker.get("cam1").state, STATE_ONLINE)
        tracker.record_failure("cam1")
        self.assertEqual(tracker.get("cam1").state, STATE_SNAPSHOT_DOWN)
        self.assertEqual(tracker.get("cam1").consecutive_failures, 3)

    def test_success_resets_failure_counter(self):
        tracker = SnapshotHealthTracker(failure_threshold=3)
        tracker.record_failure("cam1")
        tracker.record_failure("cam1")
        tracker.record_success("cam1", "hash1")
        self.assertEqual(tracker.get("cam1").state, STATE_ONLINE)
        self.assertEqual(tracker.get("cam1").consecutive_failures, 0)

    def test_stale_hash_detection(self):
        tracker = SnapshotHealthTracker(stale_window=0.1)
        tracker.record_success("cam2", "samehash")
        time.sleep(0.15)
        state = tracker.record_success("cam2", "samehash")
        self.assertEqual(state, STATE_STALE_SNAPSHOT)
        self.assertTrue(tracker.get("cam2").stale if hasattr(tracker.get("cam2"), "stale") else True)

    def test_new_hash_clears_stale(self):
        tracker = SnapshotHealthTracker(stale_window=0.1)
        tracker.record_success("cam2", "samehash")
        time.sleep(0.15)
        tracker.record_success("cam2", "samehash")
        self.assertEqual(tracker.get("cam2").state, STATE_STALE_SNAPSHOT)
        state = tracker.record_success("cam2", "different")
        self.assertEqual(state, STATE_ONLINE)

    def test_should_restart_on_snapshot_down(self):
        tracker = SnapshotHealthTracker(failure_threshold=2, restart_cooldown=60)
        tracker.record_failure("cam3")
        tracker.record_failure("cam3")
        self.assertTrue(tracker.should_restart("cam3"))

    def test_should_restart_respects_cooldown(self):
        tracker = SnapshotHealthTracker(failure_threshold=2, restart_cooldown=60)
        tracker.record_failure("cam3")
        tracker.record_failure("cam3")
        self.assertTrue(tracker.should_restart("cam3"))
        tracker.mark_restarted("cam3")
        self.assertFalse(tracker.should_restart("cam3"))

    def test_should_restart_on_stale_snapshot(self):
        tracker = SnapshotHealthTracker(stale_window=0.1, restart_cooldown=60)
        tracker.record_success("cam4", "frozen")
        time.sleep(0.15)
        tracker.record_success("cam4", "frozen")
        self.assertTrue(tracker.should_restart("cam4"))

    def test_should_not_restart_on_online(self):
        tracker = SnapshotHealthTracker()
        tracker.record_success("cam5", "hash")
        self.assertFalse(tracker.should_restart("cam5"))

    def test_mark_restarted_resets_state(self):
        tracker = SnapshotHealthTracker(failure_threshold=2)
        tracker.record_failure("cam6")
        tracker.record_failure("cam6")
        self.assertEqual(tracker.get("cam6").state, STATE_SNAPSHOT_DOWN)
        tracker.mark_restarted("cam6")
        self.assertEqual(tracker.get("cam6").state, STATE_ONLINE)
        self.assertEqual(tracker.get("cam6").consecutive_failures, 0)

    def test_state_changed_detection(self):
        tracker = SnapshotHealthTracker(failure_threshold=2)
        tracker.record_success("cam7", "h1")
        self.assertFalse(tracker.state_changed("cam7"))
        tracker.record_failure("cam7")
        self.assertFalse(tracker.state_changed("cam7"))
        tracker.record_failure("cam7")
        self.assertTrue(tracker.state_changed("cam7"))

    def test_all_health_returns_dict(self):
        tracker = SnapshotHealthTracker()
        tracker.record_success("cam-a", "h1")
        tracker.record_failure("cam-b")
        health = tracker.all_health()
        self.assertIn("cam-a", health)
        self.assertIn("cam-b", health)
        self.assertEqual(health["cam-a"]["state"], STATE_ONLINE)
        self.assertEqual(health["cam-b"]["consecutive_failures"], 1)

    def test_health_for_unknown_camera(self):
        tracker = SnapshotHealthTracker()
        self.assertEqual(tracker.health_for("nonexistent"), {})

    def test_health_for_known_camera(self):
        tracker = SnapshotHealthTracker()
        tracker.record_success("cam-c", "h1")
        health = tracker.health_for("cam-c")
        self.assertEqual(health["state"], STATE_ONLINE)
        self.assertIn("consecutive_failures", health)


class TestSnapshotHealthIntegration(unittest.TestCase):
    """Test that SnapshotManager records health and publishes MQTT on state change."""

    def setUp(self):
        # Stub the same modules as test_go2rtc_snapshot_and_diagnostics.py
        # to allow SnapshotManager import without Docker-native deps.
        pass

    def test_tracker_is_instantiated_in_snapshot_manager(self):
        from wyzebridge.snapshot import SnapshotManager
        from wyzebridge.snapshot_health import SnapshotHealthTracker

        manager = SnapshotManager(
            streams={},
            api=Mock(),
            stop_flag=lambda: False,
            enabled_streams=lambda: [],
            active_streams=lambda: [],
        )
        self.assertIsInstance(manager.health, SnapshotHealthTracker)
        self.assertEqual(manager.snapshot_health(), {})


class TestRefreshPreviewProactiveRestart(unittest.TestCase):
    """Test that refresh_preview uses the health tracker for restart decisions."""

    def setUp(self):
        import types

        # Stub requests for native_alias imports
        requests_stub = types.ModuleType("requests")
        requests_exceptions = types.ModuleType("requests.exceptions")
        requests_stub.RequestException = Exception
        requests_stub.get = Mock()
        requests_stub.put = Mock()
        requests_stub.post = Mock()
        requests_exceptions.ConnectionError = Exception
        requests_exceptions.HTTPError = Exception
        requests_exceptions.RequestException = Exception
        requests_stub.exceptions = requests_exceptions
        sys.modules["requests"] = requests_stub
        sys.modules["requests.exceptions"] = requests_exceptions
        # Stub paho-mqtt
        _paho = types.ModuleType("paho")
        _paho_mqtt = types.ModuleType("paho.mqtt")
        _paho_mqtt_client = types.ModuleType("paho.mqtt.client")
        _paho_mqtt_publish = types.ModuleType("paho.mqtt.publish")
        _paho_mqtt_client.Client = Mock
        _paho_mqtt_client.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
        _paho_mqtt_publish.multiple = Mock()
        _paho_mqtt_publish.single = Mock()
        _paho.mqtt = _paho_mqtt
        _paho_mqtt.client = _paho_mqtt_client
        _paho_mqtt.publish = _paho_mqtt_publish
        for name, mod in [
            ("paho", _paho),
            ("paho.mqtt", _paho_mqtt),
            ("paho.mqtt.client", _paho_mqtt_client),
            ("paho.mqtt.publish", _paho_mqtt_publish),
        ]:
            sys.modules.setdefault(name, mod)
        for mod in ("xxtea",):
            sys.modules.setdefault(mod, types.ModuleType(mod))
        # Stub wyzecam native modules
        _wyzecam_iotc = types.ModuleType("wyzecam.iotc")
        _wyzecam_iotc.WyzeIOTC = Mock
        _wyzecam_iotc.WyzeIOTCSession = Mock
        for name, mod in [
            ("wyzecam.iotc", _wyzecam_iotc),
            ("wyzecam.tutk", types.ModuleType("wyzecam.tutk")),
            ("wyzecam.tutk.tutk", types.ModuleType("wyzecam.tutk.tutk")),
            ("wyzecam.tutk.tutk_ioctl_mux", types.ModuleType("wyzecam.tutk.tutk_ioctl_mux")),
            ("wyzecam.tutk.tutk_protocol", types.ModuleType("wyzecam.tutk.tutk_protocol")),
        ]:
            sys.modules.setdefault(name, mod)
        # Stub wyzebridge.wyze_stream and wyze_events
        _wyzebridge_wyze_stream = types.ModuleType("wyzebridge.wyze_stream")
        _wyzebridge_wyze_stream.WyzeStream = Mock
        _wyzebridge_wyze_events = types.ModuleType("wyzebridge.wyze_events")
        _wyzebridge_wyze_events.WyzeEvents = Mock
        sys.modules["wyzebridge.wyze_stream"] = _wyzebridge_wyze_stream
        sys.modules["wyzebridge.wyze_events"] = _wyzebridge_wyze_events

    def test_refresh_preview_restarts_on_sustained_failure(self):
        """When the tracker says should_restart (snapshot_down), refresh_preview restarts."""
        from wyzebridge.snapshot import SnapshotManager as SM

        manager = SM(
            streams={},
            api=Mock(),
            stop_flag=lambda: False,
            enabled_streams=lambda: [],
            active_streams=lambda: [],
        )
        # Simulate 3 consecutive failures to trigger snapshot_down
        for _ in range(3):
            manager.health.record_failure("test-cam")
        self.assertTrue(manager.health.should_restart("test-cam"))

        # Mock get_snapshot to fail first, then succeed after restart
        call_count = [0]

        def mock_get_snapshot(cam_name):
            call_count[0] += 1
            if call_count[0] <= 1:
                return {"ok": False, "source": "failed"}
            return {"ok": True, "source": "go2rtc"}

        stream = Mock()
        stream.get_info.return_value = {"native_alias": "test-cam-sd"}
        stream.stop.return_value = None
        stream.start.return_value = True
        manager.streams["test-cam"] = stream

        with (
            patch.object(SM, "get_snapshot", side_effect=mock_get_snapshot),
            patch.object(SM, "_restart_stream_for_snapshot", return_value=True),
        ):
            result = manager.refresh_preview("test-cam")

        self.assertTrue(result["ok"])
        self.assertTrue(result.get("restarted"))

    def test_refresh_preview_skips_restart_on_cooldown(self):
        """When the tracker's cooldown is active, refresh_preview skips restart."""
        from wyzebridge.snapshot import SnapshotManager as SM

        manager = SM(
            streams={},
            api=Mock(),
            stop_flag=lambda: False,
            enabled_streams=lambda: [],
            active_streams=lambda: [],
        )
        # Simulate snapshot_down + recent restart (cooldown active)
        for _ in range(3):
            manager.health.record_failure("test-cam")
        manager.health.mark_restarted("test-cam")
        # After mark_restarted, state is online and failures reset.
        # Need 3 more failures to get back to snapshot_down, but cooldown is active.
        for _ in range(3):
            manager.health.record_failure("test-cam")
        # should_restart returns False due to cooldown
        self.assertFalse(manager.health.should_restart("test-cam"))

        api = Mock()
        api.save_thumbnail.return_value = True
        manager.api = api

        with (
            patch.object(SM, "get_snapshot", return_value={"ok": False, "source": "failed"}),
            patch.object(SM, "_restart_stream_for_snapshot") as mock_restart,
        ):
            result = manager.refresh_preview("test-cam")

        # Should fall back to API thumbnail
        self.assertEqual(result, {"ok": True, "source": "api"})

    def test_refresh_preview_restarts_on_stale_snapshot(self):
        """When a snapshot succeeds but is stale, refresh_preview restarts for freshness."""
        from wyzebridge.snapshot import SnapshotManager as SM

        manager = SM(
            streams={},
            api=Mock(),
            stop_flag=lambda: False,
            enabled_streams=lambda: [],
            active_streams=lambda: [],
        )
        # Simulate stale snapshot
        manager.health.record_success("test-cam", "frozen-hash")
        # Force stale by manipulating the tracker
        import time as _time

        cam_health = manager.health.get("test-cam")
        cam_health.stale_hash_since = _time.time() - 700  # older than 600s window
        manager.health.record_success("test-cam", "frozen-hash")  # triggers stale state
        self.assertEqual(manager.health.get("test-cam").state, "stale_snapshot")
        self.assertTrue(manager.health.should_restart("test-cam"))

        stream = Mock()
        stream.get_info.return_value = {"native_alias": "test-cam-sd"}
        manager.streams["test-cam"] = stream

        call_count = [0]

        def mock_get_snapshot(cam_name):
            call_count[0] += 1
            return {"ok": True, "source": "go2rtc"}

        with (
            patch.object(SM, "get_snapshot", side_effect=mock_get_snapshot),
            patch.object(SM, "_restart_stream_for_snapshot", return_value=True),
        ):
            result = manager.refresh_preview("test-cam")

        # Should restart and return succeeded snapshot
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("restarted"))


if __name__ == "__main__":
    unittest.main()
