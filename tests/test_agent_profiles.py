"""Tests for lintgate/agent_profiles.py — agent profile registry and config writers."""

from __future__ import annotations

import json
from pathlib import Path

from lintgate.agent_profiles import (
    PROFILES,
    AgentProfile,
    _atomic_write_json,
    _load_existing_config,
    _write_mcp_server_config,
    get_profile,
    write_antigravity_config,
    write_claude_config,
)


# --- get_profile ---


def test_get_profile_known_agent():
    profile = get_profile("claude")
    assert profile is not None
    assert profile.id == "claude"
    assert profile.display_name == "Claude Desktop"
    assert profile.schema_strict is False


def test_get_profile_case_insensitive():
    profile = get_profile("CLAUDE")
    assert profile is not None
    assert profile.id == "claude"


def test_get_profile_unknown_returns_none():
    result = get_profile("nonexistent_agent")
    assert result is None
    # Verify known profile returns AgentProfile (not None)
    known = get_profile("claude")
    assert isinstance(known, AgentProfile)


# --- _load_existing_config ---


def test_load_existing_config_missing_file(tmp_path: Path):
    result = _load_existing_config(tmp_path / "missing.json")
    assert result == {"mcpServers": {}}


def test_load_existing_config_valid_json(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    result = _load_existing_config(cfg)
    assert result == {"mcpServers": {"other": {"command": "x"}}}


def test_load_existing_config_invalid_json(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text("not valid json{{{")
    result = _load_existing_config(cfg)
    # Invalid JSON falls through to empty dict, then isinstance check passes
    # but the decoded empty dict {} is returned as-is
    assert result == {}


# --- _atomic_write_json ---


def test_atomic_write_json_creates_file(tmp_path: Path):
    target = tmp_path / "output.json"
    _atomic_write_json(target, {"key": "value"})
    assert target.exists()
    data = json.loads(target.read_text())
    assert data == {"key": "value"}


def test_atomic_write_json_creates_backup(tmp_path: Path):
    target = tmp_path / "output.json"
    target.write_text(json.dumps({"old": True}))
    _atomic_write_json(target, {"new": True})
    bak = tmp_path / "output.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text()) == {"old": True}
    assert json.loads(target.read_text()) == {"new": True}


# --- _write_mcp_server_config ---


def test_write_mcp_server_config_creates_new(tmp_path: Path):
    cfg = tmp_path / "sub" / "config.json"
    result = _write_mcp_server_config(cfg, "uv run lintgate")
    assert result is True
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["lintgate"] == {
        "command": "uv run lintgate",
        "args": [],
    }


def test_write_mcp_server_config_idempotent(tmp_path: Path):
    cfg = tmp_path / "config.json"
    _write_mcp_server_config(cfg, "uv run lintgate")
    result = _write_mcp_server_config(cfg, "uv run lintgate")
    assert result is False


def test_write_mcp_server_config_updates_existing(tmp_path: Path):
    cfg = tmp_path / "config.json"
    _write_mcp_server_config(cfg, "old_command")
    result = _write_mcp_server_config(cfg, "new_command")
    assert result is True
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["lintgate"]["command"] == "new_command"


# --- write_claude_config / write_antigravity_config ---


def test_write_claude_config_delegates(tmp_path: Path):
    cfg = tmp_path / "claude.json"
    result = write_claude_config(cfg, "my_cmd")
    assert result is True
    data = json.loads(cfg.read_text())
    assert "lintgate" in data["mcpServers"]


def test_write_antigravity_config_delegates(tmp_path: Path):
    cfg = tmp_path / "antigravity.json"
    result = write_antigravity_config(cfg, "ag_cmd")
    assert result is True
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["lintgate"]["command"] == "ag_cmd"


# --- PROFILES registry ---


def test_profiles_contains_claude_and_antigravity():
    assert "claude" in PROFILES
    assert "antigravity" in PROFILES
    assert len(PROFILES) == 2


def test_profiles_antigravity_is_schema_strict():
    profile = PROFILES["antigravity"]
    assert profile.schema_strict is True
    assert profile.display_name == "Antigravity"
