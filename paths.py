"""Shared installation, configuration and log paths for macOS and Linux."""

import os
from pathlib import Path
import sys


def _xdg_path(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable, "")
    # XDG directory overrides must be absolute; ignore empty/relative values.
    return Path(value) if value and Path(value).is_absolute() else fallback


def app_paths() -> dict[str, Path]:
    home = Path.home()
    if sys.platform == "darwin":
        install = home / "Library/Application Support/multi-slack-mcp"
        config = install / "config.json"
        log = home / "Library/Logs/multi-slack-mcp.log"
    elif sys.platform.startswith("linux"):
        install = _xdg_path("XDG_DATA_HOME", home / ".local/share") / "multi-slack-mcp"
        config = _xdg_path("XDG_CONFIG_HOME", home / ".config") / "multi-slack-mcp/config.json"
        log = _xdg_path("XDG_STATE_HOME", home / ".local/state") / "multi-slack-mcp/multi-slack-mcp.log"
    else:
        raise RuntimeError("Supported platforms: macOS and Linux")

    override = os.environ.get("MULTI_SLACK_MCP_CONFIG")
    if override:
        config = Path(override).expanduser().resolve()
    return {"install": install, "config": config, "log": log}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("install", "config", "log"))
    print(app_paths()[parser.parse_args().kind])
