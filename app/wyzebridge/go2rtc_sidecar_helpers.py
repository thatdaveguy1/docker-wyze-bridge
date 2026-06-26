#!/usr/bin/env python3
"""Helper functions extracted from go2rtc_sidecar.sh embedded Python.

Architecture review candidate #12: move inline `python3 -c`/heredoc
snippets out of the shell script into a reusable module. The shell
script calls these via `python3 -c "from go2rtc_sidecar_helpers import ..."`.
"""

import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# NOTE: Do NOT import the stdlib `logging` module here. When PYTHONPATH includes
# this directory, `import logging` resolves to the local wyzebridge/logging.py
# which triggers a circular import (logging.py -> bridge_utils.py -> logging.getLogger
# before the local logging module is fully initialized). Use _log() instead.
def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)

_TRUTHY = {"1", "true", "yes", "on"}


def get_ha_option(var_name: str) -> str:
    """Read a single option from /data/options.json by key name."""
    try:
        with open("/data/options.json") as f:
            data = json.load(f)
        return str(data.get(var_name, "")).strip()
    except (OSError, ValueError, AttributeError, TypeError):  # file missing, invalid JSON, or non-dict payload
        return ""


def list_active_producers() -> str:
    """Read go2rtc streams JSON from stdin, return comma-separated active aliases."""
    try:
        data = json.load(sys.stdin)
        return ",".join(
            sorted(name for name, details in data.items() if isinstance(details, dict) and details.get("producers"))
        )
    except (ValueError, AttributeError, TypeError):  # invalid JSON or non-dict stream table
        return ""


def list_active_producers_verbose() -> None:
    """Read go2rtc streams JSON from stdin, print active producer aliases list."""
    data = json.load(sys.stdin)
    active = sorted(name for name, details in data.items() if details.get("producers"))
    _log(f"GO2RTC Active producer aliases: {active}")


def extract_yaml_aliases(config_path: str) -> None:
    """Parse go2rtc YAML config and print top-level stream alias names."""
    path = Path(config_path)
    in_streams = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "streams:":
            in_streams = True
            continue
        if in_streams and line and not line.startswith((" ", "\t")):
            break
        if not in_streams:
            continue
        if line.startswith("  ") and line.rstrip().endswith(":") and not line.startswith("    "):
            print(line.strip()[:-1])


def url_host(url: str) -> str:
    """Extract hostname from a URL."""
    return (urllib.parse.urlsplit(url).hostname or "").strip()


def detect_outbound_ip(target: tuple[str, int] = ("8.8.8.8", 53)) -> str:
    """Detect the outbound source IP via a UDP connect probe."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(target)
        return sock.getsockname()[0]
    finally:
        sock.close()


def rewrite_go2rtc_config(config_path: str, api_port: str, rtsp_port: str) -> None:
    """Strip managed api/rtsp/webrtc sections and prepend fresh ones."""
    path = Path(config_path)
    if not path.exists():
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    managed = {"api", "rtsp", "webrtc"}
    kept: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if not line.startswith((" ", "\t")) and line.endswith(":") and line[:-1] in managed:
            i += 1
            while i < len(lines) and (not lines[i] or lines[i].startswith((" ", "\t"))):
                i += 1
            continue
        kept.append(line)
        i += 1

    while kept and not kept[0].strip():
        kept.pop(0)

    prefix = [
        "api:",
        f'  listen: ":{api_port}"',
        "rtsp:",
        f'  listen: ":{rtsp_port}"',
        "webrtc:",
        '  listen: "127.0.0.1:0"',
    ]

    path.write_text("\n".join(prefix + kept).rstrip() + "\n", encoding="utf-8")


def generate_initial_config(
    config_path: str,
    api_port: str,
    rtsp_port: str,
    wyze_email: str,
    api_id: str = "",
    api_key: str = "",
    wyze_password: str = "",
) -> None:
    """Generate the initial go2rtc YAML config with api/rtsp/webrtc/wyze sections."""
    config = "\n".join(
        [
            "api:",
            f'  listen: ":{api_port}"',
            "rtsp:",
            f'  listen: ":{rtsp_port}"',
            "webrtc:",
            '  listen: "127.0.0.1:0"',
            "log:",
            "  level: info",
            "wyze:",
            f"  {json.dumps(wyze_email)}:",
            f"    api_id: {json.dumps(api_id)}",
            f"    api_key: {json.dumps(api_key)}",
            f"    password: {json.dumps(wyze_password)}",
            "streams:",
            "",
        ]
    )
    Path(config_path).write_text(config, encoding="utf-8")


def resolve_bridge_api_token(wyze_email: str) -> str:
    """Resolve the bridge API token from cache files or derive from email."""
    for path in ("/config/wb_api", "/tokens/wb_api", ".runtime/tokens/wb_api"):
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            return token
    return base64.urlsafe_b64encode(hashlib.sha256(wyze_email.encode()).digest()).decode()[:40]


def payload_has_cameras(payload_json: str) -> bool:
    """Check if a bridge /api JSON payload contains a non-empty cameras dict."""
    try:
        payload = json.loads(payload_json)
    except (ValueError, TypeError):
        return False
    cameras = payload.get("cameras") if isinstance(payload, dict) else None
    return isinstance(cameras, dict) and bool(cameras)


# --- Alias seeding logic (extracted from go2rtc_sidecar.sh heredoc) ---


def _clean_cam_name(name: str, uri_sep: str = "-") -> str:
    return re.sub(r"[^-\w+]", "", name.strip().replace(" ", uri_sep)).encode("ascii", "ignore").decode().lower()


def _with_subtype(url: str, subtype: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(key, value) for key, value in query if key != "subtype"]
    filtered.append(("subtype", subtype))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(filtered)))


def _normalize_mac(value: str) -> str:
    return re.sub(r"[^0-9A-F]", "", str(value or "").upper())


def _lan_ip_overrides() -> dict[str, str]:
    overrides = {}
    raw = os.environ.get("GO2RTC_LAN_IP_OVERRIDES", "")
    if not raw:
        try:
            with open("/data/options.json", encoding="utf-8") as fh:
                options = json.load(fh)
            raw = str(options.get("GO2RTC_LAN_IP_OVERRIDES") or "")
        except (OSError, ValueError, TypeError):
            raw = ""
    for item in raw.split(","):
        if "=" not in item:
            continue
        mac, host = item.split("=", 1)
        mac = _normalize_mac(mac)
        host = host.strip()
        if mac and host:
            overrides[mac] = host
    if overrides:
        _log(f"GO2RTC LAN IP overrides loaded for {len(overrides)} camera(s)")
    return overrides


def _option_string(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        with open("/data/options.json", encoding="utf-8") as fh:
            options = json.load(fh)
    except (OSError, ValueError, TypeError):
        return ""
    return str(options.get(name) or "").strip()


def _alias_name_set(name: str) -> set[str]:
    raw = _option_string(name)
    return {_clean_cam_name(item) for item in raw.split(",") if _clean_cam_name(item)}


def _with_verbose(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(key, value) for key, value in query if key != "verbose"]
    filtered.append(("verbose", "true"))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(filtered)))


def _parse_diagnostic_aliases() -> list[tuple[str, str, str]]:
    parsed_aliases = []
    for item in _option_string("GO2RTC_DIAGNOSTIC_ALIASES").split(","):
        item = item.strip()
        if "=" not in item:
            continue
        alias_name, source_spec = item.split("=", 1)
        alias_name = _clean_cam_name(alias_name)
        source_spec = source_spec.strip()
        if not alias_name or ":" not in source_spec:
            continue
        source_name, subtype = source_spec.rsplit(":", 1)
        source_name = _clean_cam_name(source_name)
        subtype = subtype.strip().lower()
        if not source_name or subtype not in {"hd", "sd"}:
            continue
        parsed_aliases.append((alias_name, source_name, subtype))
    return parsed_aliases


def _camera_mac(cam: dict) -> str:
    value = cam.get("mac") or cam.get("mac_address")
    if value:
        return _normalize_mac(value)
    parsed = urllib.parse.urlsplit(str(cam.get("url") or ""))
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("mac", "mac_address"):
        values = query.get(key) or []
        if values:
            return _normalize_mac(values[0])
    for part in str(cam.get("info") or "").split("|"):
        candidate = _normalize_mac(part)
        if len(candidate) == 12:
            return candidate
    return ""


def _is_private_lan_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return False
    return ip.is_private


def _force_lan_ip_overrides() -> bool:
    return str(os.environ.get("GO2RTC_FORCE_LAN_IP_OVERRIDES", "")).strip().lower() in _TRUTHY


def _with_lan_ip_override(url: str, cam: dict) -> str:
    ip = _lan_ip_overrides().get(_camera_mac(cam))
    if not ip:
        return url
    parsed = urllib.parse.urlsplit(url)
    if _is_private_lan_host(parsed.hostname or "") and not _force_lan_ip_overrides():
        _log(
            f"GO2RTC LAN override for {cam.get('name', '<unknown>')} is configured, "
            f"but keeping helper LAN host {parsed.hostname}; set GO2RTC_FORCE_LAN_IP_OVERRIDES=true to force it"
        )
        return url
    return urllib.parse.urlunsplit(parsed._replace(netloc=ip))


def _parse_model(info: str, url: str) -> str:
    if info:
        model = str(info).split("|", 1)[0].strip()
        if model:
            return model
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    return (query.get("model") or [""])[0].strip()


def _helper_flag(cam: dict, key: str):
    if key not in cam:
        return None
    value = cam.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUTHY


def _bridge_api_token(email: str) -> str:
    return resolve_bridge_api_token(email)


def _fetch_json(url: str, timeout: float = 2.0, api_token: str = ""):
    try:
        request = urllib.request.Request(url)
        if api_token:
            request.add_header("api", api_token)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError):
        return None


_BRIDGE_CAMERA_CATALOG = None


def _bridge_camera_catalog() -> dict | None:
    global _BRIDGE_CAMERA_CATALOG
    if _BRIDGE_CAMERA_CATALOG is not None:
        return _BRIDGE_CAMERA_CATALOG

    base_url = os.environ.get("WB_APP_API_BASE", "").rstrip("/")
    if not base_url:
        return None

    api_token = _bridge_api_token(os.environ["WYZE_EMAIL"])
    payload = _fetch_json(f"{base_url}/api", api_token=api_token)
    if not isinstance(payload, dict):
        return None
    cameras = payload.get("cameras") if isinstance(payload, dict) else None
    _BRIDGE_CAMERA_CATALOG = cameras if isinstance(cameras, dict) else {}
    return _BRIDGE_CAMERA_CATALOG


def _bridge_published_entries(cam_uri: str):
    catalog = _bridge_camera_catalog()
    if catalog is None:
        return None

    published = []
    for uri, camera in catalog.items():
        if not isinstance(camera, dict):
            continue
        base_uri = _clean_cam_name(camera.get("camera_uri") or camera.get("name_uri") or "")
        if base_uri == cam_uri:
            published.append((uri, camera))
    return published


def _bridge_camera_state(cam_uri: str) -> dict:
    base_url = os.environ.get("WB_APP_API_BASE", "").rstrip("/")
    if not base_url:
        return {}

    api_token = _bridge_api_token(os.environ["WYZE_EMAIL"])
    cam_path = urllib.parse.quote(cam_uri, safe="")
    state = {}

    catalog = _bridge_camera_catalog()
    published = _bridge_published_entries(cam_uri)
    bridge_catalog_empty = isinstance(catalog, dict) and not catalog
    if bridge_catalog_empty:
        published = None
    if published is not None:
        enabled_entries = [(uri, camera) for uri, camera in published if bool(camera.get("enabled"))]
        state["published"] = bool(enabled_entries)
        state["enabled"] = bool(enabled_entries)
        state["hd"] = any(
            _clean_cam_name(camera.get("name_uri") or uri) == cam_uri and not bool(camera.get("substream"))
            for uri, camera in enabled_entries
        )
        state["sd"] = any(
            bool(camera.get("substream")) or _clean_cam_name(camera.get("name_uri") or uri) != cam_uri
            for uri, camera in enabled_entries
        )

    config = _fetch_json(f"{base_url}/api/{cam_path}/stream-config", api_token=api_token)
    feeds = config.get("feeds") if isinstance(config, dict) else None
    if isinstance(config, dict) and config.get("native_preview_sd"):
        state["sd"] = True
        state["sd_supported"] = True
    if isinstance(feeds, dict):
        sd_only = bool(config.get("sd_only")) if isinstance(config, dict) else False
        for feed_name in ("hd", "sd"):
            feed = feeds.get(feed_name)
            if not isinstance(feed, dict):
                continue
            if "enabled" in feed:
                if not feed.get("enabled"):
                    state[feed_name] = False
                elif sd_only:
                    state[feed_name] = bool(feed.get("enabled"))
                elif published is None or feed.get("path") == "native":
                    state[feed_name] = feed.get("enabled")
                elif not state.get(feed_name, False):
                    state[feed_name] = False
            if "supported" in feed:
                state[f"{feed_name}_supported"] = feed.get("supported")
    if "enabled" not in state:
        state["enabled"] = bool(state.get("hd") or state.get("sd"))
    elif not state.get("enabled") and (state.get("hd") or state.get("sd")):
        state["enabled"] = True
    return state


def seed_go2rtc_aliases() -> None:
    """Read camera JSON, build go2rtc config with native aliases, write to GO2RTC_CONFIG."""
    with open(os.environ["GO2RTC_CAM_JSON_FILE"], encoding="utf-8") as fh:
        data = json.load(fh)
    cams = data.get("sources", data) if isinstance(data, dict) else data
    lines = [
        "api:",
        f'  listen: ":{os.environ["GO2RTC_API_PORT"]}"',
        "rtsp:",
        f'  listen: ":{os.environ["GO2RTC_RTSP_PORT"]}"',
        "webrtc:",
        '  listen: "127.0.0.1:0"',
        "log:",
        "  level: info",
        "wyze:",
        f"  {json.dumps(os.environ['WYZE_EMAIL'])}:",
        f"    api_id: {json.dumps(os.environ.get('API_ID', ''))}",
        f"    api_key: {json.dumps(os.environ.get('API_KEY', ''))}",
        f"    password: {json.dumps(os.environ['WYZE_PASSWORD'])}",
        "streams:",
    ]

    added = 0
    seen = set()
    prepared = {}
    verbose_aliases = _alias_name_set("GO2RTC_WYZE_VERBOSE_ALIASES")
    for cam in cams:
        name = cam.get("name", "")
        url = cam.get("url", "")
        info = cam.get("info", "")
        if not name or not url:
            continue
        uri = _clean_cam_name(name)
        if not uri or uri in seen:
            continue
        seen.add(uri)
        bridge_state = _bridge_camera_state(uri)
        for key, value in bridge_state.items():
            cam.setdefault(key, value)
        published = _helper_flag(cam, "published")
        if published is False and _helper_flag(cam, "hd") is False and _helper_flag(cam, "sd") is False:
            _log(f"GO2RTC Skipping camera not published by bridge: {name}")
            continue
        enabled = _helper_flag(cam, "enabled")
        if enabled is False:
            _log(f"GO2RTC Skipping disabled camera from helper: {name}")
            continue
        model = _parse_model(info, url)
        hd_enabled = _helper_flag(cam, "hd")
        sd_enabled = _helper_flag(cam, "sd")
        hd_supported = _helper_flag(cam, "hd_supported")
        sd_supported = _helper_flag(cam, "sd_supported")

        if hd_supported is None and model == "HL_BC":
            hd_supported = False
        if sd_supported is None and model == "HL_BC":
            sd_supported = True
        if model == "HL_BC" and sd_enabled is False and sd_supported:
            sd_enabled = None

        aliases = []
        if hd_enabled is not False and hd_supported is not False:
            aliases.append((uri, "hd"))
        if sd_enabled is not False and sd_supported is not False:
            aliases.append((f"{uri}-sd", "sd"))

        if not aliases:
            _log(f"GO2RTC Skipping camera with no enabled native feeds: {name} ({info})")
            continue

        for alias, subtype in aliases:
            stream_url = _with_subtype(_with_lan_ip_override(url, cam), subtype)
            if alias in verbose_aliases:
                stream_url = _with_verbose(stream_url)
            lines.append(f"  {alias}:")
            lines.append(f"    - {stream_url}")
            _log(f"GO2RTC Prepared stream: {alias} ({info}) subtype={subtype}")
            prepared[alias] = stream_url
            added += 1

        if model == "HL_CAM4" and uri == "north-yard":
            recovery_alias = f"{uri}-v4-hd-recovery"
            recovery_source = prepared.get(f"{uri}-sd")
            if recovery_source and recovery_alias not in prepared:
                recovery_url = _with_subtype(recovery_source, "hd")
                if recovery_alias in verbose_aliases:
                    recovery_url = _with_verbose(recovery_url)
                lines.append(f"  {recovery_alias}:")
                lines.append(f"    - {recovery_url}")
                _log(f"GO2RTC Prepared North Yard recovery stream: {recovery_alias} from {uri}-sd subtype=hd")
                prepared[recovery_alias] = recovery_url
                seen.add(recovery_alias)
                added += 1

    for alias_name, source_name, subtype in _parse_diagnostic_aliases():
        if alias_name in seen or alias_name in prepared:
            _log(f"GO2RTC Skipping duplicate diagnostic alias: {alias_name}")
            continue
        source_url = prepared.get(source_name)
        if not source_url:
            _log(f"GO2RTC Skipping diagnostic alias {alias_name}: source {source_name} not prepared")
            continue
        stream_url = _with_subtype(source_url, subtype)
        if alias_name in verbose_aliases:
            stream_url = _with_verbose(stream_url)
        lines.append(f"  {alias_name}:")
        lines.append(f"    - {stream_url}")
        _log(f"GO2RTC Prepared diagnostic stream: {alias_name} from {source_name} subtype={subtype}")
        prepared[alias_name] = stream_url
        seen.add(alias_name)
        added += 1

    Path(os.environ["GO2RTC_CONFIG"]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log(f"GO2RTC Total aliases prepared in config: {added}")


if __name__ == "__main__":
    # Dispatch based on argv[1] for shell script compatibility.
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ha_option":
        print(get_ha_option(sys.argv[2]))
    elif cmd == "active_producers":
        print(list_active_producers())
    elif cmd == "active_producers_verbose":
        list_active_producers_verbose()
    elif cmd == "yaml_aliases":
        extract_yaml_aliases(os.environ["GO2RTC_CONFIG"])
    elif cmd == "url_host":
        print(url_host(sys.argv[2]))
    elif cmd == "outbound_ip":
        print(detect_outbound_ip())
