"""Focused coverage tests for lintgate.mcp_schema."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

import lintgate.mcp_schema as mcp_schema

if TYPE_CHECKING:
    from pathlib import Path


def test_provider_schema_error_records_fields() -> None:
    err = mcp_schema.ProviderSchemaError("bad enum", "demo_tool", "inputSchema.properties.tier")
    assert "demo_tool" in str(err)
    assert err.tool_name == "demo_tool"
    assert err.path == "inputSchema.properties.tier"


def test_validate_schema_node_rejects_non_string_enum() -> None:
    with pytest.raises(mcp_schema.ProviderSchemaError):
        mcp_schema.validate_schema_node(
            "demo_tool",
            "inputSchema.properties.tier",
            {"type": "integer", "enum": [0, 1, 2]},
        )


def test_validate_schema_node_rejects_empty_enum_value() -> None:
    with pytest.raises(mcp_schema.ProviderSchemaError):
        mcp_schema.validate_schema_node(
            "demo_tool",
            "inputSchema.properties.mode",
            {"type": "string", "enum": ["strict", ""]},
        )


def test_compile_and_validate_schemas_relaxed_profile_skips_validation() -> None:
    tools = [SimpleNamespace(name="demo_tool", parameters={"type": "integer", "enum": [1]})]
    # Should not raise when profile is relaxed.
    mcp_schema.compile_and_validate_schemas(tools, agent_profile="relaxed")


def test_enforce_mcp_contract_missing_file_noop(tmp_path: Path, monkeypatch) -> None:
    module_file = tmp_path / "pkg" / "mcp_schema.py"
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(mcp_schema, "__file__", str(module_file))

    # No contract file -> no-op.
    mcp_schema.enforce_mcp_contract([])


def test_enforce_mcp_contract_raises_when_safety_tool_missing(tmp_path: Path, monkeypatch) -> None:
    module_file = tmp_path / "pkg" / "mcp_schema.py"
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(mcp_schema, "__file__", str(module_file))

    contract_path = module_file.parent / "mcp_contract.yaml"
    contract_path.write_text("safety_critical_tools:\n  - lint_files\n", encoding="utf-8")

    with pytest.raises(mcp_schema.ProviderSchemaError):
        mcp_schema.enforce_mcp_contract([SimpleNamespace(name="controlplane_run")])
