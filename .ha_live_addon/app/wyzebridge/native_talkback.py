"""Native talkback helpers — extracted from native_alias.py.

Architecture review candidate #4.1: talkback (two-way audio via go2rtc) is
self-contained and has no overlap with alias readiness/status caching or
snapshot writing. This module owns the talkback lifecycle (codec probing,
temp-file staging, go2rtc stream requests) behind a narrow seam.
"""

import base64
import os
import re
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from wyzebridge.go2rtc import go2rtc_api_base
from wyzebridge.native_alias import _go2rtc_stream_details, preload_native_stream

_MEDIA_CODEC_RE = re.compile(r"^audio,\s+(sendonly|recvonly),\s+([^,]+)$", re.IGNORECASE)


def _ffmpeg_codec_from_go2rtc_media(media: str) -> str | None:
    match = _MEDIA_CODEC_RE.match(str(media).strip())
    if not match or match.group(1).lower() != "sendonly":
        return None

    codec_spec = match.group(2).strip()
    parts = codec_spec.split("/")
    codec_name = parts[0].upper()
    rate = parts[1] if len(parts) > 1 and parts[1].isdigit() else ""

    codec_map = {
        "AAC": "aac",
        "MPEG4-GENERIC": "aac",
        "PCMA": "pcma",
        "PCMU": "pcmu",
        "PCM": "pcm",
        "L16": "pcm",
        "PCML": "pcml",
        "OPUS": "opus",
    }
    ffmpeg_codec = codec_map.get(codec_name)
    if not ffmpeg_codec:
        return None
    return f"{ffmpeg_codec}/{rate}" if rate else ffmpeg_codec


def _talkback_ffmpeg_codec(alias: str, timeout: float = 2.0) -> str | None:
    details = _go2rtc_stream_details(alias, timeout=timeout)

    producers = details.get("producers") if isinstance(details, dict) else None
    if isinstance(producers, list):
        for producer in producers:
            medias = producer.get("medias") if isinstance(producer, dict) else None
            if not isinstance(medias, list):
                continue
            for media in medias:
                if codec := _ffmpeg_codec_from_go2rtc_media(str(media)):
                    return codec

    return None


def _resolve_talkback_ffmpeg_codec(
    alias: str,
    timeout: float = 2.0,
    attempts: int = 3,
    retry_delay: float = 0.35,
) -> str | None:
    for attempt in range(max(attempts, 1)):
        if codec := _talkback_ffmpeg_codec(alias, timeout=timeout):
            return codec
        if attempt == 0:
            preload_native_stream(alias, timeout=timeout)
        if attempt + 1 < max(attempts, 1):
            time.sleep(retry_delay)
    return None


def _talkback_temp_dir() -> Path:
    config_dir = Path("/config")
    if config_dir.is_dir() and os.access(config_dir, os.W_OK):
        return config_dir
    return Path(tempfile.gettempdir())


def _cleanup_stale_talkback_files(max_age_seconds: float = 600.0) -> None:
    cutoff = time.time() - max_age_seconds
    tmp_dir = _talkback_temp_dir()
    for path in tmp_dir.glob("wyze-talkback-*"):
        with suppress(OSError):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()


def _go2rtc_stream_request(alias: str, src: str, mode: str, timeout: float = 20.0) -> dict[str, Any]:
    result = {
        "status": "error",
        "source": "go2rtc",
        "alias": alias,
        "mode": mode,
    }
    try:
        response = requests.post(
            f"{go2rtc_api_base()}/api/streams",
            params={"dst": alias, "src": src},
            timeout=timeout,
        )
        result["status_code"] = response.status_code
        response.raise_for_status()
        result["status"] = "success"
        result["response"] = "ok"
        with suppress(AttributeError, ValueError):
            parsed = response.json()
            if parsed not in (None, ""):
                result["response"] = parsed
        text = getattr(response, "text", "")
        if result["response"] == "ok" and text:
            result["response"] = text.strip() or "ok"
    except requests.RequestException as ex:
        if ex.response is not None:
            result["status_code"] = ex.response.status_code
        body = ""
        with suppress(Exception):
            body = ex.response.text.strip() if ex.response is not None else ""
        result["response"] = body or str(ex)
    return result


def send_native_talkback(payload: dict[str, Any], alias: str, timeout: float = 20.0) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    text = str(payload.get("text") or payload.get("message") or "").strip()
    audio_b64 = payload.get("audio_b64")
    audio_url = str(payload.get("audio_url") or "").strip()

    if action == "stop":
        return _go2rtc_stream_request(alias, "", mode="stop", timeout=timeout)

    if text and (audio_b64 or audio_url):
        return {
            "status": "error",
            "source": "go2rtc",
            "alias": alias,
            "response": "Provide either text, audio_b64, or audio_url, not multiple talkback sources",
        }

    if audio_b64 and audio_url:
        return {
            "status": "error",
            "source": "go2rtc",
            "alias": alias,
            "response": "Provide either audio_b64 or audio_url, not both",
        }

    if not (text or audio_b64 or audio_url):
        return {
            "status": "error",
            "source": "go2rtc",
            "alias": alias,
            "response": "Talkback payload requires text, audio_b64, or audio_url",
        }

    if text:
        talkback_codec = _resolve_talkback_ffmpeg_codec(alias)
        if not talkback_codec:
            return {
                "status": "error",
                "source": "go2rtc",
                "alias": alias,
                "response": "Unable to determine a compatible go2rtc talkback codec",
            }
        src = f"ffmpeg:tts?{urlencode({'text': text})}#audio={talkback_codec}"
        voice = str(payload.get("voice") or "").strip()
        if voice:
            src = f"ffmpeg:tts?{urlencode({'text': text, 'voice': voice})}#audio={talkback_codec}"
        return _go2rtc_stream_request(alias, src, mode="text", timeout=timeout)

    if audio_url:
        talkback_codec = _resolve_talkback_ffmpeg_codec(alias)
        if not talkback_codec:
            return {
                "status": "error",
                "source": "go2rtc",
                "alias": alias,
                "response": "Unable to determine a compatible go2rtc talkback codec",
            }
        return _go2rtc_stream_request(
            alias,
            f"ffmpeg:{audio_url}#audio={talkback_codec}#input=file",
            mode="url",
            timeout=timeout,
        )

    try:
        audio_bytes = base64.b64decode(str(audio_b64), validate=True)
    except ValueError:
        return {
            "status": "error",
            "source": "go2rtc",
            "alias": alias,
            "response": "audio_b64 must be valid base64",
        }

    if not audio_bytes:
        return {
            "status": "error",
            "source": "go2rtc",
            "alias": alias,
            "response": "audio_b64 decoded to an empty payload",
        }

    talkback_codec = _resolve_talkback_ffmpeg_codec(alias)
    if not talkback_codec:
        return {
            "status": "error",
            "source": "go2rtc",
            "alias": alias,
            "response": "Unable to determine a compatible go2rtc talkback codec",
        }

    suffix = str(payload.get("file_ext") or payload.get("format") or "wav").strip().lower()
    suffix = "".join(ch for ch in suffix if ch.isalnum()) or "wav"
    try:
        _cleanup_stale_talkback_files()
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=_talkback_temp_dir(),
            prefix="wyze-talkback-",
            suffix=f".{suffix}",
        ) as handle:
            handle.write(audio_bytes)
        return _go2rtc_stream_request(
            alias,
            f"ffmpeg:{handle.name}#audio={talkback_codec}#input=file",
            mode="file",
            timeout=timeout,
        )
    except OSError as ex:
        return {
            "status": "error",
            "source": "go2rtc",
            "alias": alias,
            "response": f"Unable to stage talkback audio: {ex}",
        }
