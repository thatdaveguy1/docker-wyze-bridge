"""KVS/TUTK source selection — one selector, two adapters.

Architecture review candidate #4: the KVS-vs-TUTK decision is centralized
here instead of spread across properties in WyzeStream. Model rules,
env vars, and substream flags are all private to this module.
"""

import os
from typing import Any

HL_CAM4_MAIN_PROBE_MODES = {"kvs", "tutk_dtls", "tutk_parallel"}


def hl_cam4_main_probe_mode() -> str:
    mode = os.getenv("HL_CAM4_MAIN_PROBE_MODE", "kvs").strip().lower()
    return mode if mode in HL_CAM4_MAIN_PROBE_MODES else "kvs"


def _uses_tutk_source(camera: Any, substream: bool) -> bool:
    if substream:
        return camera.product_model == "HL_CAM3P" or (camera.product_model == "HL_CAM4" and camera.is_kvs)

    if not (camera.product_model == "HL_CAM4" and camera.is_kvs):
        return False

    return hl_cam4_main_probe_mode() in {"tutk_dtls", "tutk_parallel"}


def uses_kvs_source(camera: Any, substream: bool = False) -> bool:
    return not _uses_tutk_source(camera, substream)


def uses_tutk_source(camera: Any, substream: bool = False) -> bool:
    return _uses_tutk_source(camera, substream)
