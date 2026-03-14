"""Tests for mcp_tools._platonic_impl."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from lintgate.testing.platonic_outcomes import workflow_state_from_outputs
from lintgate.testing.platonic_workflow import (
    TERMINAL_STATES,
    PlatonicWorkflowRecord,
    load_workflow,
    save_workflow,
)
from mcp_tools._platonic_impl import (
    _compute_file_health,
    _find_mutation_result,
    _load_mutation_cache,
    _reroute_manual_contract_candidates,
    _run_mutation_sampling,
    impl_platonic_apply,
    impl_platonic_continue,
    impl_platonic_converge,
    impl_platonic_project,
)


def _make_helpers(project_root: str) -> dict[str, Any]:
    def _validate_project_root(path: str) -> str:
        return project_root

    def _json_dumps(data: Any, **kw: Any) -> str:
        return json.dumps(data, default=str)

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
    }


@dataclass
class _FakeSpecResult:
    functions: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class _FakeEvidence:
    function_key: str = "mod.py::func"
    source_file: str = "mod.py"


@dataclass
class _FakeClassification:
    evidence: _FakeEvidence = field(default_factory=_FakeEvidence)
    target_test_file: str = "tests/generated/test_mod.py"
    function_key: str = "mod.py::func"


class TestFindMutationResult:
    def test_finds_matching_key(self):
        results = [
            {"function_key": "a::b", "survival_rate": 0.5},
            {"function_key": "c::d", "survival_rate": 0.3},
        ]
        found = _find_mutation_result(results, "c::d")
        assert found is not None
        assert found["survival_rate"] == 0.3

    def test_returns_none_for_missing(self):
        assert _find_mutation_result([{"function_key": "a::b"}], "x::y") is None


class TestLoadMutationCache:
    def test_returns_none_when_no_cache(self, tmp_path):
        assert _load_mutation_cache(str(tmp_path), "mod.py") is None

    def test_loads_cached_states(self, tmp_path):
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)
        with (
            patch(
                "mcp_tools._mutation_impl.get_cache_dir",
                return_value=cache_dir,
            ),
            patch(
                "mcp_tools._mutation_impl.iter_cached_states",
                return_value=[{"function_key": "mod.py::func", "survival_rate": 0.5}],
            ),
        ):
            result = _load_mutation_cache(str(tmp_path), "mod.py")
        assert result == {"mod.py::func": {"function_key": "mod.py::func", "survival_rate": 0.5}}


class TestComputeFileHealth:
    def test_empty_functions(self):
        from lintgate.testing.convergence_orchestrator import init_from_targets

        state = init_from_targets([])
        result = _compute_file_health({}, None, state)
        assert "axes" in result

    def test_includes_composition_axis(self):
        from lintgate.testing.convergence_orchestrator import init_from_targets

        state = init_from_targets([{"function_key": "m::f"}])
        spec = {
            "m::f": {
                "specification_level": 0.5,
                "testability_score": 0.8,
                "composition_gamma": 1.0,
            }
        }
        result = _compute_file_health(spec, None, state)
        assert result["axes"]["composition"] == 0.5

    def test_empty_functions_includes_axes_measured(self):
        from lintgate.testing.platonic_health import compute_file_health

        result = compute_file_health({}, None, None)
        assert "axes_measured" in result

    def test_measured_zero_convergence_counted(self):
        from unittest.mock import MagicMock

        orch_state = MagicMock()
        target = MagicMock()
        target.kill_rate = 0.5
        target.convergence_rate = 0.0
        orch_state.targets = {"m::f": target}

        spec = {"m::f": {"specification_level": 0.5, "testability_score": 0.8}}
        result = _compute_file_health(spec, None, orch_state)
        assert result["axes_measured"]["convergence"] is True

    def test_uses_discovery_artifact_truth_label_as_veto(self):
        from lintgate.testing.convergence_orchestrator import init_from_targets

        state = init_from_targets([{"function_key": "m::f"}])
        spec = {"m::f": {"specification_level": 0.5, "testability_score": 0.8}}
        mutation_cache = {
            "m::f": {
                "discovery_state": "DISCOVERY_OK",
                "survival_interpretation": "DISCOVERY_ARTIFACT",
                "mutation_truth_label": "DISCOVERY_ARTIFACT",
                "survival_rate": 1.0,
            }
        }
        result = _compute_file_health(spec, mutation_cache, state)
        assert result["vetoes"]["discovery_artifact"] is True


class TestRerouteManualContractCandidates:
    def test_rewrites_manifest_entries(self, tmp_path):
        from lintgate.specification._regeneration_types import (
            ClassificationResult,
            ExistingTestAction,
            FunctionEvidence,
            Strategy,
        )
        from lintgate.specification.test_regeneration_strategy import build_manifest, write_manifest

        classification = ClassificationResult(
            function_key="mod.py::func",
            strategy=Strategy.AUTO_GENERATE_UNIT,
            existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
            target_test_file="tests/generated/test_mod.py",
            confidence=0.8,
            reason_codes=["pure_or_local"],
            evidence=FunctionEvidence(function_key="mod.py::func", source_file="mod.py"),
        )
        manifest = build_manifest(str(tmp_path), [classification])
        write_manifest(manifest, str(tmp_path))

        _reroute_manual_contract_candidates(str(tmp_path), ["mod.py::func"])

        from lintgate.specification.test_regeneration_strategy import load_manifest

        updated = load_manifest(str(tmp_path))
        assert updated is not None
        func = updated.functions[0]
        assert func.strategy.value == "manual_contract"
        assert func.target_test_file == ""
        assert "no_executable_witness" in func.reason_codes


class TestPlatonicConverge:
    def test_file_not_found(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        result = json.loads(impl_platonic_converge(helpers, str(tmp_path), "missing.py"))
        assert "error" in result

    def test_no_eligible_targets(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func():\n    return 1\n")

        assessment = {
            "spec_result": _FakeSpecResult(functions={}),
            "auto_targets": [],
            "decompose_targets": [],
            "primary_target": "",
            "manifest_path": str(tmp_path / ".lintgate" / "test_rebuild_manifest.json"),
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"manual_contract": 1},
            },
        }

        with patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment):
            result = json.loads(impl_platonic_converge(helpers, str(tmp_path), "mod.py"))

        assert result["state"] == "BLOCKED_NO_ELIGIBLE_TARGETS"
        assert result["status"] == "no_eligible_targets"

    def test_routes_to_decomposition(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func():\n    return 1\n")

        decompose = _FakeClassification(function_key="mod.py::func")
        assessment = {
            "spec_result": _FakeSpecResult(functions={}),
            "auto_targets": [],
            "decompose_targets": [decompose],
            "primary_target": "mod.py::func",
            "manifest_path": str(tmp_path / ".lintgate" / "test_rebuild_manifest.json"),
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"needs_decomposition": 1},
            },
        }

        with patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment):
            result = json.loads(impl_platonic_converge(helpers, str(tmp_path), "mod.py"))

        assert result["state"] == "NEEDS_DECOMPOSITION"
        assert result["primary_next_action"] == "extraction_plan"

    def test_ready_to_apply_workflow(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.1,
                        "phase": "bulk",
                        "sigma": 5,
                        "regime": "A",
                        "testability_score": 0.9,
                        "composition_gamma": 0.0,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": str(tmp_path / ".lintgate" / "test_rebuild_manifest.json"),
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                return_value=[
                    {
                        "function_key": "mod.py::func",
                        "survival_rate": 0.0,
                        "discovery_state": "",
                        "topology_state": "NORMAL",
                        "survival_interpretation": "MEANINGFUL",
                    }
                ],
            ),
            patch(
                "mcp_tools._platonic_impl._generate_tests",
                return_value={
                    "files_written": 1,
                    "manual_contract_candidates": [],
                },
            ),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": True, "gates": {}}),
            ),
        ):
            result = json.loads(
                impl_platonic_converge(
                    helpers,
                    str(tmp_path),
                    "mod.py",
                    max_iterations=1,
                )
            )

        assert result["state"] == "READY_TO_APPLY"
        assert result["primary_next_action"] == "platonic_apply"
        workflow = load_workflow(
            str(tmp_path),
            ".lintgate/platonic_workflows",
            result["workflow_id"],
        )
        assert workflow is not None
        assert workflow.state == "READY_TO_APPLY"

    def test_uses_platonic_workflow_config_threshold(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "lintgate.yaml").write_text(
            "controlplane:\n"
            "  enabled: true\n"
            "  platonic_workflow:\n"
            "    reconciliation_confidence_threshold: 0.85\n"
        )

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.1,
                        "phase": "bulk",
                        "sigma": 5,
                        "regime": "A",
                        "testability_score": 0.9,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": str(tmp_path / ".lintgate" / "test_rebuild_manifest.json"),
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }
        seen: list[float] = []

        def fake_reconcile(static_spec_level, overlay, confidence_threshold=0.7):
            del static_spec_level, overlay
            seen.append(confidence_threshold)
            return (0.1, "static")

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                return_value=[{"function_key": "mod.py::func", "survival_rate": 0.1}],
            ),
            patch("mcp_tools._platonic_impl._generate_tests", return_value={"files_written": 0}),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": False, "gates": {}}),
            ),
            patch(
                "lintgate.specification.static_empirical_reconciliation.reconcile_spec_level",
                side_effect=fake_reconcile,
            ),
        ):
            result = json.loads(
                impl_platonic_converge(helpers, str(tmp_path), "mod.py", max_iterations=1)
            )

        assert seen
        assert 0.85 in seen
        assert result["reconciliation_threshold"] == 0.85

    def test_persists_runtime_mutation_results(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.1,
                        "phase": "bulk",
                        "sigma": 5,
                        "regime": "A",
                        "testability_score": 0.9,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": str(tmp_path / ".lintgate" / "test_rebuild_manifest.json"),
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                return_value=[{"function_key": "mod.py::func", "survival_rate": 0.0}],
            ),
            patch("mcp_tools._platonic_impl._generate_tests", return_value={"files_written": 0}),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": False, "gates": {}}),
            ),
            patch(
                "mcp_tools._mutation_impl.get_cache_dir",
                return_value=tmp_path / ".lintgate" / "mutation",
            ),
            patch("mcp_tools._mutation_impl.save_cached_state") as mock_save,
        ):
            impl_platonic_converge(helpers, str(tmp_path), "mod.py", max_iterations=1)

        mock_save.assert_called()
        assert mock_save.call_args_list[-1].args[1] == "mod.py::func"


class TestPlatonicProjectContinueApply:
    def test_platonic_project_delegates_to_converge(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        with (
            patch(
                "lintgate.testing.platonic_selection.select_project_target",
                return_value={"selected_file": "mod.py", "assessment": {}, "files_inspected": 1},
            ),
            patch(
                "mcp_tools._platonic_impl.impl_platonic_converge",
                return_value=json.dumps({"workflow_id": "wf1", "state": "PROFILING"}),
            ) as mock_converge,
        ):
            result = json.loads(impl_platonic_project(helpers, str(tmp_path)))

        assert result["workflow_id"] == "wf1"
        mock_converge.assert_called_once()

    def test_platonic_continue_returns_terminal_envelope(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            primary_target="mod.py::func",
            primary_next_action="platonic_apply",
            primary_next_args={"path": str(tmp_path), "workflow_id": "wf1", "dry_run": True},
            autopilot_safe=True,
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        result = json.loads(impl_platonic_continue(helpers, str(tmp_path), "wf1"))
        assert result["state"] == "READY_TO_APPLY"
        assert result["status"] == "terminal"

    def test_platonic_continue_reinvokes_converge_for_non_terminal(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="PROFILING",
            step="profile",
            config={
                "max_iterations": 2,
                "target_spec_level": 0.8,
                "target_kill_rate": 0.7,
                "budget_ms": 1234,
                "reconciliation_threshold": 0.75,
                "workflow_dir": ".lintgate/platonic_workflows",
            },
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        with patch(
            "mcp_tools._platonic_impl.impl_platonic_converge",
            return_value=json.dumps({"workflow_id": "wf1", "state": "VALIDATING"}),
        ) as mock_converge:
            result = json.loads(impl_platonic_continue(helpers, str(tmp_path), "wf1"))

        assert result["state"] == "VALIDATING"
        assert mock_converge.call_args.kwargs["workflow_id"] == "wf1"
        assert mock_converge.call_args.kwargs["budget_ms"] == 1234

    def test_platonic_apply_rejects_non_ready_workflow(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="VALIDATING",
            step="validate",
            autopilot_safe=False,
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1"))
        assert result["state"] == "FAILED"
        assert result["reason_code"] == "APPLY_NOT_READY"

    def test_platonic_apply_dry_run(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            autopilot_safe=True,
            evidence_summary={"validation": {"ready_to_apply": True}},
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        with (
            patch(
                "mcp_tools._test_regeneration_apply._load_validation",
                return_value={"ready_to_apply": True},
            ),
            patch(
                "mcp_tools._test_regeneration_apply.impl_rebuild_apply",
                return_value=json.dumps({"dry_run": True, "actions": []}),
            ),
        ):
            result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=True))

        assert result["state"] == "READY_TO_APPLY"
        assert result["apply_result"]["dry_run"] is True
        assert result["next_actions"][0]["tool"] == "platonic_apply"

    def test_platonic_apply_executes_and_marks_converged(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            autopilot_safe=True,
            evidence_summary={"validation": {"ready_to_apply": True}},
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        with (
            patch(
                "mcp_tools._test_regeneration_apply._load_validation",
                return_value={"ready_to_apply": True},
            ),
            patch(
                "mcp_tools._test_regeneration_apply.impl_rebuild_apply",
                return_value=json.dumps({"dry_run": False, "actions": [{"action": "promote"}]}),
            ),
        ):
            result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=False))

        assert result["state"] == "CONVERGED"
        workflow = load_workflow(str(tmp_path), ".lintgate/platonic_workflows", "wf1")
        assert workflow is not None
        assert workflow.state == "CONVERGED"


class TestOrchestratorStateSerialization:
    """Phase 0: to_dict/from_dict round-trip for convergence orchestrator."""

    def test_orch_state_empty_round_trip(self):
        from lintgate.testing.convergence_orchestrator import OrchestratorState

        state = OrchestratorState()
        d = state.to_dict()
        restored = OrchestratorState.from_dict(d)
        assert restored.targets == {}
        assert restored.iteration_count == 0
        assert restored.start_ms == 0.0
        assert restored.elapsed_ms == 0.0

    def test_orch_state_with_targets_round_trip(self):
        from lintgate.testing.convergence_orchestrator import (
            OrchestratorState,
            init_from_targets,
            update_target,
        )

        state = init_from_targets(
            [
                {
                    "function_key": "m::f",
                    "source_file": "m.py",
                    "target_test_file": "tests/test_m.py",
                },
            ]
        )
        update_target(state, "m::f", spec_level=0.5, kill_rate=0.3, phase="bulk")

        d = state.to_dict()
        restored = OrchestratorState.from_dict(d)
        assert "m::f" in restored.targets
        t = restored.targets["m::f"]
        assert t.spec_level == 0.5
        assert t.kill_rate == 0.3
        assert t.source_file == "m.py"
        assert restored.iteration_count == state.iteration_count

    def test_trajectory_preserved_in_round_trip(self):
        from lintgate.testing.convergence_orchestrator import (
            OrchestratorState,
            init_from_targets,
            update_target,
        )

        state = init_from_targets([{"function_key": "m::f"}])
        update_target(state, "m::f", spec_level=0.2, kill_rate=0.1, phase="bulk")
        update_target(state, "m::f", spec_level=0.5, kill_rate=0.4, phase="transition")

        d = state.to_dict()
        restored = OrchestratorState.from_dict(d)
        traj = restored.targets["m::f"].trajectory
        assert len(traj) == 2
        assert traj[0].spec_level == 0.2
        assert traj[1].spec_level == 0.5
        assert traj[1].phase == "transition"


class TestPlatonicContinueStepAware:
    """Gap 1: step-aware continue dispatches VALIDATING to validate_only_fn."""

    def test_platonic_continue_validating_skips_converge(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="VALIDATING",
            step="validate",
            config={
                "max_iterations": 2,
                "target_spec_level": 0.8,
                "target_kill_rate": 0.7,
                "budget_ms": 1234,
                "reconciliation_threshold": 0.75,
                "workflow_dir": ".lintgate/platonic_workflows",
            },
            evidence_summary={"convergence": {}, "health": {}},
            validation_artifact_path="",
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        validate_called = []

        def fake_validate(h, p, wid, wf):
            validate_called.append(wid)
            return json.dumps({"workflow_id": wid, "state": "READY_TO_APPLY"})

        from lintgate.testing.platonic_entrypoints import run_platonic_continue

        with patch(
            "mcp_tools._platonic_impl.impl_platonic_converge",
            return_value=json.dumps({"workflow_id": "wf1", "state": "PROFILING"}),
        ) as mock_converge:
            json.loads(
                run_platonic_continue(
                    helpers,
                    str(tmp_path),
                    "wf1",
                    converge_fn=mock_converge,
                    validate_only_fn=fake_validate,
                )
            )

        assert validate_called == ["wf1"]
        mock_converge.assert_not_called()

    def test_platonic_continue_profiling_runs_converge(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="PROFILING",
            step="profile",
            config={
                "max_iterations": 2,
                "target_spec_level": 0.8,
                "target_kill_rate": 0.7,
                "budget_ms": 1234,
                "reconciliation_threshold": 0.75,
                "workflow_dir": ".lintgate/platonic_workflows",
            },
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        validate_called = []

        def fake_validate(h, p, wid, wf):
            validate_called.append(wid)
            return json.dumps({"workflow_id": wid, "state": "READY_TO_APPLY"})

        from lintgate.testing.platonic_entrypoints import run_platonic_continue

        with patch(
            "mcp_tools._platonic_impl.impl_platonic_converge",
            return_value=json.dumps({"workflow_id": "wf1", "state": "VALIDATING"}),
        ) as mock_converge:
            json.loads(
                run_platonic_continue(
                    helpers,
                    str(tmp_path),
                    "wf1",
                    converge_fn=mock_converge,
                    validate_only_fn=fake_validate,
                )
            )

        assert validate_called == []
        mock_converge.assert_called_once()

    def test_validating_sink_reroutes_to_profiling(self, tmp_path):
        """P1 regression: repeated validate_only failures must not keep the
        workflow stuck in VALIDATING forever.  After one validate re-entry
        the state should transition to PROFILING so converge_fn takes over."""
        helpers = _make_helpers(str(tmp_path))

        # Workflow already has a history entry with step=validate (first attempt)
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="VALIDATING",
            step="validate",
            config={
                "max_iterations": 2,
                "target_spec_level": 0.8,
                "target_kill_rate": 0.7,
                "budget_ms": 1234,
                "reconciliation_threshold": 0.75,
                "workflow_dir": ".lintgate/platonic_workflows",
            },
            evidence_summary={"convergence": {}, "health": {}},
            validation_artifact_path="",
            history=[{"state": "VALIDATING", "step": "validate"}],
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        # validate_only_fn returns a PROFILING reroute after detecting history
        from mcp_tools._platonic_impl import impl_platonic_validate_only

        with patch(
            "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
            return_value=json.dumps(
                {
                    "ready_to_apply": False,
                    "gates": {"generated_tests_run": False},
                }
            ),
        ):
            result = json.loads(impl_platonic_validate_only(helpers, str(tmp_path), "wf1", record))

        assert result["state"] == "PROFILING"
        assert result["reason_code"] == "VALIDATION_FAILED_REROUTE"


class TestStagedGeneration:
    """Gap 2: staging_dir param, content hashing, 3-path artifact model."""

    def test_generate_tests_with_staging_dir(self, tmp_path):
        from lintgate.testing.platonic_generation import generate_tests

        staging = tmp_path / "staged"
        staging.mkdir()

        class FakeResult:
            target_test_file = "tests/generated/test_mod.py"
            content = "def test_foo():\n    assert 1 == 1\n"
            functions_covered = ["mod::func"]
            enrichment_sources = []
            manual_contract_candidates = []

        class FakeTarget:
            class evidence:
                function_key = "mod.py::func"

            target_test_file = "tests/generated/test_mod.py"

        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as MockRegen:
            MockRegen.return_value.generate_for_file.return_value = FakeResult()
            result = generate_tests(
                str(tmp_path),
                str(tmp_path / "mod.py"),
                [FakeTarget()],
                staging_dir=str(staging),
            )

        assert result["files_written"] == 1
        assert result["staging_path"] == str(staging / "test_mod.py")
        assert result["content_hash"]
        assert result["generated_path"] == "tests/generated/test_mod.py"
        assert (staging / "test_mod.py").exists()

    def test_staged_artifacts_include_three_paths(self, tmp_path):
        """Verify the 3-path model: generated_path, staging_path, apply_destination."""
        from lintgate.testing.platonic_generation import generate_tests

        staging = tmp_path / "staged"
        staging.mkdir()

        class FakeResult:
            target_test_file = "tests/generated/test_mod.py"
            content = "def test_bar():\n    assert 2 == 2\n"
            functions_covered = ["mod::func"]
            enrichment_sources = []
            manual_contract_candidates = []

        class FakeTarget:
            class evidence:
                function_key = "mod.py::func"

            target_test_file = "tests/generated/test_mod.py"

        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as MockRegen:
            MockRegen.return_value.generate_for_file.return_value = FakeResult()
            result = generate_tests(
                str(tmp_path),
                str(tmp_path / "mod.py"),
                [FakeTarget()],
                staging_dir=str(staging),
            )

        assert "generated_path" in result
        assert "staging_path" in result
        assert "content_hash" in result
        # content_hash is a sha256 hex string
        assert len(result["content_hash"]) == 64

    def test_content_hash_staleness_detected(self, tmp_path):
        """Verify that _apply_staged_artifacts detects hash mismatch."""
        from lintgate.testing.platonic_entrypoints import _apply_staged_artifacts

        staging = tmp_path / "staged"
        staging.mkdir()
        (staging / "test_mod.py").write_text("original content")

        artifacts = [
            {
                "generated_path": "tests/generated/test_mod.py",
                "staging_path": str(staging / "test_mod.py"),
                "apply_destination": "tests/test_mod.py",
                "content_hash": "wrong_hash",
                "source_iteration": 1,
            }
        ]

        actions = _apply_staged_artifacts(str(tmp_path), artifacts)
        assert len(actions) == 1
        assert actions[0]["action"] == "skip"
        assert actions[0]["reason"] == "content_hash_mismatch"


class TestExistingFilePolicy:
    """Gap 5: _has_nontrivial_tests and skip-on-adequate behavior."""

    def test_has_nontrivial_tests_true_for_real_test(self, tmp_path):
        from lintgate.testing.platonic_generation import _has_nontrivial_tests

        f = tmp_path / "test_real.py"
        f.write_text("def test_foo():\n    assert 1 + 1 == 2\n")
        assert _has_nontrivial_tests(str(f)) is True

    def test_has_nontrivial_tests_true_for_pytest_raises(self, tmp_path):
        from lintgate.testing.platonic_generation import _has_nontrivial_tests

        f = tmp_path / "test_raises.py"
        f.write_text(
            "import pytest\n"
            "def test_err():\n"
            "    with pytest.raises(ValueError):\n"
            "        int('x')\n"
        )
        assert _has_nontrivial_tests(str(f)) is True

    def test_has_nontrivial_tests_false_for_stub(self, tmp_path):
        from lintgate.testing.platonic_generation import _has_nontrivial_tests

        f = tmp_path / "test_stub.py"
        f.write_text("def test_placeholder():\n    assert True\n")
        assert _has_nontrivial_tests(str(f)) is False

    def test_generate_tests_skips_existing_nontrivial(self, tmp_path):
        from lintgate.testing.platonic_generation import generate_tests

        staging = tmp_path / "staged"
        staging.mkdir()
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_real():\n    assert 1 == 1\n")

        class FakeResult:
            target_test_file = "tests/generated/test_mod.py"
            content = "new content"
            functions_covered = ["mod::func"]
            enrichment_sources = []
            manual_contract_candidates = []

        class FakeTarget:
            class evidence:
                function_key = "mod.py::func"

            target_test_file = "tests/generated/test_mod.py"

        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as MockRegen:
            MockRegen.return_value.generate_for_file.return_value = FakeResult()
            result = generate_tests(
                str(tmp_path),
                str(tmp_path / "mod.py"),
                [FakeTarget()],
                staging_dir=str(staging),
            )

        assert result["files_written"] == 0
        assert result["skipped_reason"] == "existing_tests_adequate"

    def test_generate_tests_overwrites_empty_stub(self, tmp_path):
        from lintgate.testing.platonic_generation import generate_tests

        staging = tmp_path / "staged"
        staging.mkdir()
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_placeholder():\n    assert True\n")

        class FakeResult:
            target_test_file = "tests/generated/test_mod.py"
            content = "def test_real():\n    assert 1 == 1\n"
            functions_covered = ["mod::func"]
            enrichment_sources = []
            manual_contract_candidates = []

        class FakeTarget:
            class evidence:
                function_key = "mod.py::func"

            target_test_file = "tests/generated/test_mod.py"

        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as MockRegen:
            MockRegen.return_value.generate_for_file.return_value = FakeResult()
            result = generate_tests(
                str(tmp_path),
                str(tmp_path / "mod.py"),
                [FakeTarget()],
                staging_dir=str(staging),
            )

        assert result["files_written"] == 1
        assert (staging / "test_mod.py").exists()


class TestWorkflowScopedApply:
    """Gap 3: apply promotes only staged artifacts, uses workflow validation."""

    def test_apply_promotes_only_staged_artifacts(self, tmp_path):
        import hashlib

        from lintgate.testing.platonic_entrypoints import _apply_staged_artifacts

        staging = tmp_path / "staged"
        staging.mkdir()
        content = "def test_x():\n    assert True\n"
        (staging / "test_mod.py").write_text(content)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        artifacts = [
            {
                "generated_path": "tests/generated/test_mod.py",
                "staging_path": str(staging / "test_mod.py"),
                "apply_destination": "tests/test_mod.py",
                "content_hash": content_hash,
                "source_iteration": 1,
            }
        ]

        actions = _apply_staged_artifacts(str(tmp_path), artifacts)
        assert len(actions) == 1
        assert actions[0]["action"] == "promote"
        dest = tmp_path / "tests" / "test_mod.py"
        assert dest.exists()
        assert dest.read_text() == content

    def test_apply_uses_workflow_validation_path(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        import hashlib

        # Create staged artifact
        staging = tmp_path / ".lintgate" / "platonic_workflows" / "wf1" / "staged"
        staging.mkdir(parents=True)
        content = "def test_x():\n    assert 1 == 1\n"
        (staging / "test_mod.py").write_text(content)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Create workflow validation
        val_path = tmp_path / ".lintgate" / "platonic_workflows" / "wf1" / "validation.json"
        val_path.write_text(json.dumps({"ready_to_apply": True, "gates": {}}))

        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            autopilot_safe=True,
            evidence_summary={"validation": {"ready_to_apply": True}},
            validation_artifact_path=str(val_path),
            staged_artifacts=[
                {
                    "generated_path": "tests/generated/test_mod.py",
                    "staging_path": str(staging / "test_mod.py"),
                    "apply_destination": "tests/test_mod.py",
                    "content_hash": content_hash,
                    "source_iteration": 1,
                }
            ],
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=True))
        assert result["apply_result"]["dry_run"] is True
        assert len(result["apply_result"]["actions"]) == 1
        assert result["apply_result"]["actions"][0]["destination"] == "tests/test_mod.py"

    def test_apply_falls_back_to_legacy(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            autopilot_safe=True,
            evidence_summary={"validation": {"ready_to_apply": True}},
            staged_artifacts=[],  # empty → legacy path
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        with (
            patch(
                "mcp_tools._test_regeneration_apply._load_validation",
                return_value={"ready_to_apply": True},
            ),
            patch(
                "mcp_tools._test_regeneration_apply.impl_rebuild_apply",
                return_value=json.dumps({"dry_run": True, "actions": []}),
            ),
        ):
            result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=True))

        assert result["apply_result"]["dry_run"] is True


class TestPartialSuccessSemantics:
    """Gap 6: READY_TO_APPLY_WITH_REVIEW state."""

    def test_workflow_state_ready_to_apply_with_review(self):
        from lintgate.testing.platonic_outcomes import workflow_state_from_outputs

        validation = {
            "ready_to_apply": False,
            "review_ready_to_apply": True,
            "gates": {
                "generated_tests_run": True,
                "review_share_ok": False,
                "kill_rate_ok": True,
                "zero_kill_ok": True,
                "preserve_tests_pass": True,
                "no_artifact_auto_targets": True,
                "hygiene_ok": True,
            },
        }
        state, reason, msg = workflow_state_from_outputs(
            {"total_targets": 1, "converged": 0},
            {},
            validation,
            [],
        )
        assert state == "READY_TO_APPLY_WITH_REVIEW"
        assert reason == "REVIEW_CEILING_EXCEEDED"

    def test_review_state_denied_when_zero_kill_fails(self):
        """P1 regression: READY_TO_APPLY_WITH_REVIEW must NOT be granted when
        non-review gates are also failing."""
        from lintgate.testing.platonic_outcomes import workflow_state_from_outputs

        validation = {
            "ready_to_apply": False,
            "review_ready_to_apply": False,
            "gates": {
                "generated_tests_run": True,
                "review_share_ok": False,
                "kill_rate_ok": True,
                "zero_kill_ok": False,  # non-review gate failing
            },
        }
        state, _, _ = workflow_state_from_outputs(
            {"total_targets": 1, "converged": 0},
            {},
            validation,
            [],
            iteration_log=[{"tests_generated": 1}],
        )
        assert state != "READY_TO_APPLY_WITH_REVIEW"

    def test_review_state_denied_when_hygiene_fails(self):
        """P1 regression: hygiene_ok=False blocks READY_TO_APPLY_WITH_REVIEW."""
        from lintgate.testing.platonic_outcomes import workflow_state_from_outputs

        validation = {
            "ready_to_apply": False,
            "review_ready_to_apply": False,
            "gates": {
                "generated_tests_run": True,
                "review_share_ok": False,
                "kill_rate_ok": True,
                "zero_kill_ok": True,
                "hygiene_ok": False,  # non-review gate failing
            },
        }
        state, _, _ = workflow_state_from_outputs(
            {"total_targets": 1, "converged": 0},
            {},
            validation,
            [],
            iteration_log=[{"tests_generated": 1}],
        )
        assert state != "READY_TO_APPLY_WITH_REVIEW"

    def test_apply_accepts_ready_to_apply_with_review(self, tmp_path):
        """P1 regression: end-to-end apply with review_ready_to_apply=true
        in persisted validation (NOT ready_to_apply=true, which the outcomes
        branch never emits for this state)."""
        helpers = _make_helpers(str(tmp_path))

        # Validation file has ready_to_apply=False but review_ready_to_apply=True
        val_path = tmp_path / ".lintgate" / "platonic_workflows" / "wf1" / "validation.json"
        val_path.parent.mkdir(parents=True)
        val_path.write_text(
            json.dumps(
                {
                    "ready_to_apply": False,
                    "review_ready_to_apply": True,
                    "gates": {},
                }
            )
        )

        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY_WITH_REVIEW",
            step="validate",
            autopilot_safe=False,
            human_review_required=True,
            evidence_summary={"validation": {"ready_to_apply": False}},
            validation_artifact_path=str(val_path),
            staged_artifacts=[],
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        with patch(
            "mcp_tools._test_regeneration_apply.impl_rebuild_apply",
            return_value=json.dumps({"dry_run": True, "actions": []}),
        ):
            result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=True))

        assert result.get("reason_code") != "STALE_VALIDATION"
        assert result.get("reason_code") != "APPLY_NOT_READY"


class TestValidationDirParams:
    """Gap 4: generated_dir and validation_path flow through gates."""

    def test_validate_uses_generated_dir_param(self, tmp_path):
        """Verify _check_generated_gate uses generated_dir when provided."""
        from mcp_tools._test_regeneration_gates import _check_generated_gate

        custom_dir = tmp_path / "custom_gen"
        custom_dir.mkdir()
        (custom_dir / "test_foo.py").write_text("def test_x():\n    assert 1 == 1\n")

        class FakePlan:
            functions = []

        gates: dict = {}
        ok, gates = _check_generated_gate(
            FakePlan(),
            str(tmp_path),
            gates,
            True,
            generated_dir=str(custom_dir),
        )
        # With no auto targets and generated tests found, should pass
        assert gates["generated_tests_run"] is True


class TestRunMutationSampling:
    @patch("mcp_tools._mutation_impl.run_on_functions_with_tests", return_value=[])
    @patch("mcp_tools._mutation_impl.resolve_function")
    @patch("mcp_tools._mutation_tools_impl._build_mutation_context")
    def test_forwards_generated_test_files(self, mock_ctx, mock_resolve, mock_run):
        mock_resolve.return_value = ("/project/mod.py", None, None)
        mock_ctx.return_value = object()

        result = _run_mutation_sampling(
            "/project",
            "mod.py",
            generated_test_files=["tests/generated/test_mod.py"],
        )

        assert result == []
        mock_ctx.assert_called_once_with(
            "/project",
            "/project/mod.py",
            extra_test_files=["tests/generated/test_mod.py"],
        )


class TestProfilingResumeFromSnapshot:
    """orch_state_snapshot resume: PROFILING continue hydrates state
    and skips completed iterations instead of re-running the full loop."""

    def test_profiling_continue_passes_snapshot_to_converge(self, tmp_path):
        """run_platonic_continue threads orch_state_snapshot when PROFILING."""
        helpers = _make_helpers(str(tmp_path))

        snapshot = {
            "targets": {
                "mod.py::func": {
                    "function_key": "mod.py::func",
                    "source_file": "mod.py",
                    "target_test_file": "tests/generated/test_mod.py",
                    "spec_level": 0.3,
                    "kill_rate": 0.2,
                    "phase": "bulk",
                    "convergence_rate": 0.0,
                    "trajectory": [],
                    "status": "eligible",
                    "halt_reason": "",
                }
            },
            "iteration_count": 1,
            "start_ms": 0.0,
            "elapsed_ms": 100.0,
        }
        staged = [
            {
                "generated_path": "tests/generated/test_mod.py",
                "staging_path": "/tmp/staged/test_mod.py",
                "apply_destination": "tests/test_mod.py",
                "content_hash": "abc123",
                "source_iteration": 1,
            }
        ]

        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="PROFILING",
            step="profile",
            config={
                "max_iterations": 3,
                "target_spec_level": 0.8,
                "target_kill_rate": 0.7,
                "budget_ms": 5000,
                "reconciliation_threshold": 0.7,
                "workflow_dir": ".lintgate/platonic_workflows",
            },
            iterations_completed=1,
            orch_state_snapshot=snapshot,
            staged_artifacts=staged,
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        from lintgate.testing.platonic_entrypoints import run_platonic_continue

        captured_kwargs: list[dict] = []

        def spy_converge(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return json.dumps({"workflow_id": "wf1", "state": "VALIDATING"})

        run_platonic_continue(
            helpers,
            str(tmp_path),
            "wf1",
            converge_fn=spy_converge,
            validate_only_fn=None,
        )

        assert len(captured_kwargs) == 1
        kw = captured_kwargs[0]
        assert kw["orch_state_snapshot"] == snapshot
        assert kw["staged_artifacts_resume"] == staged
        assert kw["iterations_completed"] == 1

    def test_profiling_continue_without_snapshot_omits_resume_kwargs(self, tmp_path):
        """When no snapshot, converge_fn receives no resume kwargs."""
        helpers = _make_helpers(str(tmp_path))

        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="PROFILING",
            step="profile",
            config={
                "max_iterations": 2,
                "target_spec_level": 0.8,
                "target_kill_rate": 0.7,
                "budget_ms": 1234,
                "reconciliation_threshold": 0.75,
                "workflow_dir": ".lintgate/platonic_workflows",
            },
            orch_state_snapshot={},  # empty = no snapshot
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        from lintgate.testing.platonic_entrypoints import run_platonic_continue

        captured_kwargs: list[dict] = []

        def spy_converge(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return json.dumps({"workflow_id": "wf1", "state": "VALIDATING"})

        run_platonic_continue(
            helpers,
            str(tmp_path),
            "wf1",
            converge_fn=spy_converge,
            validate_only_fn=None,
        )

        kw = captured_kwargs[0]
        assert "orch_state_snapshot" not in kw
        assert "staged_artifacts_resume" not in kw

    def test_converge_resumes_from_snapshot_skips_completed_iterations(self, tmp_path):
        """impl_platonic_converge hydrates orch_state and starts from
        iterations_completed+1, avoiding re-running completed iterations."""
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.3,
                        "phase": "bulk",
                        "sigma": 5,
                        "regime": "A",
                        "testability_score": 0.9,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": "",
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }

        # Snapshot with 2 completed iterations (iteration_count=2 means 2 update_target calls)
        snapshot = {
            "targets": {
                "mod.py::func": {
                    "function_key": "mod.py::func",
                    "source_file": "mod.py",
                    "target_test_file": "tests/generated/test_mod.py",
                    "spec_level": 0.3,
                    "kill_rate": 0.2,
                    "phase": "bulk",
                    "convergence_rate": 0.0,
                    "trajectory": [
                        {
                            "iteration": 1,
                            "spec_level": 0.2,
                            "kill_rate": 0.1,
                            "delta_spec": 0.2,
                            "delta_kill": 0.1,
                            "phase": "bulk",
                            "timestamp_ms": 0.0,
                        },
                        {
                            "iteration": 2,
                            "spec_level": 0.3,
                            "kill_rate": 0.2,
                            "delta_spec": 0.1,
                            "delta_kill": 0.1,
                            "phase": "bulk",
                            "timestamp_ms": 0.0,
                        },
                    ],
                    "status": "eligible",
                    "halt_reason": "",
                }
            },
            "iteration_count": 2,
            "start_ms": 0.0,
            "elapsed_ms": 200.0,
        }

        iteration_numbers_seen: list[int] = []

        original_run_sampling = (
            _run_mutation_sampling.__wrapped__
            if hasattr(_run_mutation_sampling, "__wrapped__")
            else None
        )

        def tracking_mutation_sampling(*args, **kwargs):
            return [{"function_key": "mod.py::func", "survival_rate": 0.0}]

        def tracking_generate(*args, **kwargs):
            return {"files_written": 0}

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                side_effect=tracking_mutation_sampling,
            ) as mock_sampling,
            patch("mcp_tools._platonic_impl._generate_tests", side_effect=tracking_generate),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": True, "gates": {}}),
            ),
        ):
            result = json.loads(
                impl_platonic_converge(
                    helpers,
                    str(tmp_path),
                    "mod.py",
                    max_iterations=3,
                    orch_state_snapshot=snapshot,
                    staged_artifacts_resume=[],
                    iterations_completed=2,
                )
            )

        # With max_iterations=3 and iterations_completed=2, only iteration 3 runs
        assert mock_sampling.call_count == 1
        # Workflow should have persisted with iterations_completed=3 (2 prior + 1 new)
        wf = load_workflow(str(tmp_path), ".lintgate/platonic_workflows", result["workflow_id"])
        assert wf is not None
        assert wf.iterations_completed == 3

    def test_converge_resume_restores_staged_artifacts(self, tmp_path):
        """Staged artifacts from prior iterations are carried forward on resume."""
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.3,
                        "phase": "bulk",
                        "sigma": 5,
                        "regime": "A",
                        "testability_score": 0.9,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": "",
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }

        snapshot = {
            "targets": {
                "mod.py::func": {
                    "function_key": "mod.py::func",
                    "source_file": "mod.py",
                    "target_test_file": "tests/generated/test_mod.py",
                    "spec_level": 0.3,
                    "kill_rate": 0.2,
                    "phase": "bulk",
                    "convergence_rate": 0.0,
                    "trajectory": [],
                    "status": "eligible",
                    "halt_reason": "",
                }
            },
            "iteration_count": 1,
            "start_ms": 0.0,
            "elapsed_ms": 100.0,
        }
        prior_staged = [
            {
                "generated_path": "tests/generated/test_mod.py",
                "staging_path": str(tmp_path / "staged" / "test_mod.py"),
                "apply_destination": "tests/test_mod.py",
                "content_hash": "abc123",
                "source_iteration": 1,
            }
        ]

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                return_value=[{"function_key": "mod.py::func", "survival_rate": 0.0}],
            ),
            patch(
                "mcp_tools._platonic_impl._generate_tests",
                return_value={"files_written": 0},
            ),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": True, "gates": {}}),
            ),
        ):
            result = json.loads(
                impl_platonic_converge(
                    helpers,
                    str(tmp_path),
                    "mod.py",
                    max_iterations=2,
                    orch_state_snapshot=snapshot,
                    staged_artifacts_resume=prior_staged,
                    iterations_completed=1,
                )
            )

        wf = load_workflow(str(tmp_path), ".lintgate/platonic_workflows", result["workflow_id"])
        assert wf is not None
        # Prior staged artifact should still be present
        assert len(wf.staged_artifacts) >= 1
        assert wf.staged_artifacts[0]["content_hash"] == "abc123"

    def test_converge_resume_profiles_staged_artifact_paths(self, tmp_path):
        """Resumed profiling should point mutation sampling at staged files."""
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.3,
                        "phase": "bulk",
                        "sigma": 5,
                        "regime": "A",
                        "testability_score": 0.9,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": "",
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }
        staged_dir = tmp_path / ".lintgate" / "platonic_workflows" / "wf1" / "staged"
        staged_dir.mkdir(parents=True)
        staged_file = staged_dir / "test_mod.py"
        staged_file.write_text("def test_x():\n    assert 1 == 1\n")
        snapshot = {
            "targets": {
                "mod.py::func": {
                    "function_key": "mod.py::func",
                    "source_file": "mod.py",
                    "target_test_file": "tests/generated/test_mod.py",
                    "spec_level": 0.3,
                    "kill_rate": 0.2,
                    "phase": "bulk",
                    "convergence_rate": 0.0,
                    "trajectory": [],
                    "status": "eligible",
                    "halt_reason": "",
                }
            },
            "iteration_count": 1,
            "start_ms": 0.0,
            "elapsed_ms": 100.0,
        }
        prior_staged = [
            {
                "generated_path": "tests/generated/test_mod.py",
                "staging_path": str(staged_file),
                "apply_destination": "tests/test_mod.py",
                "content_hash": "abc123",
                "source_iteration": 1,
            }
        ]

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                return_value=[{"function_key": "mod.py::func", "survival_rate": 0.0}],
            ) as mock_sampling,
            patch(
                "mcp_tools._platonic_impl._generate_tests",
                return_value={"files_written": 0},
            ),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": True, "gates": {}}),
            ),
        ):
            impl_platonic_converge(
                helpers,
                str(tmp_path),
                "mod.py",
                max_iterations=2,
                orch_state_snapshot=snapshot,
                staged_artifacts_resume=prior_staged,
                iterations_completed=1,
            )

        profiled_files = mock_sampling.call_args.kwargs["generated_test_files"]
        assert str(staged_file) in profiled_files
        assert "tests/generated/test_mod.py" not in profiled_files

    def test_converge_fresh_start_ignores_snapshot_params(self, tmp_path):
        """When orch_state_snapshot is None, converge uses init_from_targets."""
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.1,
                        "phase": "bulk",
                        "sigma": 5,
                        "regime": "A",
                        "testability_score": 0.9,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": "",
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                return_value=[{"function_key": "mod.py::func", "survival_rate": 0.5}],
            ) as mock_sampling,
            patch(
                "mcp_tools._platonic_impl._generate_tests",
                return_value={"files_written": 0},
            ),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": False, "gates": {}}),
            ),
        ):
            impl_platonic_converge(
                helpers,
                str(tmp_path),
                "mod.py",
                max_iterations=2,
                # No snapshot params → fresh start
            )

        # Fresh start runs all iterations (1 and 2)
        assert mock_sampling.call_count == 2


class TestTerminalStatesInFrozenset:
    def test_existing_tests_sufficient_is_terminal(self):
        assert "EXISTING_TESTS_SUFFICIENT" in TERMINAL_STATES

    def test_plateau_no_generation_is_terminal(self):
        assert "PLATEAU_NO_GENERATION" in TERMINAL_STATES


class TestWorkflowStateExistingTestsSufficient:
    def test_all_iterations_skipped_existing(self):
        state, code, msg = workflow_state_from_outputs(
            conv_summary={"total_targets": 1, "converged": 0, "targets": []},
            health_data={"vetoes": {}},
            validation={},
            decompose_targets=[],
            iteration_log=[
                {"skipped_reason": "existing_tests_adequate", "tests_generated": 0},
                {"skipped_reason": "existing_tests_adequate", "tests_generated": 0},
            ],
        )
        assert state == "EXISTING_TESTS_SUFFICIENT"
        assert code == "EXISTING_TESTS_SUFFICIENT"

    def test_mixed_iterations_not_sufficient(self):
        """If any iteration did NOT skip for existing_tests_adequate, not this state."""
        state, _, _ = workflow_state_from_outputs(
            conv_summary={"total_targets": 1, "converged": 0, "targets": []},
            health_data={"vetoes": {}},
            validation={},
            decompose_targets=[],
            iteration_log=[
                {"skipped_reason": "existing_tests_adequate", "tests_generated": 0},
                {"tests_generated": 0},
            ],
        )
        assert state == "PLATEAU_NO_GENERATION"


class TestWorkflowStatePlateauNoGeneration:
    def test_no_tests_generated_across_iterations(self):
        state, code, msg = workflow_state_from_outputs(
            conv_summary={"total_targets": 1, "converged": 0, "targets": []},
            health_data={"vetoes": {}},
            validation={},
            decompose_targets=[],
            iteration_log=[
                {"tests_generated": 0},
                {"tests_generated": 0},
            ],
        )
        assert state == "PLATEAU_NO_GENERATION"
        assert code == "PLATEAU_NO_GENERATION"

    def test_some_tests_generated_not_plateau(self):
        state, _, _ = workflow_state_from_outputs(
            conv_summary={"total_targets": 1, "converged": 0, "targets": []},
            health_data={"vetoes": {}},
            validation={},
            decompose_targets=[],
            iteration_log=[
                {"tests_generated": 1},
                {"tests_generated": 0},
            ],
        )
        assert state == "VALIDATING"


class TestConvergeExistingTestsAdequate:
    """Test that existing_tests_adequate skips produce reference artifacts and terminal state."""

    def test_converge_existing_tests_adequate_produces_terminal(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        src = tmp_path / "mod.py"
        src.write_text("def func(x):\n    return x + 1\n")

        fake_ev = _FakeEvidence(function_key="mod.py::func", source_file="mod.py")
        fake_cls = _FakeClassification(
            evidence=fake_ev,
            function_key="mod.py::func",
            target_test_file="tests/generated/test_mod.py",
        )
        # Use low spec_level so the orchestrator doesn't auto-converge
        assessment = {
            "spec_result": _FakeSpecResult(
                functions={
                    "mod.py::func": {
                        "specification_level": 0.2,
                        "phase": "bulk",
                        "sigma": 10,
                        "regime": "A",
                        "testability_score": 0.9,
                    }
                }
            ),
            "auto_targets": [fake_cls],
            "decompose_targets": [],
            "primary_target": "mod.py::func",
            "manifest_path": "",
            "summary": {
                "total_functions": 1,
                "strategy_distribution": {"auto_generate_unit": 1},
            },
        }

        def gen_existing_tests_skip(*args, **kwargs):
            return {
                "files_written": 0,
                "skipped_reason": "existing_tests_adequate",
                "generated_path": "tests/generated/test_mod.py",
                "canonical_path": str(tmp_path / "tests" / "generated" / "test_mod.py"),
            }

        with (
            patch("lintgate.testing.platonic_selection.assess_file", return_value=assessment),
            patch("mcp_tools._platonic_impl._load_mutation_cache", return_value=None),
            patch(
                "mcp_tools._platonic_impl._run_mutation_sampling",
                # High survival = low kill rate, so orchestrator won't converge
                return_value=[{"function_key": "mod.py::func", "survival_rate": 0.8}],
            ),
            patch(
                "mcp_tools._platonic_impl._generate_tests",
                side_effect=gen_existing_tests_skip,
            ),
            patch(
                "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
                return_value=json.dumps({"ready_to_apply": False, "gates": {}}),
            ),
        ):
            result = json.loads(
                impl_platonic_converge(
                    helpers,
                    str(tmp_path),
                    "mod.py",
                    max_iterations=2,
                )
            )

        assert result["state"] in ("EXISTING_TESTS_SUFFICIENT", "PLATEAU_NO_GENERATION")
        wf = load_workflow(str(tmp_path), ".lintgate/platonic_workflows", result["workflow_id"])
        assert wf is not None
        assert wf.is_terminal()
        # Should have reference-only artifacts
        ref_artifacts = [a for a in wf.staged_artifacts if a.get("reference_only")]
        assert len(ref_artifacts) >= 1
        assert ref_artifacts[0]["generated_path"] == "tests/generated/test_mod.py"
        # Should NOT suggest platonic_continue
        assert result.get("primary_next_action") != "platonic_continue"


class TestApplyVacuousDetection:
    """P1-1: Apply with zero promoted artifacts should FAIL, not CONVERGED."""

    def test_apply_fails_when_all_artifacts_skipped(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            autopilot_safe=True,
            evidence_summary={"validation": {"ready_to_apply": True}},
            staged_artifacts=[
                {
                    "generated_path": "tests/generated/test_mod.py",
                    "staging_path": "/nonexistent/staged/test_mod.py",
                    "apply_destination": "tests/generated/test_mod.py",
                    "content_hash": "abc123",
                    "source_iteration": 1,
                }
            ],
            validation_artifact_path=str(tmp_path / ".lintgate" / "validation.json"),
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)
        # Write validation file
        val_dir = tmp_path / ".lintgate"
        val_dir.mkdir(parents=True, exist_ok=True)
        (val_dir / "validation.json").write_text(json.dumps({"ready_to_apply": True}))

        result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=False))

        assert result["state"] == "FAILED"
        assert result["reason_code"] == "APPLY_VACUOUS"
        wf = load_workflow(str(tmp_path), ".lintgate/platonic_workflows", "wf1")
        assert wf is not None
        assert wf.state == "FAILED"

    def test_apply_dry_run_not_blocked_by_vacuous_check(self, tmp_path):
        """Dry run should still preview even if nothing would promote."""
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            autopilot_safe=True,
            evidence_summary={"validation": {"ready_to_apply": True}},
            staged_artifacts=[
                {
                    "generated_path": "tests/generated/test_mod.py",
                    "staging_path": "/nonexistent/staged/test_mod.py",
                    "apply_destination": "tests/generated/test_mod.py",
                    "content_hash": "abc123",
                    "source_iteration": 1,
                }
            ],
            validation_artifact_path=str(tmp_path / ".lintgate" / "validation.json"),
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)
        val_dir = tmp_path / ".lintgate"
        val_dir.mkdir(parents=True, exist_ok=True)
        (val_dir / "validation.json").write_text(json.dumps({"ready_to_apply": True}))

        result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=True))

        # Dry run returns preview, not failure
        assert result["state"] == "READY_TO_APPLY"

    def test_legacy_apply_path_not_blocked(self, tmp_path):
        """Legacy apply (no staged_artifacts) should not trigger vacuous check."""
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="READY_TO_APPLY",
            step="validate",
            autopilot_safe=True,
            evidence_summary={"validation": {"ready_to_apply": True}},
            # No staged_artifacts → legacy path
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        with (
            patch(
                "mcp_tools._test_regeneration_apply._load_validation",
                return_value={"ready_to_apply": True},
            ),
            patch(
                "mcp_tools._test_regeneration_apply.impl_rebuild_apply",
                return_value=json.dumps({"dry_run": False, "actions": [{"action": "promote"}]}),
            ),
        ):
            result = json.loads(impl_platonic_apply(helpers, str(tmp_path), "wf1", dry_run=False))

        assert result["state"] == "CONVERGED"


class TestApplyQuarantineScoping:
    """P1-2: Apply should only quarantine files owned by this workflow."""

    def test_quarantine_scoped_to_workflow_files(self, tmp_path):
        from lintgate.testing.platonic_entrypoints import _build_staged_apply_actions

        # Create a staged artifact
        staged_dir = tmp_path / "staged"
        staged_dir.mkdir()
        staged_file = staged_dir / "test_mod.py"
        staged_file.write_text("def test_x(): assert True\n")

        staged_artifacts = [
            {
                "generated_path": "tests/generated/test_mod.py",
                "staging_path": str(staged_file),
                "apply_destination": "tests/generated/test_mod.py",
                "content_hash": "",
                "source_iteration": 1,
            }
        ]

        # Mock manifest with quarantine for an UNRELATED file
        mock_plan = type(
            "Plan",
            (),
            {
                "quarantine_test_files": ["tests/test_unrelated.py", "tests/generated/test_mod.py"],
                "functions": [],
            },
        )()

        with (
            patch(
                "lintgate.specification.test_regeneration_strategy.load_manifest",
                return_value=mock_plan,
            ),
            patch(
                "mcp_tools._test_regeneration_apply._quarantine_files",
                return_value=[],
            ) as mock_quarantine,
        ):
            _build_staged_apply_actions(str(tmp_path), staged_artifacts, dry_run=True)

        # _quarantine_files should have been called with filtered quarantine list
        called_plan = mock_quarantine.call_args[0][0]
        assert "tests/test_unrelated.py" not in called_plan.quarantine_test_files
        assert "tests/generated/test_mod.py" in called_plan.quarantine_test_files


class TestValidateOnlyTerminalStates:
    """P2-2: validate_only should not suggest continue for terminal states."""

    def test_failed_validation_does_not_suggest_continue(self, tmp_path):
        helpers = _make_helpers(str(tmp_path))
        record = PlatonicWorkflowRecord(
            workflow_id="wf1",
            scope="file",
            target="mod.py",
            rel_file="mod.py",
            state="VALIDATING",
            step="validate",
            staged_artifacts=[
                {
                    "generated_path": "tests/generated/test_mod.py",
                    "staging_path": str(tmp_path / "staged" / "test_mod.py"),
                    "apply_destination": "tests/generated/test_mod.py",
                    "content_hash": "",
                    "source_iteration": 1,
                }
            ],
            evidence_summary={
                "convergence": {"total_targets": 1, "converged": 0, "targets": []},
                "health": {"vetoes": {}},
            },
        )
        save_workflow(str(tmp_path), ".lintgate/platonic_workflows", record)

        with patch(
            "mcp_tools._test_regeneration_gates.impl_rebuild_validate",
            return_value=json.dumps({"error": "validation_boom"}),
        ):
            result = json.loads(impl_platonic_continue(helpers, str(tmp_path), "wf1"))

        assert result["state"] == "FAILED"
        assert result.get("primary_next_action") != "platonic_continue"
        assert result.get("autopilot_safe") is not True


class TestExhaustedSnapshotResume:
    """P2-1: Exhausted PROFILING snapshot should not loop back to ASSESSING."""

    def test_zero_iteration_with_all_halted_targets_is_terminal(self):
        state, code, _ = workflow_state_from_outputs(
            conv_summary={
                "total_targets": 1,
                "converged": 0,
                "targets": [{"function_key": "mod.py::func", "status": "halted"}],
            },
            health_data={"vetoes": {}},
            validation={},
            decompose_targets=[],
            iteration_log=[],  # zero iterations
        )
        assert state == "PLATEAU_NO_GENERATION"
        assert code == "ALL_TARGETS_HALTED"

    def test_zero_iteration_with_mixed_targets_returns_assessing(self):
        """If some targets are still eligible, ASSESSING is correct."""
        state, _, _ = workflow_state_from_outputs(
            conv_summary={
                "total_targets": 2,
                "converged": 0,
                "targets": [
                    {"function_key": "mod.py::f1", "status": "halted"},
                    {"function_key": "mod.py::f2", "status": "eligible"},
                ],
            },
            health_data={"vetoes": {}},
            validation={},
            decompose_targets=[],
            iteration_log=[],
        )
        assert state == "ASSESSING"

    def test_zero_iteration_all_converged_returns_converged(self):
        """All converged in summary → CONVERGED via the existing check."""
        state, _, _ = workflow_state_from_outputs(
            conv_summary={
                "total_targets": 1,
                "converged": 1,
                "targets": [{"function_key": "mod.py::func", "status": "converged"}],
            },
            health_data={"vetoes": {}},
            validation={},
            decompose_targets=[],
            iteration_log=[],
        )
        assert state == "CONVERGED"
