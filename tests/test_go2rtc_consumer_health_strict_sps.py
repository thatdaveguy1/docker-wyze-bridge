"""Strict H.264 parameter-set regressions for the consumer watchdog."""

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "app" / "wyzebridge"
sys.path.insert(0, str(HELPERS))

from go2rtc_consumer_health import (  # noqa: E402
    _parse_h264_pps_sps_id,
    _parse_h264_sps,
    rtsp_sdp_has_h264_video,
)

VALID_SPS = "Z01ANJp0ASAFGQgAAB9AAAF3ACA="
VALID_PPS = "aO48MA=="


def _rtsp_response(sdp: str) -> str:
    return f"RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}"


def _h264_sdp(sps: bytes, pps: bytes) -> str:
    encoded_sps = base64.b64encode(sps).decode()
    encoded_pps = base64.b64encode(pps).decode()
    return (
        "v=0\r\n"
        "m=video 0 RTP/AVP 96\r\n"
        "a=rtpmap:96 H264/90000\r\n"
        f"a=fmtp:96 packetization-mode=1;sprop-parameter-sets={encoded_sps},{encoded_pps}\r\n"
    )


def test_rejects_nal_type_only_sps_pps():
    sdp = (
        "v=0\r\n"
        "m=video 0 RTP/AVP 96\r\n"
        "a=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1;sprop-parameter-sets=Bw==,CA==\r\n"
    )
    assert not rtsp_sdp_has_h264_video(_rtsp_response(sdp))


def test_parseable_parameter_sets_produce_dimensions_and_matching_ids():
    sps = base64.b64decode(VALID_SPS, validate=True)
    pps = base64.b64decode(VALID_PPS, validate=True)

    sps_id, width, height = _parse_h264_sps(sps)
    assert width > 0
    assert height > 0
    assert _parse_h264_pps_sps_id(pps) == sps_id


def test_parameter_sets_require_complete_rbsp_trailing_bits():
    sps = base64.b64decode(VALID_SPS, validate=True)
    pps = base64.b64decode(VALID_PPS, validate=True)

    with pytest.raises(ValueError):
        _parse_h264_sps(sps[:-1])
    with pytest.raises(ValueError):
        _parse_h264_pps_sps_id(pps[:-1])

    assert not rtsp_sdp_has_h264_video(_rtsp_response(_h264_sdp(sps[:-1], pps)))
    assert not rtsp_sdp_has_h264_video(_rtsp_response(_h264_sdp(sps, pps[:-1])))
