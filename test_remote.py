"""OAuth and MCP boundary tests using dummy credentials and in-process HTTP."""

import base64
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from starlette.testclient import TestClient

from remote import connection_password, create_remote_app, validate_origin


ORIGIN = "https://mcp.example.test"
PASSWORD = "test-only-connection-password-0123456789"
CALLBACK = "https://client.example.test/callback"
VERIFIER = "v" * 64


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        config = Path(self.temp) / "config.json"
        config.write_text(json.dumps({"workspaces": {"example": {"user_token": "xoxp-dummy-secret"}}}))
        self.enterContext(patch("server.CONFIG_PATH", config))
        self.client = self.enterContext(TestClient(create_remote_app(ORIGIN, PASSWORD), base_url=ORIGIN))

    def register(self, **overrides):
        data = {"client_name": "Test client", "redirect_uris": [CALLBACK],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none"}
        data.update(overrides)
        response = self.client.post("/register", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def begin(self, client_id, **overrides):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
        params = {"client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code",
                  "code_challenge": challenge, "code_challenge_method": "S256",
                  "scope": "slack", "state": "keep-me", "resource": ORIGIN + "/mcp"}
        params.update(overrides)
        return self.client.get("/authorize", params=params, follow_redirects=False)

    def approve(self, client_id):
        start = self.begin(client_id)
        self.assertEqual(start.status_code, 302, start.text)
        location = start.headers["location"]
        page = self.client.get(location)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        response = self.client.post(location, data={"csrf": csrf, "password": PASSWORD, "decision": "allow"},
                                    headers={"Origin": ORIGIN}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        result = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(result["state"], ["keep-me"])
        return result["code"][0]

    def exchange(self, client_id, code, **overrides):
        data = {"client_id": client_id, "grant_type": "authorization_code", "code": code,
                "redirect_uri": CALLBACK, "code_verifier": VERIFIER, "resource": ORIGIN + "/mcp"}
        data.update(overrides)
        return self.client.post("/token", data=data)

    def credentials(self):
        client_id = self.register()["client_id"]
        response = self.exchange(client_id, self.approve(client_id))
        self.assertEqual(response.status_code, 200, response.text)
        return client_id, response.json()

    def rpc(self, token, method, params=None):
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        if token:
            headers["Authorization"] = "Bearer " + token
        return self.client.post("/mcp", headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def test_discovery_and_unauthenticated_access(self):
        response = self.rpc(None, "tools/list")
        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata", response.headers["www-authenticate"])
        metadata = self.client.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(metadata["resource"], ORIGIN + "/mcp")
        auth = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(auth["registration_endpoint"], ORIGIN + "/register")
        self.assertIn("S256", auth["code_challenge_methods_supported"])
        self.assertEqual(self.rpc("fake", "tools/list").status_code, 401)

    def test_full_oauth_and_mcp_flow(self):
        _, tokens = self.credentials()
        token = tokens["access_token"]
        response = self.rpc(token, "initialize", {"protocolVersion": "2025-11-25",
            "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(response.status_code, 200, response.text)
        tools = self.rpc(token, "tools/list").json()["result"]["tools"]
        self.assertEqual(len(tools), 9)
        send = next(t for t in tools if t["name"] == "slack_send_message")
        self.assertFalse(send["annotations"]["readOnlyHint"])
        result = self.rpc(token, "tools/call", {"name": "slack_list_workspaces", "arguments": {}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn("example", result.text)
        self.assertNotIn("xoxp-dummy-secret", result.text)

    def test_wrong_password_and_csrf_rejected(self):
        client_id = self.register()["client_id"]
        location = self.begin(client_id).headers["location"]
        page = self.client.get(location)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        data = {"csrf": csrf, "password": PASSWORD, "decision": "allow"}
        self.assertEqual(self.client.post(location, data=data).status_code, 403)
        self.assertEqual(self.client.post(location, data=data, headers={"Origin": "https://evil.test"}).status_code, 403)
        data["csrf"] = "wrong"
        self.assertEqual(self.client.post(location, data=data, headers={"Origin": ORIGIN}).status_code, 403)
        data.update(csrf=csrf, password="wrong")
        response = self.client.post(location, data=data, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("location", response.headers)

    def test_pkce_client_binding_and_code_reuse(self):
        client_id = self.register()["client_id"]
        other = self.register()["client_id"]
        code = self.approve(client_id)
        self.assertEqual(self.exchange(client_id, code, code_verifier="wrong").status_code, 400)
        self.assertEqual(self.exchange(other, code).status_code, 400)
        self.assertEqual(self.exchange(client_id, code).status_code, 200)
        self.assertEqual(self.exchange(client_id, code).status_code, 400)

    def test_resource_redirect_and_scope_validation(self):
        client_id = self.register()["client_id"]
        response = self.begin(client_id, redirect_uri="https://evil.test/callback")
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("https://evil.test", response.headers.get("location", ""))
        response = self.begin(client_id, resource="https://other.test/mcp")
        self.assertNotIn("/connect?", response.headers.get("location", ""))
        response = self.begin(client_id, scope="admin")
        self.assertNotIn("/connect?", response.headers.get("location", ""))
        code = self.approve(client_id)
        self.assertEqual(self.exchange(client_id, code, resource="https://other.test/mcp").status_code, 400)

    def test_refresh_rotation_and_revocation(self):
        client_id, original = self.credentials()
        request = {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": original["refresh_token"]}
        refreshed = self.client.post("/token", data=request)
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        tokens = refreshed.json()
        self.assertEqual(self.client.post("/token", data=request).status_code, 400)
        self.assertEqual(self.rpc(original["access_token"], "tools/list").status_code, 401)
        response = self.client.post("/revoke", data={"client_id": client_id, "token": tokens["refresh_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.rpc(tokens["access_token"], "tools/list").status_code, 401)

    def test_hosts_body_limit_and_no_admin_routes(self):
        self.assertEqual(self.client.get("/health", headers={"host": "evil.test"}).status_code, 400)
        self.assertEqual(self.client.post("/register", content=b"x" * 65537).status_code, 413)
        self.assertEqual(self.client.get("/edit?key=example").status_code, 404)
        self.assertEqual(self.client.get("/health").headers["referrer-policy"], "no-referrer")

    def test_confidential_client_needs_its_secret(self):
        client = self.register(token_endpoint_auth_method="client_secret_post")
        code = self.approve(client["client_id"])
        self.assertEqual(self.exchange(client["client_id"], code).status_code, 401)
        response = self.exchange(client["client_id"], code, client_secret=client["client_secret"])
        self.assertEqual(response.status_code, 200, response.text)

    def test_expiry_and_refresh_scope_escalation(self):
        client_id, tokens = self.credentials()
        request = {"client_id": client_id, "grant_type": "refresh_token",
                   "refresh_token": tokens["refresh_token"], "scope": "slack admin"}
        self.assertEqual(self.client.post("/token", data=request).status_code, 400)
        future = time.time() + 3601
        with patch("remote_auth.time.time", return_value=future):
            self.assertEqual(self.rpc(tokens["access_token"], "tools/list").status_code, 401)

    def test_denied_consent_and_expired_code(self):
        client_id = self.register()["client_id"]
        location = self.begin(client_id).headers["location"]
        page = self.client.get(location)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        response = self.client.post(location, data={"csrf": csrf, "decision": "deny"},
            headers={"Origin": ORIGIN}, follow_redirects=False)
        self.assertIn("error=access_denied", response.headers["location"])
        self.assertNotIn("code=", response.headers["location"])
        self.assertEqual(self.client.get(location).status_code, 400)
        code = self.approve(client_id)
        with patch("remote_auth.time.time", return_value=time.time() + 121):
            self.assertEqual(self.exchange(client_id, code).status_code, 400)

    def test_cross_client_revocation_does_not_revoke(self):
        _, tokens = self.credentials()
        other = self.register()["client_id"]
        response = self.client.post("/revoke", data={"client_id": other, "token": tokens["refresh_token"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rpc(tokens["access_token"], "tools/list").status_code, 200)

    def test_remote_password_file_and_origin(self):
        path = Path(self.temp) / "remote-password"
        password = connection_password(path)
        self.assertGreaterEqual(len(password), 32)
        self.assertEqual(connection_password(path), password)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for origin in ("http://example.test", "https://example.test/mcp", "https://user:pass@example.test", "https://example.test?x=1"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                validate_origin(origin)


if __name__ == "__main__":
    unittest.main()
