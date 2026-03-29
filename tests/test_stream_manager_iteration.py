#!/usr/bin/env python3

import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / ".ha_live_addon" / "app")
)

from wyzebridge.stream_manager import StreamManager


class MutatingStream:
    def __init__(self, manager, uri):
        self.manager = manager
        self.uri = uri
        self.enabled = True
        self.motion = False
        self._mutated = False

    def health_check(self):
        if not self._mutated:
            self._mutated = True
            self.manager.streams["late-cam"] = PassiveStream()
        return 1

    def status(self):
        return "connected"

    def get_info(self):
        return {"name_uri": self.uri}


class PassiveStream:
    enabled = True
    motion = False

    def health_check(self):
        return 1

    def status(self):
        return "connected"

    def get_info(self):
        return {"name_uri": "late-cam"}


class TestStreamManagerIteration(unittest.TestCase):
    def test_active_streams_tolerates_concurrent_add(self):
        manager = StreamManager(SimpleNamespace())
        manager.streams["cam-a"] = MutatingStream(manager, "cam-a")

        active = manager.active_streams()

        self.assertEqual(active, ["cam-a"])
        self.assertIn("late-cam", manager.streams)


if __name__ == "__main__":
    unittest.main()
