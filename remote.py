#!/usr/bin/env python3
"""Run the same Slack tools over authenticated Streamable HTTP and HTTPS tunnel."""

import argparse
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.auth.routes import cors_middleware
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
import uvicorn

from paths import app_paths
from remote_auth import OwnerOAuthProvider, SCOPE
from server import create_server


class RequestBoundary:
    """Bound request bodies and prevent caching, framing and referrer leakage."""
    def __init__(self, app, resource):
        self.app, self.resource = app, resource

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            chunks.append(chunk)
            size += len(chunk)
            if size > 65536:
                return await PlainTextResponse("Request too large", status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        if scope["path"] == "/token":
            resources = parse_qs(body.decode("utf-8", errors="replace")).get("resource", [])
            if any(value != self.resource for value in resources):
                return await JSONResponse({"error": "invalid_target"}, status_code=400)(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def safe_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"content-security-policy", b"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"),
                ]
            await send(message)
        await self.app(scope, replay, safe_send)


def validate_origin(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("Public URL must be an HTTPS origin, such as https://slack.example.com")
    return value.rstrip("/")


def create_remote_app(origin: str, password: str):
    origin = validate_origin(origin)
    if len(password) < 32:
        raise ValueError("Use a generated connection password of at least 32 characters")
    provider = OwnerOAuthProvider(origin, password)
    host = urlsplit(origin).hostname
    mcp = create_server(
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=origin, resource_server_url=provider.resource,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[urlsplit(origin).netloc, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[origin],
        ),
    )
    mcp.custom_route("/connect", methods=["GET", "POST"])(provider.consent)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok"})

    app = mcp.streamable_http_app()
    app.router.routes = [
        Route("/revoke", endpoint=cors_middleware(provider.revoke_request, ["POST", "OPTIONS"]),
              methods=["POST", "OPTIONS"]) if getattr(route, "path", None) == "/revoke" else route
        for route in app.router.routes
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host, "127.0.0.1", "localhost"])
    app.add_middleware(RequestBoundary, resource=provider.resource)
    return app


def connection_password(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as file:
            password = file.read().strip()
        if len(password) < 32:
            raise ValueError("Invalid connection password file; remove it to generate a new one")
        return password
    password = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as file:
        file.write(password + "\n")
    return password


def start_tunnel(port: int, log_path: Path):
    executable = shutil.which("cloudflared")
    if not executable:
        raise RuntimeError("Install cloudflared first, or use --public-url with an existing HTTPS tunnel")
    # Ignore any default named-tunnel config. This mode needs no account credentials.
    with log_path.open("w") as log:
        process = subprocess.Popen([
            executable, "--no-autoupdate", "tunnel", "--config", os.devnull,
            "--url", f"http://127.0.0.1:{port}",
        ], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    deadline = time.monotonic() + 45
    try:
        while time.monotonic() < deadline:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log_path.read_text())
            if match and process.poll() is None:
                return process, match.group(0)
            if process.poll() is not None:
                raise RuntimeError(f"Tunnel exited; see {log_path}")
            time.sleep(0.2)
        raise RuntimeError(f"Tunnel did not become ready; see {log_path}")
    except BaseException:
        stop_tunnel(process)
        raise


def stop_tunnel(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tunnel", action="store_true", help="Start a temporary Cloudflare HTTPS tunnel")
    mode.add_argument("--public-url", help="HTTPS origin already forwarding to this service")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--show-password", action="store_true", help="Print the local connection password and exit")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    config_dir = app_paths()["config"].parent
    password_path = config_dir / "remote-password"
    password = connection_password(password_path)
    if args.show_password:
        print(password)
        return
    if not args.tunnel and not args.public_url:
        parser.error("choose --tunnel or --public-url")
    tunnel = None
    try:
        if args.tunnel:
            tunnel, origin = start_tunnel(args.port, config_dir / "remote-tunnel.log")
        else:
            origin = validate_origin(args.public_url)
        app = create_remote_app(origin, password)
        print(f"MCP URL: {origin}/mcp", flush=True)
        print(f"Connection password: saved in {password_path} (use --show-password to display)", flush=True)
        print("Keep this process running. OAuth connections must be authorized again after a restart.", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False,
                    proxy_headers=False, limit_concurrency=64, timeout_keep_alive=5)
    finally:
        if tunnel:
            stop_tunnel(tunnel)


if __name__ == "__main__":
    main()
