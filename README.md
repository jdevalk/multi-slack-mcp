# multi-slack-mcp

A local MCP server that exposes one Slack toolset across **N Slack
workspaces**. Each tool takes an optional `workspace` argument; if omitted,
the `default_workspace` from `config.json` is used (drop-in compatible with
the existing single-workspace morning-brief flow).

- Language: Python **3.10+** required (the `mcp` SDK won't install on
  macOS system Python 3.9). Easiest fix: `brew install python@3.12`.
- Transport: stdio (Claude Desktop / Claude Code spawn the process per session)
- Install location: `~/Library/Application Support/multi-slack-mcp/`
- Logs: `~/Library/Logs/multi-slack-mcp.log`

## Install

From this folder:

```bash
bash install.sh
```

The installer copies files to `~/Library/Application Support/multi-slack-mcp/`,
creates a `.venv/`, pip-installs requirements, seeds `config.json` from
`config.example.json` (only if it doesn't exist), and prints the registration
snippet at the end. Re-run it any time to upgrade code or deps — it never
overwrites `config.json`.

## 1. Create one Slack app per workspace

This MCP uses **User OAuth Tokens** (`xoxp-…`) — the app calls Slack *as you*
and can see every channel/DM you can see. (Bot tokens only see channels the
bot is explicitly invited to.) Trade-off: `chat.postMessage` posts **as you**,
not as a bot. Fine for self-DMs; be deliberate elsewhere.

For each Slack workspace you want to expose:

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
   Name it (e.g. `Multi-Slack`) and pick the target workspace.
2. **OAuth & Permissions** → scroll to **Scopes** → under **User Token
   Scopes** (NOT Bot Token Scopes) add all of:
   `channels:history`, `channels:read`, `groups:history`, `groups:read`,
   `im:history`, `im:read`, `mpim:history`, `mpim:read`, `chat:write`,
   `users:read`, `search:read`.
   The same list is in `scopes.txt` as a comma-separated string for
   paste-in convenience.
3. **Install to Workspace** and approve.
4. Copy the **User OAuth Token** (`xoxp-…`) — it's the *top* token on the
   OAuth page. Do **not** grab the Bot User OAuth Token (`xoxb-…`) that
   Slack shows below it.

Repeat for each workspace.

## 2. Fill in `config.json`

### Option A — local web admin (easier)

```bash
~/Library/Application\ Support/multi-slack-mcp/.venv/bin/python \
  ~/Library/Application\ Support/multi-slack-mcp/admin.py
```

Opens a localhost-only UI at <http://127.0.0.1:8765/>. Add workspaces with a
form, paste the `xoxp-…` token, click **Test** to verify it works (calls
`auth.test` and shows the team + user it resolved to). Edit / delete / set
default with one click. Tokens are masked in the table.

Writes `config.json` atomically and keeps the previous version at
`config.json.bak`. Refuses to bind to anything other than 127.0.0.1 unless
explicitly overridden — tokens are stored plaintext, never expose this UI to
the network.

The MCP server reloads `config.json` before each tool call. Saved tokens,
cookies, workspace changes and defaults take effect on the next call without
restarting the client. Requests already in progress finish with their original
credentials. If the file is temporarily unreadable or invalid, the server keeps
the last valid configuration and retries on the next call.

When upgrading from a version without automatic reload, restart the MCP
connection once to load the updated server code.

### Option B — edit JSON directly

Edit `~/Library/Application Support/multi-slack-mcp/config.json`:

```json
{
  "workspaces": {
    "example-team": { "user_token": "xoxp-…", "team_name": "Example Team" },
    "example-company": { "user_token": "xoxp-…", "team_name": "Example Company" }
  },
  "default_workspace": "example-team"
}
```

Legacy `bot_token` keys are still read (with a deprecation warning) so any
config you partially filled in earlier keeps working — just rename when
convenient.

Workspace keys are kebab-case identifiers used as the `workspace` argument
when calling tools. Add as many entries as you like — the server is purely
config-driven.

## 3. Register with Claude Desktop

Open **Claude Desktop → Settings → Developer → Edit Config**, or edit
`~/Library/Application Support/Claude/claude_desktop_config.json` directly,
and add the `multi-slack` entry to `mcpServers`:

```json
{
  "mcpServers": {
    "multi-slack": {
      "command": "/Users/YOUR_USERNAME/Library/Application Support/multi-slack-mcp/.venv/bin/python",
      "args": ["/Users/YOUR_USERNAME/Library/Application Support/multi-slack-mcp/server.py"]
    }
  }
}
```

Fully quit Claude Desktop (⌘Q) and relaunch — it only reads the config at
startup. The MCP indicator in the bottom-right of the input box should show
the new `multi-slack` tools.

### Alternative: Claude Code (CLI)

```bash
claude mcp add multi-slack --scope user -- \
  "$HOME/Library/Application Support/multi-slack-mcp/.venv/bin/python" \
  "$HOME/Library/Application Support/multi-slack-mcp/server.py"
```

## Tools

| Tool                              | Slack API                  | Notes |
|-----------------------------------|----------------------------|-------|
| `slack_list_workspaces`           | (none)                     | Returns configured workspace keys + team names |
| `slack_search_public_and_private` | `search.messages`          | `query`, `count?` |
| `slack_read_channel`              | `conversations.history`    | `channel_id`, `limit?`, `oldest?`, `latest?`, `cursor?` |
| `slack_read_thread`               | `conversations.replies`    | `channel_id`, `thread_ts` |
| `slack_read_user_profile`         | `users.info`               | `user_id` |
| `slack_send_message`              | `chat.postMessage`         | `channel_id`, `text`, `thread_ts?` |
| `slack_search_channels`           | `conversations.list`       | Optional `query` name filter; paginated |
| `slack_search_users`              | `users.list`               | Fragment match on name/display_name/real_name/email |
| `slack_list_channel_members`      | `conversations.members`    | Paginated |

Every tool except `slack_list_workspaces` accepts an optional `workspace`
argument. Omit it to use `default_workspace`.

Errors are returned as structured payloads, e.g.
`{"error": "channel_not_found", "message": "...", "_workspace": "example-team"}`.

## Out of scope (v1, future work)

Canvases, reactions, scheduled messages, drafts, files, custom emojis.

## Troubleshooting

- **Indicator missing in Claude Desktop**: tail `~/Library/Logs/Claude/mcp*.log`
  and `~/Library/Logs/multi-slack-mcp.log`. Most common cause: typo in
  `claude_desktop_config.json` — validate with `python3 -m json.tool`.
- **`invalid_auth`**: user token is wrong or app wasn't installed to that
  workspace. Re-install the app and copy the `xoxp-…` (User OAuth Token,
  not Bot User OAuth Token) again.
- **`not_in_channel`**: posting to a channel you haven't joined. Join it in
  Slack first, or pick a different channel.
- **`missing_scope`**: re-check the scope list above (User Token Scopes,
  not Bot Token Scopes) and reinstall the app.
