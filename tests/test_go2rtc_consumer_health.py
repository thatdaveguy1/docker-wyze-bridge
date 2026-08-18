"""Regression tests for consumer-facing go2rtc health checks."""

import io
import json
import signal
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "app" / "wyzebridge"
sys.path.insert(0, str(HELPERS))

import go2rtc_consumer_health as health  # noqa: E402
from go2rtc_consumer_health import (  # noqa: E402
    ConsumerHealthState,
    active_aliases,
    jpeg_is_decodable_candidate,
    probe_decoded_frame,
    restart_go2rtc_child,
    rtsp_sdp_has_h264_video,
)


def _rtsp_response(sdp: str, status: str = "200 OK") -> str:
    return f"RTSP/1.0 {status}\r\nCSeq: 1\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}"


def _valid_h264_sdp() -> str:
    return (
        "v=0\r\n"
        "m=video 0 RTP/AVP 96\r\n"
        "a=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1;profile-level-id=4D4034;"
        "sprop-parameter-sets=Z01ANJp0ASAFGQgAAB9AAAF3ACA=,aO48MA==\r\n"
    )


def _jpeg_bytes(size: tuple[int, int] = (640, 360)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "black").save(output, format="JPEG", quality=90)
    return output.getvalue()


def test_rtsp_sdp_requires_h264_video_track_and_parameter_sets():
    assert rtsp_sdp_has_h264_video(_rtsp_response(_valid_h264_sdp()))

    audio_only = "v=0\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 MPEG4-GENERIC/48000\r\n"
    assert not rtsp_sdp_has_h264_video(_rtsp_response(audio_only))

    h264_without_fmtp = "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
    assert not rtsp_sdp_has_h264_video(_rtsp_response(h264_without_fmtp))

    h264_with_invalid_parameter_sets = (
        "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1;sprop-parameter-sets=not-base64,still-not-base64\r\n"
    )
    assert not rtsp_sdp_has_h264_video(_rtsp_response(h264_with_invalid_parameter_sets))

    h265 = (
        "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H265/90000\r\n"
        "a=fmtp:96 sprop-vps=AAAA;sprop-sps=AAAA;sprop-pps=AAAA\r\n"
    )
    assert not rtsp_sdp_has_h264_video(_rtsp_response(h265))
    assert not rtsp_sdp_has_h264_video(_rtsp_response(_valid_h264_sdp(), status="404 Not Found"))


def test_jpeg_probe_requires_actual_decodable_frame():
    valid = _jpeg_bytes()
    assert len(valid) >= 2048
    assert jpeg_is_decodable_candidate(valid)

    fake = b"\xff\xd8" + (b"not-a-jpeg" * 300) + b"\xff\xd9"
    assert len(fake) >= 2048
    assert not jpeg_is_decodable_candidate(fake)
    assert not jpeg_is_decodable_candidate(b"\xff\xd8tiny\xff\xd9")
    assert not jpeg_is_decodable_candidate(b"x" * 3000)
    assert not jpeg_is_decodable_candidate(valid[:-2])


def test_probe_decoded_frame_uses_real_jpeg_validation(monkeypatch):
    monkeypatch.setattr(health, "_http_get", lambda *_args, **_kwargs: _jpeg_bytes())
    ok, detail = probe_decoded_frame("garage-sd", "http://127.0.0.1:11984")
    assert ok is True
    assert detail.startswith("frame_ok_bytes=")

    fake = b"\xff\xd8" + (b"not-a-jpeg" * 300) + b"\xff\xd9"
    monkeypatch.setattr(health, "_http_get", lambda *_args, **_kwargs: fake)
    ok, detail = probe_decoded_frame("garage-sd", "http://127.0.0.1:11984")
    assert ok is False
    assert detail == f"frame_invalid_bytes={len(fake)}"


def test_active_aliases_only_returns_streams_with_producers(monkeypatch):
    payload = json.dumps(
        {
            "garage-sd": {"producers": [{"url": "wyze://garage"}]},
            "hamster-sd": {"producers": []},
            "south-yard-sd": {"producers": [{"url": "wyze://south"}]},
            "metadata": "ignored",
        }
    ).encode()
    monkeypatch.setattr(health, "_http_get", lambda *_args, **_kwargs: payload)
    assert active_aliases("http://127.0.0.1:11984") == ["garage-sd", "south-yard-sd"]


def test_restart_targets_only_exact_go2rtc_processes(tmp_path, monkeypatch):
    for pid, comm in (("101", "go2rtc"), ("102", "go2rtc-helper"), ("103", "python3")):
        proc = tmp_path / pid
        proc.mkdir()
        (proc / "comm").write_text(f"{comm}\n", encoding="utf-8")
    (tmp_path / "not-a-pid").mkdir()

    signalled: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(health.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    assert restart_go2rtc_child(tmp_path) == 1
    assert signalled == [(101, signal.SIGTERM)]


def test_sidecar_does_not_use_stream_to_camera_post_as_restart():
    sidecar = (ROOT / "app" / "go2rtc_sidecar.sh").read_text(encoding="utf-8")
    assert "/api/streams?src=&dst=" not in sidecar


def test_single_consumer_failure_crosses_recovery_threshold_after_two_cycles():
    state = ConsumerHealthState(failure_threshold=2, process_threshold=3)

    aliases, process = state.record_cycle({"garage-sd": False, "hamster-sd": True})
    assert aliases == []
    assert process is False

    aliases, process = state.record_cycle({"garage-sd": False, "hamster-sd": True})
    assert aliases == ["garage-sd"]
    assert process is False


def test_shared_consumer_failure_escalates_to_process_restart():
    state = ConsumerHealthState(failure_threshold=2, process_threshold=3)
    all_failed = {"garage-sd": False, "hamster-sd": False, "south-yard-sd": False}

    aliases, process = state.record_cycle(all_failed)
    assert aliases == []
    assert process is False

    aliases, process = state.record_cycle(all_failed)
    assert set(aliases) == set(all_failed)
    assert process is False

    aliases, process = state.record_cycle(all_failed)
    assert aliases == []
    assert process is True


def test_one_healthy_alias_prevents_shared_process_restart():
    state = ConsumerHealthState(failure_threshold=2, process_threshold=3)
    for _ in range(5):
        _, process = state.record_cycle({"garage-sd": False, "hamster-sd": True})
        assert process is False
    assert state.shared_failure_cycles == 0


def test_recovery_resets_alias_and_shared_failure_state():
    state = ConsumerHealthState(failure_threshold=3, process_threshold=3)
    state.record_cycle({"garage-sd": False})
    state.record_cycle({"garage-sd": False})
    aliases, process = state.record_cycle({"garage-sd": True})
    assert aliases == []
    assert process is False
    assert state.failures["garage-sd"] == 0
    assert state.shared_failure_cycles == 0
