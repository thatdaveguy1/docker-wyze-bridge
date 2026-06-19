import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "scripts" / "ha_wyze_scrypted_snapshot_probe.sh"


class TestHAWyzeScryptedSnapshotProbe(unittest.TestCase):
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
        env["HA_WYZE_SCRYPTED_DEVICES"] = "cam-c:10003 garage:12;reboot"

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
        self.assertIn("Invalid HA_WYZE_SCRYPTED_DEVICES", result.stdout)

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

    def test_uses_exact_scrypted_snapshot_endpoint_and_freshness_markers(self):
        script = PROBE.read_text(encoding="utf-8")

        expected_snippets = [
            'jq -c \'.data.entries[] | select(.domain=="scrypted" and (.disabled_by | not)) | .data\' "$SCRYPTED_CONFIG_PATH"',
            'https://$SCRYPTED_HOST/login',
            "/endpoint/@scrypted/snapshot/$device_id/Camera",
            "is_jpeg_file",
            "od -An -tx1 -N2",
            '"valid_count"',
            '"unique_hashes"',
            "login_status=ok",
            "PASS: Wyze Scrypted snapshot probe passed.",
            "FAIL: Wyze Scrypted snapshot probe failed.",
        ]

        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, script)

        library = (ROOT / "scripts" / "ha_bridge_probe.sh").read_text()
        self.assertIn("ha_bridge_probe.sh", script)
        self.assertIn('s/api=[^" ]+/api=<redacted>/g', library)
        self.assertIn('s/(Bearer )[A-Za-z0-9._~-]+/\\1<redacted>/g', script)


if __name__ == "__main__":
    unittest.main()
