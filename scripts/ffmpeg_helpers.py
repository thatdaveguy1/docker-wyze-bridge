"""Shared ffmpeg/ffprobe command-building helpers for local probe scripts.

These functions construct the common RTSP probe command lists used across
reolink_direct_stability_probe.py, wyze_rtsp_stability_probe.py,
local_camera_uptime_smoke_test.py, and wyze_cam_rtsp_smoke_test.py.

Each script keeps its own response parsing and probe loop logic; this
module only shares the command construction and binary resolution.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

DEFAULT_FFPROBE_ENTRIES = (
    "stream=index,codec_name,codec_type,width,height,"
    "avg_frame_rate,r_frame_rate:format=format_name"
)


def detect_timeout_flag(binary_path: str) -> str | None:
    """Detect the first supported timeout flag for a ffmpeg/ffprobe binary."""
    result = subprocess.run(
        [binary_path, "-h", "full"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    text = result.stdout or ""
    for candidate in ("rw_timeout", "timeout", "stimeout"):
        if f"-{candidate}" in text:
            return candidate
    return None


def ensure_binary(path: str | None, name: str) -> str:
    """Resolve and validate a binary path.

    Falls back to ``shutil.which(name)`` when *path* is empty, then
    verifies the resolved path exists on disk.
    """
    resolved = path or shutil.which(name)
    if not resolved:
        raise SystemExit(f"{name} was not found on PATH.")
    if not Path(resolved).exists():
        raise SystemExit(f"{name} does not exist: {resolved}")
    return resolved


def build_ffprobe_cmd(
    ffprobe_path: str,
    url: str,
    transport: str,
    timeout_flag: str | None = None,
    timeout_us: int = 0,
    entries: str = DEFAULT_FFPROBE_ENTRIES,
) -> list[str]:
    """Build a standard ffprobe RTSP command list.

    The caller is responsible for running the command and parsing the
    JSON output — different scripts extract different fields.
    """
    command = [
        ffprobe_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        transport,
    ]
    if timeout_flag:
        command.extend([f"-{timeout_flag}", str(timeout_us)])
    command.extend(["-show_entries", entries, "-of", "json", url])
    return command


def build_ffmpeg_rtsp_cmd(
    ffmpeg_path: str,
    url: str,
    transport: str,
    duration: float | str,
    *,
    loglevel: str = "warning",
    nostats: bool = True,
    nostdin: bool = True,
    output_format: str = "null",
    output_target: str = "/dev/null",
    progress_pipe: Optional[int] = None,
    extra_input_args: list[str] | None = None,
    extra_output_args: list[str] | None = None,
) -> list[str]:
    """Build a standard ffmpeg RTSP probe command list.

    Parameters:
    - ffmpeg_path: Path to the ffmpeg binary.
    - url: RTSP input URL.
    - transport: ``"tcp"`` or ``"udp"``.
    - duration: Probe duration in seconds (``-t``).
    - loglevel: ffmpeg loglevel (default ``"warning"``).
    - nostats: Include ``-nostats`` (default True).
    - nostdin: Include ``-nostdin`` (default True).
    - output_format: ffmpeg output format (default ``"null"``).
    - output_target: output target (default ``"/dev/null"``).
    - progress_pipe: If set, add ``-progress pipe:N``.
    - extra_input_args: Additional args before ``-i``.
    - extra_output_args: Additional args after ``-t`` / before format.
    """
    cmd: list[str] = [ffmpeg_path, "-hide_banner"]
    if nostats:
        cmd.append("-nostats")
    cmd.extend(["-loglevel", loglevel])
    if nostdin:
        cmd.append("-nostdin")
    cmd.extend(["-rtsp_transport", transport])
    if extra_input_args:
        cmd.extend(extra_input_args)
    cmd.extend(["-i", url])
    cmd.extend(["-t", str(duration)])
    if extra_output_args:
        cmd.extend(extra_output_args)
    if progress_pipe is not None:
        cmd.extend(["-progress", f"pipe:{progress_pipe}"])
    cmd.extend(["-f", output_format, output_target])
    return cmd
