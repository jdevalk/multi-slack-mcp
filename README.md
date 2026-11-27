# Multi-Slack MCP

Connect your MCP client to multiple Slack workspaces using **your existing
Slack browser session, with no Slack app to create or install**. Copy your
browser's `xoxc-` token and `d` cookie into the local web admin to connect.

You can also create a Slack app and use its User OAuth Token (`xoxp-`).
Choose either method for each workspace, and mix both in the same server.

| Connection method | What you need | Create a Slack app? |
|---|---|---|
| [Slack browser session](#option-1-use-your-slack-browser-session-no-app-needed) | An `xoxc-` token and the `d` cookie from the same signed-in browser session | No |
| [Installed Slack app](#option-2-create-a-slack-app) | A User OAuth Token (`xoxp-`) from an app installed in the workspace | Yes |

Search messages, read channels and threads, look up people, and send messages
across your workspaces through one set of MCP tools. Each tool accepts an
optional `workspace` argument; omit it to use your configured default.

- Platforms: **macOS and Linux**.
- Requirements: **Python 3.10+**, with `venv` support, and Bash.
- Transport: **stdio**; your MCP client launches the server process.

## Install

### macOS

Install Python 3.10+ if needed. With Homebrew:

```bash
brew install python@3.12
```

Then run the installer from this folder:

```bash
bash install.sh
```

### Linux

Install Python 3.10+ and venv support with your distribution's package
manager. On Debian/Ubuntu:

```bash
sudo apt install python3 python3-venv
```

Check that your Python version is 3.10 or newer, then run the installer
from this folder:

```bash
python3 --version
bash install.sh
```

### Installation, config and logs

The installer copies the server files, creates a `.venv/`, installs
requirements, seeds `config.json` from
`config.example.json` (only if it doesn't exist), and prints the registration
snippet at the end. Re-run it any time to upgrade code or deps — it never
overwrites `config.json`.

| Location | macOS | Linux defaults |
|---|---|---|
| Server and virtual environment | `~/Library/Application Support/multi-slack-mcp/` | `~/.local/share/multi-slack-mcp/` |
| Workspace config | `~/Library/Application Support/multi-slack-mcp/config.json` | `~/.config/multi-slack-mcp/config.json` |
| Server log | `~/Library/Logs/multi-slack-mcp.log` | `~/.local/state/multi-slack-mcp/multi-slack-mcp.log` |

On Linux, the installer and server respect `XDG_DATA_HOME`, `XDG_CONFIG_HOME`
and `XDG_STATE_HOME`. Set them to absolute paths; empty or relative values
use the defaults above. Keep these environment settings consistent when
installing and launching the server or admin.

On either platform, `MULTI_SLACK_MCP_CONFIG` overrides the workspace config
path. The installer prints commands and MCP configuration with the resolved
paths, including this override.

If you previously installed on Linux with the old macOS-style paths, the
installer copies that config to the new location only when no config exists
there. Register the newly printed server command in your MCP client; the
old installation stays in place.

## 1. Choose how to connect each workspace

Both methods make requests as your Slack user. Messages sent through
`slack_send_message` appear **as you**. App tokens are also limited by the
user scopes you grant when installing the app.

### Option 1: Use your Slack browser session (no app needed)

Use the Slack workspace you already have open in your browser. You need two
values from the same browser session: an **`xoxc-` token** and the **`d`
cookie**, whose value usually starts with `xoxd-`.

#### Find the `xoxc-` token

1. Open the workspace in Chrome or Edge and sign in.
2. Open Developer Tools: **Option + Command + I** on Mac, or
   **Ctrl + Shift + I** on Windows/Linux.
3. Select **Network**, then **Fetch/XHR**, and reload Slack.
4. Select a request to `slack.com/api/` and open **Payload**. Find a `token`
   field starting with `xoxc-`. If it is missing, try another API request
   or open a channel to create more requests.
5. Copy the complete value into the web admin's **Token** field.

#### Find the browser cookie

1. In the same Slack tab's Developer Tools, open **Application**. It may be
   under the **»** menu.
2. Under **Storage > Cookies**, select the Slack site and find the cookie
   named exactly `d`.
3. Copy its complete **Value** into **Browser cookie** in the web admin.
   Copy only the value, without `d=` or surrounding quotes. Leave any `%`
   sequences unchanged.

Save both values, then click **Test**. Repeat for each workspace you want
to connect. The edit screen also includes these instructions.

### Option 2: Create a Slack app

For each Slack workspace you want to expose:

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
   Name it (e.g. `Multi-Slack`) and pick the target workspace.
2. **OAuth & Permissions** → scroll to **Scopes** → under **User Token
   Scopes** (NOT Bot Token Scopes) add all of:
   `channels:history`, `channels:read`, `groups:history`, `groups:read`,
   `im:history`, `im:read`, `mpim:history`, `mpim:read`, `chat:write`,
   `users:read`, `search:read`.
3. **Install to Workspace** and approve.
4. Copy the **User OAuth Token** (`xoxp-…`) from **OAuth & Permissions**
   into the web admin's **Token** field. The Bot User OAuth Token starts
   with `xoxb-`.

Leave **Browser cookie** blank for this method. If replacing browser-session
credentials, type `clear` in that field to remove the old cookie.

## 2. Fill in `config.json`

### Use the local web admin

Copy the launch command printed by the installer, or use the command for
your platform below.

**macOS:**

```bash
"$HOME/Library/Application Support/multi-slack-mcp/.venv/bin/python" \
  "$HOME/Library/Application Support/multi-slack-mcp/admin.py"
```

**Linux:**

```bash
"${XDG_DATA_HOME:-$HOME/.local/share}/multi-slack-mcp/.venv/bin/python" \
  "${XDG_DATA_HOME:-$HOME/.local/share}/multi-slack-mcp/admin.py"
```

Opens a localhost-only UI at <http://127.0.0.1:8765/>. Add a workspace and
enter the credentials for your chosen method:

- **Browser session:** paste the `xoxc-…` token into **Token** and the
  `d` cookie value into **Browser cookie**.
- **Installed app:** paste the `xoxp-…` User OAuth Token into **Token**
  and leave **Browser cookie** blank.

Save, then click **Test** to verify the connection. The test calls `auth.test`
and shows the workspace and user. You can also edit or delete workspaces
and select a default. The table masks tokens and cookies.

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

### Or edit JSON directly

Edit the workspace config path printed by the installer (see the platform
paths above).
This example connects one workspace through a browser session and another
through an installed app:

```json
{
  "workspaces": {
    "example-company": {
      "user_token": "xoxc-REPLACE_WITH_BROWSER_TOKEN",
      "web_client_cookie": "xoxd-REPLACE_WITH_COOKIE_VALUE",
      "team_name": "Example Company"
    },
    "example-team": {
      "user_token": "xoxp-REPLACE_WITH_USER_OAUTH_TOKEN",
      "team_name": "Example Team"
    }
  },
  "default_workspace": "example-company"
}
```

Legacy `bot_token` keys are still read (with a deprecation warning) so any
config you partially filled in earlier keeps working — just rename when
convenient.

Workspace keys are kebab-case identifiers used as the `workspace` argument
when calling tools. Add as many entries as you like — the server is purely
config-driven.

## 3. Register with your MCP client

The installer prints registration commands containing your actual paths.
Use these if you chose custom XDG directories or a config override.

### Claude Code (macOS or Linux)

**macOS:**

```bash
claude mcp add multi-slack --scope user -- \
  "$HOME/Library/Application Support/multi-slack-mcp/.venv/bin/python" \
  "$HOME/Library/Application Support/multi-slack-mcp/server.py"
```

**Linux:**

```bash
claude mcp add multi-slack --scope user -- \
  "${XDG_DATA_HOME:-$HOME/.local/share}/multi-slack-mcp/.venv/bin/python" \
  "${XDG_DATA_HOME:-$HOME/.local/share}/multi-slack-mcp/server.py"
```

### Claude Desktop (macOS)

Open **Claude Desktop → Settings → Developer → Edit Config**, or edit
`~/Library/Application Support/Claude/claude_desktop_config.json` directly,
and add the `multi-slack` entry to `mcpServers`.

Copy the configuration printed by `install.sh`; it contains the absolute
paths for your account. In the example below, replace `YOUR_USERNAME` in
both paths with your macOS account username:

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

### ChatGPT desktop / Codex (macOS or Linux)

Use ChatGPT desktop's local MCP settings on the computer where you installed
Multi-Slack MCP. The desktop app and Codex share MCP configuration on the
same host. See [OpenAI's MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp).

1. Open **Settings > MCP servers > Add server**.
2. Name the server `multi-slack` and select **STDIO**.
3. Set **Command** to the installed `.venv/bin/python` and add the installed
   `server.py` path as an argument. Copy the absolute paths from the installer's
   printed configuration.
4. If the printed configuration includes `env`, add those environment
   variables too, including `MULTI_SLACK_MCP_CONFIG`.
5. Save and select **Restart**. In the chat, use `/mcp` to check the connection,
   then ask to list the configured Slack workspaces.

If you have the Codex CLI, you can register the same server from the terminal.

**macOS:**

```bash
codex mcp add multi-slack -- \
  "$HOME/Library/Application Support/multi-slack-mcp/.venv/bin/python" \
  "$HOME/Library/Application Support/multi-slack-mcp/server.py"
```

**Linux:**

```bash
codex mcp add multi-slack -- \
  "${XDG_DATA_HOME:-$HOME/.local/share}/multi-slack-mcp/.venv/bin/python" \
  "${XDG_DATA_HOME:-$HOME/.local/share}/multi-slack-mcp/server.py"
```

For a custom config path, add `--env "MULTI_SLACK_MCP_CONFIG=/absolute/path/to/config.json"`
before `--`. Run `codex mcp list` to confirm registration.

#### ChatGPT on the web

ChatGPT's web app uses remote MCP connections; it cannot launch this local
stdio server directly. This project does not currently provide a remote MCP
endpoint. Connecting it to ChatGPT web would require an additional remote
transport and connection setup. See [OpenAI's connection guidance](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt-beta).

The web admin at `http://127.0.0.1:8765/` manages Slack credentials; it is
**not an MCP endpoint** to enter in ChatGPT's app settings.

### Meta Muse

There is currently **no verified direct connection procedure for this project
in the Meta Muse app**. Multi-Slack MCP runs as a local stdio process; Meta's
[Muse connector guide](https://www.meta.com/en-gb/help/superintelligence/1687253048996149/)
describes listed connectors and custom connectors, but does not document
registering a local stdio server from your computer.

To connect Slack through Muse's own connector, open **Settings > Connectors**,
select **Connect** beside Slack if it is available, and follow the sign-in
steps. This uses Meta's Slack integration, not this project's multiple-workspace
or browser-session authentication features.

Meta's separate **Muse Code** product does support local MCP servers; its
[MCP documentation](https://meta-models.github.io/muse-code-sdk/1.4/guides/extend/mcp-servers/)
describes that setup. Muse Code configuration does not connect the Muse app.

### Other MCP clients (macOS or Linux)

Use a client that supports local stdio MCP servers. Set its command to the
installed `.venv/bin/python` and pass the installed `server.py` as the
argument. If the client uses the `mcpServers` JSON format, copy the block
printed by the installer. For other formats, use the same command, argument
and environment values in the client's MCP settings.

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

## Troubleshooting

- **Server fails to start on either platform**: check the server log in the
  platform table above, or the resolved log path printed by the installer.
- **Linux virtual environment creation fails**: install your distribution's
  Python venv package (for example, `python3-venv` on Debian/Ubuntu), then
  rerun the installer.
- **Indicator missing in Claude Desktop on macOS**: check
  `~/Library/Logs/Claude/mcp.log` for MCP connection logs and
  `~/Library/Logs/Claude/mcp-server-multi-slack.log` for this server's output
  (the filename follows the `multi-slack` name in your Claude configuration).
  Check
  `claude_desktop_config.json` for typos with `python3 -m json.tool`.
- **`invalid_auth` with a browser session**: copy a fresh `xoxc-` token and
  `d` cookie from the same signed-in Slack browser session. Save both and
  click **Test**. The next MCP tool call uses the saved values.
- **`invalid_auth` with an installed app**: check that the app is installed
  in the intended workspace and copy its `xoxp-…` User OAuth Token again.
- **`not_in_channel`**: posting to a channel you haven't joined. Join it in
  Slack first, or pick a different channel.
- **`missing_scope` with an installed app**: check the User Token Scopes
  listed above and reinstall the app.
