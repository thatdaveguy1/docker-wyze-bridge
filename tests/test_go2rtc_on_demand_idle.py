"""Regressions for go2rtc on-demand streams that are intentionally idle."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "app" / "wyzebridge"
sys.path.insert(0, str(HELPERS))

import go2rtc_consumer_health as health  # noqa: E402


def test_consumer_watchdog_skips_url_only_idle_producer(monkeypatch):
    payload = json.dumps(
        {
            "idle": {"producers": [{"url": "ffmpeg:virtual?video=testsrc"}], "consumers": []},
            "connected": {
                "producers": [
                    {
                        "url": "ffmpeg:virtual?video=testsrc",
                        "format_name": "rtsp",
                        "protocol": "rtsp+tcp",
                        "remote_addr": "127.0.0.1:12345",
                        "medias": [{"kind": "video"}],
                        "receivers": [{"bytes": 1000}],
                    }
                ],
                "consumers": [],
            },
            "demanded": {
                "producers": [{"url": "wyze://camera"}],
                "consumers": [{"format_name": "rtsp"}],
            },
        }
    ).encode()
    monkeypatch.setattr(health, "_http_get", lambda *_args, **_kwargs: payload)

    assert health.consumer_probe_aliases("http://127.0.0.1:11984") == ["connected", "demanded"]


def test_connected_producer_requires_runtime_metadata():
    assert not health._producer_is_connected({"url": "wyze://camera"})
    assert health._producer_is_connected({"protocol": "wyze/dtls"})
    assert health._producer_is_connected({"medias": [{"kind": "video"}]})
    assert health._producer_is_connected({"receivers": [{"bytes": 1}]})


def test_sidecar_keeps_idle_on_demand_streams_out_of_recovery_loops():
    sidecar = (ROOT / "app" / "go2rtc_sidecar.sh").read_text(encoding="utf-8")

    assert "_go2rtc_stream_connected()" in sidecar
    assert 'alive_aliases=""' in sidecar
    assert 'if [ "${connected_producer}" = "0" ] && [ "${consumer_count}" = "0" ]; then' in sidecar
    assert "Intentional on-demand idle state" in sidecar
    # Health, proactive refresh, WiFi monitor, and snapshot canary all use the
    # same connected-vs-placeholder test instead of treating a URL object as live.
    assert sidecar.count('_go2rtc_stream_connected "${alias}" "${streams_json}"') >= 4
