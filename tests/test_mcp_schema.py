from dataclasses import dataclass
from unittest.mock import mock_open, patch

import pytest

from lintgate.mcp_schema import (
    ProviderSchemaError,
    compile_and_validate_schemas,
    enforce_mcp_contract,
    validate_schema_node,
)


@dataclass
class MockTool:
    name: str
    input_schema: dict

    def __post_init__(self) -> None:
        # Provider contracts reference `inputSchema`; expose it for test doubles.
        self.inputSchema = self.input_schema


def test_validate_schema_node_valid_string_enum():
    schema = {"type": "string", "enum": ["a", "b", "c"]}
    # Should not raise
    validate_schema_node("test_tool", "path", schema)


def test_validate_schema_node_invalid_type_enum():
    schema = {"type": "integer", "enum": [1, 2, 3]}
    with pytest.raises(ProviderSchemaError) as excinfo:
        validate_schema_node("test_tool", "path", schema)
    assert "Enums are only allowed on strictly 'string' types" in str(excinfo.value)
    assert excinfo.value.tool_name == "test_tool"
    assert excinfo.value.path == "path"


def test_validate_schema_node_empty_string_enum():
    schema = {"type": "string", "enum": ["a", "", "c"]}
    with pytest.raises(ProviderSchemaError) as excinfo:
        validate_schema_node("test_tool", "path", schema)
    assert "Empty string found in enum at index 1" in str(excinfo.value)


def test_validate_schema_node_recursive():
    schema = {"type": "object", "properties": {"p1": {"type": "integer", "enum": [1]}}}
    with pytest.raises(ProviderSchemaError) as excinfo:
        validate_schema_node("test_tool", "path", schema)
    assert "path.properties.p1" in excinfo.value.path


def test_compile_and_validate_schemas_strict():
    tools = [
        MockTool("tool1", {"type": "string", "enum": ["a"]}),
        MockTool("tool2", {"type": "integer", "enum": [1]}),
    ]
    with pytest.raises(ProviderSchemaError):
        compile_and_validate_schemas(tools, agent_profile="strict")


def test_compile_and_validate_schemas_relaxed():
    tools = [MockTool("tool2", {"type": "integer", "enum": [1]})]
    # Should not raise in relaxed mode
    compile_and_validate_schemas(tools, agent_profile="relaxed")


def test_enforce_mcp_contract_success():
    contract = {"safety_critical_tools": ["tool1", "tool2"]}
    tools = [MockTool("tool1", {}), MockTool("tool2", {})]

    with (
        patch("pathlib.Path") as mock_path,
        patch("builtins.open", mock_open()),
        patch("yaml.safe_load", return_value=contract),
    ):
        mock_path.return_value.__truediv__.return_value.exists.return_value = True
        enforce_mcp_contract(tools)


def test_enforce_mcp_contract_missing_tool():
    contract = {"safety_critical_tools": ["tool1", "tool2"]}
    tools = [MockTool("tool1", {})]

    with (
        patch("pathlib.Path") as mock_path,
        patch("builtins.open", mock_open()),
        patch("yaml.safe_load", return_value=contract),
    ):
        mock_path.return_value.__truediv__.return_value.exists.return_value = True
        with pytest.raises(ProviderSchemaError) as excinfo:
            enforce_mcp_contract(tools)
        assert "Safety-critical tools are missing" in str(excinfo.value)
        assert "tool2" in str(excinfo.value)
