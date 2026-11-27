"""Single-owner OAuth provider for the optional remote MCP server.

The MCP SDK handles discovery, client authentication, redirect matching and
PKCE. This provider handles explicit owner consent and short-lived grants.
OAuth sessions intentionally live only for the lifetime of the server.
"""

from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response


SCOPE = "slack"
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 86400 * 30


@dataclass
class PendingConsent:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    expires_at: float
    csrf: str


class OwnerOAuthProvider:
    def __init__(self, origin: str, password: str):
        self.origin = origin.rstrip("/")
        self.resource = self.origin + "/mcp"
        self.password_digest = hashlib.sha256(password.encode()).digest()
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, PendingConsent] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        self.pairs: dict[str, str] = {}
        self.login_attempts: deque[float] = deque()

    def _prune(self):
        now = time.time()
        for records in (self.pending, self.codes, self.access, self.refresh):
            for key, value in list(records.items()):
                if value.expires_at is not None and value.expires_at <= now:
                    records.pop(key, None)
        for refresh, access in list(self.pairs.items()):
            if refresh not in self.refresh:
                self.access.pop(access, None)
                del self.pairs[refresh]

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def register_client(self, client_info):
        if len(self.clients) >= 256:
            raise RegistrationError("invalid_client_metadata", "Registration limit reached")
        redirects = client_info.redirect_uris or []
        if not 1 <= len(redirects) <= 10:
            raise RegistrationError("invalid_redirect_uri", "Provide 1 to 10 redirect URLs")
        for redirect in redirects:
            url = urlsplit(str(redirect))
            local_http = url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
            if (url.scheme != "https" and not local_http) or not url.hostname or url.fragment or url.username or url.password:
                raise RegistrationError("invalid_redirect_uri", "Use HTTPS or a loopback callback")
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client, params):
        self._prune()
        if params.resource not in (None, self.resource):
            raise AuthorizeError("invalid_request", "Incorrect MCP resource")
        if set(params.scopes or [SCOPE]) != {SCOPE}:
            raise AuthorizeError("invalid_scope", "The slack scope is required")
        if len(self.pending) >= 128:
            raise AuthorizeError("temporarily_unavailable", "Too many pending connections")
        request_id = secrets.token_urlsafe(32)
        self.pending[request_id] = PendingConsent(
            client, params, time.time() + 600, secrets.token_urlsafe(32),
        )
        return self.origin + "/connect?request=" + request_id

    async def consent(self, request: Request):
        self._prune()
        request_id = request.query_params.get("request", "")
        pending = self.pending.get(request_id)
        if pending is None:
            return HTMLResponse("Connection request expired. Start again from your MCP client.", status_code=400)
        error = ""
        if request.method == "POST":
            if request.headers.get("origin") != self.origin:
                return Response("Invalid origin", status_code=403)
            form = await request.form()
            csrf = str(form.get("csrf", ""))
            cookie = request.cookies.get("mcp_consent", "")
            if not (hmac.compare_digest(csrf, pending.csrf) and hmac.compare_digest(cookie, pending.csrf)):
                return Response("Invalid consent session", status_code=403)
            if form.get("decision") == "deny":
                del self.pending[request_id]
                return RedirectResponse(construct_redirect_uri(
                    str(pending.params.redirect_uri), error="access_denied", state=pending.params.state,
                ), status_code=303)
            now = time.monotonic()
            while self.login_attempts and self.login_attempts[0] < now - 60:
                self.login_attempts.popleft()
            if len(self.login_attempts) >= 20:
                return Response("Too many attempts. Wait one minute.", status_code=429)
            self.login_attempts.append(now)
            supplied = hashlib.sha256(str(form.get("password", "")).encode()).digest()
            if hmac.compare_digest(supplied, self.password_digest):
                del self.pending[request_id]
                code = secrets.token_urlsafe(32)
                params = pending.params
                self.codes[code] = AuthorizationCode(
                    code=code, scopes=[SCOPE], expires_at=time.time() + 120,
                    client_id=pending.client.client_id, code_challenge=params.code_challenge,
                    redirect_uri=params.redirect_uri,
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=self.resource, subject="owner",
                )
                response = RedirectResponse(construct_redirect_uri(
                    str(params.redirect_uri), code=code, state=params.state,
                ), status_code=303)
                response.delete_cookie("mcp_consent", path="/connect")
                return response
            error = "Incorrect connection password."

        name = html.escape(pending.client.client_name or "MCP client")
        callback = html.escape(str(pending.params.redirect_uri))
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect Multi-Slack MCP</title><style>
body {{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem;color:#222;background:#fff}}
h1 {{font-size:1.6rem}} label {{display:block;margin-top:1.5rem}}
input {{box-sizing:border-box;width:100%;font:inherit;padding:.6rem;margin:.4rem 0 1rem}}
button {{font:inherit;padding:.6rem 1rem;margin-right:.5rem;cursor:pointer}}
code {{overflow-wrap:anywhere}} .error {{color:#a11}}
</style></head><body><h1>Connect Multi-Slack MCP</h1>
<p><strong>{name}</strong> is requesting access to all configured Slack workspaces.</p>
<p>Allowing this connection lets it read messages and profiles, search, and <strong>send messages as you</strong>.</p>
<p>Return address: <code>{callback}</code></p>
<form method="post"><input type="hidden" name="csrf" value="{pending.csrf}">
<label for="password">Connection password</label>
<p>Use the password saved by your local remote-server launcher. This is separate from your Slack password, token and cookie.</p>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<p class="error" role="alert">{error}</p>
<button name="decision" value="allow">Allow connection</button>
<button name="decision" value="deny" formnovalidate>Deny</button></form></body></html>"""
        response = HTMLResponse(body, status_code=401 if error else 200)
        response.set_cookie("mcp_consent", pending.csrf, max_age=600, httponly=True,
                            secure=self.origin.startswith("https:"), samesite="lax", path="/connect")
        return response

    async def load_authorization_code(self, client, authorization_code):
        self._prune()
        return self.codes.get(authorization_code)

    def _issue(self, client_id, scopes):
        self._prune()
        if len(self.refresh) >= 1024:
            raise TokenError("invalid_grant", "Connection limit reached; restart the server")
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        self.access[access] = AccessToken(token=access, client_id=client_id, scopes=scopes,
            expires_at=now + ACCESS_SECONDS, resource=self.resource, subject="owner")
        self.refresh[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes,
            expires_at=now + REFRESH_SECONDS, subject="owner")
        self.pairs[refresh] = access
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_SECONDS,
                          refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        code = self.codes.pop(authorization_code.code, None)
        if code is None or code.client_id != client.client_id or code.expires_at <= time.time():
            raise TokenError("invalid_grant", "Expired or consumed authorization code")
        return self._issue(client.client_id, code.scopes)

    async def load_refresh_token(self, client, refresh_token):
        self._prune()
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        token = self.refresh.get(refresh_token.token)
        if token is None or token.client_id != client.client_id or token.expires_at <= time.time():
            raise TokenError("invalid_grant", "Expired or consumed refresh token")
        if not set(scopes).issubset(token.scopes):
            raise TokenError("invalid_scope", "Cannot expand access")
        await self.revoke_token(token)
        return self._issue(client.client_id, scopes)

    async def load_access_token(self, token):
        self._prune()
        result = self.access.get(token)
        return result if result and result.resource == self.resource else None

    async def revoke_token(self, token):
        for refresh, access in list(self.pairs.items()):
            if token.token in (refresh, access):
                self.refresh.pop(refresh, None)
                self.access.pop(access, None)
                del self.pairs[refresh]

    async def revoke_request(self, request: Request):
        # SDK 1.28's revocation form requires a client_secret field even for
        # public clients. Authenticate with the SDK, then accept the RFC 7009
        # token form without requiring a secret that public clients do not have.
        try:
            client = await ClientAuthenticator(self).authenticate_request(request)
        except AuthenticationError:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        form = await request.form()
        value = form.get("token")
        if not isinstance(value, str) or not value:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        self._prune()
        token = self.access.get(value) or self.refresh.get(value)
        if token is not None and token.client_id == client.client_id:
            await self.revoke_token(token)
        return Response(status_code=200)
