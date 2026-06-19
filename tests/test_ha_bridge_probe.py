import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "scripts" / "ha_bridge_probe.sh"


class TestHaBridgeProbeLibrary(unittest.TestCase):
    def test_library_is_shell_syntax_valid(self):
        result = subprocess.run(
            ["sh", "-n", str(LIBRARY)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_defines_required_functions(self):
        text = LIBRARY.read_text()
        for func in (
            "section()",
            "mark_fail()",
            "redact_api_keys()",
            "derive_bridge_token()",
            "validate_slug()",
            "validate_base_url()",
            "validate_number()",
            "bool_true()",
        ):
            with self.subTest(func=func):
                self.assertIn(func, text)

    def test_uses_api_header_not_query_string(self):
        text = LIBRARY.read_text()
        # The token derivation must use the proven sha256 -> xxd -> base64 chain
        self.assertIn("sha256sum", text)
        self.assertIn("xxd -r -p", text)
        self.assertIn("base64", text)
        # Must not use query-string auth
        self.assertNotIn("?api=$", text)

    def test_redact_pattern_matches_existing_convention(self):
        text = LIBRARY.read_text()
        self.assertIn('s/api=[^" ]+/api=<redacted>/g', text)

    def test_validate_slug_rejects_injection(self):
        result = subprocess.run(
            ["sh", "-c", '. "%s"; validate_slug "TEST" "bad;reboot"' % str(LIBRARY)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid TEST", result.stdout)

    def test_validate_slug_accepts_clean_value(self):
        result = subprocess.run(
            ["sh", "-c", '. "%s"; validate_slug "TEST" "local_docker_wyze_bridge_v4"; echo OK' % str(LIBRARY)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("OK", result.stdout)

    def test_validate_base_url_rejects_path(self):
        result = subprocess.run(
            ["sh", "-c", '. "%s"; validate_base_url "TEST" "http://172.30.32.1:5000/path?api=secret"' % str(LIBRARY)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid TEST", result.stdout)

    def test_validate_base_url_accepts_clean_url(self):
        result = subprocess.run(
            ["sh", "-c", '. "%s"; validate_base_url "TEST" "http://172.30.32.1:5000"; echo OK' % str(LIBRARY)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("OK", result.stdout)

    def test_validate_number_rejects_injection(self):
        result = subprocess.run(
            ["sh", "-c", '. "%s"; validate_number "TEST" "60;rm"' % str(LIBRARY)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid TEST", result.stdout)

    def test_validate_number_accepts_digits(self):
        result = subprocess.run(
            ["sh", "-c", '. "%s"; validate_number "TEST" "60"; echo OK' % str(LIBRARY)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("OK", result.stdout)

    def test_bool_true_recognizes_truthy_values(self):
        for val in ("true", "True", "TRUE", "1", "yes", "on", "ON"):
            with self.subTest(val=val):
                result = subprocess.run(
                    ["sh", "-c", '. "%s"; bool_true "%s" && echo YES' % (str(LIBRARY), val)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0)
                self.assertIn("YES", result.stdout)

    def test_bool_true_rejects_falsy_values(self):
        for val in ("false", "0", "no", "off", "", "random"):
            with self.subTest(val=val):
                result = subprocess.run(
                    ["sh", "-c", '. "%s"; bool_true "%s" || echo NO' % (str(LIBRARY), val)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0)
                self.assertIn("NO", result.stdout)

    def _run_derive_bridge_token(self, info_json):
        """Run derive_bridge_token with JSON passed via env var to avoid quoting issues."""
        result = subprocess.run(
            ["sh", "-c", '. "%s"; derive_bridge_token "$INFO_JSON"' % str(LIBRARY)],
            cwd=ROOT,
            env={**__import__("os").environ, "INFO_JSON": info_json},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return result

    def test_derive_bridge_token_from_stored_api(self):
        info = '{"data":{"options":{"WB_API":"my-stored-key"}}}'
        result = self._run_derive_bridge_token(info)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "my-stored-key")

    def test_derive_bridge_token_from_wyze_email(self):
        info = '{"data":{"options":{"WYZE_EMAIL":"test@example.com"}}}'
        result = self._run_derive_bridge_token(info)
        self.assertEqual(result.returncode, 0)
        token = result.stdout.strip()
        self.assertEqual(len(token), 40)

    def test_derive_bridge_token_empty_when_neither_available(self):
        info = '{"data":{"options":{}}}'
        result = self._run_derive_bridge_token(info)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_no_forbidden_commands(self):
        import re
        text = LIBRARY.read_text()
        forbidden = [
            r"\bha apps stop\b",
            r"\bha apps start\b",
            r"\bha apps restart\b",
            r"\bha apps rebuild\b",
            r"\bha apps update\b",
            r"\bha apps uninstall\b",
            r"\bha host reboot\b",
            r"\bha host shutdown\b",
            r"\brm -rf\b",
        ]
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))


if __name__ == "__main__":
    unittest.main()
