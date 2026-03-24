"""Tests for lintgate.testing.platonic_entrypoints.

Covers run_platonic_project, run_platonic_continue, run_platonic_apply,
_load_validation_from_path, _apply_staged_artifacts, and
_build_staged_apply_actions.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

from lintgate.testing.platonic_entrypoints import (
    _apply_staged_artifacts,
    _load_validation_from_path,
    run_platonic_apply,
    run_platonic_continue,
    run_platonic_project,
)
from lintgate.testing.platonic_workflow import PlatonicWorkflowRecord

def _load_tool_result(json_str):
    import json as _j, os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ── Helpers ──────────────────────────────────────────────────────────


def _make_helpers(project_root: str) -> dict[str, Any]:
    def _validate_project_root(path: str) -> str:
        return project_root

    def _json_dumps(data: Any, **kw: Any) -> str:
        return json.dumps(data, default=str)

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
    }


def _make_workflow(
    workflow_id: str = "wf001",
    state: str = "PROFILING",
    step: str = "assess",
    **overrides: Any,
) -> PlatonicWorkflowRecord:
    defaults: dict[str, Any] = {
        "workflow_id": workflow_id,
        "scope": "file",
        "target": "src/foo.py",
        "state": state,
        "step": step,
        "rel_file": "src/foo.py",
        "config": {
            "max_iterations": 5,
            "target_spec_level": 0.80,
            "target_kill_rate": 0.70,
            "budget_ms": 30000,
            "reconciliation_threshold": 0.7,
            "workflow_dir": ".lintgate/platonic_workflows",
        },
    }
    defaults.update(overrides)
    return PlatonicWorkflowRecord(**defaults)  # type: ignore[arg-type]


# ── _load_validation_from_path ──────────────────────────────────────


class TestLoadValidationFromPath:
    def test_returns_dict_for_valid_json(self, tmp_path):
        vpath = tmp_path / "validation.json"
        vpath.write_text('{"ready_to_apply": true}', encoding="utf-8")
        result = _load_validation_from_path(str(vpath))
        assert result == {"ready_to_apply": True}

    def test_returns_none_for_missing_file(self, tmp_path):
        result = _load_validation_from_path(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        vpath = tmp_path / "bad.json"
        vpath.write_text("not json {{", encoding="utf-8")
        result = _load_validation_from_path(str(vpath))
        assert result is None

    def test_returns_nested_dict(self, tmp_path):
        vpath = tmp_path / "val.json"
        data = {"ready_to_apply": False, "review_ready_to_apply": True, "details": {"k": 1}}
        vpath.write_text(json.dumps(data), encoding="utf-8")
        result = _load_validation_from_path(str(vpath))
        assert result == data
        assert result["details"]["k"] == 1


# ── _apply_staged_artifacts ─────────────────────────────────────────


class TestApplyStagedArtifacts:
    def test_skip_reference_only(self, tmp_path):
        artifacts = [
            {"reference_only": True, "canonical_path": "tests/existing.py"},
        ]
        actions = _apply_staged_artifacts(str(tmp_path), artifacts, dry_run=False)
        assert len(actions) == 1
        assert actions[0]["action"] == "skip"
        assert actions[0]["reason"] == "reference_only_existing_tests_adequate"

    def test_skip_missing_staging_file(self, tmp_path):
        artifacts = [
            {"staging_path": str(tmp_path / "missing.py"), "apply_destination": "out.py"},
        ]
        actions = _apply_staged_artifacts(str(tmp_path), artifacts, dry_run=False)
        assert len(actions) == 1
        assert actions[0]["action"] == "skip"
        assert actions[0]["reason"] == "staging_file_missing"

    def test_promote_dry_run(self, tmp_path):
        staging = tmp_path / "staged" / "gen.py"
        staging.parent.mkdir(parents=True)
        staging.write_text("content", encoding="utf-8")
        content_hash = hashlib.sha256(b"content").hexdigest()

        artifacts = [
            {
                "staging_path": str(staging),
                "apply_destination": "tests/gen.py",
                "content_hash": content_hash,
            },
        ]
        actions = _apply_staged_artifacts(str(tmp_path), artifacts, dry_run=True)
        assert len(actions) == 1
        assert actions[0]["action"] == "promote"
        assert actions[0]["dry_run"] is True
        # File should NOT exist since dry_run
        dest = tmp_path / "tests" / "gen.py"
        assert not dest.exists()

    def test_promote_actual(self, tmp_path):
        staging = tmp_path / "staged" / "gen.py"
        staging.parent.mkdir(parents=True)
        staging.write_text("promoted_content", encoding="utf-8")
        content_hash = hashlib.sha256(b"promoted_content").hexdigest()

        artifacts = [
            {
                "staging_path": str(staging),
                "apply_destination": "tests/gen.py",
                "content_hash": content_hash,
            },
        ]
        actions = _apply_staged_artifacts(str(tmp_path), artifacts, dry_run=False)
        assert len(actions) == 1
        assert actions[0]["action"] == "promote"
        assert actions[0]["dry_run"] is False
        dest = tmp_path / "tests" / "gen.py"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "promoted_content"

    def test_skip_hash_mismatch(self, tmp_path):
        staging = tmp_path / "staged" / "gen.py"
        staging.parent.mkdir(parents=True)
        staging.write_text("actual_content", encoding="utf-8")

        artifacts = [
            {
                "staging_path": str(staging),
                "apply_destination": "tests/gen.py",
                "content_hash": "0000000000000000000000000000000000000000000000000000000000000000",
            },
        ]
        actions = _apply_staged_artifacts(str(tmp_path), artifacts, dry_run=False)
        assert len(actions) == 1
        assert actions[0]["action"] == "skip"
        assert actions[0]["reason"] == "content_hash_mismatch"

    def test_promote_without_hash(self, tmp_path):
        staging = tmp_path / "staged" / "gen.py"
        staging.parent.mkdir(parents=True)
        staging.write_text("no_hash_content", encoding="utf-8")

        artifacts = [
            {
                "staging_path": str(staging),
                "apply_destination": "tests/gen.py",
                "content_hash": "",
            },
        ]
        actions = _apply_staged_artifacts(str(tmp_path), artifacts, dry_run=False)
        assert len(actions) == 1
        assert actions[0]["action"] == "promote"

    def test_absolute_apply_destination(self, tmp_path):
        staging = tmp_path / "staged" / "gen.py"
        staging.parent.mkdir(parents=True)
        staging.write_text("abs_content", encoding="utf-8")
        abs_dest = str(tmp_path / "absolute" / "gen.py")

        artifacts = [
            {
                "staging_path": str(staging),
                "apply_destination": abs_dest,
                "content_hash": "",
            },
        ]
        actions = _apply_staged_artifacts(str(tmp_path), artifacts, dry_run=False)
        assert len(actions) == 1
        assert actions[0]["action"] == "promote"
        assert os.path.isfile(abs_dest)


# ── run_platonic_project ────────────────────────────────────────────


class TestRunPlatonicProject:
    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch(
        "lintgate.testing.platonic_selection.select_project_target",
        return_value={"selected_file": None, "files_inspected": 3},
    )
    @patch("lintgate.testing.platonic_workflow.create_workflow_id", return_value="wf_test_001")
    @patch("lintgate.testing.platonic_workflow.save_workflow")
    @patch("lintgate.testing.platonic_workflow.append_history")
    def test_no_eligible_targets_returns_blocked(
        self, mock_append, mock_save, mock_wfid, mock_select, mock_config
    ):
        helpers = _make_helpers("/proj")
        converge_fn = MagicMock(return_value="converge_result")

        result = run_platonic_project(
            helpers, "/proj", max_files=5, budget_ms=30000, converge_fn=converge_fn
        )
        parsed = _load_tool_result(result)
        assert parsed["state"] == "BLOCKED_NO_ELIGIBLE_TARGETS"
        assert parsed["reason_code"] == "BLOCKED_NO_ELIGIBLE_TARGETS"
        assert parsed["workflow_id"] == "wf_test_001"
        assert parsed["status"] == "no_eligible_targets"
        converge_fn.assert_not_called()

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch(
        "lintgate.testing.platonic_selection.select_project_target",
        return_value={"selected_file": "src/core.py"},
    )
    @patch("lintgate.testing.platonic_workflow.create_workflow_id", return_value="wf_test_002")
    def test_eligible_target_calls_converge(self, mock_wfid, mock_select, mock_config):
        helpers = _make_helpers("/proj")
        converge_fn = MagicMock(return_value="converge_output")

        result = run_platonic_project(
            helpers, "/proj", max_files=5, budget_ms=30000, converge_fn=converge_fn
        )
        assert result == "converge_output"
        converge_fn.assert_called_once()
        call_kwargs = converge_fn.call_args
        assert call_kwargs[0][2] == "src/core.py"  # selected_file
        assert call_kwargs[1]["workflow_id"] == "wf_test_002"
        assert call_kwargs[1]["scope"] == "project"


# ── run_platonic_continue ───────────────────────────────────────────


class TestRunPlatonicContinue:
    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow", return_value=None)
    def test_workflow_not_found(self, mock_load, mock_config):
        helpers = _make_helpers("/proj")
        converge_fn = MagicMock()

        result = run_platonic_continue(helpers, "/proj", "missing_wf", converge_fn=converge_fn)
        parsed = _load_tool_result(result)
        assert parsed["error"] == "Workflow not found: missing_wf"
        converge_fn.assert_not_called()

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow")
    def test_terminal_ready_to_apply(self, mock_load, mock_config):
        wf = _make_workflow(state="READY_TO_APPLY", step="validate")
        mock_load.return_value = wf
        helpers = _make_helpers("/proj")
        converge_fn = MagicMock()

        result = run_platonic_continue(helpers, "/proj", "wf001", converge_fn=converge_fn)
        parsed = _load_tool_result(result)
        assert parsed["status"] == "terminal"
        assert parsed["state"] == "READY_TO_APPLY"
        assert len(parsed["next_actions"]) == 1
        assert parsed["next_actions"][0]["tool"] == "platonic_apply"
        converge_fn.assert_not_called()

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow")
    def test_terminal_needs_decomposition(self, mock_load, mock_config):
        wf = _make_workflow(
            state="NEEDS_DECOMPOSITION",
            step="profile",
            primary_target="complex_function",
        )
        mock_load.return_value = wf
        helpers = _make_helpers("/proj")
        converge_fn = MagicMock()

        result = run_platonic_continue(helpers, "/proj", "wf001", converge_fn=converge_fn)
        parsed = _load_tool_result(result)
        assert parsed["status"] == "terminal"
        assert len(parsed["next_actions"]) == 1
        assert parsed["next_actions"][0]["tool"] == "extraction_plan"

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow")
    def test_validating_dispatches_validate_only(self, mock_load, mock_config):
        wf = _make_workflow(state="VALIDATING", step="validate")
        mock_load.return_value = wf
        helpers = _make_helpers("/proj")
        converge_fn = MagicMock()
        validate_fn = MagicMock(return_value="validate_result")

        result = run_platonic_continue(
            helpers,
            "/proj",
            "wf001",
            converge_fn=converge_fn,
            validate_only_fn=validate_fn,
        )
        assert result == "validate_result"
        validate_fn.assert_called_once()
        converge_fn.assert_not_called()

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow")
    def test_profiling_resume_passes_snapshot(self, mock_load, mock_config):
        wf = _make_workflow(
            state="PROFILING",
            step="profile",
            orch_state_snapshot={"iteration": 2},
            staged_artifacts=[{"file": "a.py"}],
            iterations_completed=2,
        )
        mock_load.return_value = wf
        helpers = _make_helpers("/proj")
        converge_fn = MagicMock(return_value="resumed")

        result = run_platonic_continue(helpers, "/proj", "wf001", converge_fn=converge_fn)
        assert result == "resumed"
        call_kwargs = converge_fn.call_args[1]
        assert call_kwargs["orch_state_snapshot"] == {"iteration": 2}
        assert call_kwargs["iterations_completed"] == 2


# ── run_platonic_apply ──────────────────────────────────────────────


class TestRunPlatonicApply:
    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow", return_value=None)
    def test_workflow_not_found(self, mock_load, mock_config):
        helpers = _make_helpers("/proj")
        result = run_platonic_apply(helpers, "/proj", "missing_wf")
        parsed = _load_tool_result(result)
        assert parsed["error"] == "Workflow not found: missing_wf"

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow")
    def test_not_ready_state_returns_failed(self, mock_load, mock_config):
        wf = _make_workflow(state="PROFILING", step="profile")
        mock_load.return_value = wf
        helpers = _make_helpers("/proj")

        result = run_platonic_apply(helpers, "/proj", "wf001")
        parsed = _load_tool_result(result)
        assert parsed["state"] == "FAILED"
        assert parsed["reason_code"] == "APPLY_NOT_READY"

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow")
    @patch(
        "mcp_tools._test_regeneration_apply._load_validation",
        return_value=None,
    )
    def test_no_validation_returns_stale(self, mock_load_val, mock_load_wf, mock_config):
        wf = _make_workflow(state="READY_TO_APPLY", step="validate")
        mock_load_wf.return_value = wf
        helpers = _make_helpers("/proj")

        result = run_platonic_apply(helpers, "/proj", "wf001")
        parsed = _load_tool_result(result)
        assert parsed["state"] == "FAILED"
        assert parsed["reason_code"] == "STALE_VALIDATION"

    @patch("lintgate.config.load_controlplane_config", side_effect=Exception("no config"))
    @patch("lintgate.testing.platonic_workflow.load_workflow")
    @patch(
        "mcp_tools._test_regeneration_apply._load_validation",
        return_value={"ready_to_apply": True},
    )
    @patch(
        "mcp_tools._test_regeneration_apply.impl_rebuild_apply",
        return_value='{"dry_run": true, "actions": []}',
    )
    def test_dry_run_apply_succeeds(self, mock_impl, mock_load_val, mock_load_wf, mock_config):
        wf = _make_workflow(state="READY_TO_APPLY", step="validate")
        mock_load_wf.return_value = wf
        helpers = _make_helpers("/proj")

        result = run_platonic_apply(helpers, "/proj", "wf001", dry_run=True)
        parsed = _load_tool_result(result)
        assert parsed["state"] == "READY_TO_APPLY"
        assert len(parsed["next_actions"]) == 1
        assert parsed["next_actions"][0]["tool"] == "platonic_apply"
        assert parsed["next_actions"][0]["args"]["dry_run"] is False
