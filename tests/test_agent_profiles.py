"""Tests for lintgate.agent_profiles transactional config writers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lintgate.agent_profiles import (
    _atomic_write_json,
    get_profile,
    write_antigravity_config,
    write_claude_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_atomic_write_json_writes_and_creates_backup(tmp_path: Path) -> None:
    config_path = tmp_path / "claude_desktop_config.json"
    config_path.write_text('{"legacy": true}\n', encoding="utf-8")

    payload = {"mcpServers": {"lintgate": {"command": "lintgate-mcp", "args": []}}}
    _atomic_write_json(config_path, payload)

    backup = tmp_path / "claude_desktop_config.json.bak"
    assert backup.exists()
    assert json.loads(config_path.read_text(encoding="utf-8")) == payload


def test_write_claude_config_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "claude" / "claude_desktop_config.json"

    changed = write_claude_config(config_path, "lintgate-mcp")
    unchanged = write_claude_config(config_path, "lintgate-mcp")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert changed is True
    assert unchanged is False
    assert data["mcpServers"]["lintgate"] == {"command": "lintgate-mcp", "args": []}


def test_write_antigravity_config_recovers_from_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "antigravity" / "mcp.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{invalid", encoding="utf-8")

    changed = write_antigravity_config(config_path, "lintgate-mcp")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert changed is True
    assert data["mcpServers"]["lintgate"]["command"] == "lintgate-mcp"


def test_get_profile_case_insensitive_and_missing() -> None:
    assert get_profile("CLAUDE") is not None
    assert get_profile("aNtIgRaViTy") is not None
    assert get_profile("missing-agent") is None
