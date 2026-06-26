"""Reolink camera babysitter package.

Self-contained watchdog for detecting and recovering Reolink/Scrypted/Frigate
camera wedges. Feature-flagged via ENABLE_BABYSITTER env var.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["logger"]
