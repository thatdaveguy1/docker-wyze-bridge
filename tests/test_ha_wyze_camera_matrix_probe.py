import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "scripts" / "ha_wyze_camera_matrix_probe.sh"


class TestHAWyzeCameraMatrixProbe(unittest.TestCase):
    def test_script_is_shell_syntax_valid(self):
        result = subprocess.run(
            ["sh", "-n", str(PROBE)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)

    def test_rejects_unsafe_env_values_before_ssh(self):
        env = os.environ.copy()
        env["HA_WYZE_CAMERAS"] = "south-yard;reboot"

        result = subprocess.run(
            [str(PROBE)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid camera name", result.stdout)

        env = os.environ.copy()
        env["HA_WYZE_BRIDGE_BASE"] = "http://192.0.2.10:5000/path?api=secret"

        result = subprocess.run(
            [str(PROBE)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid HA_WYZE_BRIDGE_BASE", result.stdout)

    def test_static_commands_stay_read_only(self):
        script = PROBE.read_text(encoding="utf-8")
        forbidden_patterns = [
            r"\bha apps stop\b",
            r"\bha apps start\b",
            r"\bha apps restart\b",
            r"\bha apps rebuild\b",
            r"\bha apps update\b",
            r"\bha apps uninstall\b",
            r"\bha host reboot\b",
            r"\bha host shutdown\b",
            r"\bcurl\b[^\n]*\b-X POST\b",
            r"\bcurl\b[^\n]*\b-X DELETE\b",
            r"\brm -rf\b",
        ]

        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, script))

    def test_collects_expected_wyze_matrix_signals(self):
        script = PROBE.read_text(encoding="utf-8")

        expected_snippets = [
            "/api",
            "/api/$camera",
            "/api/$camera/stream-config",
            "/health/details?stream=$detail_stream",
            "$GO2RTC_BASE/api/streams",
            "/img/$camera.jpg?exp=0",
            "$GO2RTC_BASE/api/frame.jpeg?src=$native_alias",
            "is_jpeg_file",
            "od -An -tx1 -N2",
            "native_selected",
            "native_alias",
            "snapshot_source",
            '"img_valid_count"',
            '"img_unique_hashes"',
            '"native_valid_count"',
            '"native_unique_hashes"',
            "PASS: Wyze camera matrix probe passed.",
            "FAIL: Wyze camera matrix probe failed.",
        ]

        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, script)

        library = (ROOT / "scripts" / "ha_bridge_probe.sh").read_text()
        self.assertIn("ha_bridge_probe.sh", script)
        self.assertIn('s/api=[^" ]+/api=<redacted>/g', library)
        self.assertNotIn("options:.data.options", script)


if __name__ == "__main__":
    unittest.main()
