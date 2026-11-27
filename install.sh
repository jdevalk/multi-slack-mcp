#!/usr/bin/env bash
# install.sh — installs multi-slack-mcp on macOS or Linux.
# Idempotent: re-running upgrades code + deps, never overwrites config.json.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- pick a Python 3.10+ interpreter ---------------------------------------
# mcp SDK requires Python 3.10+. macOS system python3 is often 3.9 → fails.
# Honor $PYTHON if set, else try newest-first: 3.13 → 3.12 → 3.11 → 3.10,
# falling back to plain python3 only if it is itself >= 3.10.
pick_python() {
  if [ -n "${PYTHON:-}" ]; then
    if "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      echo "$PYTHON"; return 0
    fi
    return 1
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

macOS (Homebrew):
    brew install python@3.12
Linux: install Python 3.10+ and venv support using your distribution's
package manager. On Debian/Ubuntu, the packages are python3 and python3-venv;
check that your distribution provides Python 3.10 or newer.

Then re-run this installer. (Or set PYTHON to a 3.10+ interpreter, e.g.
    PYTHON=/absolute/path/to/python3 bash install.sh)
ERR
  exit 1
fi

PYVER="$("$PYBIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
echo "==> Using $PYBIN (Python $PYVER)"

DEST_DIR="$("$PYBIN" "$SRC_DIR/paths.py" install)"
CONFIG_PATH="$("$PYBIN" "$SRC_DIR/paths.py" config)"
LOG_PATH="$("$PYBIN" "$SRC_DIR/paths.py" log)"
VENV_DIR="$DEST_DIR/.venv"

echo "==> Installing multi-slack-mcp into: $DEST_DIR"
mkdir -p "$DEST_DIR"

# 1. Copy source files (never overwrites config.json).
for f in paths.py server.py admin.py requirements.txt config.example.json README.md; do
  if [ -f "$SRC_DIR/$f" ]; then
    cp "$SRC_DIR/$f" "$DEST_DIR/$f"
    echo "    copied $f"
  fi
done

# 2. Create config.json from example if missing (idempotent).
mkdir -p "$(dirname "$CONFIG_PATH")"
if [ ! -f "$CONFIG_PATH" ]; then
  LEGACY_CONFIG="$HOME/Library/Application Support/multi-slack-mcp/config.json"
  if [ "$(uname -s)" = "Linux" ] && [ -f "$LEGACY_CONFIG" ]; then
    cp "$LEGACY_CONFIG" "$CONFIG_PATH"
    echo "    copied existing config from the previous installation"
  else
    cp "$DEST_DIR/config.example.json" "$CONFIG_PATH"
    echo "    seeded config.json from config.example.json"
  fi
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
  if ! "$PYBIN" -m venv "$VENV_DIR"; then
    echo "ERROR: Could not create the virtual environment. Install Python venv support and retry." >&2
    exit 1
  fi
fi
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install -r "$DEST_DIR/requirements.txt"

# 4. Quick syntax check.
"$VENV_DIR/bin/python" -m py_compile "$DEST_DIR/paths.py" "$DEST_DIR/server.py" "$DEST_DIR/admin.py"
echo "==> server.py + admin.py compile cleanly"

# Remove files from the retired remote-access feature when upgrading.
rm -f "$DEST_DIR/remote.py" "$DEST_DIR/remote_auth.py" \
  "$DEST_DIR/__pycache__"/remote.cpython-*.pyc \
  "$DEST_DIR/__pycache__"/remote_auth.cpython-*.pyc \
  "$(dirname "$CONFIG_PATH")/remote-password"

cat <<EOF

==============================================================
  multi-slack-mcp installed.

  Next steps:
    1. Launch the local web admin:
       MULTI_SLACK_MCP_CONFIG="$CONFIG_PATH" "$VENV_DIR/bin/python" "$DEST_DIR/admin.py"
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
         Under OAuth & Permissions > User Token Scopes, add the scopes listed
         in README.md under "Option 2: Create a Slack app":
           $DEST_DIR/README.md
         Install the app, then copy its User OAuth Token (xoxp-...) into
         Token. Leave Browser cookie blank for this method.
         If replacing browser credentials, enter clear to remove the old cookie.

    3. Save the workspace, then click Test to check the connection.
       Repeat for each workspace. Saved credentials apply on the next MCP
       tool call. When upgrading from a version without automatic reload,
       restart the MCP connection once to load the updated server code.

  You can also edit $CONFIG_PATH directly:
    Browser session: set user_token to xoxc-... and web_client_cookie to
    the d cookie value. Installed app: set user_token to the xoxp-... token.
    Full setup instructions and JSON examples: $DEST_DIR/README.md

  Register with Claude Code (macOS or Linux), user scope:
    claude mcp add multi-slack --scope user --env "MULTI_SLACK_MCP_CONFIG=$CONFIG_PATH" -- "$VENV_DIR/bin/python" "$DEST_DIR/server.py"

  Config: $CONFIG_PATH
  Logs:   $LOG_PATH
==============================================================
EOF

if [ "$(uname -s)" = "Darwin" ]; then
  cat <<EOF

  Claude Desktop on macOS:
    Open Settings > Developer > Edit Config and add the entry below.
    Config file: $HOME/Library/Application Support/Claude/claude_desktop_config.json
    Fully quit and relaunch Claude Desktop after registering the server.
EOF
fi

echo "  MCP configuration for clients that support mcpServers:"
"$PYBIN" - "$DEST_DIR" "$CONFIG_PATH" <<'PY'
import json
from pathlib import Path
import sys

install = Path(sys.argv[1])
print(json.dumps({"mcpServers": {"multi-slack": {
    "command": str(install / ".venv/bin/python"),
    "args": [str(install / "server.py")],
    "env": {"MULTI_SLACK_MCP_CONFIG": sys.argv[2]},
}}}, indent=2))
PY
