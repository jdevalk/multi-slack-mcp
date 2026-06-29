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
  echo "    >> EDIT $DEST_DIR/config.json and paste your xoxp- User OAuth tokens."
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
    1. Create one Slack app per workspace at https://api.slack.com/apps
       (Add scopes under User Token Scopes — see $DEST_DIR/scopes.txt.)
    2. Install each app into its workspace and copy the User OAuth Token
       (xoxp-...) — NOT the Bot User OAuth Token below it.
    3. Edit $DEST_DIR/config.json — fill in xoxp-... user_token values.

  Easy way to add/edit workspaces — launch the local admin UI:
    "$VENV_DIR/bin/python" "$DEST_DIR/admin.py"
    then open http://127.0.0.1:8765/ in your browser.

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
