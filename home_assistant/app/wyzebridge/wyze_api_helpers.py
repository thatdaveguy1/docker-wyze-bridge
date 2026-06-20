"""Helper functions for WyzeApi — decorators, KVS trace, thumbnail utils, filters.

Architecture review candidate #5: extracted from wyze_api.py to separate
auth decorators, cache helpers, KVS trace, thumbnail validation, and camera
filtering from the WyzeApi class. wyze_api.py re-exports everything for
backward compatibility.
"""
import json
import pickle
from datetime import datetime
from functools import wraps
from os import environ
from pathlib import Path
from time import time
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from requests import get
from requests.exceptions import ConnectionError, HTTPError, RequestException

from wyzecam.api_models import WyzeCamera
from wyzecam.api import (
    AccessTokenError,
    RateLimitError,
    WyzeAPIError,
    _headers,
)
from wyzebridge.bridge_utils import env_bool, env_list
from wyzebridge.config import TOKEN_PATH
from wyzebridge.logging import logger
from wyzebridge.preview_validation import (
    preview_bytes_are_valid_image,
    preview_file_is_image,
    preview_payload_matches_existing,
    record_preview_hash,
)


def cached(func: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(self, *args: Any, **kwargs: Any):
        name = "auth" if func.__name__ == "login" else func.__name__.split("_", 1)[-1]
        if not self.auth and not self.creds.is_set and name != "auth":
            return
        if not kwargs.get("fresh_data"):
            if getattr(self, name, None):
                return func(self, *args, **kwargs)
            try:
                with open(TOKEN_PATH + name + ".pickle", "rb") as pkl_f:
                    if not (data := pickle.load(pkl_f)):
                        raise OSError
                if name == "user" and not self.creds.same_email(data.email):
                    raise ValueError("🕵️ Cached email doesn't match 'WYZE_EMAIL'")
                cache_logger = logger.debug if name == "cameras" else logger.info
                cache_logger(f"📚 Using '{name}' from local cache...")
                setattr(self, name, data)
                return data
            except OSError:
                cache_logger = logger.debug if name == "cameras" else logger.info
                cache_logger(f"🔍 Could not find local cache for '{name}'")
            except Exception as ex:
                logger.warning(
                    f"Error restoring data for '{name}': [{type(ex).__name__}] {ex}"
                )
                self.clear_cache()
        fetch_logger = logger.debug if name == "cameras" else logger.info
        fetch_logger(f"☁️ Fetching '{name}' from the Wyze API...")
        result = func(self, *args, **kwargs)
        if result and (data := getattr(self, name, None)):
            pickle_dump(name, data)
        return result

    return wrapper


def authenticated(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(self, *args: Any, **kwargs: Any):
        if not self.auth and not self.login():
            return

        try:
            return func(self, *args, **kwargs)
        except AccessTokenError:
            logger.warning("[API] ⚠️ Expired token?")
            self.refresh_token()
            return func(self, *args, **kwargs)
        except (RateLimitError, WyzeAPIError) as ex:
            logger.error(f"[API] [{type(ex).__name__}] {ex}")
        except ConnectionError as ex:
            logger.error(f"[API] [{type(ex).__name__}] {ex}")

    return wrapper


def sanitize_url(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return (
        f"{parts.scheme}://{parts.netloc}{parts.path}"
        if parts.scheme and parts.netloc
        else parts.path or "<redacted>"
    )


def kvs_trace_enabled(stream_name: str) -> bool:
    raw = environ.get("KVS_TRACE_STREAM", "").strip()
    if not raw:
        return False
    if raw.lower() in {"1", "true", "yes", "all", "*"}:
        return True
    return stream_name.upper() in env_list("KVS_TRACE_STREAM")


def sanitize_kvs_trace(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_kvs_trace_field(key, val) for key, val in value.items()}
    if isinstance(value, list):
        return [sanitize_kvs_trace(item) for item in value]
    return value


def sanitize_kvs_trace_field(key: str, value: Any) -> Any:
    lowered = key.lower()
    if lowered in {
        "auth_token",
        "signaltoken",
        "authorization",
        "credential",
        "username",
        "phone_id",
        "clientid",
    }:
        return "<redacted>"
    if lowered in {"signaling_url", "url", "urls"} and isinstance(value, str):
        return sanitize_url(value)
    return sanitize_kvs_trace(value)


def log_kvs_trace(stream_name: str, stage: str, payload: Any) -> None:
    if not kvs_trace_enabled(stream_name):
        return
    trace = {
        "camera": stream_name,
        "stage": stage,
        "payload": sanitize_kvs_trace(payload),
    }
    logger.info(f"[KVS_TRACE] {json.dumps(trace, sort_keys=True)}")


def url_timestamp(url: str) -> int:
    try:
        path_parts = [part for part in urlparse(url).path.split("/") if part]
        for part in reversed(path_parts):
            for token in part.split("_"):
                if token.isdigit() and len(token) >= 10:
                    value = int(token)
                    return value // 1000 if len(token) > 10 else value
    except Exception:
        pass
    return 0


def valid_s3_url(url: Optional[str]) -> bool:
    if not url:
        return False

    try:
        query_parameters = parse_qs(urlparse(url).query)
        x_amz_date = query_parameters["X-Amz-Date"][0]
        x_amz_expires = query_parameters["X-Amz-Expires"][0]
        amz_date = datetime.strptime(x_amz_date, "%Y%m%dT%H%M%SZ")
        return amz_date.timestamp() + int(x_amz_expires) > time()
    except (ValueError, TypeError, KeyError):
        return False


def _looks_like_html(payload: bytes) -> bool:
    snippet = payload.lstrip().lower()[:64]
    return snippet.startswith((b"<!doctype html", b"<html", b"<?xml"))


def _looks_like_image_bytes(payload: bytes) -> bool:
    if not payload or _looks_like_html(payload):
        return False

    header = payload[:16]
    return (
        header.startswith(b"\xff\xd8\xff")
        or header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith((b"GIF87a", b"GIF89a"))
        or (len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def _thumbnail_response_is_image(response) -> bool:
    return preview_bytes_are_valid_image(response.content or b"")


def _cached_thumbnail_is_valid(path: Path) -> bool:
    return preview_file_is_image(path)


def env_filter(cam: WyzeCamera) -> bool:
    """Check if cam is being filtered in any env."""
    if not cam.nickname:
        return False
    return (
        cam.nickname.upper().strip() in env_list("FILTER_NAMES")
        or cam.mac in env_list("FILTER_MACS")
        or cam.product_model in env_list("FILTER_MODELS")
        or cam.model_name.upper() in env_list("FILTER_MODELS")
    )


def filter_cams(cams: list[WyzeCamera]) -> list[WyzeCamera]:
    total = len(cams)
    if env_bool("FILTER_BLOCK"):
        if filtered := list(filter(lambda cam: not env_filter(cam), cams)):
            logger.info(f"🪄 FILTER BLOCKING: {total - len(filtered)} of {total} cams")
            return filtered
    elif any(key.startswith("FILTER_") for key in environ):
        if filtered := list(filter(env_filter, cams)):
            logger.info(f"🪄 FILTER ALLOWING: {len(filtered)} of {total} cams")
            return filtered
    return cams


def pickle_dump(name: str, data: object):
    with open(TOKEN_PATH + name + ".pickle", "wb") as f:
        save_logger = logger.debug if name == "cameras" else logger.info
        save_logger(f"💾 Saving '{name}' to local cache...")
        pickle.dump(data, f)


def parse_token(access_token: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not access_token:
        return None, None

    access_token = access_token.strip(" '\"")

    try:
        json_token = json.loads(access_token)
        json_token = json_token.get("data", json_token)

        return json_token.get("access_token"), json_token.get("refresh_token")
    except ValueError:
        return access_token, None
