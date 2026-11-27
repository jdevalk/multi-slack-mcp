"""Platform paths and overrides stay consistent across installation and runtime."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from paths import app_paths


class AppPathsTests(unittest.TestCase):
    def resolve(self, platform, environment=None):
        with patch("paths.sys.platform", platform), \
             patch("paths.Path.home", return_value=Path("/users/test account")), \
             patch.dict(os.environ, environment or {}, clear=True):
            return app_paths()

    def test_macos_preserves_existing_paths(self):
        paths = self.resolve("darwin")
        self.assertEqual(paths["install"], Path("/users/test account/Library/Application Support/multi-slack-mcp"))
        self.assertEqual(paths["config"], paths["install"] / "config.json")
        self.assertEqual(paths["log"], Path("/users/test account/Library/Logs/multi-slack-mcp.log"))

    def test_linux_defaults(self):
        paths = self.resolve("linux")
        self.assertEqual(paths["install"], Path("/users/test account/.local/share/multi-slack-mcp"))
        self.assertEqual(paths["config"], Path("/users/test account/.config/multi-slack-mcp/config.json"))
        self.assertEqual(paths["log"], Path("/users/test account/.local/state/multi-slack-mcp/multi-slack-mcp.log"))

    def test_linux_xdg_overrides(self):
        paths = self.resolve("linux", {
            "XDG_DATA_HOME": "/custom data",
            "XDG_CONFIG_HOME": "/custom config",
            "XDG_STATE_HOME": "/custom state",
        })
        self.assertEqual(paths["install"], Path("/custom data/multi-slack-mcp"))
        self.assertEqual(paths["config"], Path("/custom config/multi-slack-mcp/config.json"))
        self.assertEqual(paths["log"], Path("/custom state/multi-slack-mcp/multi-slack-mcp.log"))

    def test_invalid_xdg_values_fall_back(self):
        self.assertEqual(self.resolve("linux", {
            "XDG_DATA_HOME": "relative", "XDG_CONFIG_HOME": "", "XDG_STATE_HOME": "~/state",
        }), self.resolve("linux"))

    def test_config_override_on_both_platforms(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                paths = self.resolve(platform, {"MULTI_SLACK_MCP_CONFIG": "/custom/config.json"})
                self.assertEqual(paths["config"], Path("/custom/config.json"))

    def test_unsupported_platform_has_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "macOS and Linux"):
            self.resolve("win32")


if __name__ == "__main__":
    unittest.main()
