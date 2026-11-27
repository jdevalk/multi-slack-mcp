#!/usr/bin/env bash
# install.sh — installs multi-slack-mcp into ~/Library/Application Support.
# Idempotent: re-running upgrades code + deps, never overwrites config.json.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/Library/Application Support/multi-slack-mcp"
VENV_DIR="$DEST_DIR/.venv"

# --- pick a Python 3.10+ interpreter ---------------------------------------
# mcp SDK requires Python 3.10+. macOS system python3 is often 3.9 → fails.
# Honor $PYTHON if set, else try newest-first: 3.13 → 3.12 → 3.11 → 3.10,
# falling back to plain python3 only if it is itself >= 3.10.
pick_python() {
  if [ -n "${PYTHON:-}" ]; then
    echo "$PYTHON"; return 0
  fi
  for cand in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"; return 0
    fi
  done
  if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
      echo "python3"; return 0
    fi
  fi
  return 1
}

if ! PYBIN="$(pick_python)"; then
  cat >&2 <<'ERR'
ERROR: no Python 3.10+ found.

The mcp SDK requires Python 3.10 or newer. macOS ships 3.9 by default.

Install a newer Python with Homebrew:
    brew install python@3.12

Then re-run this installer. (Or set PYTHON to a 3.10+ interpreter, e.g.
    PYTHON=/opt/homebrew/bin/python3.12 bash install.sh)
ERR
  exit 1
fi

PYVER="$("$PYBIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
echo "==> Using $PYBIN (Python $PYVER)"

echo "==> Installing multi-slack-mcp into: $DEST_DIR"
mkdir -p "$DEST_DIR"

# 1. Copy source files (never overwrites config.json).
for f in server.py admin.py requirements.txt config.example.json scopes.txt README.md; do
  if [ -f "$SRC_DIR/$f" ]; then
    cp "$SRC_DIR/$f" "$DEST_DIR/$f"
    echo "    copied $f"
  fi
done

# 2. Create config.json from example if missing (idempotent).
if [ ! -f "$DEST_DIR/config.json" ]; then
  cp "$DEST_DIR/config.example.json" "$DEST_DIR/config.json"
  echo "    seeded config.json from config.example.json"
  echo "    >> Add credentials using either connection option below; creating a Slack app is optional."
else
  echo "    config.json already exists — left untouched"
fi

# 3. Create venv and install requirements.
# Rebuild the venv if it doesn't exist or was created with an old Python.
NEEDS_VENV=1
if [ -x "$VENV_DIR/bin/python" ]; then
  if "$VENV_DIR/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    NEEDS_VENV=0
  else
    echo "==> Existing venv uses an old Python; rebuilding."
    rm -rf "$VENV_DIR"
  fi
fi
if [ "$NEEDS_VENV" = "1" ]; then
  echo "==> Creating venv with $PYBIN"
  "$PYBIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install -r "$DEST_DIR/requirements.txt"

# 4. Quick syntax check.
"$VENV_DIR/bin/python" -m py_compile "$DEST_DIR/server.py" "$DEST_DIR/admin.py"
echo "==> server.py + admin.py compile cleanly"

cat <<EOF

==============================================================
  multi-slack-mcp installed.

  Next steps:
    1. Launch the local web admin:
       "$VENV_DIR/bin/python" "$DEST_DIR/admin.py"
       Then open http://127.0.0.1:8765/ in your browser.

    2. Choose a connection option for each workspace.
       Creating a Slack app is optional; you can mix both methods.

       Option A: Use your Slack browser session (no app needed).
         Open your workspace in Chrome or Edge and open Developer Tools.
         Under Network > Fetch/XHR, reload Slack and select a request to
         slack.com/api/. Copy the xoxc- token from its Payload into Token.
         Under Application > Storage > Cookies, select the Slack site
         and copy the cookie named d into Browser cookie. Copy only its
         value (usually xoxd-...), without d= or quotes. Keep any % sequences.
         Use the token and cookie from the same signed-in browser session.

       Option B: Create and install a Slack app.
         Create an app for the workspace at https://api.slack.com/apps.
         Under OAuth & Permissions > User Token Scopes, add the scopes in:
           $DEST_DIR/scopes.txt
         Install the app, then copy its User OAuth Token (xoxp-...) into
         Token. Leave Browser cookie blank for this method.
         If replacing browser credentials, enter clear to remove the old cookie.

    3. Save the workspace, then click Test to check the connection.
       Repeat for each workspace. Saved credentials apply on the next MCP
       tool call. When upgrading from a version without automatic reload,
       restart the MCP connection once to load the updated server code.

  You can also edit $DEST_DIR/config.json directly:
    Browser session: set user_token to xoxc-... and web_client_cookie to
    the d cookie value. Installed app: set user_token to the xoxp-... token.
    Full setup instructions and JSON examples: $DEST_DIR/README.md

  Register with Claude Desktop:
    Open Claude Desktop > Settings > Developer > Edit Config and add the
    "multi-slack" entry below to "mcpServers". Then fully quit & relaunch
    Claude Desktop.

    Config file: ~/Library/Application Support/Claude/claude_desktop_config.json

    {
      "mcpServers": {
        "multi-slack": {
          "command": "$VENV_DIR/bin/python",
          "args": ["$DEST_DIR/server.py"]
        }
      }
    }

  Register with Claude Code (CLI), user scope:
    claude mcp add multi-slack --scope user -- "$VENV_DIR/bin/python" "$DEST_DIR/server.py"

  Logs:  ~/Library/Logs/multi-slack-mcp.log
==============================================================
EOF
