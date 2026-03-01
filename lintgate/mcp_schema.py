"""Schema validation and compilation engine for LintGate MCP server.

Strict agents like Antigravity reject tool registrations containing
incompatible JSON schemas (e.g. enum entries on integer types or empty strings).
This module provides runtime assertions and schema translation post tool registration.
"""

from __future__ import annotations

from typing import Any


class ProviderSchemaError(Exception):
    """Raised when an MCP tool fails strict schema compatibility validation."""

    def __init__(self, message: str, tool_name: str, path: str):
        super().__init__(f"[{tool_name}] Schema error at {path}: {message}")
        self.tool_name = tool_name
        self.path = path


def validate_schema_node(tool_name: str, path: str, node: Any) -> None:
    """Recursively validate a JSON Schema node for provider compat."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            for i, item in enumerate(node):
                validate_schema_node(tool_name, f"{path}[{i}]", item)
        return

    # Enforce: if "enum" is present, the type MUST be strictly "string"
    if "enum" in node:
        node_type = node.get("type", "")
        if node_type != "string":
            raise ProviderSchemaError(
                "Enums are only allowed on strictly 'string' types.", tool_name, path
            )

        # Enforce: no empty strings inside an enum array
        for i, enum_val in enumerate(node["enum"]):
            if isinstance(enum_val, str) and not enum_val:
                raise ProviderSchemaError(
                    f"Empty string found in enum at index {i}", tool_name, path
                )

    for key, value in node.items():
        validate_schema_node(tool_name, f"{path}.{key}", value)


def compile_and_validate_schemas(
    tools: list[Any], agent_profile: str = "strict"
) -> None:
    """Validate schemas natively derived from fastmcp tool objects before proceeding.

    Args:
        tools: The result of `await mcp.list_tools()` (list of fastmcp.models.Tool)
        agent_profile: The target provider constraint profile.
    """
    if agent_profile != "strict":
        return

    for tool in tools:
        schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None)
        if schema:
            validate_schema_node(tool.name, "inputSchema", schema)


def enforce_mcp_contract(tools: list[Any]) -> None:
    """Verify that all safety_critical_tools are present and correctly loaded."""
    from pathlib import Path

    import yaml

    contract_path = Path(__file__).parent / "mcp_contract.yaml"
    if not contract_path.exists():
        return

    with open(contract_path) as f:
        contract = yaml.safe_load(f)

    safety_critical = contract.get("safety_critical_tools", [])
    tool_names = {t.name for t in tools}

    missing = set(safety_critical) - tool_names
    if missing:
        raise ProviderSchemaError(
            f"Safety-critical tools are missing from the active registry: {missing}",
            "STARTUP",
            "mcp_contract",
        )
