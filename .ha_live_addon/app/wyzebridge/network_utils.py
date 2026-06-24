"""Network diagnostic utilities — extracted from frontend.py.

Architecture review candidate #14: network probing helpers (DNS resolution,
route parsing, TUTK library host scanning, outbound IP detection) separated
from Flask route handlers. frontend.py imports these for the /health/details
route.
"""

import os
import re
import socket
import time
from functools import lru_cache
from urllib.parse import urlsplit

from wyzebridge.bridge_utils import truthy

WYZE_DNS_URLS = (
    "https://auth-prod.api.wyze.com",
    "https://api.wyzecam.com/app",
    "https://app-core.cloud.wyze.com/app",
    "https://app.wyzecam.com/app",
    "https://devicemgmt-service.wyze.com",
    "https://webrtc.api.wyze.com",
)
WEBRTC_SIGNAL_API = "https://webrtc.api.wyze.com"
TUTK_HOST_SCAN_PATHS = (
    "/usr/local/lib/libIOTCAPIs_ALL.so",
    "/usr/local/lib/libAVAPIs.so",
)
TUTK_HOST_KEYWORDS = ("iotc", "tutk", "throughtek", "kalay")
HOSTNAME_PATTERN = re.compile(rb"(?<![A-Za-z0-9-])([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def _truthy_query_value(value: str | None) -> bool:
    return truthy(value)


def _parse_resolv_conf(path: str = "/etc/resolv.conf") -> dict:
    data = {"path": path, "nameservers": [], "search": [], "options": []}
    try:
        with open(path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                key, *values = line.split()
                if key == "nameserver" and values:
                    data["nameservers"].append(values[0])
                elif key == "search":
                    data["search"] = values
                elif key == "options":
                    data["options"] = values
    except OSError as ex:
        data["error"] = f"{type(ex).__name__}: {ex}"
    return data


def _decode_route_ipv4(hex_value: str) -> str:
    return socket.inet_ntoa(bytes.fromhex(hex_value)[::-1])


def _parse_default_routes(path: str = "/proc/net/route") -> dict:
    routes = {"path": path, "default": []}
    try:
        with open(path, encoding="utf-8") as handle:
            next(handle, None)
            for raw_line in handle:
                fields = raw_line.split()
                if len(fields) < 4:
                    continue
                iface, destination_hex, gateway_hex, flags_hex = fields[:4]
                if destination_hex != "00000000":
                    continue
                routes["default"].append(
                    {
                        "interface": iface,
                        "gateway": _decode_route_ipv4(gateway_hex),
                        "flags": flags_hex,
                    }
                )
    except OSError as ex:
        routes["error"] = f"{type(ex).__name__}: {ex}"
    return routes


def _detect_outbound_ipv4(target: tuple[str, int] = ("8.8.8.8", 53)) -> dict:
    probe = {"target": f"{target[0]}:{target[1]}", "source_ip": None}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(target)
        probe["source_ip"] = sock.getsockname()[0]
    except OSError as ex:
        probe["error"] = f"{type(ex).__name__}: {ex}"
    finally:
        sock.close()
    return probe


def _host_from_url(value: str) -> str | None:
    host = urlsplit(value).hostname
    return host.lower() if host else None


def _is_plausible_hostname(host: str) -> bool:
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return False
    tld = labels[-1]
    return len(tld) >= 2 and tld.isalpha()


@lru_cache(maxsize=1)
def _tutk_library_hosts() -> tuple[str, ...]:
    hosts: set[str] = set()
    for path in TUTK_HOST_SCAN_PATHS:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        for match in HOSTNAME_PATTERN.finditer(data):
            host = match.group(1).decode("ascii", "ignore").lower().strip(".")
            if _is_plausible_hostname(host) and any(keyword in host for keyword in TUTK_HOST_KEYWORDS):
                hosts.add(host)
    return tuple(sorted(hosts))


def _candidate_dns_targets() -> list[str]:
    hosts = {"homeassistant.local"}
    for url in WYZE_DNS_URLS:
        if host := _host_from_url(url):
            hosts.add(host)
    hosts.update(_tutk_library_hosts())
    return sorted(hosts)


def _socket_enum_name(value: int, prefix: str) -> str:
    for name in dir(socket):
        if name.startswith(prefix) and getattr(socket, name, object()) == value:
            return name
    return str(value)


def _resolve_dns_target(host: str, port: int = 443) -> dict:
    result = {"host": host, "port": port, "addresses": []}
    started = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        seen: set[tuple[int, int, int, str | None]] = set()
        for family, socktype, proto, _canonname, sockaddr in infos:
            address = sockaddr[0] if sockaddr else None
            key = (family, socktype, proto, address)
            if key in seen:
                continue
            seen.add(key)
            result["addresses"].append(
                {
                    "family": _socket_enum_name(family, "AF_"),
                    "socktype": _socket_enum_name(socktype, "SOCK_"),
                    "proto": proto,
                    "address": address,
                }
            )
        result["reachable"] = bool(result["addresses"])
    except OSError as ex:
        result["reachable"] = False
        result["error"] = f"{type(ex).__name__}: {ex}"
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def network_snapshot() -> dict:
    return {
        "hostname": socket.gethostname(),
        "wb_ip": os.getenv("WB_IP"),
        "outbound_ipv4": _detect_outbound_ipv4(),
        "resolv_conf": _parse_resolv_conf(),
        "routes": _parse_default_routes(),
        "dns": {
            "targets": [_resolve_dns_target(host) for host in _candidate_dns_targets()],
            "tutk_library_hosts": list(_tutk_library_hosts()),
        },
    }
