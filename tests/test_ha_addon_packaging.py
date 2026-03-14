import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = ROOT / "home_assistant"


class TestHomeAssistantAddonPackaging(unittest.TestCase):
    def test_all_ha_dockerfiles_avoid_hidden_env_dependency(self):
        dockerfiles = [
            ADDON_DIR / "Dockerfile",
            ADDON_DIR / "docker/Dockerfile",
            ADDON_DIR / "docker/Dockerfile.multiarch",
            ADDON_DIR / "docker/Dockerfile.hwaccel",
        ]
        for dockerfile_path in dockerfiles:
            with self.subTest(dockerfile=str(dockerfile_path.relative_to(ROOT))):
                dockerfile = dockerfile_path.read_text()
                self.assertNotIn(
                    ". app/.env",
                    dockerfile,
                    f"{dockerfile_path.relative_to(ROOT)} should not depend on a hidden .env file that HA strips from build context",
                )

    def test_addon_dockerfile_avoids_buildkit_only_mount_syntax(self):
        dockerfile = (ADDON_DIR / "Dockerfile").read_text()
        self.assertNotIn(
            "--mount=type=cache",
            dockerfile,
            "home_assistant/Dockerfile should avoid BuildKit-only cache mounts so HA can build the add-on locally",
        )

    def test_addon_build_env_version_matches_public_addon_version(self):
        config_text = (ADDON_DIR / "config.yml").read_text()
        env_text = (ADDON_DIR / "app/build.env").read_text()

        config_version = re.search(r"^version:\s*(.+)$", config_text, re.MULTILINE)
        env_version = re.search(r"^VERSION=(.+)$", env_text, re.MULTILINE)

        self.assertIsNotNone(config_version)
        self.assertIsNotNone(env_version)
        assert config_version is not None
        assert env_version is not None
        self.assertEqual(
            config_version.group(1).strip(),
            env_version.group(1).strip(),
            "home_assistant/app/build.env VERSION should match home_assistant/config.yml version for source-built add-ons",
        )


if __name__ == "__main__":
    unittest.main()
