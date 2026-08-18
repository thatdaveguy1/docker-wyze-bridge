#!/usr/bin/env python3
"""Consumer-facing health checks for the native go2rtc sidecar.

The existing sidecar monitor primarily observes producer state and receiver-byte
progress. Those signals can remain healthy while a new RTSP consumer cannot
negotiate a usable H.264 stream. This module probes both the RTSP SDP exposed
to consumers and go2rtc's decoded-frame endpoint, then performs bounded recovery.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import signal
import socket
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError


def _log(message: str) -> None:
    print(f"[GO2RTC_CONSUMER] {message}", file=sys.stderr, flush=True)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class _BitReader:
    """Minimal MSB-first reader for H.264 Exp-Golomb parameter-set syntax."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._bit_offset = 0

    @property
    def bits_left(self) -> int:
        return len(self._data) * 8 - self._bit_offset

    def read_bits(self, count: int) -> int:
        if count < 0 or self._bit_offset + count > len(self._data) * 8:
            raise ValueError("truncated H.264 bitstream")
        value = 0
        for _ in range(count):
            byte = self._data[self._bit_offset // 8]
            shift = 7 - (self._bit_offset % 8)
            value = (value << 1) | ((byte >> shift) & 1)
            self._bit_offset += 1
        return value

    def read_bit(self) -> int:
        return self.read_bits(1)

    def read_ue(self) -> int:
        leading_zero_bits = 0
        while self.read_bit() == 0:
            leading_zero_bits += 1
            if leading_zero_bits > 31:
                raise ValueError("H.264 Exp-Golomb value too large")
        suffix = self.read_bits(leading_zero_bits) if leading_zero_bits else 0
        return (1 << leading_zero_bits) - 1 + suffix

    def read_se(self) -> int:
        code_num = self.read_ue()
        magnitude = (code_num + 1) // 2
        return magnitude if code_num % 2 else -magnitude

    def peek_bits(self, count: int) -> int:
        offset = self._bit_offset
        try:
            return self.read_bits(count)
        finally:
            self._bit_offset = offset

    def has_only_rbsp_trailing_bits(self) -> bool:
        """Return True when the unread suffix is exactly rbsp_stop_one_bit + zero padding."""
        if not 1 <= self.bits_left <= 8:
            return False
        return self.peek_bits(self.bits_left) == 1 << (self.bits_left - 1)

    def read_rbsp_trailing_bits(self) -> None:
        """Consume the mandatory H.264 rbsp_trailing_bits suffix."""
        if not self.has_only_rbsp_trailing_bits():
            raise ValueError("invalid or missing H.264 RBSP trailing bits")
        self.read_bit()  # rbsp_stop_one_bit
        while self.bits_left:
            if self.read_bit() != 0:
                raise ValueError("nonzero H.264 RBSP alignment bit")


def _ebsp_to_rbsp(payload: bytes) -> bytes:
    rbsp = bytearray()
    zero_count = 0
    for byte in payload:
        if zero_count >= 2 and byte == 0x03:
            zero_count = 0
            continue
        rbsp.append(byte)
        zero_count = zero_count + 1 if byte == 0 else 0
    return bytes(rbsp)


def _skip_scaling_list(reader: _BitReader, size: int) -> None:
    last_scale = 8
    next_scale = 8
    for _ in range(size):
        if next_scale != 0:
            delta_scale = reader.read_se()
            next_scale = (last_scale + delta_scale + 256) % 256
        last_scale = next_scale if next_scale != 0 else last_scale


def _skip_h264_hrd_parameters(reader: _BitReader) -> None:
    cpb_cnt_minus1 = reader.read_ue()
    if cpb_cnt_minus1 > 31:
        raise ValueError("invalid H.264 HRD CPB count")
    reader.read_bits(4)  # bit_rate_scale
    reader.read_bits(4)  # cpb_size_scale
    for _ in range(cpb_cnt_minus1 + 1):
        reader.read_ue()  # bit_rate_value_minus1
        reader.read_ue()  # cpb_size_value_minus1
        reader.read_bit()  # cbr_flag
    reader.read_bits(5)  # initial_cpb_removal_delay_length_minus1
    reader.read_bits(5)  # cpb_removal_delay_length_minus1
    reader.read_bits(5)  # dpb_output_delay_length_minus1
    reader.read_bits(5)  # time_offset_length


def _skip_h264_vui_parameters(reader: _BitReader) -> None:
    if reader.read_bit():  # aspect_ratio_info_present_flag
        aspect_ratio_idc = reader.read_bits(8)
        if aspect_ratio_idc == 255:  # Extended_SAR
            reader.read_bits(16)  # sar_width
            reader.read_bits(16)  # sar_height
    if reader.read_bit():  # overscan_info_present_flag
        reader.read_bit()  # overscan_appropriate_flag
    if reader.read_bit():  # video_signal_type_present_flag
        reader.read_bits(3)  # video_format
        reader.read_bit()  # video_full_range_flag
        if reader.read_bit():  # colour_description_present_flag
            reader.read_bits(8)  # colour_primaries
            reader.read_bits(8)  # transfer_characteristics
            reader.read_bits(8)  # matrix_coefficients
    if reader.read_bit():  # chroma_loc_info_present_flag
        reader.read_ue()  # chroma_sample_loc_type_top_field
        reader.read_ue()  # chroma_sample_loc_type_bottom_field
    if reader.read_bit():  # timing_info_present_flag
        reader.read_bits(32)  # num_units_in_tick
        reader.read_bits(32)  # time_scale
        reader.read_bit()  # fixed_frame_rate_flag

    nal_hrd_parameters_present = bool(reader.read_bit())
    if nal_hrd_parameters_present:
        _skip_h264_hrd_parameters(reader)
    vcl_hrd_parameters_present = bool(reader.read_bit())
    if vcl_hrd_parameters_present:
        _skip_h264_hrd_parameters(reader)
    if nal_hrd_parameters_present or vcl_hrd_parameters_present:
        reader.read_bit()  # low_delay_hrd_flag

    reader.read_bit()  # pic_struct_present_flag
    if reader.read_bit():  # bitstream_restriction_flag
        reader.read_bit()  # motion_vectors_over_pic_boundaries_flag
        reader.read_ue()  # max_bytes_per_pic_denom
        reader.read_ue()  # max_bits_per_mb_denom
        reader.read_ue()  # log2_max_mv_length_horizontal
        reader.read_ue()  # log2_max_mv_length_vertical
        reader.read_ue()  # max_num_reorder_frames
        reader.read_ue()  # max_dec_frame_buffering


def _parse_h264_sps_details(nal: bytes) -> tuple[int, int, int, int]:
    """Parse a complete SPS RBSP and return id, dimensions, and chroma format."""
    if len(nal) < 5 or nal[0] & 0x80 or nal[0] & 0x1F != 7:
        raise ValueError("invalid SPS NAL")

    reader = _BitReader(_ebsp_to_rbsp(nal[1:]))
    profile_idc = reader.read_bits(8)
    constraints = reader.read_bits(8)
    if constraints & 0x03:
        raise ValueError("reserved SPS constraint bits are nonzero")
    reader.read_bits(8)  # level_idc
    sps_id = reader.read_ue()
    if sps_id > 31:
        raise ValueError("invalid SPS id")

    chroma_format_idc = 1
    separate_colour_plane_flag = 0
    high_profiles = {44, 83, 86, 100, 110, 118, 122, 128, 134, 135, 138, 139, 244}
    if profile_idc in high_profiles:
        chroma_format_idc = reader.read_ue()
        if chroma_format_idc > 3:
            raise ValueError("invalid chroma_format_idc")
        if chroma_format_idc == 3:
            separate_colour_plane_flag = reader.read_bit()
        if reader.read_ue() > 6 or reader.read_ue() > 6:
            raise ValueError("invalid SPS bit depth")
        reader.read_bit()  # qpprime_y_zero_transform_bypass_flag
        if reader.read_bit():  # seq_scaling_matrix_present_flag
            scaling_lists = 8 if chroma_format_idc != 3 else 12
            for index in range(scaling_lists):
                if reader.read_bit():
                    _skip_scaling_list(reader, 16 if index < 6 else 64)

    if reader.read_ue() > 12:  # log2_max_frame_num_minus4
        raise ValueError("invalid frame_num range")
    pic_order_cnt_type = reader.read_ue()
    if pic_order_cnt_type == 0:
        if reader.read_ue() > 12:
            raise ValueError("invalid POC range")
    elif pic_order_cnt_type == 1:
        reader.read_bit()  # delta_pic_order_always_zero_flag
        reader.read_se()  # offset_for_non_ref_pic
        reader.read_se()  # offset_for_top_to_bottom_field
        cycle_count = reader.read_ue()
        if cycle_count > 255:
            raise ValueError("invalid POC cycle count")
        for _ in range(cycle_count):
            reader.read_se()
    elif pic_order_cnt_type != 2:
        raise ValueError("invalid pic_order_cnt_type")

    reader.read_ue()  # max_num_ref_frames
    reader.read_bit()  # gaps_in_frame_num_value_allowed_flag
    pic_width_in_mbs_minus1 = reader.read_ue()
    pic_height_in_map_units_minus1 = reader.read_ue()
    if pic_width_in_mbs_minus1 > 4095 or pic_height_in_map_units_minus1 > 4095:
        raise ValueError("SPS dimensions are unreasonable")

    frame_mbs_only_flag = reader.read_bit()
    if not frame_mbs_only_flag:
        reader.read_bit()  # mb_adaptive_frame_field_flag
    reader.read_bit()  # direct_8x8_inference_flag

    crop_left = crop_right = crop_top = crop_bottom = 0
    if reader.read_bit():  # frame_cropping_flag
        crop_left = reader.read_ue()
        crop_right = reader.read_ue()
        crop_top = reader.read_ue()
        crop_bottom = reader.read_ue()

    if reader.read_bit():  # vui_parameters_present_flag
        _skip_h264_vui_parameters(reader)
    reader.read_rbsp_trailing_bits()

    width = (pic_width_in_mbs_minus1 + 1) * 16
    height = (pic_height_in_map_units_minus1 + 1) * 16 * (2 - frame_mbs_only_flag)
    chroma_array_type = 0 if separate_colour_plane_flag else chroma_format_idc
    if chroma_array_type == 0:
        crop_unit_x = 1
        crop_unit_y = 2 - frame_mbs_only_flag
    else:
        sub_width_c = 1 if chroma_array_type == 3 else 2
        sub_height_c = 2 if chroma_array_type == 1 else 1
        crop_unit_x = sub_width_c
        crop_unit_y = sub_height_c * (2 - frame_mbs_only_flag)
    width -= (crop_left + crop_right) * crop_unit_x
    height -= (crop_top + crop_bottom) * crop_unit_y
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise ValueError("invalid decoded SPS dimensions")
    return sps_id, width, height, chroma_format_idc


def _parse_h264_sps(nal: bytes) -> tuple[int, int, int]:
    """Parse a complete SPS and return its id and positive coded dimensions."""
    sps_id, width, height, _ = _parse_h264_sps_details(nal)
    return sps_id, width, height


def _parse_h264_pps_sps_id(nal: bytes, chroma_format_idc: int = 1) -> int:
    """Parse a complete PPS RBSP and return the referenced SPS id."""
    if len(nal) < 2 or nal[0] & 0x80 or nal[0] & 0x1F != 8:
        raise ValueError("invalid PPS NAL")
    if not 0 <= chroma_format_idc <= 3:
        raise ValueError("invalid PPS chroma format")

    reader = _BitReader(_ebsp_to_rbsp(nal[1:]))
    pps_id = reader.read_ue()
    sps_id = reader.read_ue()
    if pps_id > 255 or sps_id > 31:
        raise ValueError("invalid PPS/SPS id")
    reader.read_bit()  # entropy_coding_mode_flag
    reader.read_bit()  # bottom_field_pic_order_in_frame_present_flag
    num_slice_groups_minus1 = reader.read_ue()
    if num_slice_groups_minus1 > 7:
        raise ValueError("invalid slice group count")
    if num_slice_groups_minus1:
        slice_group_map_type = reader.read_ue()
        if slice_group_map_type == 0:
            for _ in range(num_slice_groups_minus1 + 1):
                reader.read_ue()
        elif slice_group_map_type == 2:
            for _ in range(num_slice_groups_minus1):
                reader.read_ue()
                reader.read_ue()
        elif slice_group_map_type in {3, 4, 5}:
            reader.read_bit()
            reader.read_ue()
        elif slice_group_map_type == 6:
            pic_size_in_map_units_minus1 = reader.read_ue()
            if pic_size_in_map_units_minus1 > 65535:
                raise ValueError("invalid slice-group map size")
            group_count = num_slice_groups_minus1 + 1
            bits_per_group = max(1, (group_count - 1).bit_length())
            for _ in range(pic_size_in_map_units_minus1 + 1):
                reader.read_bits(bits_per_group)
        else:
            raise ValueError("invalid slice_group_map_type")

    if reader.read_ue() > 31 or reader.read_ue() > 31:
        raise ValueError("invalid default reference-index count")
    reader.read_bit()  # weighted_pred_flag
    reader.read_bits(2)  # weighted_bipred_idc
    pic_init_qp_minus26 = reader.read_se()
    pic_init_qs_minus26 = reader.read_se()
    chroma_qp_index_offset = reader.read_se()
    if not -26 <= pic_init_qp_minus26 <= 25 or not -26 <= pic_init_qs_minus26 <= 25:
        raise ValueError("invalid PPS initial QP")
    if not -12 <= chroma_qp_index_offset <= 12:
        raise ValueError("invalid PPS chroma QP offset")
    reader.read_bit()  # deblocking_filter_control_present_flag
    reader.read_bit()  # constrained_intra_pred_flag
    reader.read_bit()  # redundant_pic_cnt_present_flag

    if not reader.has_only_rbsp_trailing_bits():
        transform_8x8_mode_flag = reader.read_bit()
        if reader.read_bit():  # pic_scaling_matrix_present_flag
            scaling_lists = 6 + (6 if chroma_format_idc == 3 else 2) * transform_8x8_mode_flag
            for index in range(scaling_lists):
                if reader.read_bit():
                    _skip_scaling_list(reader, 16 if index < 6 else 64)
        second_chroma_qp_index_offset = reader.read_se()
        if not -12 <= second_chroma_qp_index_offset <= 12:
            raise ValueError("invalid PPS second chroma QP offset")

    reader.read_rbsp_trailing_bits()
    return sps_id


def _h264_sdp_has_parameter_sets(sdp: str) -> bool:
    """Require H.264 SPS/PPS that parse completely and describe positive dimensions."""
    lines = [line.strip() for line in sdp.splitlines() if line.strip()]
    video_payloads: set[str] = set()
    for line in lines:
        if not line.lower().startswith("m=video "):
            continue
        fields = line.split()
        if len(fields) >= 4:
            video_payloads.update(fields[3:])

    h264_payloads: set[str] = set()
    for line in lines:
        if not line.lower().startswith("a=rtpmap:"):
            continue
        payload, separator, codec = line[len("a=rtpmap:") :].partition(" ")
        if separator and payload in video_payloads and codec.strip().lower().startswith("h264/90000"):
            h264_payloads.add(payload)

    for line in lines:
        if not line.lower().startswith("a=fmtp:"):
            continue
        payload, separator, params = line[len("a=fmtp:") :].partition(" ")
        if not separator or payload not in h264_payloads:
            continue
        parsed: dict[str, str] = {}
        for token in params.split(";"):
            key, equals, value = token.strip().partition("=")
            if equals:
                parsed[key.strip().lower()] = value.strip()
        encoded_sets = [item.strip() for item in parsed.get("sprop-parameter-sets", "").split(",") if item.strip()]
        if len(encoded_sets) < 2:
            continue
        try:
            nals = [base64.b64decode(encoded, validate=True) for encoded in encoded_sets]
        except (binascii.Error, ValueError):
            continue

        valid_sps: dict[int, tuple[int, int, int]] = {}
        for nal in nals:
            if not nal or nal[0] & 0x1F != 7:
                continue
            try:
                sps_id, width, height, chroma_format_idc = _parse_h264_sps_details(nal)
            except ValueError:
                continue
            valid_sps[sps_id] = (width, height, chroma_format_idc)
        if not valid_sps:
            continue

        for nal in nals:
            if not nal or nal[0] & 0x1F != 8:
                continue
            for sps_id, (_, _, chroma_format_idc) in valid_sps.items():
                try:
                    referenced_sps = _parse_h264_pps_sps_id(nal, chroma_format_idc)
                except ValueError:
                    continue
                if referenced_sps == sps_id:
                    return True
    return False


def rtsp_sdp_has_h264_video(response: bytes | str) -> bool:
    """Return True when DESCRIBE advertises H.264 with usable SPS/PPS metadata."""
    if isinstance(response, bytes):
        text = response.decode("utf-8", errors="replace")
    else:
        text = response
    header, _, sdp = text.partition("\r\n\r\n")
    first_line = header.splitlines()[0] if header else ""
    if " 200 " not in f" {first_line} ":
        return False
    return _h264_sdp_has_parameter_sets(sdp)


def jpeg_is_decodable_candidate(payload: bytes, minimum_bytes: int = 2048) -> bool:
    """Decode a non-trivial JPEG and require positive dimensions."""
    if len(payload) < minimum_bytes:
        return False
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "JPEG" or image.width <= 0 or image.height <= 0:
                return False
            image.load()
    except (OSError, UnidentifiedImageError, ValueError):
        return False
    return True


def _read_rtsp_response(sock: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data and len(data) < 64 * 1024:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    header, marker, body = bytes(data).partition(b"\r\n\r\n")
    if not marker:
        return bytes(data)
    content_length = 0
    for raw_line in header.split(b"\r\n")[1:]:
        key, sep, value = raw_line.partition(b":")
        if sep and key.strip().lower() == b"content-length":
            try:
                content_length = int(value.strip())
            except ValueError:
                content_length = 0
            break
    while len(body) < content_length and len(body) < 256 * 1024:
        chunk = sock.recv(min(4096, content_length - len(body)))
        if not chunk:
            break
        body += chunk
    return header + marker + body


def probe_rtsp_sdp(alias: str, rtsp_port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """DESCRIBE a local RTSP alias and require usable H.264 SPS/PPS metadata."""
    request = (
        f"DESCRIBE rtsp://127.0.0.1:{rtsp_port}/{alias} RTSP/1.0\r\n"
        "CSeq: 1\r\n"
        "Accept: application/sdp\r\n"
        "User-Agent: wyze-bridge-consumer-health\r\n\r\n"
    ).encode()
    try:
        with socket.create_connection(("127.0.0.1", rtsp_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            response = _read_rtsp_response(sock)
    except OSError as exc:
        return False, f"rtsp_error={type(exc).__name__}"
    if not rtsp_sdp_has_h264_video(response):
        first_line = response.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        return False, f"rtsp_invalid_h264_metadata={first_line or 'empty'}"
    return True, "rtsp_h264_sps_pps_ok"


def _http_get(url: str, timeout: float = 5.0) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def probe_decoded_frame(alias: str, api_base: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Require a genuinely decodable JPEG from go2rtc's frame endpoint."""
    url = f"{api_base}/api/frame.jpeg?{urllib.parse.urlencode({'src': alias})}"
    try:
        payload = _http_get(url, timeout=timeout)
    except requests.RequestException as exc:
        return False, f"frame_error={type(exc).__name__}"
    if not jpeg_is_decodable_candidate(payload):
        return False, f"frame_invalid_bytes={len(payload)}"
    return True, f"frame_ok_bytes={len(payload)}"


def active_aliases(api_base: str, timeout: float = 5.0) -> list[str]:
    """Return aliases that currently have at least one producer entry."""
    payload = _http_get(f"{api_base}/api/streams", timeout=timeout)
    data = json.loads(payload)
    if not isinstance(data, dict):
        return []
    return sorted(
        name
        for name, details in data.items()
        if isinstance(name, str) and isinstance(details, dict) and details.get("producers")
    )


def _producer_is_connected(producer: object) -> bool:
    """Distinguish a live producer from go2rtc's URL-only lazy placeholder."""
    if not isinstance(producer, dict):
        return False
    return any(bool(producer.get(field)) for field in ("format_name", "protocol", "remote_addr", "medias", "receivers"))


def consumer_probe_aliases(api_base: str, timeout: float = 5.0) -> list[str]:
    """Return connected aliases plus aliases with active consumer demand.

    go2rtc keeps configured on-demand streams in the API as a producer object
    containing only the source URL. Probing those placeholders would create
    artificial demand and can make an intentionally idle stream look failed.
    """
    payload = _http_get(f"{api_base}/api/streams", timeout=timeout)
    data = json.loads(payload)
    if not isinstance(data, dict):
        return []

    aliases = []
    for name, details in data.items():
        if not isinstance(name, str) or not isinstance(details, dict):
            continue
        producers = details.get("producers") or []
        connected = any(_producer_is_connected(producer) for producer in producers)
        demanded = bool(details.get("consumers"))
        if connected or demanded:
            aliases.append(name)
    return sorted(aliases)


def restart_go2rtc_child(proc_root: Path = Path("/proc")) -> int:
    """Signal exact go2rtc children; the sidecar wrapper restarts them cleanly."""
    killed = 0
    try:
        entries = proc_root.iterdir()
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text(encoding="utf-8").strip() != "go2rtc":
                continue
            os.kill(int(entry.name), signal.SIGTERM)
            killed += 1
        except (OSError, ValueError):
            continue
    return killed


@dataclass
class ConsumerHealthState:
    """Bounded failure state for individual aliases and shared RTSP egress."""

    failure_threshold: int = 2
    process_threshold: int = 3
    failures: dict[str, int] = field(default_factory=dict)
    shared_failure_cycles: int = 0

    def record_cycle(self, results: dict[str, bool]) -> tuple[list[str], bool]:
        """Return aliases requiring recovery and whether all-stream failure escalated."""
        active = set(results)
        for alias in list(self.failures):
            if alias not in active:
                self.failures.pop(alias, None)

        failed_aliases: list[str] = []
        for alias, healthy in results.items():
            if healthy:
                self.failures[alias] = 0
                continue
            count = self.failures.get(alias, 0) + 1
            self.failures[alias] = count
            if count >= self.failure_threshold:
                failed_aliases.append(alias)
                self.failures[alias] = 0

        if results and not any(results.values()):
            self.shared_failure_cycles += 1
        else:
            self.shared_failure_cycles = 0

        shared_failure = self.shared_failure_cycles >= self.process_threshold
        if shared_failure:
            self.shared_failure_cycles = 0
            for alias in results:
                self.failures[alias] = 0
        return failed_aliases, shared_failure


def run() -> None:
    if not _truthy("GO2RTC_CONSUMER_HEALTH", True):
        _log("disabled by GO2RTC_CONSUMER_HEALTH")
        return

    api_port = int(os.environ.get("GO2RTC_API_PORT", "11984"))
    rtsp_port = int(os.environ.get("GO2RTC_RTSP_PORT", "19554"))
    interval = max(10, int(os.environ.get("GO2RTC_CONSUMER_HEALTH_INTERVAL", "30")))
    initial_delay = max(0, int(os.environ.get("GO2RTC_CONSUMER_HEALTH_INITIAL_DELAY", "20")))
    api_base = f"http://127.0.0.1:{api_port}"
    state = ConsumerHealthState()
    _log(f"watchdog started interval={interval}s rtsp=:{rtsp_port} api=:{api_port}")
    time.sleep(initial_delay)

    while True:
        try:
            aliases = consumer_probe_aliases(api_base)
        except (OSError, ValueError, requests.RequestException) as exc:
            _log(f"stream table unavailable: {type(exc).__name__}")
            time.sleep(interval)
            continue

        results: dict[str, bool] = {}
        details: dict[str, str] = {}
        for alias in aliases:
            rtsp_ok, rtsp_detail = probe_rtsp_sdp(alias, rtsp_port)
            frame_ok, frame_detail = probe_decoded_frame(alias, api_base)
            results[alias] = rtsp_ok and frame_ok
            details[alias] = f"{rtsp_detail} {frame_detail}"
            if not results[alias]:
                _log(f"{alias}: consumer probe failed ({details[alias]})")

        failed_aliases, shared_failure = state.record_cycle(results)
        if failed_aliases or shared_failure:
            killed = restart_go2rtc_child()
            if shared_failure:
                reason = "all active consumer probes failed repeatedly"
            else:
                reason = f"repeated consumer failure aliases={','.join(failed_aliases)}"
            _log(f"{reason}; signalled go2rtc child count={killed}")
            time.sleep(max(interval, 5))
            continue

        time.sleep(interval)


if __name__ == "__main__":
    run()
