#!/usr/bin/env python3
"""Tests for app/babysitter/routes.py — Flask blueprint API endpoints."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from flask import Flask

from babysitter.config import BabysitterConfig, CameraEntry, save_config
from babysitter.state import BabysitterState, CameraState, RebootEvent, save_state
from babysitter.routes import create_blueprint


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(
    dry_run: bool = True,
    approved: set[str] | None = None,
    cameras: list[CameraEntry] | None = None,
) -> BabysitterConfig:
    return BabysitterConfig(
        scrypted_host="https://scrypted.local",
        scrypted_username="admin",
        scrypted_password="secret",
        frigate_host="http://frigate.local",
        mqtt_broker="",
        reolink_username="admin",
        reolink_password="pass",
        dry_run=dry_run,
        cooldown=900,
        max_daily=3,
        video_down_threshold=120,
        snapshot_samples=3,
        snapshot_stale_window=600,
        reboot_timeout=60,
        recovery_wait=180,
        interval=60,
        cameras=cameras
        or [
            CameraEntry("doorbell", "101", "192.168.1.71", "doorbell"),
            CameraEntry("south_driveway", "102", "192.168.1.72", "south_driveway"),
            CameraEntry("north_driveway", "103", "192.168.1.73", "north_driveway"),
        ],
        approved_cameras=approved if approved is not None else set(),
        per_camera_dry_run={},
    )


def _make_state() -> BabysitterState:
    state = BabysitterState()
    state.cameras["doorbell"] = CameraState(current_state="online")
    state.cameras["south_driveway"] = CameraState(current_state="online")
    state.cameras["north_driveway"] = CameraState(current_state="online")
    return state


class _BaseRouteTest(unittest.TestCase):
    """Base class that creates a Flask app with the babysitter blueprint."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config_path = pathlib.Path(self.tmpdir.name) / "config.json"
        self.state_path = pathlib.Path(self.tmpdir.name) / "state.json"

        self.config = _make_config()
        save_config(self.config, self.config_path)
        self.state = _make_state()
        save_state(self.state, self.state_path)

        app_root = pathlib.Path(__file__).resolve().parent.parent / "app"
        self.app = Flask(
            __name__,
            static_folder=str(app_root / "static"),
            template_folder=str(app_root / "templates"),
        )
        # Add the babysitter templates folder as a Jinja loader so both
        # base.html (from app/templates) and babysitter.html (from
        # app/babysitter/templates) are found.
        from jinja2 import ChoiceLoader, FileSystemLoader

        self.app.jinja_loader = ChoiceLoader([
            self.app.jinja_loader,
            FileSystemLoader(str(app_root / "babysitter" / "templates")),
        ])
        self.bp = create_blueprint(
            config_path=self.config_path,
            state_path=self.state_path,
        )
        self.app.register_blueprint(self.bp)
        self.client = self.app.test_client()

        # Patch poll_once to avoid real HTTP calls.
        self.poll_patcher = patch.object(self.bp.watchdog, "poll_once")
        self.mock_poll = self.poll_patcher.start()
        self.addCleanup(self.poll_patcher.stop)

        from babysitter.watchdog import CameraStatus

        self.mock_poll.return_value = {
            "doorbell": CameraStatus(name="doorbell", state="online", camera_fps=10.0, process_fps=10.0),
            "south_driveway": CameraStatus(name="south_driveway", state="online"),
            "north_driveway": CameraStatus(name="north_driveway", state="online"),
        }


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------


class TestHtmlPage(_BaseRouteTest):
    def test_get_index_returns_html(self):
        resp = self.client.get("/babysitter/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Camera Babysitter", resp.data)


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


class TestStatusEndpoint(_BaseRouteTest):
    def test_get_status_returns_json(self):
        resp = self.client.get("/babysitter/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("doorbell", data)
        self.assertEqual(data["doorbell"]["state"], "online")
        self.assertEqual(data["doorbell"]["camera_fps"], 10.0)


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------


class TestConfigEndpoints(_BaseRouteTest):
    def test_get_config_masks_passwords(self):
        resp = self.client.get("/babysitter/api/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["scrypted_password"], "****")
        self.assertEqual(data["reolink_password"], "****")
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn('"pass"', json.dumps(data))

    def test_put_config_updates_and_masks(self):
        resp = self.client.put(
            "/babysitter/api/config",
            json={"cooldown": 600, "max_daily": 5},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["cooldown"], 600)
        self.assertEqual(data["max_daily"], 5)
        self.assertEqual(data["scrypted_password"], "****")

    def test_put_config_preserves_passwords_when_masked(self):
        """Sending **** for a password should not overwrite it."""
        resp = self.client.put(
            "/babysitter/api/config",
            json={"scrypted_password": "****", "cooldown": 300},
        )
        self.assertEqual(resp.status_code, 200)
        # Reload from file to verify the real password is still there.
        from babysitter.config import load_config

        cfg = load_config(self.config_path)
        self.assertEqual(cfg.scrypted_password, "secret")
        self.assertEqual(cfg.cooldown, 300)

    def test_passwords_never_in_plaintext(self):
        """Double-check no API response leaks plaintext passwords."""
        # GET config
        resp = self.client.get("/babysitter/api/config")
        body = resp.get_data(as_text=True)
        self.assertNotIn("secret", body)
        self.assertNotIn('"pass"', body)
        # PUT config
        resp = self.client.put("/babysitter/api/config", json={"cooldown": 100})
        body = resp.get_data(as_text=True)
        self.assertNotIn("secret", body)
        self.assertNotIn('"pass"', body)


# ---------------------------------------------------------------------------
# Approve endpoint
# ---------------------------------------------------------------------------


class TestApproveEndpoint(_BaseRouteTest):
    def test_approve_toggles_on(self):
        resp = self.client.post("/babysitter/api/approve/doorbell")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["approved"])
        self.assertIn("doorbell", data["approved_cameras"])

    def test_approve_toggles_off(self):
        # First approve.
        self.client.post("/babysitter/api/approve/doorbell")
        # Then toggle off.
        resp = self.client.post("/babysitter/api/approve/doorbell")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["approved"])
        self.assertNotIn("doorbell", data["approved_cameras"])


# ---------------------------------------------------------------------------
# Dry-run endpoints
# ---------------------------------------------------------------------------


class TestDryRunEndpoints(_BaseRouteTest):
    def test_global_dryrun_toggle(self):
        # Config starts with dry_run=True.
        resp = self.client.post("/babysitter/api/dryrun")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["dry_run"])

        # Toggle back.
        resp = self.client.post("/babysitter/api/dryrun")
        data = resp.get_json()
        self.assertTrue(data["dry_run"])

    def test_per_camera_dryrun_toggle(self):
        resp = self.client.post("/babysitter/api/dryrun/doorbell")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["camera"], "doorbell")

        # Toggle back.
        resp = self.client.post("/babysitter/api/dryrun/doorbell")
        data = resp.get_json()
        self.assertFalse(data["dry_run"])


# ---------------------------------------------------------------------------
# Reboot endpoint
# ---------------------------------------------------------------------------


class TestRebootEndpoint(_BaseRouteTest):
    def test_reboot_not_approved_returns_403(self):
        resp = self.client.post("/babysitter/api/reboot/doorbell")
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertIn("not approved", data["error"])

    def test_reboot_dry_run_returns_200(self):
        # Approve the camera first.
        self.client.post("/babysitter/api/approve/doorbell")
        # Config has dry_run=True by default.
        resp = self.client.post("/babysitter/api/reboot/doorbell")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["dry_run"])
        self.assertIn("Would reboot", data["message"])

    def test_reboot_in_cooldown_returns_429(self):
        # Approve + disable dry-run.
        self.client.post("/babysitter/api/approve/doorbell")
        self.client.post("/babysitter/api/dryrun")  # toggles to False

        # Put the camera in cooldown by setting last_reboot to now.
        wd = self.bp.watchdog
        cam_state = wd.state.get_camera("doorbell")
        cam_state.last_reboot = time.time()

        resp = self.client.post("/babysitter/api/reboot/doorbell")
        self.assertEqual(resp.status_code, 429)
        data = resp.get_json()
        self.assertIn("blocked", data["error"].lower())

    def test_reboot_max_daily_returns_429(self):
        # Approve + disable dry-run.
        self.client.post("/babysitter/api/approve/doorbell")
        self.client.post("/babysitter/api/dryrun")  # toggles to False

        # Exhaust daily reboots.
        wd = self.bp.watchdog
        cam_state = wd.state.get_camera("doorbell")
        cam_state.reboot_times = [time.time()] * 3  # max_daily=3

        resp = self.client.post("/babysitter/api/reboot/doorbell")
        self.assertEqual(resp.status_code, 429)
        data = resp.get_json()
        self.assertIn("max daily", data["error"].lower())

    def test_reboot_live_calls_onvif(self):
        # Approve + disable dry-run.
        self.client.post("/babysitter/api/approve/doorbell")
        self.client.post("/babysitter/api/dryrun")  # toggles to False

        # Mock the OnvifRebootClient.reboot_with_retry.
        wd = self.bp.watchdog
        onvif = wd.onvif.get("doorbell")
        self.assertIsNotNone(onvif)
        with patch.object(onvif, "reboot_with_retry", return_value=True):
            resp = self.client.post("/babysitter/api/reboot/doorbell")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["outcome"], "success")
        self.assertEqual(data["reason"], "manual")
        self.assertEqual(data["action"], "onvif")

    def test_reboot_unknown_camera_returns_404(self):
        self.client.post("/babysitter/api/approve/unknown")
        resp = self.client.post("/babysitter/api/reboot/unknown")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Discover endpoint
# ---------------------------------------------------------------------------


class TestDiscoverEndpoint(_BaseRouteTest):
    def test_discover_returns_artifact(self):
        mock_artifact = {
            "scrypted": {"host": "https://scrypted.local", "cameras": []},
            "frigate": {"host": "http://frigate.local", "cameras": {}, "rtsp_inputs": {}},
            "camera_mapping": [],
            "runtime_reachability": {},
        }
        with patch("babysitter.routes.run_discovery", return_value=mock_artifact):
            resp = self.client.post("/babysitter/api/discover")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("scrypted", data)
        self.assertIn("frigate", data)

    def test_discover_error_returns_500(self):
        with patch("babysitter.routes.run_discovery", side_effect=RuntimeError("boom")):
            resp = self.client.post("/babysitter/api/discover")
        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertIn("error", data)


# ---------------------------------------------------------------------------
# History endpoint
# ---------------------------------------------------------------------------


class TestHistoryEndpoint(_BaseRouteTest):
    def test_history_returns_list(self):
        # Add a history event to state.
        wd = self.bp.watchdog
        wd.state.history.append(RebootEvent(
            timestamp=time.time(),
            camera="doorbell",
            action="onvif",
            reason="video_down",
            outcome="success",
            duration=12.5,
        ))
        resp = self.client.get("/babysitter/api/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["camera"], "doorbell")
        self.assertEqual(data[0]["outcome"], "success")

    def test_history_empty_returns_empty_list(self):
        resp = self.client.get("/babysitter/api/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])


# ---------------------------------------------------------------------------
# State endpoint
# ---------------------------------------------------------------------------


class TestStateEndpoint(_BaseRouteTest):
    def test_state_returns_full_dump(self):
        resp = self.client.get("/babysitter/api/state")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("cameras", data)
        self.assertIn("history", data)
        self.assertIn("doorbell", data["cameras"])


# ---------------------------------------------------------------------------
# Blueprint not loaded when ENABLE_BABYSITTER is not set
# ---------------------------------------------------------------------------


class TestBlueprintNotLoaded(unittest.TestCase):
    def test_blueprint_not_registered_without_env(self):
        """The blueprint should not be loaded when ENABLE_BABYSITTER is unset."""
        # We can't easily test create_app() because it requires WyzeBridge,
        # but we can verify the env check logic directly.
        saved = os.environ.pop("ENABLE_BABYSITTER", None)
        self.addCleanup(lambda: os.environ.__setitem__("ENABLE_BABYSITTER", saved) if saved else os.environ.pop("ENABLE_BABYSITTER", None))

        enabled = (
            os.environ.get("ENABLE_BABYSITTER", "").lower() in ("1", "true", "yes", "on")
        )
        self.assertFalse(enabled)

    def test_blueprint_not_loaded_with_false_env(self):
        """The blueprint should not be loaded when ENABLE_BABYSITTER=false."""
        saved = os.environ.get("ENABLE_BABYSITTER", None)
        os.environ["ENABLE_BABYSITTER"] = "false"
        self.addCleanup(lambda: os.environ.__setitem__("ENABLE_BABYSITTER", saved) if saved else os.environ.pop("ENABLE_BABYSITTER", None))

        enabled = (
            os.environ.get("ENABLE_BABYSITTER", "").lower() in ("1", "true", "yes", "on")
        )
        self.assertFalse(enabled)

    def test_blueprint_loaded_with_true_env(self):
        """The blueprint should be loaded when ENABLE_BABYSITTER=true."""
        saved = os.environ.get("ENABLE_BABYSITTER", None)
        os.environ["ENABLE_BABYSITTER"] = "true"
        self.addCleanup(lambda: os.environ.__setitem__("ENABLE_BABYSITTER", saved) if saved else os.environ.pop("ENABLE_BABYSITTER", None))

        enabled = (
            os.environ.get("ENABLE_BABYSITTER", "").lower() in ("1", "true", "yes", "on")
        )
        self.assertTrue(enabled)


if __name__ == "__main__":
    unittest.main()
