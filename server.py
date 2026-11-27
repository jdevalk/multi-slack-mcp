#!/usr/bin/env python3
"""
Multi-workspace Slack MCP server.

Exposes a single set of Slack tools across N configured Slack workspaces.
Each tool takes an optional `workspace` argument (kebab-case key from
config.json). If omitted, the `default_workspace` from config is used.

Auth: per-workspace User OAuth Tokens (xoxp-...). Calls run as the
installing user and can see everything that user can see. Legacy
`bot_token` entries are still honoured with a deprecation warning.
For workspaces where you can't install a custom app, an optional
`web_client_cookie` (the `d=` browser cookie value, usually starting
with `xoxd-`) can be paired with a `xoxc-` token in `user_token`.

Transport: stdio. Logs use the platform's log/state directory (see README).
Stdout is reserved for MCP JSON-RPC framing.
"""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from threading import Lock
from typing import Any

from paths import app_paths

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: missing dependency 'mcp'. Run install.sh first.\n"
        f"Underlying error: {exc}\n"
    )
    raise

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: missing dependency 'slack_sdk'. Run install.sh first.\n"
        f"Underlying error: {exc}\n"
    )
    raise


# ---------- Logging --------------------------------------------------------

_PATHS = app_paths()
LOG_FILE = _PATHS["log"]
LOG_DIR = LOG_FILE.parent
LOG_DIR.mkdir(parents=True, exist_ok=True)

_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
)
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("multi-slack-mcp")


# ---------- Config ---------------------------------------------------------

# Shared with the admin and installer; MULTI_SLACK_MCP_CONFIG overrides it.
CONFIG_PATH = _PATHS["config"]


def _load_config() -> dict[str, Any] | None:
    """Read one complete snapshot; invalid/unavailable files keep the last config."""
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        log.warning("Cannot read config; retaining the last valid configuration")
        return None

    if not isinstance(cfg, dict):
        log.warning("Invalid config structure; retaining the last valid configuration")
        return None
    workspaces = cfg.get("workspaces", {})
    default = cfg.get("default_workspace")
    if (
        not isinstance(workspaces, dict)
        or (default is not None and not isinstance(default, str))
        or any(
            not isinstance(ws, dict)
            or any(
                ws.get(field) is not None and not isinstance(ws[field], str)
                for field in ("user_token", "bot_token", "web_client_cookie", "team_name")
            )
            for ws in workspaces.values()
        )
    ):
        log.warning("Invalid config structure; retaining the last valid configuration")
        return None
    if default and default not in workspaces:
        log.warning(
            "default_workspace '%s' is not in workspaces map; ignoring",
            default,
        )
        default = None
    if not default and workspaces:
        default = next(iter(workspaces.keys()))
    return {"workspaces": workspaces, "default_workspace": default}


CONFIG = _load_config() or {"workspaces": {}, "default_workspace": None}
_CONFIG_LOCK = Lock()


# ---------- Slack client cache --------------------------------------------

_CLIENTS: dict[str, WebClient] = {}


def _refresh_config() -> None:
    """Reload before each tool call. Caller holds _CONFIG_LOCK."""
    global CONFIG
    updated = _load_config()
    if updated is None or updated == CONFIG:
        return
    for key in list(_CLIENTS):
        if (
            key not in updated["workspaces"]
            or updated["workspaces"][key] != CONFIG["workspaces"].get(key)
        ):
            del _CLIENTS[key]
    CONFIG = updated
    log.info("Reloaded config: %d workspace(s)", len(CONFIG["workspaces"]))


def _resolve_workspace(workspace: str | None) -> tuple[str | None, dict | None]:
    """Return (key, error_dict). error_dict is None when key is usable."""
    key = workspace or CONFIG["default_workspace"]
    workspaces = CONFIG["workspaces"]
    if not key:
        return None, {
            "error": "no_workspace",
            "message": (
                "No workspace specified and no default_workspace configured. "
                "Available: " + ", ".join(workspaces.keys() or ["<none>"])
            ),
        }
    if key not in workspaces:
        return None, {
            "error": "unknown_workspace",
            "message": (
                f"Workspace '{key}' is not configured. "
                "Available: " + ", ".join(workspaces.keys() or ["<none>"])
            ),
        }
    return key, None


_WARNED_BOT_TOKEN: set[str] = set()


def _client_for(workspace_key: str) -> WebClient:
    client = _CLIENTS.get(workspace_key)
    if client is None:
        ws = CONFIG["workspaces"][workspace_key]
        token = ws.get("user_token")
        if not token:
            # Back-compat: accept legacy bot_token, warn once per workspace.
            token = ws.get("bot_token")
            if token and workspace_key not in _WARNED_BOT_TOKEN:
                msg = (
                    f"DEPRECATION: workspace '{workspace_key}' uses "
                    "'bot_token'; rename to 'user_token' (xoxp-...) in "
                    "config.json. Bot tokens still work but only see "
                    "channels the bot is invited to.\n"
                )
                sys.stderr.write(msg)
                log.warning(msg.strip())
                _WARNED_BOT_TOKEN.add(workspace_key)
        if not token:
            raise RuntimeError(
                f"Workspace '{workspace_key}' has no user_token (or "
                "legacy bot_token) configured."
            )
        # Optional: web-client auth (xoxc- token + d= browser cookie).
        # Used when the workspace doesn't allow installing custom apps.
        cookie = ws.get("web_client_cookie")
        if cookie:
            client = WebClient(
                token=token, headers={"Cookie": f"d={cookie}"}
            )
        else:
            client = WebClient(token=token)
        _CLIENTS[workspace_key] = client
    return client


def _wrap_call(workspace: str | None, fn):
    """Resolve workspace, run fn(client, key), return dict or error dict."""
    key = workspace
    try:
        # Resolve and select a client from the same snapshot. In-flight Slack
        # requests keep their client; the next request uses updated credentials.
        with _CONFIG_LOCK:
            _refresh_config()
            key, err = _resolve_workspace(workspace)
            if err:
                return err
            client = _client_for(key)
        result = fn(client, key)
        # SlackResponse acts like a dict; coerce to plain dict.
        if hasattr(result, "data"):
            data = dict(result.data)
        else:
            data = result
        data["_workspace"] = key
        return data
    except SlackApiError as e:
        log.warning(
            "Slack API error (workspace=%s): %s", key, e.response.get("error")
        )
        return {
            "error": e.response.get("error", "slack_api_error"),
            "message": str(e),
            "_workspace": key,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected error (workspace=%s)", key)
        return {
            "error": "internal_error",
            "message": f"{type(e).__name__}: {e}",
            "_workspace": key,
        }


# ---------- MCP server -----------------------------------------------------

def slack_list_workspaces() -> dict:
    """List all configured Slack workspaces (kebab-case keys + team names)."""
    with _CONFIG_LOCK:
        _refresh_config()
        return {
            "default_workspace": CONFIG["default_workspace"],
            "workspaces": [
                {"key": k, "team_name": v.get("team_name", k)}
                for k, v in CONFIG["workspaces"].items()
            ],
        }


def slack_search_public_and_private(
    query: str,
    count: int = 20,
    workspace: str | None = None,
) -> dict:
    """Search messages across public+private channels (search.messages)."""
    return _wrap_call(
        workspace,
        lambda c, _: c.search_messages(query=query, count=count),
    )


def slack_read_channel(
    channel_id: str,
    limit: int = 50,
    oldest: str | None = None,
    latest: str | None = None,
    cursor: str | None = None,
    workspace: str | None = None,
) -> dict:
    """Read messages from a channel (conversations.history)."""

    def call(c: WebClient, _k: str):
        kwargs: dict[str, Any] = {"channel": channel_id, "limit": limit}
        if oldest is not None:
            kwargs["oldest"] = oldest
        if latest is not None:
            kwargs["latest"] = latest
        if cursor is not None:
            kwargs["cursor"] = cursor
        return c.conversations_history(**kwargs)

    return _wrap_call(workspace, call)


def slack_read_thread(
    channel_id: str,
    thread_ts: str,
    workspace: str | None = None,
) -> dict:
    """Read replies in a thread (conversations.replies)."""
    return _wrap_call(
        workspace,
        lambda c, _: c.conversations_replies(channel=channel_id, ts=thread_ts),
    )


def slack_read_user_profile(
    user_id: str,
    workspace: str | None = None,
) -> dict:
    """Read a user profile (users.info)."""
    return _wrap_call(
        workspace, lambda c, _: c.users_info(user=user_id)
    )


def slack_send_message(
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
    workspace: str | None = None,
) -> dict:
    """Post a message to a channel or thread (chat.postMessage).

    Note: with a user token this posts AS the installing user, not as a bot.
    """

    def call(c: WebClient, _k: str):
        kwargs: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        return c.chat_postMessage(**kwargs)

    return _wrap_call(workspace, call)


def slack_search_channels(
    query: str | None = None,
    workspace: str | None = None,
) -> dict:
    """List/search channels by name fragment (conversations.list)."""

    def call(c: WebClient, _k: str):
        matches: list[dict] = []
        cursor: str | None = None
        q = (query or "").lower().strip()
        # Paginate; bail after a reasonable cap to avoid runaway calls.
        for _ in range(20):
            resp = c.conversations_list(
                exclude_archived=True,
                limit=200,
                types="public_channel,private_channel",
                cursor=cursor,
            )
            for ch in resp.get("channels", []):
                if not q or q in ch.get("name", "").lower():
                    matches.append(
                        {
                            "id": ch.get("id"),
                            "name": ch.get("name"),
                            "is_private": ch.get("is_private", False),
                            "is_member": ch.get("is_member", False),
                            "num_members": ch.get("num_members"),
                        }
                    )
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return {"ok": True, "channels": matches}

    return _wrap_call(workspace, call)


def slack_search_users(
    query: str,
    workspace: str | None = None,
) -> dict:
    """Search users by name/display_name/real_name fragment (users.list)."""

    def call(c: WebClient, _k: str):
        matches: list[dict] = []
        cursor: str | None = None
        q = query.lower().strip()
        for _ in range(20):
            resp = c.users_list(limit=200, cursor=cursor)
            for m in resp.get("members", []):
                if m.get("deleted") or m.get("is_bot"):
                    continue
                profile = m.get("profile") or {}
                hay = " ".join(
                    [
                        m.get("name", ""),
                        m.get("real_name", ""),
                        profile.get("display_name", ""),
                        profile.get("real_name", ""),
                        profile.get("email", ""),
                    ]
                ).lower()
                if q in hay:
                    matches.append(
                        {
                            "id": m.get("id"),
                            "name": m.get("name"),
                            "real_name": m.get("real_name"),
                            "display_name": profile.get("display_name"),
                            "email": profile.get("email"),
                        }
                    )
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return {"ok": True, "users": matches}

    return _wrap_call(workspace, call)


def slack_list_channel_members(
    channel_id: str,
    workspace: str | None = None,
) -> dict:
    """List members of a channel (conversations.members, paginated)."""

    def call(c: WebClient, _k: str):
        members: list[str] = []
        cursor: str | None = None
        for _ in range(50):
            resp = c.conversations_members(
                channel=channel_id, limit=200, cursor=cursor
            )
            members.extend(resp.get("members", []))
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return {"ok": True, "members": members}

    return _wrap_call(workspace, call)


def create_server(**options) -> FastMCP:
    """Expose the same tools over local stdio or authenticated remote HTTP."""
    instance = FastMCP("multi-slack-mcp", **options)
    for function in (
        slack_list_workspaces, slack_search_public_and_private,
        slack_read_channel, slack_read_thread, slack_read_user_profile,
        slack_send_message, slack_search_channels, slack_search_users,
        slack_list_channel_members,
    ):
        read_only = function is not slack_send_message
        instance.add_tool(function, annotations=ToolAnnotations(
            readOnlyHint=read_only, destructiveHint=False,
            idempotentHint=read_only, openWorldHint=True,
        ))
    return instance


mcp = create_server()


# ---------- Entrypoint -----------------------------------------------------

if __name__ == "__main__":
    log.info("Starting multi-slack-mcp (stdio); config=%s", CONFIG_PATH)
    mcp.run()
