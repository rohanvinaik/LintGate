"""MCP-level tests for model profile calibration workflow."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MCP_SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server.py"
_MCP_SPEC = importlib.util.spec_from_file_location("lintgate_local_mcp_server", _MCP_SERVER_PATH)
assert _MCP_SPEC is not None and _MCP_SPEC.loader is not None
_MCP_MODULE = importlib.util.module_from_spec(_MCP_SPEC)
_MCP_SPEC.loader.exec_module(_MCP_MODULE)

model_profile_probe_start = _MCP_MODULE.model_profile_probe_start
model_profile_probe_submit = _MCP_MODULE.model_profile_probe_submit
model_profile_status = _MCP_MODULE.model_profile_status

_ANSWERS = {
    "q1_failure_response": "B",
    "q2_verification_habits": "A",
    "q3_constraint_discovery": "D",
    "q4_model_updating": "C",
    "q5_tool_patterns": "B",
}


def test_probe_submit_increments_probe_runs(monkeypatch, tmp_path) -> None:
    lintgate_home = tmp_path / "lintgate_home"
    monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

    first = json.loads(
        model_profile_probe_submit(
            path=str(tmp_path),
            model_id="claude-opus-4",
            answers=_ANSWERS,
        )
    )
    second = json.loads(
        model_profile_probe_submit(
            path=str(tmp_path),
            model_id="claude-opus-4",
            answers=_ANSWERS,
        )
    )
    status = json.loads(model_profile_status(path=str(tmp_path), model_id="claude-opus-4"))

    assert first["probe_runs"] == 1
    assert second["probe_runs"] == 2
    assert status["probe_runs"] == 2


def test_probe_start_rejects_unsupported_probe_set(monkeypatch, tmp_path) -> None:
    lintgate_home = tmp_path / "lintgate_home"
    monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

    result = json.loads(
        model_profile_probe_start(
            path=str(tmp_path),
            model_id="claude-opus-4",
            probe_set="full",
        )
    )
    assert "error" in result
    assert "Unsupported probe_set" in result["error"]
    assert result["supported_probe_sets"] == ["quick"]
