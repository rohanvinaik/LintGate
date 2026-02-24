"""Agent Profile Registry for LintGate integrations.

Defines the configuration, location, capabilities, and schema rules for each supported MCP agent.
Includes transactional configuration writers to ensure safe, idempotent updates to user configs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class AgentProfile:
    id: str
    display_name: str
    config_path: Path
    schema_strict: bool
    config_writer: Callable[[Path, str], bool]  # Returns True if wrote/changed, False if already configured
    # e.g., command to check if agent is active or installed
    check_command: str | None = None


# --- Transactional Writers ---

def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically with a .bak fallback."""
    if path.exists():
        bak_path = path.with_suffix(".json.bak")
        path.rename(bak_path)

    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)

    # Valid parse test
    with open(tmp_path) as f:
        json.load(f)

    tmp_path.rename(path)


def write_claude_config(config_path: Path, server_cmd: str) -> bool:
    """Write a Claude Desktop config idempotently."""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            with open(config_path) as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = {"mcpServers": {}}
    else:
        data = {"mcpServers": {}}

    mcp_servers = data.setdefault("mcpServers", {})
    existing = mcp_servers.get("lintgate")

    desired = {
        "command": server_cmd,
        "args": []
    }

    if existing and existing == desired:
        return False  # perfectly configured

    mcp_servers["lintgate"] = desired

    _atomic_write_json(config_path, data)
    return True

def write_antigravity_config(config_path: Path, server_cmd: str) -> bool:
    """Write Antigravity MCP config idempotently."""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            with open(config_path) as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = {"mcpServers": {}}
    else:
        data = {"mcpServers": {}}

    mcp_servers = data.setdefault("mcpServers", {})
    existing = mcp_servers.get("lintgate")

    desired = {
        "command": server_cmd,
        "args": []
    }

    if existing and existing == desired:
        return False  # perfectly configured

    mcp_servers["lintgate"] = desired

    _atomic_write_json(config_path, data)
    return True


# --- Registry ---

PROFILES = {
    "claude": AgentProfile(
        id="claude",
        display_name="Claude Desktop",
        config_path=Path(os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")),
        schema_strict=False,
        config_writer=write_claude_config,
    ),
    "antigravity": AgentProfile(
        id="antigravity",
        display_name="Antigravity",
        config_path=Path(os.path.expanduser("~/.gemini/antigravity/mcp.json")),  # Hypothetical config path
        schema_strict=True,
        config_writer=write_antigravity_config,
    )
}

def get_profile(agent_id: str) -> AgentProfile | None:
    return PROFILES.get(agent_id.lower())
