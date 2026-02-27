import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_tools.mutation_tools import register


class _FakeMCP:
    def tool(self):
        def _decorator(fn):
            return fn

        return _decorator


def _stub_helpers(**overrides):
    defaults = {
        "_validate_project_root": lambda p, **kw: p or "/tmp/test",
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def mock_engine():
    with patch("mcp_tools.mutation_tools._get_engine") as mock:
        engine = MagicMock()
        mock.return_value = engine
        yield engine


def test_mutation_run_sampling_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())
    mock_engine.run_inline_sampling.return_value = []

    result = json.loads(tools["mutation_run_sampling"](path="/tmp", files=["a.py"]))

    assert "run_id" in result
    assert mock_engine.run_inline_sampling.called


def test_mutation_run_full_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())
    mock_engine.run_background_profiling.return_value = []

    result = json.loads(tools["mutation_run_full"](path="/tmp", files=["a.py"]))

    assert "run_id" in result
    assert mock_engine.run_background_profiling.called


def test_mutation_get_state_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())

    mock_state = MagicMock()
    mock_state.file_path = "a.py"
    mock_state.to_dict.return_value = {"func": "f"}
    mock_engine.state_manager.state = {"a.py::f": mock_state}

    result = json.loads(tools["mutation_get_state"](path="/tmp"))

    assert "a.py" in result["files"]
    assert len(result["files"]["a.py"]) == 1


def test_mutation_clear_state_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())

    mock_engine.state_manager.state = {"a.py::f": MagicMock()}

    # Clear specific file
    result = json.loads(tools["mutation_clear_state"](path="/tmp", files=["a.py"]))
    assert "Cleared state for 1 files" in result["message"]
    assert mock_engine.state_manager.save.called

    # Clear all
    result = json.loads(tools["mutation_clear_state"](path="/tmp"))
    assert "Cleared all mutation state" in result["message"]
    assert mock_engine.state_manager.state == {}


def test_mutation_prescribe_tool(mock_engine, monkeypatch):
    tools = register(_FakeMCP(), _stub_helpers())

    mock_state = MagicMock()
    mock_state.file_path = "/tmp/src/a.py"
    mock_state.function_name = "f"
    from lintgate.mutation.state import CoverageDepth

    mock_state.depth = CoverageDepth.SAMPLED
    mock_state.survival_rate = 0.6
    mock_state.total = 10
    mock_state.survived_by_category = {"arithmetic": 3, "conditional": 2, "string": 1}
    mock_state.to_dict.return_value = {
        "func": "f",
        "survived_by_category": mock_state.survived_by_category,
    }

    mock_engine.state_manager.state = {"/tmp/src/a.py::f": mock_state}

    result = json.loads(tools["mutation_prescribe"](path="/tmp", file="a.py"))

    assert result["schema_version"] == 2
    assert len(result["profiles"]) == 1
    assert len(result["diagnoses"]) == 1
    assert result["gate_status"] in ["FAIL", "WARN", "PASS"]

    assert "prescriptions" in result
    assert isinstance(result["prescriptions"], list)
    assert len(result["prescriptions"]) > 0
    assert "category" in result["prescriptions"][0]
    assert "gate_lift_projection_percent" in result["prescriptions"][0]

    assert "next_actions" in result
    assert (
        "mutation_decompose" in result["next_actions"]
        or "mutation_refactor_loop" in result["next_actions"]
    )
