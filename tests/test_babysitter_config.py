#!/usr/bin/env python3
"""Tests for app/babysitter/config.py — env defaults, JSON loading, updates."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from babysitter.config import (  # noqa: E402
    BabysitterConfig,
    CameraEntry,
    from_env,
    load_config,
    save_config,
    update_config,
)


class TestFromEnv(unittest.TestCase):
    def setUp(self):
        # Save and clear relevant env vars
        self._saved = {k: os.environ.pop(k, None) for k in [
            "SCRYPTED_HOST", "SCRYPTED_USERNAME", "SCRYPTED_PASSWORD",
            "FRIGATE_HOST", "MQTT_BROKER", "MQTT_PORT",
            "REOLINK_USERNAME", "REOLINK_PASSWORD",
            "BABYSIT_DRY_RUN", "BABYSIT_COOLDOWN", "BABYSIT_MAX_DAILY",
            "BABYSIT_VIDEO_DOWN_THRESHOLD", "BABYSIT_INTERVAL",
            "BABYSIT_CAMERAS", "BABYSIT_APPROVED_CAMERAS",
        ]}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_defaults(self):
        cfg = from_env()
        self.assertTrue(cfg.dry_run)
        self.assertEqual(cfg.cooldown, 900)
        self.assertEqual(cfg.max_daily, 3)
        self.assertEqual(cfg.video_down_threshold, 120)
        self.assertEqual(cfg.interval, 60)

    def test_env_override(self):
        os.environ["SCRYPTED_HOST"] = "https://test:10443"
        os.environ["BABYSIT_DRY_RUN"] = "false"
        os.environ["BABYSIT_COOLDOWN"] = "600"
        os.environ["BABYSIT_MAX_DAILY"] = "5"
        cfg = from_env()
        self.assertEqual(cfg.scrypted_host, "https://test:10443")
        self.assertFalse(cfg.dry_run)
        self.assertEqual(cfg.cooldown, 600)
        self.assertEqual(cfg.max_daily, 5)

    def test_camera_parsing(self):
        os.environ["BABYSIT_CAMERAS"] = "doorbell:54:192.168.1.69:doorbell,north:221:192.168.1.235:north_driveway"
        cfg = from_env()
        self.assertEqual(len(cfg.cameras), 2)
        self.assertEqual(cfg.cameras[0].friendly_name, "doorbell")
        self.assertEqual(cfg.cameras[0].scrypted_id, "54")
        self.assertEqual(cfg.cameras[0].ip, "192.168.1.69")
        self.assertEqual(cfg.cameras[1].frigate_name, "north_driveway")

    def test_approved_cameras(self):
        os.environ["BABYSIT_APPROVED_CAMERAS"] = "doorbell,north"
        cfg = from_env()
        self.assertEqual(cfg.approved_cameras, {"doorbell", "north"})


class TestSecretMasking(unittest.TestCase):
    def test_mask_secrets(self):
        cfg = BabysitterConfig(
            scrypted_password="supersecret",
            mqtt_password="mqpass",
            reolink_password="reopass",
        )
        d = cfg.to_dict(mask_secrets=True)
        self.assertEqual(d["scrypted_password"], "****")
        self.assertEqual(d["mqtt_password"], "****")
        self.assertEqual(d["reolink_password"], "****")

    def test_no_mask_secrets(self):
        cfg = BabysitterConfig(scrypted_password="supersecret")
        d = cfg.to_dict(mask_secrets=False)
        self.assertEqual(d["scrypted_password"], "supersecret")


class TestLoadSaveConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = pathlib.Path(self.tmpdir) / "test_config.json"

    def test_save_and_load(self):
        cfg = BabysitterConfig(
            scrypted_host="https://test:10443",
            frigate_host="http://frigate:5000",
            cooldown=600,
            cameras=[CameraEntry("doorbell", "54", "192.168.1.69", "doorbell")],
        )
        cfg.approved_cameras = {"doorbell"}
        save_config(cfg, self.config_path)

        loaded = load_config(self.config_path)
        self.assertEqual(loaded.scrypted_host, "https://test:10443")
        self.assertEqual(loaded.cooldown, 600)
        self.assertEqual(len(loaded.cameras), 1)
        self.assertEqual(loaded.cameras[0].friendly_name, "doorbell")
        self.assertIn("doorbell", loaded.approved_cameras)

    def test_load_missing_file_uses_env(self):
        cfg = load_config(pathlib.Path(self.tmpdir) / "nonexistent.json")
        # Should return env defaults
        self.assertTrue(cfg.dry_run)

    def test_load_corrupt_file_uses_env(self):
        self.config_path.write_text("not valid json{{{")
        cfg = load_config(self.config_path)
        self.assertTrue(cfg.dry_run)  # env default


class TestUpdateConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = pathlib.Path(self.tmpdir) / "test_config.json"
        # Save initial config
        cfg = BabysitterConfig(
            scrypted_host="https://test:10443",
            scrypted_password="secret",
            cooldown=900,
        )
        save_config(cfg, self.config_path)

    def test_update_cooldown(self):
        cfg = update_config({"cooldown": 600}, self.config_path)
        self.assertEqual(cfg.cooldown, 600)

    def test_update_does_not_mask_password(self):
        """Updating with '****' should not overwrite the real password."""
        cfg = update_config({"scrypted_password": "****"}, self.config_path)
        self.assertEqual(cfg.scrypted_password, "secret")

    def test_update_password(self):
        cfg = update_config({"scrypted_password": "newpass"}, self.config_path)
        self.assertEqual(cfg.scrypted_password, "newpass")

    def test_update_cameras(self):
        cfg = update_config({
            "cameras": [
                {"friendly_name": "doorbell", "scrypted_id": "54", "ip": "192.168.1.69", "frigate_name": "doorbell"}
            ]
        }, self.config_path)
        self.assertEqual(len(cfg.cameras), 1)
        self.assertEqual(cfg.cameras[0].scrypted_id, "54")

    def test_update_approved(self):
        cfg = update_config({"approved_cameras": ["doorbell", "north"]}, self.config_path)
        self.assertEqual(cfg.approved_cameras, {"doorbell", "north"})


if __name__ == "__main__":
    unittest.main()
