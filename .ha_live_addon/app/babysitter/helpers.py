"""Helper clients for the Reolink camera babysitter.

All HTTP clients use ``requests`` (already in app/requirements.txt).
All secrets are redacted in logs via :func:`redact_url`.
"""

from __future__ import annotations

import hashlib
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Minimum size for a valid JPEG snapshot (bytes).
MIN_JPEG_SIZE = 2048

# JPEG magic bytes.
JPEG_MAGIC = b"\xff\xd8"
JPEG_END_MAGIC = b"\xff\xd9"

# Regex for redacting credentials in rtsp:// URLs.
_RTSP_CRED_RE = re.compile(r"(rtsp://)[^/@\s]+:[^/@\s]+@")


def redact_url(url: str) -> str:
    """Redact credentials in a URL for safe logging."""
    return _RTSP_CRED_RE.sub(r"\1<redacted>@", url)


def redact_token(token: str) -> str:
    """Redact a bearer/token for safe logging."""
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return "<redacted>"
    return token[:4] + "..." + token[-4:]


# ---------------------------------------------------------------------------
# JPEG validation
# ---------------------------------------------------------------------------


def is_valid_jpeg(data: bytes, min_size: int = MIN_JPEG_SIZE) -> bool:
    """Return True if *data* looks like a valid JPEG of reasonable size."""
    if not data or len(data) < min_size:
        return False
    if not data.startswith(JPEG_MAGIC):
        return False
    if JPEG_END_MAGIC not in data:
        return False
    return True


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# TCP probe
# ---------------------------------------------------------------------------


def tcp_reachable(host: str, port: int = 554, timeout: float = 3.0) -> bool:
    """Return True if a TCP connection to *host*:*port* succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.gaierror):
        return False


# ---------------------------------------------------------------------------
# Scrypted HTTP client (detection only — snapshot + login)
# ---------------------------------------------------------------------------


@dataclass
class ScryptedClient:
    """Minimal Scrypted HTTP client for snapshot probing and login."""

    host: str
    username: str = ""
    password: str = ""
    verify_ssl: bool = False
    _token: str | None = field(default=None, repr=False)

    def login(self, timeout: float = 10.0) -> str:
        """Authenticate and return a bearer token.

        Scrypted's /login expects credentials in the JSON body
        (``{"username": ..., "password": ...}``), not HTTP basic auth.
        The response's ``authorization`` field already includes the
        ``Bearer `` prefix, so we strip it before storing the raw token.
        """
        resp = requests.post(
            f"{self.host}/login",
            headers={"Content-Type": "application/json"},
            json={"username": self.username, "password": self.password},
            verify=self.verify_ssl,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        raw = body.get("authorization") or body.get("token") or ""
        if raw.startswith("Bearer "):
            raw = raw[len("Bearer "):]
        if not raw:
            raise ValueError("Scrypted login returned no token")
        self._token = raw
        return raw

    @property
    def token(self) -> str | None:
        return self._token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self.login()
        return {"Authorization": f"Bearer {self._token}"}

    def snapshot(self, device_id: str, timeout: float = 15.0) -> bytes:
        """Fetch a snapshot JPEG from Scrypted for *device_id*.

        Uses the full endpoint per AGENTS.md rule:
        /endpoint/@scrypted/snapshot/<device-id>/Camera
        """
        url = f"{self.host}/endpoint/@scrypted/snapshot/{device_id}/Camera"
        resp = requests.get(url, headers=self._headers(), verify=self.verify_ssl, timeout=timeout)
        resp.raise_for_status()
        return resp.content

    def snapshot_probe(
        self, device_id: str, timeout: float = 15.0
    ) -> dict[str, Any]:
        """Probe a snapshot and return a dict with validation info."""
        try:
            data = self.snapshot(device_id, timeout=timeout)
            return {
                "device_id": device_id,
                "http_status": 200,
                "content_length": len(data),
                "jpeg_valid": is_valid_jpeg(data),
                "sha256": sha256_hex(data),
            }
        except requests.RequestException as exc:
            return {
                "device_id": device_id,
                "http_status": getattr(exc.response, "status_code", None),
                "content_length": 0,
                "jpeg_valid": False,
                "sha256": "",
                "error": str(exc),
            }

    def discover_cameras(
        self, id_range: range = range(1, 301), timeout: float = 10.0
    ) -> list[dict[str, Any]]:
        """Probe a range of Scrypted device IDs for valid camera snapshots.

        Returns a list of dicts with device_id and snapshot proof.
        """
        results: list[dict[str, Any]] = []
        for device_id in id_range:
            probe = self.snapshot_probe(str(device_id), timeout=timeout)
            if probe["jpeg_valid"]:
                results.append(probe)
            elif probe.get("http_status") == 404:
                # Short-circuit: once we hit 404s past known ID ranges, stop.
                # But we still scan the full range to be safe.
                pass
        return results


# ---------------------------------------------------------------------------
# Frigate REST client
# ---------------------------------------------------------------------------


@dataclass
class FrigateClient:
    """Minimal Frigate REST client for stats, config, and ffprobe."""

    host: str
    verify_ssl: bool = False

    def stats(self, timeout: float = 10.0) -> dict[str, Any]:
        """GET /api/stats — returns per-camera FPS and service status."""
        resp = requests.get(f"{self.host}/api/stats", verify=self.verify_ssl, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def config(self, timeout: float = 10.0) -> dict[str, Any]:
        """GET /api/config — returns camera config including RTSP input URLs."""
        resp = requests.get(f"{self.host}/api/config", verify=self.verify_ssl, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def camera_fps(self, camera_name: str, timeout: float = 10.0) -> dict[str, float]:
        """Return fps dict for a single camera from /api/stats.

        Keys: camera_fps, process_fps, skipped_fps, detection_fps.
        """
        all_stats = self.stats(timeout=timeout)
        cam_stats = all_stats.get(camera_name, {})
        return {
            "camera_fps": float(cam_stats.get("camera_fps", 0)),
            "process_fps": float(cam_stats.get("process_fps", 0)),
            "skipped_fps": float(cam_stats.get("skipped_fps", 0)),
            "detection_fps": float(cam_stats.get("detection_fps", 0)),
        }

    def rtsp_inputs(self, timeout: float = 10.0) -> dict[str, str]:
        """Return mapping of camera_name → RTSP input URL from /api/config."""
        cfg = self.config(timeout=timeout)
        result: dict[str, str] = {}
        for cam_name, cam_cfg in cfg.get("cameras", {}).items():
            inputs = cam_cfg.get("ffmpeg", {}).get("inputs", [])
            for inp in inputs:
                if inp.get("roles") and "record" in inp.get("roles", []):
                    result[cam_name] = inp.get("path", "")
                    break
            if cam_name not in result and inputs:
                result[cam_name] = inputs[0].get("path", "")
        return result

    def ffprobe(self, rtsp_url: str, timeout: float = 30.0) -> dict[str, Any]:
        """GET /api/ffprobe?paths=<url> — probe an RTSP input."""
        resp = requests.get(
            f"{self.host}/api/ffprobe",
            params={"paths": rtsp_url},
            verify=self.verify_ssl,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Reolink CGI client (reboot only)
# ---------------------------------------------------------------------------


@dataclass
class ReolinkClient:
    """Minimal Reolink CGI client for login + reboot."""

    ip: str
    username: str = "admin"
    password: str = ""
    port: int = 80
    use_https: bool = False
    timeout: float = 10.0
    _token: str | None = field(default=None, repr=False)

    @property
    def _base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.ip}:{self.port}"

    def login(self) -> str:
        """Login and store the Reolink API token."""
        body = [{"cmd": "Login", "param": {"User": {"userName": self.username, "password": self.password}}}]
        resp = requests.post(
            f"{self._base_url}/cgi-bin/api.cgi?cmd=Login",
            json=body,
            timeout=self.timeout,
            verify=False,  # Reolink self-signed certs
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data and "value" in data[0]:
            token = data[0]["value"].get("Token", {}).get("name")
            if token:
                self._token = token
                return token
        raise ValueError("Reolink login failed: no token in response")

    def reboot(self) -> bool:
        """Send a reboot command. Returns True if the API accepted it."""
        if not self._token:
            self.login()
        body = [{"cmd": "Reboot"}]
        try:
            resp = requests.post(
                f"{self._base_url}/cgi-bin/api.cgi?cmd=Reboot&token={self._token}",
                json=body,
                timeout=self.timeout,
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                code = data[0].get("code", 1)
                return code == 0
            return False
        except requests.RequestException:
            # Camera is rebooting — connection may drop. That's expected.
            return True

    def reboot_with_retry(self, max_retries: int = 1) -> bool:
        """Login + reboot with one retry on token expiry."""
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    self._token = None
                    self.login()
                return self.reboot()
            except (requests.RequestException, ValueError):
                if attempt >= max_retries:
                    raise
        return False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def run_discovery(
    scrypted: ScryptedClient,
    frigate: FrigateClient,
    camera_ips: dict[str, str],
    mqtt_broker: str = "",
    id_range: range = range(1, 301),
    configured_scrypted_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a read-only discovery probe and return a mapping artifact.

    Args:
        scrypted: Authenticated ScryptedClient.
        frigate: FrigateClient.
        camera_ips: Mapping of frigate_camera_name → camera IP.
        mqtt_broker: Optional MQTT broker host for reachability check.
        id_range: Scrypted device ID range to probe.
        configured_scrypted_ids: Optional mapping of frigate_camera_name →
            already-configured Scrypted device ID, used as a fallback when
            auto-matching cannot resolve an ambiguous multi-camera setup.

    Returns:
        Discovery artifact dict with all secrets redacted.
    """
    configured_scrypted_ids = configured_scrypted_ids or {}

    # Scrypted cameras
    scrypted_cameras = scrypted.discover_cameras(id_range=id_range)

    # Frigate stats + RTSP inputs
    frigate_stats = frigate.stats()
    frigate_rtsp = frigate.rtsp_inputs()

    # Build camera mapping by cross-referencing
    camera_mapping: list[dict[str, Any]] = []
    for cam_name, ip in camera_ips.items():
        tcp_ok = tcp_reachable(ip, 554)
        scrypted_id = _match_scrypted_id(cam_name, scrypted_cameras, frigate_rtsp)
        if scrypted_id is None:
            scrypted_id = configured_scrypted_ids.get(cam_name)
        camera_mapping.append({
            "friendly_name": cam_name,
            "scrypted_id": scrypted_id,
            "frigate_name": cam_name,
            "ip": ip,
            "tcp_554_reachable": tcp_ok,
        })

    # Runtime reachability
    reachability: dict[str, Any] = {
        "scrypted_host_reachable": _http_reachable(scrypted.host),
        "frigate_host_reachable": _http_reachable(frigate.host),
        "mqtt_broker_reachable": False,
        "camera_ips_reachable": {ip: tcp_reachable(ip, 554) for ip in camera_ips.values()},
    }
    if mqtt_broker:
        reachability["mqtt_broker_reachable"] = tcp_reachable(mqtt_broker, 1883)

    return {
        "scrypted": {
            "host": scrypted.host,
            "cameras": [
                {**cam, "sha256": cam.get("sha256", "")[:16] + "..."} for cam in scrypted_cameras
            ],
        },
        "frigate": {
            "host": frigate.host,
            "cameras": {
                name: {"fps": {
                    "camera": float(stats.get("camera_fps", 0)),
                    "process": float(stats.get("process_fps", 0)),
                    "skipped": float(stats.get("skipped_fps", 0)),
                }}
                for name, stats in frigate_stats.items()
                if isinstance(stats, dict) and "camera_fps" in stats
            },
            "rtsp_inputs": {k: redact_url(v) for k, v in frigate_rtsp.items()},
        },
        "camera_mapping": camera_mapping,
        "runtime_reachability": reachability,
    }


def _match_scrypted_id(
    cam_name: str, scrypted_cameras: list[dict[str, Any]], frigate_rtsp: dict[str, str]
) -> str | None:
    """Try to match a Frigate camera name to a Scrypted device ID.

    Heuristic: if the Frigate RTSP URL contains a Scrypted rebroadcast port,
    we can't directly map it. Instead, we rely on the camera name matching
    the Scrypted device name. For now, return the first valid Scrypted ID
    if there's only one camera, or None if ambiguous.
    """
    # TODO: improve matching by cross-referencing Scrypted rebroadcast ports
    # with Frigate RTSP input URLs. For now, return None and let the user
    # configure the mapping via the settings page.
    if len(scrypted_cameras) == 1:
        return scrypted_cameras[0]["device_id"]
    return None


def _http_reachable(url: str, timeout: float = 5.0) -> bool:
    """Check if an HTTP(S) host is reachable."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return tcp_reachable(host, port, timeout=timeout)
    except (ValueError, socket.gaierror):
        return False
