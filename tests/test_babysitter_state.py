#!/usr/bin/env python3
"""Tests for app/babysitter/state.py — atomic writes, cooldown, daily counts, history."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from babysitter.state import (  # noqa: E402
    BabysitterState,
    CameraState,
    RebootEvent,
    can_reboot,
    cooldown_remaining,
    daily_reboot_count,
    is_in_cooldown,
    load_state,
    prune_old_reboots,
    record_reboot,
    save_state,
)


class TestCooldown(unittest.TestCase):
    def test_not_in_cooldown_initially(self):
        cam = CameraState()
        self.assertFalse(is_in_cooldown(cam, 900))

    def test_in_cooldown_after_reboot(self):
        cam = CameraState(last_reboot=time.time())
        self.assertTrue(is_in_cooldown(cam, 900))

    def test_cooldown_expired(self):
        cam = CameraState(last_reboot=time.time() - 1000)
        self.assertFalse(is_in_cooldown(cam, 900))

    def test_cooldown_remaining(self):
        cam = CameraState(last_reboot=time.time() - 100)
        remaining = cooldown_remaining(cam, 900)
        self.assertGreater(remaining, 700)
        self.assertLess(remaining, 850)

    def test_cooldown_remaining_zero_when_expired(self):
        cam = CameraState(last_reboot=time.time() - 1000)
        self.assertEqual(cooldown_remaining(cam, 900), 0)


class TestDailyRebootCount(unittest.TestCase):
    def test_empty(self):
        cam = CameraState()
        self.assertEqual(daily_reboot_count(cam), 0)

    def test_count_recent(self):
        now = time.time()
        cam = CameraState(reboot_times=[now - 100, now - 200, now - 300])
        self.assertEqual(daily_reboot_count(cam), 3)

    def test_prune_old(self):
        now = time.time()
        cam = CameraState(reboot_times=[now - 100, now - 100000])
        prune_old_reboots(cam)
        self.assertEqual(len(cam.reboot_times), 1)

    def test_count_excludes_old(self):
        now = time.time()
        cam = CameraState(reboot_times=[now - 100, now - 100000])
        self.assertEqual(daily_reboot_count(cam), 1)


class TestCanReboot(unittest.TestCase):
    def test_allowed_initially(self):
        cam = CameraState()
        ok, reason = can_reboot(cam, 900, 3)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_blocked_by_cooldown(self):
        cam = CameraState(last_reboot=time.time())
        ok, reason = can_reboot(cam, 900, 3)
        self.assertFalse(ok)
        self.assertIn("cooldown", reason)

    def test_blocked_by_max_daily(self):
        now = time.time()
        cam = CameraState(reboot_times=[now - 100, now - 200, now - 300])
        ok, reason = can_reboot(cam, 900, 3)
        self.assertFalse(ok)
        self.assertIn("max daily", reason)


class TestRecordReboot(unittest.TestCase):
    def test_record_success(self):
        state = BabysitterState()
        record_reboot(state, "doorbell", "reolink_cgi", "video_down", "success", 75.0)
        cam = state.get_camera("doorbell")
        self.assertGreater(cam.last_reboot, 0)
        self.assertEqual(len(cam.reboot_times), 1)
        self.assertEqual(len(state.history), 1)
        self.assertEqual(state.history[0].camera, "doorbell")
        self.assertEqual(state.history[0].outcome, "success")
        self.assertEqual(state.history[0].duration, 75.0)

    def test_record_failed_no_count(self):
        """Failed reboots should not count toward daily limit."""
        state = BabysitterState()
        record_reboot(state, "doorbell", "reolink_cgi", "video_down", "failed")
        cam = state.get_camera("doorbell")
        self.assertEqual(len(cam.reboot_times), 0)
        self.assertEqual(len(state.history), 1)

    def test_history_trimmed(self):
        state = BabysitterState()
        for i in range(25):
            record_reboot(state, f"cam{i}", "reolink_cgi", "test", "success")
        self.assertEqual(len(state.history), 20)


class TestAtomicSaveLoad(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = pathlib.Path(self.tmpdir) / "test_state.json"

    def test_save_and_load(self):
        state = BabysitterState()
        record_reboot(state, "doorbell", "reolink_cgi", "video_down", "success", 75.0)
        save_state(state, self.state_path)

        loaded = load_state(self.state_path)
        self.assertIn("doorbell", loaded.cameras)
        self.assertEqual(len(loaded.history), 1)
        self.assertEqual(loaded.history[0].camera, "doorbell")

    def test_load_missing_file(self):
        state = load_state(pathlib.Path(self.tmpdir) / "nonexistent.json")
        self.assertEqual(len(state.cameras), 0)
        self.assertEqual(len(state.history), 0)

    def test_load_corrupt_file(self):
        self.state_path.write_text("not json{{{")
        state = load_state(self.state_path)
        self.assertEqual(len(state.cameras), 0)

    def test_no_temp_file_left_after_save(self):
        state = BabysitterState()
        save_state(state, self.state_path)
        tmp = self.state_path.with_suffix(".json.tmp")
        self.assertFalse(tmp.exists())

    def test_save_creates_parent_dir(self):
        deep_path = pathlib.Path(self.tmpdir) / "sub" / "dir" / "state.json"
        state = BabysitterState()
        save_state(state, deep_path)
        self.assertTrue(deep_path.exists())


if __name__ == "__main__":
    unittest.main()
