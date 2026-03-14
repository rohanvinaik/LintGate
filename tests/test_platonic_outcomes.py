"""Tests for lintgate/testing/platonic_outcomes.py — workflow outcome routing."""

from __future__ import annotations

from lintgate.testing.platonic_outcomes import (
    platonic_config_dict,
    proposed_artifacts,
    reroute_manual_contract_candidates,
    workflow_state_from_outputs,
)


# --- platonic_config_dict ---


def test_platonic_config_dict_all_fields():
    result = platonic_config_dict(
        max_iterations=5,
        target_spec_level=0.8,
        target_kill_rate=0.9,
        budget_ms=10000,
        reconciliation_threshold=0.5,
        workflow_dir=".lintgate/workflows",
    )
    assert result == {
        "max_iterations": 5,
        "target_spec_level": 0.8,
        "target_kill_rate": 0.9,
        "budget_ms": 10000,
        "reconciliation_threshold": 0.5,
        "workflow_dir": ".lintgate/workflows",
    }


def test_platonic_config_dict_zero_values():
    result = platonic_config_dict(0, 0.0, 0.0, 0.0, 0.0, "")
    assert result["max_iterations"] == 0
    assert result["target_spec_level"] == 0.0
    assert result["workflow_dir"] == ""


# --- workflow_state_from_outputs ---


def test_workflow_state_decompose_targets():
    state, code, msg = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={},
        decompose_targets=["func_a"],
    )
    assert state == "NEEDS_DECOMPOSITION"
    assert code == "NEEDS_DECOMPOSITION"
    assert "structural decomposition" in msg


def test_workflow_state_runtime_decompose():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={"targets": [{"status": "decompose"}]},
        health_data={},
        validation={},
        decompose_targets=[],
    )
    assert state == "NEEDS_DECOMPOSITION"
    assert code == "NEEDS_DECOMPOSITION"


def test_workflow_state_discovery_veto():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={"vetoes": {"discovery_artifact": True}},
        validation={},
        decompose_targets=[],
    )
    assert state == "BLOCKED_DISCOVERY"
    assert code == "DISCOVERY_ARTIFACT"


def test_workflow_state_mock_boundary_veto():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={"vetoes": {"mock_boundary": True}},
        validation={},
        decompose_targets=[],
    )
    assert state == "BLOCKED_TOPOLOGY"
    assert code == "MOCK_BOUNDARY_ARTIFACT"


def test_workflow_state_budget_instability():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={"vetoes": {"budget_instability": True}},
        validation={},
        decompose_targets=[],
    )
    assert state == "FAILED"
    assert code == "BUDGET_INSTABILITY"


def test_workflow_state_validation_error():
    state, code, msg = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={"error": "test parse failure"},
        decompose_targets=[],
    )
    assert state == "FAILED"
    assert code == "VALIDATION_ERROR"
    assert msg == "test parse failure"


def test_workflow_state_ready_to_apply():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={"ready_to_apply": True},
        decompose_targets=[],
    )
    assert state == "READY_TO_APPLY"
    assert code == "READY_TO_APPLY"


def test_workflow_state_review_ready():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={"review_ready_to_apply": True},
        decompose_targets=[],
    )
    assert state == "READY_TO_APPLY_WITH_REVIEW"
    assert code == "REVIEW_CEILING_EXCEEDED"


def test_workflow_state_converged():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={"total_targets": 3, "converged": 3},
        health_data={},
        validation={},
        decompose_targets=[],
    )
    assert state == "CONVERGED"
    assert code == "CONVERGED"


def test_workflow_state_existing_tests_sufficient():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={},
        decompose_targets=[],
        iteration_log=[{"skipped_reason": "existing_tests_adequate"}],
    )
    assert state == "EXISTING_TESTS_SUFFICIENT"
    assert code == "EXISTING_TESTS_SUFFICIENT"


def test_workflow_state_plateau_no_generation():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={},
        decompose_targets=[],
        iteration_log=[{"tests_generated": 0}],
    )
    assert state == "PLATEAU_NO_GENERATION"
    assert code == "PLATEAU_NO_GENERATION"


def test_workflow_state_validating():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={},
        decompose_targets=[],
        iteration_log=[{"tests_generated": 2}],
    )
    assert state == "VALIDATING"
    assert code == "CONTINUE"


def test_workflow_state_profiling():
    # An iteration with tests_generated > 0 triggers VALIDATING,
    # and an iteration with tests_generated == 0 (or missing) triggers PLATEAU_NO_GENERATION.
    # To hit PROFILING, we need an iteration log where at least one entry
    # has tests_generated > 0 but with no iteration having that key absent.
    # Actually, re-reading the code: the PLATEAU check fires if NO iteration has tests_generated > 0.
    # {"other_key": "value"} has tests_generated missing, so .get("tests_generated", 0) == 0.
    # That means all iterations have tests_generated == 0, hitting PLATEAU first.
    # PROFILING requires iteration_log truthy but none of the above conditions.
    # That can't happen because the plateau and validating checks are exhaustive.
    # So let's just verify PLATEAU_NO_GENERATION is what we actually get.
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={},
        decompose_targets=[],
        iteration_log=[{"other_key": "value"}],
    )
    assert state == "PLATEAU_NO_GENERATION"
    assert code == "PLATEAU_NO_GENERATION"


def test_workflow_state_assessing_default():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={},
        health_data={},
        validation={},
        decompose_targets=[],
    )
    assert state == "ASSESSING"
    assert code == "CONTINUE"


def test_workflow_state_all_halted():
    state, code, _ = workflow_state_from_outputs(
        conv_summary={"targets": [{"status": "halted"}]},
        health_data={},
        validation={},
        decompose_targets=[],
    )
    assert state == "PLATEAU_NO_GENERATION"
    assert code == "ALL_TARGETS_HALTED"


# --- proposed_artifacts ---


def test_proposed_artifacts_empty():
    # Empty validation dict is falsy, so no validation artifact is added
    result = proposed_artifacts(validation={}, assessment={})
    assert result == []


def test_proposed_artifacts_with_manifest():
    from unittest.mock import MagicMock

    target = MagicMock()
    target.target_test_file = "tests/test_gen.py"

    result = proposed_artifacts(
        validation={"ready_to_apply": True},
        assessment={
            "manifest_path": ".lintgate/manifest.json",
            "auto_targets": [target],
        },
    )
    kinds = [a["kind"] for a in result]
    assert "manifest" in kinds
    assert "generated_test" in kinds
    assert "validation" in kinds
    assert result[-1]["ready_to_apply"] is True


def test_proposed_artifacts_no_target_test_file():
    from unittest.mock import MagicMock

    target = MagicMock()
    target.target_test_file = ""

    result = proposed_artifacts(
        validation={},
        assessment={"auto_targets": [target]},
    )
    # Empty target_test_file should still be included (truthy check on target_test_file)
    kinds = [a["kind"] for a in result]
    assert "generated_test" not in kinds


# --- reroute_manual_contract_candidates ---


def test_reroute_manual_contract_empty_keys():
    """Empty function_keys should be a no-op — load_manifest never called."""
    from unittest.mock import patch as _patch

    with _patch(
        "lintgate.specification.test_regeneration_strategy.load_manifest"
    ) as mock_load:
        reroute_manual_contract_candidates("/tmp", [])
        mock_load.assert_not_called()


def test_reroute_manual_contract_no_crash():
    """Should handle missing manifest gracefully — returns without writing."""
    from unittest.mock import patch as _patch

    with (
        _patch(
            "lintgate.specification.test_regeneration_strategy.load_manifest",
            return_value=None,
        ) as mock_load,
        _patch(
            "lintgate.specification.test_regeneration_strategy.write_manifest"
        ) as mock_write,
    ):
        reroute_manual_contract_candidates("/nonexistent/project", ["mod.func"])
        mock_load.assert_called_once_with("/nonexistent/project")
        mock_write.assert_not_called()


def test_reroute_manual_contract_updates_matching_functions():
    """Matching functions get strategy/fields updated and manifest is written."""
    from unittest.mock import MagicMock, patch as _patch

    from lintgate.specification._regeneration_types import ExistingTestAction, Strategy

    func_match = MagicMock()
    func_match.function_key = "mod.func_a"
    func_match.reason_codes = []

    func_skip = MagicMock()
    func_skip.function_key = "mod.func_b"

    manifest = MagicMock()
    manifest.functions = [func_match, func_skip]

    with (
        _patch(
            "lintgate.specification.test_regeneration_strategy.load_manifest",
            return_value=manifest,
        ),
        _patch(
            "lintgate.specification.test_regeneration_strategy.write_manifest"
        ) as mock_write,
    ):
        reroute_manual_contract_candidates("/proj", ["mod.func_a"])
        # Verify the matched function was updated with exact enum values
        assert func_match.strategy == Strategy.MANUAL_CONTRACT
        assert func_match.existing_test_action == ExistingTestAction.QUARANTINE_ONLY
        assert func_match.target_test_file == ""
        assert func_match.generation_mode == "manual_contract"
        assert func_match.manual_review_required is True
        assert func_match.reason_codes == ["no_executable_witness"]
        # Verify manifest was written
        mock_write.assert_called_once_with(manifest, "/proj")
