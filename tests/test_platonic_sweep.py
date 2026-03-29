"""Tests for the platonic_sweep scheduler-driven specification sweep."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r



def _make_helpers(project_root="/tmp/project"):
    return {
        "_validate_project_root": MagicMock(return_value=project_root),
        "_json_dumps": MagicMock(side_effect=lambda x, **kw: json.dumps(x, default=str)),
    }


def _mock_rollup(hotspot_files=None):
    rollup = MagicMock()
    rollup.hotspot_files = hotspot_files or []
    rollup.mean_spec_level = 0.1
    rollup.mean_reconciled_spec_level = 0.15
    rollup.total_functions = 100
    return rollup


def _mock_assessment(funcs=None, error=None, veto=False):
    spec_result = MagicMock()
    spec_result.functions = funcs or {}
    return {
        "error": error,
        "majority_hard_veto": veto,
        "spec_result": spec_result,
        "auto_targets": [],
        "decompose_targets": [],
    }


class TestPlatonicSweep:
    @patch("lintgate.testing.platonic_selection.assess_file")
    @patch("lintgate.specification.project_rollup.rollup_project")
    def test_sweep_enqueues_from_rollup(self, mock_rollup, mock_assess, tmp_path):
        from mcp_tools._platonic_impl import impl_platonic_sweep

        mock_rollup.return_value = _mock_rollup(hotspot_files=[{"file": "mod.py"}])
        mock_assess.return_value = _mock_assessment(
            funcs={
                "mod.py::func": {
                    "sigma": 10,
                    "risk_score": 0.5,
                    "is_pure": True,
                    "regime": "A",
                    "phase": "bulk",
                }
            }
        )
        helpers = _make_helpers(str(tmp_path))
        result = _load_tool_result(impl_platonic_sweep(helpers, str(tmp_path), budget_s=0.01))
        assert "scheduler_status" in result
        assert result["scheduler_status"]["completed_count"] >= 0

    @patch("lintgate.specification.project_rollup.rollup_project")
    def test_sweep_empty_project_returns_gracefully(self, mock_rollup, tmp_path):
        from mcp_tools._platonic_impl import impl_platonic_sweep

        mock_rollup.return_value = _mock_rollup(hotspot_files=[])
        helpers = _make_helpers(str(tmp_path))
        result = _load_tool_result(impl_platonic_sweep(helpers, str(tmp_path), budget_s=0.01))
        assert result["functions_swept"] == 0

    @patch("lintgate.testing.platonic_selection.assess_file")
    @patch("lintgate.specification.project_rollup.rollup_project")
    def test_sweep_budget_enforcement(self, mock_rollup, mock_assess, tmp_path):
        from mcp_tools._platonic_impl import impl_platonic_sweep

        mock_rollup.return_value = _mock_rollup(
            hotspot_files=[{"file": f"mod{i}.py"} for i in range(10)]
        )
        mock_assess.return_value = _mock_assessment(
            funcs={
                f"mod.py::func{i}": {"sigma": 10, "regime": "A", "phase": "bulk"} for i in range(20)
            }
        )
        helpers = _make_helpers(str(tmp_path))
        result = _load_tool_result(impl_platonic_sweep(helpers, str(tmp_path), budget_s=0.001))
        assert result["elapsed_s"] < 5  # Should respect budget

    @patch("lintgate.testing.platonic_selection.assess_file")
    @patch("lintgate.specification.project_rollup.rollup_project")
    def test_sweep_skips_vetoed_files(self, mock_rollup, mock_assess, tmp_path):
        from mcp_tools._platonic_impl import impl_platonic_sweep

        mock_rollup.return_value = _mock_rollup(hotspot_files=[{"file": "bad.py"}])
        mock_assess.return_value = _mock_assessment(veto=True)
        helpers = _make_helpers(str(tmp_path))
        result = _load_tool_result(impl_platonic_sweep(helpers, str(tmp_path), budget_s=0.01))
        assert result["functions_swept"] == 0

    @patch("lintgate.specification.project_rollup.rollup_project")
    def test_sweep_resumes_from_persisted_state(self, mock_rollup, tmp_path):
        from mcp_tools._platonic_impl import impl_platonic_sweep

        # Write a scheduler state file
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)
        state = {
            "queue": [
                {
                    "function_key": "a.py::f",
                    "file_path": "a.py",
                    "priority": 10.0,
                    "tier": "sampling",
                    "enqueued_at": 0,
                }
            ],
            "completed": [],
            "last_batch_at": 0,
        }
        (cache_dir / "scheduler_state.json").write_text(json.dumps(state))

        mock_rollup.return_value = _mock_rollup(hotspot_files=[])
        helpers = _make_helpers(str(tmp_path))
        result = _load_tool_result(impl_platonic_sweep(helpers, str(tmp_path), budget_s=0.01))
        assert result["resumed"] is True

    @patch("lintgate.testing.platonic_selection.assess_file")
    @patch("lintgate.specification.project_rollup.rollup_project")
    def test_sweep_saves_state_after_each_batch(self, mock_rollup, mock_assess, tmp_path):
        from mcp_tools._platonic_impl import impl_platonic_sweep

        mock_rollup.return_value = _mock_rollup(hotspot_files=[{"file": "mod.py"}])
        mock_assess.return_value = _mock_assessment(
            funcs={"mod.py::func": {"sigma": 5, "regime": "A", "phase": "bulk"}}
        )
        helpers = _make_helpers(str(tmp_path))
        impl_platonic_sweep(helpers, str(tmp_path), budget_s=5.0)
        state_file = tmp_path / ".lintgate" / "mutation" / "scheduler_state.json"
        # Verify state file was written (persistence works)
        assert state_file.exists(), "scheduler_state.json should be written after sweep"
        state_data = json.loads(state_file.read_text())
        assert "queue" in state_data
        assert "completed" in state_data
