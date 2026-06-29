#!/usr/bin/env python3
"""
multi-slack-mcp admin — tiny localhost web UI for editing config.json.

Binds 127.0.0.1 only. No external auth: relies on localhost isolation.
Run:
    .venv/bin/python admin.py            # default port 8765
    .venv/bin/python admin.py --port 9000

Then open http://127.0.0.1:8765/ in a browser. Add / edit / delete
workspaces, set the default, and "Test" each token against Slack's
auth.test endpoint to confirm it works.

Writes are atomic: writes to config.json.tmp, fsyncs, renames, and keeps
config.json.bak as the previous version.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "ERROR: slack_sdk not installed. Run install.sh first.\n"
    )
    raise


CONFIG_PATH = Path(
    os.environ.get(
        "MULTI_SLACK_MCP_CONFIG",
        str(
            Path.home()
            / "Library"
            / "Application Support"
            / "multi-slack-mcp"
            / "config.json"
        ),
    )
)

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")
# Show one flash message per request via query string.
FLASH_QS = "msg"


# ---------- Config IO ------------------------------------------------------


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"workspaces": {}, "default_workspace": None}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        bak = CONFIG_PATH.with_suffix(".json.bak")
        bak.write_bytes(CONFIG_PATH.read_bytes())
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(CONFIG_PATH)


def mask(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 12:
        return "•" * len(token)
    return token[:8] + "…" + token[-4:]


# ---------- HTML rendering -------------------------------------------------

PAGE_CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.45 -apple-system, system-ui, sans-serif;
       max-width: 880px; margin: 28px auto; padding: 0 16px; }
h1 { margin: 0 0 4px; font-size: 22px; }
p.subtitle { color: #888; margin: 0 0 24px; }
.flash { padding: 10px 14px; border-radius: 8px; margin: 0 0 18px;
         background: #eaf5ea; border: 1px solid #cfe3cf; }
.flash.err { background: #fbeaea; border-color: #e8c5c5; }
table { border-collapse: collapse; width: 100%; margin-bottom: 24px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #ddd;
         vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
     color: #666; }
.row-default td { background: #fffbe6; }
code, .mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
form.inline { display: inline; }
button, input[type=submit] { font: inherit; padding: 4px 10px;
  border: 1px solid #bbb; background: #f5f5f5; border-radius: 6px;
  cursor: pointer; }
button.danger { color: #b00; }
.card { border: 1px solid #ddd; border-radius: 10px; padding: 16px 18px;
        margin-bottom: 16px; }
.card h2 { margin: 0 0 10px; font-size: 16px; }
label { display: block; margin: 8px 0 3px; font-size: 12px; color: #555; }
input[type=text], input[type=password] { width: 100%; padding: 6px 8px;
  font: inherit; border: 1px solid #bbb; border-radius: 6px;
  box-sizing: border-box; }
.hint { color: #888; font-size: 12px; margin-top: 6px; }
.actions { display: flex; gap: 6px; flex-wrap: wrap; }
.pill { display: inline-block; padding: 1px 7px; border-radius: 999px;
        background: #eef; color: #336; font-size: 11px; margin-left: 6px; }
@media (prefers-color-scheme: dark) {
  body { background: #1a1a1a; color: #eee; }
  .flash { background: #1f3320; border-color: #2c5530; }
  .flash.err { background: #3a1f1f; border-color: #5a2c2c; }
  th, td { border-color: #333; }
  .row-default td { background: #2a2510; }
  button, input[type=submit] { background: #2a2a2a; border-color: #555;
    color: #eee; }
  input[type=text], input[type=password] { background: #222;
    border-color: #555; color: #eee; }
  .card { border-color: #333; }
  .pill { background: #234; color: #cde; }
}
"""


def render(body: str, flash: tuple[str, str] | None = None) -> bytes:
    flash_html = ""
    if flash:
        kind, msg = flash
        cls = "flash err" if kind == "err" else "flash"
        flash_html = f'<div class="{cls}">{html.escape(msg)}</div>'
    page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>multi-slack-mcp admin</title>
<style>{PAGE_CSS}</style>
</head><body>
<h1>multi-slack-mcp</h1>
<p class="subtitle">Local admin · <span class="mono">{html.escape(str(CONFIG_PATH))}</span></p>
{flash_html}
{body}
</body></html>"""
    return page.encode("utf-8")


def page_index(cfg: dict, flash: tuple[str, str] | None) -> bytes:
    workspaces = cfg.get("workspaces") or {}
    default = cfg.get("default_workspace")
    rows: list[str] = []
    for key, ws in workspaces.items():
        token = ws.get("user_token") or ws.get("bot_token") or ""
        cookie = ws.get("web_client_cookie") or ""
        if token.startswith("xoxc-"):
            token_kind = "xoxc + cookie" if cookie else "xoxc (NO COOKIE!)"
        elif ws.get("user_token"):
            token_kind = "user_token"
        elif ws.get("bot_token"):
            token_kind = "bot_token (legacy)"
        else:
            token_kind = "—"
        token_cell = html.escape(mask(token))
        if cookie:
            token_cell += (
                f'<br><span class="hint">cookie: {html.escape(mask(cookie))}</span>'
            )
        is_default = key == default
        default_pill = '<span class="pill">default</span>' if is_default else ""
        rows.append(f"""
<tr class="{'row-default' if is_default else ''}">
  <td><strong>{html.escape(key)}</strong>{default_pill}</td>
  <td>{html.escape(ws.get('team_name', ''))}</td>
  <td class="mono">{token_cell}<br><span class="hint">{html.escape(token_kind)}</span></td>
  <td class="actions">
    <form class="inline" method="post" action="/test">
      <input type="hidden" name="key" value="{html.escape(key)}">
      <button type="submit">Test</button>
    </form>
    <form class="inline" method="get" action="/edit">
      <input type="hidden" name="key" value="{html.escape(key)}">
      <button type="submit">Edit</button>
    </form>
    {'' if is_default else f'''
    <form class="inline" method="post" action="/set-default">
      <input type="hidden" name="key" value="{html.escape(key)}">
      <button type="submit">Set default</button>
    </form>'''}
    <form class="inline" method="post" action="/delete"
          onsubmit="return confirm('Delete workspace {html.escape(key)}?');">
      <input type="hidden" name="key" value="{html.escape(key)}">
      <button type="submit" class="danger">Delete</button>
    </form>
  </td>
</tr>""")

    table = (
        f"""<table>
<tr><th>Key</th><th>Team name</th><th>Token</th><th>Actions</th></tr>
{''.join(rows) or '<tr><td colspan="4" style="color:#888">No workspaces configured yet.</td></tr>'}
</table>"""
    )

    add_form = """
<div class="card">
<h2>Add workspace</h2>
<form method="post" action="/add">
  <label for="key">Key <span class="hint">(kebab-case, used as <code>workspace</code> argument)</span></label>
  <input id="key" name="key" type="text" required pattern="[a-z0-9][a-z0-9-]{0,38}"
         placeholder="example-team">
  <label for="team_name">Team name <span class="hint">(human-readable)</span></label>
  <input id="team_name" name="team_name" type="text" placeholder="Example Team">
  <label for="user_token">Token <span class="hint">(<code>xoxp-…</code> for installed apps, or <code>xoxc-…</code> from a browser session)</span></label>
  <input id="user_token" name="user_token" type="password" required placeholder="xoxp-... or xoxc-...">
  <label for="web_client_cookie">Browser cookie <span class="hint">(<code>d=</code> cookie value, usually starts with <code>xoxd-</code>) — required only for <code>xoxc-</code> tokens, leave blank for <code>xoxp-</code></span></label>
  <input id="web_client_cookie" name="web_client_cookie" type="password" placeholder="xoxd-...">
  <label><input type="checkbox" name="make_default" value="1"> Make this the default workspace</label>
  <div class="hint" style="margin: 10px 0;">
    After saving, click <strong>Test</strong> to verify the token works.
    Restart the MCP host (Claude Desktop / Claude Code) for it to pick up
    config changes — the MCP server only reads config at startup.
  </div>
  <input type="submit" value="Add workspace">
</form>
</div>
"""
    return render(table + add_form, flash)


def page_edit(cfg: dict, key: str, flash: tuple[str, str] | None) -> bytes:
    ws = (cfg.get("workspaces") or {}).get(key)
    if not ws:
        return render(
            f'<p>Unknown workspace: <code>{html.escape(key)}</code>. '
            '<a href="/">Back</a></p>',
            ("err", f"Workspace '{key}' not found"),
        )
    token = ws.get("user_token") or ws.get("bot_token") or ""
    cookie = ws.get("web_client_cookie") or ""
    cookie_current = (
        f"current: <code>{html.escape(mask(cookie))}</code>"
        if cookie else "currently none"
    )
    body = f"""
<div class="card">
<h2>Edit workspace · <code>{html.escape(key)}</code></h2>
<form method="post" action="/update">
  <input type="hidden" name="old_key" value="{html.escape(key)}">
  <label for="new_key">Key
    <span class="hint">(kebab-case; renaming updates <code>default_workspace</code> if it pointed here)</span>
  </label>
  <input id="new_key" name="new_key" type="text" required
         pattern="[a-z0-9][a-z0-9-]{{0,38}}"
         value="{html.escape(key)}">
  <label for="team_name">Team name</label>
  <input id="team_name" name="team_name" type="text"
         value="{html.escape(ws.get('team_name', ''))}">
  <label for="user_token">Token
    <span class="hint">(leave blank to keep current: <code>{html.escape(mask(token))}</code>)</span>
  </label>
  <input id="user_token" name="user_token" type="password" placeholder="xoxp-... or xoxc-...">
  <label for="web_client_cookie">Browser cookie
    <span class="hint">(<code>d=</code> cookie value, only for <code>xoxc-</code> tokens; leave blank to keep current — {cookie_current}. Type "clear" to remove.)</span>
  </label>
  <input id="web_client_cookie" name="web_client_cookie" type="password" placeholder="xoxd-... or 'clear'">
  <div style="margin-top: 14px;" class="actions">
    <input type="submit" value="Save">
    <a href="/"><button type="button">Cancel</button></a>
  </div>
</form>
</div>"""
    return render(body, flash)


# ---------- Handler --------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "MultiSlackAdmin/1.0"

    # Quieter access log
    def log_message(self, fmt, *args):  # noqa: D401
        sys.stderr.write(
            f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}\n"
        )

    # ---- helpers
    def _flash_from_query(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        if FLASH_QS in params:
            msg = params[FLASH_QS][0]
            kind = "err" if msg.startswith("err:") else "ok"
            return (kind, msg[4:] if msg.startswith("err:") else msg)
        return None

    def _redirect(self, location: str, flash: str | None = None):
        if flash:
            sep = "&" if "?" in location else "?"
            location = (
                f"{location}{sep}{FLASH_QS}="
                + urllib.parse.quote(flash)
            )
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _send_html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {
            k: v[0]
            for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()
        }

    # ---- GET routes
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_html(page_index(load_config(), self._flash_from_query()))
        elif parsed.path == "/edit":
            params = urllib.parse.parse_qs(parsed.query)
            key = (params.get("key") or [""])[0]
            self._send_html(
                page_edit(load_config(), key, self._flash_from_query())
            )
        elif parsed.path == "/healthz":
            self._send_html(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    # ---- POST routes
    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        form = self._read_form()
        try:
            if parsed.path == "/add":
                self._handle_add(form)
            elif parsed.path == "/update":
                self._handle_update(form)
            elif parsed.path == "/delete":
                self._handle_delete(form)
            elif parsed.path == "/set-default":
                self._handle_set_default(form)
            elif parsed.path == "/test":
                self._handle_test(form)
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:  # noqa: BLE001
            self._redirect("/", f"err:{type(e).__name__}: {e}")

    def _handle_add(self, form: dict[str, str]):
        key = (form.get("key") or "").strip()
        if not KEY_RE.match(key):
            self._redirect(
                "/",
                "err:Key must be kebab-case (a-z, 0-9, -), starting with a letter/digit.",
            )
            return
        token = (form.get("user_token") or "").strip()
        if not token:
            self._redirect("/", "err:user_token is required.")
            return
        cfg = load_config()
        cfg.setdefault("workspaces", {})
        if key in cfg["workspaces"]:
            self._redirect("/", f"err:Workspace '{key}' already exists; edit it instead.")
            return
        entry: dict = {
            "user_token": token,
            "team_name": (form.get("team_name") or "").strip() or key,
        }
        cookie = (form.get("web_client_cookie") or "").strip()
        if cookie:
            entry["web_client_cookie"] = cookie
        elif token.startswith("xoxc-"):
            self._redirect(
                "/",
                "err:xoxc- tokens require a browser cookie (d= value, usually "
                "starts with xoxd-). Paste both, then save.",
            )
            return
        cfg["workspaces"][key] = entry
        if form.get("make_default") or not cfg.get("default_workspace"):
            cfg["default_workspace"] = key
        save_config(cfg)
        self._redirect(
            "/",
            f"Added '{key}'. Click Test to verify, then restart the MCP host.",
        )

    def _handle_update(self, form: dict[str, str]):
        old_key = (form.get("old_key") or form.get("key") or "").strip()
        new_key = (form.get("new_key") or old_key).strip()
        cfg = load_config()
        workspaces = cfg.setdefault("workspaces", {})
        ws = workspaces.get(old_key)
        if not ws:
            self._redirect("/", f"err:Unknown workspace '{old_key}'.")
            return
        # Rename if requested.
        rename_msg = ""
        if new_key != old_key:
            if not KEY_RE.match(new_key):
                self._redirect(
                    "/edit?key=" + urllib.parse.quote(old_key),
                    "err:New key must be kebab-case (a-z, 0-9, -).",
                )
                return
            if new_key in workspaces:
                self._redirect(
                    "/edit?key=" + urllib.parse.quote(old_key),
                    f"err:Key '{new_key}' already exists.",
                )
                return
            # Preserve insertion order: rebuild dict with the renamed key
            # in the same position.
            workspaces = {
                (new_key if k == old_key else k): v
                for k, v in workspaces.items()
            }
            cfg["workspaces"] = workspaces
            ws = workspaces[new_key]
            if cfg.get("default_workspace") == old_key:
                cfg["default_workspace"] = new_key
            rename_msg = f" Renamed '{old_key}' → '{new_key}'."
        team_name = (form.get("team_name") or "").strip()
        if team_name:
            ws["team_name"] = team_name
        new_token = (form.get("user_token") or "").strip()
        if new_token:
            ws["user_token"] = new_token
            # If they're providing a new token, drop any legacy bot_token.
            ws.pop("bot_token", None)
        # Cookie: blank = leave alone, "clear" = remove, anything else = set.
        cookie_field = (form.get("web_client_cookie") or "").strip()
        if cookie_field.lower() == "clear":
            ws.pop("web_client_cookie", None)
        elif cookie_field:
            ws["web_client_cookie"] = cookie_field
        # Sanity-check: xoxc- token must have a cookie.
        effective_token = ws.get("user_token") or ws.get("bot_token") or ""
        if effective_token.startswith("xoxc-") and not ws.get("web_client_cookie"):
            self._redirect(
                "/edit?key=" + urllib.parse.quote(new_key),
                "err:xoxc- tokens need a browser cookie. Paste the d= value "
                "(xoxd-...) in the Browser cookie field.",
            )
            return
        save_config(cfg)
        self._redirect(
            "/",
            f"Updated '{new_key}'.{rename_msg} Restart the MCP host to pick it up.",
        )

    def _handle_delete(self, form: dict[str, str]):
        key = (form.get("key") or "").strip()
        cfg = load_config()
        if key in (cfg.get("workspaces") or {}):
            del cfg["workspaces"][key]
            if cfg.get("default_workspace") == key:
                # Promote first remaining workspace, or clear.
                remaining = list(cfg["workspaces"].keys())
                cfg["default_workspace"] = remaining[0] if remaining else None
            save_config(cfg)
            self._redirect("/", f"Deleted '{key}'.")
        else:
            self._redirect("/", f"err:Unknown workspace '{key}'.")

    def _handle_set_default(self, form: dict[str, str]):
        key = (form.get("key") or "").strip()
        cfg = load_config()
        if key not in (cfg.get("workspaces") or {}):
            self._redirect("/", f"err:Unknown workspace '{key}'.")
            return
        cfg["default_workspace"] = key
        save_config(cfg)
        self._redirect("/", f"Default workspace set to '{key}'.")

    def _handle_test(self, form: dict[str, str]):
        key = (form.get("key") or "").strip()
        cfg = load_config()
        ws = (cfg.get("workspaces") or {}).get(key)
        if not ws:
            self._redirect("/", f"err:Unknown workspace '{key}'.")
            return
        token = ws.get("user_token") or ws.get("bot_token")
        if not token:
            self._redirect("/", f"err:No token configured for '{key}'.")
            return
        try:
            cookie = ws.get("web_client_cookie")
            if cookie:
                client = WebClient(
                    token=token, headers={"Cookie": f"d={cookie}"}
                )
            else:
                client = WebClient(token=token)
            resp = client.auth_test()
            team = resp.get("team", "?")
            user = resp.get("user", "?")
            url = resp.get("url", "")
            self._redirect(
                "/",
                f"OK '{key}': team={team}, user={user}, url={url}",
            )
        except SlackApiError as e:
            err = e.response.get("error", "slack_api_error")
            self._redirect("/", f"err:'{key}' test failed: {err}")
        except Exception as e:  # noqa: BLE001
            self._redirect("/", f"err:'{key}' test failed: {type(e).__name__}: {e}")


# ---------- Entrypoint -----------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="multi-slack-mcp admin UI")
    ap.add_argument("--port", type=int, default=8765, help="default 8765")
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="default 127.0.0.1 — do NOT bind to 0.0.0.0; tokens in plaintext.",
    )
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write(
            "WARNING: binding to non-localhost host exposes plaintext "
            "OAuth tokens over the network. Aborting. Pass "
            "--host 127.0.0.1 or use an SSH tunnel.\n"
        )
        sys.exit(2)

    server = HTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    sys.stderr.write(f"multi-slack-mcp admin listening on {url}\n")
    sys.stderr.write(f"Editing: {CONFIG_PATH}\n")
    sys.stderr.write("Press Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nShutting down.\n")
        server.server_close()


if __name__ == "__main__":
    main()
