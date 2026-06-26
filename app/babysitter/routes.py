"""Flask blueprint for the Reolink camera babysitter settings/status page.

All API endpoints return JSON. The HTML settings page is served at GET /.
Passwords are never returned in plaintext — ``to_dict(mask_secrets=True)``
is used for every config response.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, render_template, request

# Template folder is relative to this module (app/babysitter/templates).
_TEMPLATE_FOLDER = str(Path(__file__).resolve().parent / "templates")

from babysitter.config import (
    DEFAULT_CONFIG_PATH,
    BabysitterConfig,
    load_config,
    save_config,
    update_config,
)
from babysitter.helpers import (
    FrigateClient,
    ReolinkClient,
    ScryptedClient,
    run_discovery,
)
from babysitter.state import (
    DEFAULT_STATE_PATH,
    can_reboot,
    cooldown_remaining,
    daily_reboot_count,
    is_in_cooldown,
    load_state,
    record_reboot,
    save_state,
)
from babysitter.watchdog import Watchdog

logger = logging.getLogger(__name__)


def create_blueprint(
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
) -> Blueprint:
    """Create and return the babysitter Flask blueprint.

    Args:
        config_path: Path to the babysitter config JSON file.
        state_path: Path to the babysitter state JSON file.

    Returns:
        A configured Flask Blueprint with all endpoints registered.
    """
    bp = Blueprint(
        "babysitter",
        __name__,
        url_prefix="/babysitter",
        template_folder=_TEMPLATE_FOLDER,
    )

    # Load config + state and create the watchdog instance.
    config = load_config(config_path)
    state = load_state(state_path)
    watchdog = Watchdog(config, state)

    # Store references on the blueprint for access from endpoints.
    bp.config_path = config_path  # type: ignore[attr-defined]
    bp.state_path = state_path  # type: ignore[attr-defined]
    bp.watchdog = watchdog  # type: ignore[attr-defined]

    def get_watchdog() -> Watchdog:
        """Return the watchdog instance for this blueprint."""
        return bp.watchdog  # type: ignore[attr-defined]

    def _reload_watchdog() -> Watchdog:
        """Reload config + state and re-create the watchdog (and clients)."""
        nonlocal watchdog
        new_config = load_config(config_path)
        new_state = load_state(state_path)
        # Preserve the background thread if running.
        was_running = watchdog._thread is not None and watchdog._thread.is_alive()
        watchdog.stop()
        watchdog = Watchdog(new_config, new_state)
        bp.watchdog = watchdog  # type: ignore[attr-defined]
        if was_running:
            watchdog.start_background()
        return watchdog

    # ------------------------------------------------------------------
    # HTML settings page
    # ------------------------------------------------------------------

    @bp.route("/")
    def index():
        """Render the babysitter settings/status page."""
        return render_template("babysitter.html")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @bp.route("/api/status")
    def api_status():
        """Return all camera statuses (polls once or returns cached)."""
        wd = get_watchdog()
        try:
            statuses = wd.poll_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("poll_once failed: %s — returning cached status", exc)
            statuses = wd._last_status
        return jsonify({
            name: s.to_dict() for name, s in statuses.items()
        })

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @bp.route("/api/config", methods=["GET"])
    def api_config_get():
        """Return current config with passwords masked."""
        cfg = load_config(config_path)
        return jsonify(cfg.to_dict(mask_secrets=True))

    @bp.route("/api/config", methods=["PUT"])
    def api_config_put():
        """Update config from JSON body and return new masked config."""
        data = request.get_json(silent=True) or {}
        try:
            new_cfg = update_config(data, path=config_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to update config: %s", exc)
            return jsonify({"error": str(exc)}), 400
        # Reload the watchdog with the new config.
        _reload_watchdog()
        return jsonify(new_cfg.to_dict(mask_secrets=True))

    # ------------------------------------------------------------------
    # Manual reboot
    # ------------------------------------------------------------------

    @bp.route("/api/reboot/<camera>", methods=["POST"])
    def api_reboot(camera: str):
        """Manually reboot a camera (bypasses convergence, respects guards)."""
        wd = get_watchdog()
        cfg = wd.config

        # Check approval.
        if camera not in cfg.approved_cameras:
            return jsonify({
                "error": f"Camera '{camera}' is not approved for reboot",
            }), 403

        # Find the camera entry.
        entry = wd._camera_entry(camera)
        if entry is None:
            return jsonify({"error": f"Camera '{camera}' not found in config"}), 404

        # Check cooldown + max daily.
        cam_state = wd.state.get_camera(camera)
        ok, reason = can_reboot(cam_state, cfg.cooldown, cfg.max_daily)
        if not ok:
            return jsonify({
                "error": f"Reboot blocked: {reason}",
                "cooldown_remaining": cooldown_remaining(cam_state, cfg.cooldown),
                "reboots_today": daily_reboot_count(cam_state),
                "max_daily": cfg.max_daily,
            }), 429

        # Dry-run guard (global or per-camera).
        is_dry = cfg.per_camera_dry_run.get(camera, False) or cfg.dry_run
        if is_dry:
            from babysitter.helpers import tcp_reachable as _tcp
            reolink_dr = wd.reolink.get(camera)
            onvif_dr = wd.onvif.get(camera)
            cgi_ok_dr = bool(reolink_dr and _tcp(entry.ip, reolink_dr.port))
            method_dr = "reolink_cgi" if cgi_ok_dr else "onvif"
            return jsonify({
                "dry_run": True,
                "method": method_dr,
                "message": f"Would reboot {camera} via {method_dr} at {entry.ip}",
            }), 200

        # Perform the reboot: try CGI first, fall back to ONVIF.
        reolink = wd.reolink.get(camera)
        onvif = wd.onvif.get(camera)
        entry = wd._camera_entry(camera)

        # Determine which method to use.
        from babysitter.helpers import tcp_reachable
        cgi_ok = bool(reolink and entry and tcp_reachable(entry.ip, reolink.port))
        if cgi_ok and reolink:
            action = "reolink_cgi"
            client = reolink
        elif onvif:
            action = "onvif"
            client = onvif
        else:
            return jsonify({
                "error": f"No reboot path available for '{camera}'",
            }), 500

        start = time.time()
        try:
            success = client.reboot_with_retry()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s: manual %s reboot failed: %s", camera, action, exc)
            # If CGI failed and ONVIF is available, try it.
            if action == "reolink_cgi" and onvif:
                logger.info("%s: CGI failed, trying ONVIF fallback", camera)
                action = "onvif"
                start = time.time()
                try:
                    success = onvif.reboot_with_retry()
                except Exception as exc2:  # noqa: BLE001
                    logger.error("%s: ONVIF fallback also failed: %s", camera, exc2)
                    record_reboot(
                        wd.state, camera, "onvif", "manual", "failed",
                        duration=time.time() - start,
                    )
                    save_state(wd.state, state_path)
                    return jsonify({
                        "camera": camera,
                        "action": "onvif",
                        "reason": "manual",
                        "outcome": "failed",
                        "error": str(exc2),
                    }), 500
            else:
                record_reboot(
                    wd.state, camera, action, "manual", "failed",
                    duration=time.time() - start,
                )
                save_state(wd.state, state_path)
                return jsonify({
                    "camera": camera,
                    "action": action,
                    "reason": "manual",
                    "outcome": "failed",
                    "error": str(exc),
                }), 500

        duration = time.time() - start
        outcome = "success" if success else "failed"
        record_reboot(
            wd.state, camera, action, "manual", outcome,
            duration=duration,
        )
        save_state(wd.state, state_path)

        return jsonify({
            "camera": camera,
            "action": action,
            "reason": "manual",
            "outcome": outcome,
            "duration": round(duration, 2),
        })

    # ------------------------------------------------------------------
    # Approve / disapprove
    # ------------------------------------------------------------------

    @bp.route("/api/approve/<camera>", methods=["POST"])
    def api_approve(camera: str):
        """Toggle approval for a camera."""
        cfg = load_config(config_path)
        if camera in cfg.approved_cameras:
            cfg.approved_cameras.discard(camera)
            approved = False
        else:
            cfg.approved_cameras.add(camera)
            approved = True
        save_config(cfg, config_path)
        _reload_watchdog()
        return jsonify({
            "camera": camera,
            "approved": approved,
            "approved_cameras": sorted(cfg.approved_cameras),
        })

    # ------------------------------------------------------------------
    # Dry-run toggles
    # ------------------------------------------------------------------

    @bp.route("/api/dryrun", methods=["POST"])
    def api_dryrun_global():
        """Toggle global dry-run mode."""
        cfg = load_config(config_path)
        cfg.dry_run = not cfg.dry_run
        save_config(cfg, config_path)
        _reload_watchdog()
        return jsonify({
            "dry_run": cfg.dry_run,
            "message": "Dry-run mode enabled" if cfg.dry_run else "Dry-run mode disabled (LIVE)",
        })

    @bp.route("/api/dryrun/<camera>", methods=["POST"])
    def api_dryrun_camera(camera: str):
        """Toggle per-camera dry-run override."""
        cfg = load_config(config_path)
        current = cfg.per_camera_dry_run.get(camera, False)
        cfg.per_camera_dry_run[camera] = not current
        save_config(cfg, config_path)
        _reload_watchdog()
        return jsonify({
            "camera": camera,
            "dry_run": cfg.per_camera_dry_run[camera],
            "message": f"Dry-run override for {camera} enabled" if cfg.per_camera_dry_run[camera]
                       else f"Dry-run override for {camera} disabled",
        })

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @bp.route("/api/discover", methods=["POST"])
    def api_discover():
        """Run a one-shot discovery probe."""
        cfg = load_config(config_path)
        scrypted = ScryptedClient(
            host=cfg.scrypted_host,
            username=cfg.scrypted_username,
            password=cfg.scrypted_password,
        )
        frigate = FrigateClient(host=cfg.frigate_host)
        camera_ips = {cam.frigate_name: cam.ip for cam in cfg.cameras}
        configured_ids = {cam.frigate_name: cam.scrypted_id for cam in cfg.cameras}
        try:
            artifact = run_discovery(
                scrypted=scrypted,
                frigate=frigate,
                camera_ips=camera_ips,
                mqtt_broker=cfg.mqtt_broker,
                configured_scrypted_ids=configured_ids,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Discovery failed: %s", exc)
            return jsonify({"error": str(exc)}), 500
        return jsonify(artifact)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    @bp.route("/api/history")
    def api_history():
        """Return reboot history from state."""
        wd = get_watchdog()
        from dataclasses import asdict
        return jsonify([asdict(e) for e in wd.state.history])

    # ------------------------------------------------------------------
    # Full state dump
    # ------------------------------------------------------------------

    @bp.route("/api/state")
    def api_state():
        """Return full state (cameras + history) for debugging."""
        wd = get_watchdog()
        return jsonify(wd.state.to_dict())

    return bp
