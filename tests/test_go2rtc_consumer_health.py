"""Regression tests for consumer-facing go2rtc health checks."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "app" / "wyzebridge"
sys.path.insert(0, str(HELPERS))

from go2rtc_consumer_health import (  # noqa: E402
    ConsumerHealthState,
    jpeg_is_decodable_candidate,
    rtsp_sdp_has_h264_video,
)


def _rtsp_response(sdp: str, status: str = "200 OK") -> str:
    return f"RTSP/1.0 {status}\r\nCSeq: 1\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}"


def test_rtsp_sdp_requires_h264_video_track():
    good = "\r\nv=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
    assert rtsp_sdp_has_h264_video(_rtsp_response(good))

    audio_only = "v=0\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 MPEG4-GENERIC/48000\r\n"
    assert not rtsp_sdp_has_h264_video(_rtsp_response(audio_only))

    video_without_codec = "v=0\r\nm=video 0 RTP/AVP 96\r\n"
    assert not rtsp_sdp_has_h264_video(_rtsp_response(video_without_codec))
    assert not rtsp_sdp_has_h264_video(_rtsp_response(good, status="404 Not Found"))


def test_jpeg_probe_requires_complete_nontrivial_frame():
    valid = b"\xff\xd8" + (b"x" * 3000) + b"\xff\xd9"
    assert jpeg_is_decodable_candidate(valid)
    assert not jpeg_is_decodable_candidate(b"\xff\xd8tiny\xff\xd9")
    assert not jpeg_is_decodable_candidate(b"x" * 3000)


def test_single_consumer_failure_restarts_only_failed_alias_after_two_cycles():
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
