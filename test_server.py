"""Credential reload regressions; no real credentials or Slack requests."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ConfigReloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.cfg = {
            "default_workspace": "one",
            "workspaces": {
                "one": {"user_token": "xoxc-test-old", "web_client_cookie": "xoxd-test-old"},
                "two": {"user_token": "xoxp-test-two"},
            },
        }
        self.save()
        spec = importlib.util.spec_from_file_location("reload_test_server", Path(__file__).with_name("server.py"))
        self.server = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"MULTI_SLACK_MCP_CONFIG": str(self.path)}):
            spec.loader.exec_module(self.server)

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.cfg))
        temporary.replace(self.path)

    def client(self, workspace="one"):
        seen = []
        result = self.server._wrap_call(workspace, lambda c, _: seen.append(c) or {"ok": True})
        self.assertTrue(result.get("ok"), result)
        return seen[0]

    def test_token_and_cookie_rotation_refreshes_only_changed_workspace(self):
        old = self.client()
        other = self.client("two")
        self.cfg["workspaces"]["one"].update(user_token="xoxc-test-new", web_client_cookie="xoxd-test-new")
        self.save()
        new = self.client()
        self.assertIsNot(old, new)
        self.assertEqual(new.token, "xoxc-test-new")
        self.assertEqual(new.headers["Cookie"], "d=xoxd-test-new")
        self.assertIs(other, self.client("two"))
        self.assertIs(new, self.client())

    def test_cookie_only_change_and_removal(self):
        old = self.client()
        self.cfg["workspaces"]["one"]["web_client_cookie"] = "xoxd-test-new"
        self.save()
        new = self.client()
        self.assertIsNot(old, new)
        self.assertEqual(new.headers["Cookie"], "d=xoxd-test-new")
        self.cfg["workspaces"]["one"] = {"user_token": "xoxp-test-new"}
        self.save()
        self.assertNotIn("Cookie", self.client().headers)

    def test_added_removed_renamed_workspaces_and_default(self):
        self.client()
        self.cfg["workspaces"]["renamed"] = self.cfg["workspaces"].pop("one")
        self.cfg["default_workspace"] = "renamed"
        self.save()
        listing = self.server.slack_list_workspaces()
        self.assertEqual(listing["default_workspace"], "renamed")
        self.assertEqual({w["key"] for w in listing["workspaces"]}, {"renamed", "two"})
        self.assertNotIn("one", self.server._CLIENTS)
        self.assertEqual(self.server._wrap_call("one", lambda *_: self.fail()), {
            "error": "unknown_workspace",
            "message": "Workspace 'one' is not configured. Available: two, renamed",
        })
        self.assertEqual(self.client(None).token, "xoxc-test-old")
        self.cfg["workspaces"] = {}
        self.cfg["default_workspace"] = None
        self.save()
        self.assertEqual(self.server._wrap_call(None, lambda *_: self.fail())["error"], "no_workspace")

    def test_invalid_or_missing_file_retains_config_then_recovers(self):
        old = self.client()
        for text in ('{', '[]', '{"workspaces": []}', '{"workspaces": {"one": 3}}',
                     '{"workspaces": {"one": {"user_token": []}}}', '{"default_workspace": []}'):
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertIs(old, self.client())
        self.path.unlink()
        self.assertIs(old, self.client())
        self.cfg["workspaces"]["one"]["user_token"] = "xoxc-test-recovered"
        self.save()
        self.assertEqual(self.client().token, "xoxc-test-recovered")

    def test_public_tool_uses_new_credentials_without_retrying_request(self):
        with patch.object(self.server, "WebClient") as factory:
            factory.return_value.search_messages.return_value = {"ok": True}
            self.server.slack_search_public_and_private("test", workspace="one")
            self.cfg["workspaces"]["one"]["user_token"] = "xoxc-test-updated"
            self.save()
            self.server.slack_search_public_and_private("test", workspace="one")
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(factory.call_args.kwargs["token"], "xoxc-test-updated")
            self.assertEqual(factory.return_value.search_messages.call_count, 2)


if __name__ == "__main__":
    unittest.main()
