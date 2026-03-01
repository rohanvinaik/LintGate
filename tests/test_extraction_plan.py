"""Tests for B1: Extraction Plan Builder."""

from __future__ import annotations

import ast

from lintgate.convergence.evidence import (
    Actionability,
    ConvergenceResult,
    LensEvidence,
    LensKind,
)
from lintgate.convergence.extraction_plan import (
    ExtractionPlan,
    ExtractionStep,
    build_batch_extraction_plan,
    build_extraction_plan,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_convergence(
    target: str = "module.py::my_func",
    net: float = 0.8,
    actionability: Actionability = Actionability.EXTRACT,
    evidence: list[LensEvidence] | None = None,
    target_type: str = "function",
    split_proposals: list[dict] | None = None,
) -> ConvergenceResult:
    ev = evidence or []
    supporting = sorted(
        set(e.lens for e in ev if e.signal == "support"), key=lambda l: l.value
    )
    opposing = sorted(
        set(e.lens for e in ev if e.signal == "oppose"), key=lambda l: l.value
    )
    return ConvergenceResult(
        target=target,
        support_prob=net,
        oppose_prob=0.0,
        net_confidence=net,
        supporting_lenses=supporting,
        opposing_lenses=opposing,
        actionability=actionability,
        evidence=ev,
        target_type=target_type,
        split_proposals=split_proposals or [],
    )


def _dep_clustering_evidence(
    target: str,
    proposed_name: str = "_compute_result",
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    lines: tuple[int, int] | None = None,
    cc_reduction: int = 8,
    kind: str = "extract_function",
    basis: list[str] | None = None,
    action: str = "",
) -> LensEvidence:
    raw: dict = {
        "prescription": f"Extract {proposed_name}",
        "proposed_name": proposed_name,
        "inputs": inputs if inputs is not None else ["x", "y"],
        "outputs": outputs if outputs is not None else ["result"],
        "lines": list(lines) if lines else [10, 25],
        "expected_delta": {"cc_reduction": cc_reduction},
        "kind": kind,
        "basis": basis or ["variable_clustering"],
        "action": action or f"Extract lines into `{proposed_name}()`",
    }
    return LensEvidence(
        lens=LensKind.DEP_CLUSTERING,
        target=target,
        confidence=0.7,
        signal="support",
        detail=f"Extractable block: {proposed_name}",
        raw=raw,
    )


def _handler_evidence(
    target: str,
    handler_name: str = "handle_request",
    proposed_name: str = "_impl_handle_request",
    inputs: list[str] | None = None,
) -> LensEvidence:
    raw: dict = {
        "prescription": f"Extract handler {handler_name}",
        "proposed_name": proposed_name,
        "inputs": inputs or ["engine", "helpers"],
        "outputs": [],
        "lines": [45, 82],
        "expected_delta": {"cc_reduction": 5},
        "kind": "extract_function",
        "basis": ["nested_handler", "closure_analysis"],
        "action": f"Extract nested handler `{handler_name}` to module-level `{proposed_name}()`",
    }
    return LensEvidence(
        lens=LensKind.DEP_CLUSTERING,
        target=target,
        confidence=0.75,
        signal="support",
        detail=f"Extractable block: {handler_name}",
        raw=raw,
    )


def _fan_in_oppose(target: str, count: int = 8) -> LensEvidence:
    return LensEvidence(
        lens=LensKind.FAN_IN,
        target=target,
        confidence=min(count / 10, 1.0),
        signal="oppose",
        detail=f"Fan-in={count} (high, extraction risky)",
        raw={"fan_in": count},
    )


def _cochange_oppose(target: str, strength: float = 0.7) -> LensEvidence:
    return LensEvidence(
        lens=LensKind.COCHANGE,
        target=target,
        confidence=strength,
        signal="oppose",
        detail=f"Coupled with other_module.py ({strength:.2f})",
        raw={
            "file_a": target,
            "file_b": "other_module.py",
            "coupling_strength": strength,
        },
    )


def _purity_support(target: str) -> LensEvidence:
    return LensEvidence(
        lens=LensKind.PURITY,
        target=target,
        confidence=0.9,
        signal="support",
        detail="Pure function, hints=['cacheable']",
        raw={"confidence": 0.9, "hints": ["cacheable"]},
    )


def _mutation_support(target: str, rate: float = 0.6) -> LensEvidence:
    return LensEvidence(
        lens=LensKind.MUTATION,
        target=target,
        confidence=rate,
        signal="support",
        detail=f"Survival {rate:.0%} across 3 categories",
        raw={
            "survival_rate": rate,
            "survived_categories": ["arithmetic", "comparison", "boundary"],
        },
    )


# ── ExtractionStep tests ──────────────────────────────────────────────


class TestExtractionStep:
    def test_to_dict(self):
        step = ExtractionStep(
            order=1,
            action="create_function",
            target="my_func",
            detail={"parameters": ["x", "y"]},
            rationale="Test rationale",
        )
        d = step.to_dict()
        assert d["order"] == 1
        assert d["action"] == "create_function"
        assert d["target"] == "my_func"
        assert d["detail"]["parameters"] == ["x", "y"]
        assert d["rationale"] == "Test rationale"

    def test_default_fields(self):
        step = ExtractionStep(order=1, action="create_function", target="f")
        assert step.detail == {}
        assert step.rationale == ""


# ── ExtractionPlan tests ──────────────────────────────────────────────


class TestExtractionPlan:
    def test_to_dict(self):
        conv = _make_convergence()
        plan = ExtractionPlan(
            source_function="my_func",
            source_file="module.py",
            steps=[ExtractionStep(order=1, action="create_function", target="f")],
            estimated_impact={"CC_reduction": 10},
            warnings=["Warning: high fan-in"],
            convergence=conv,
        )
        d = plan.to_dict()
        assert d["source_function"] == "my_func"
        assert d["source_file"] == "module.py"
        assert len(d["steps"]) == 1
        assert d["estimated_impact"]["CC_reduction"] == 10
        assert len(d["warnings"]) == 1
        assert "convergence" in d

    def test_to_dict_no_convergence(self):
        plan = ExtractionPlan(source_function="f", source_file="m.py")
        d = plan.to_dict()
        assert "convergence" not in d


# ── Block extraction plan tests ───────────────────────────────────────


class TestBlockExtractionPlan:
    def test_two_extractable_blocks_produce_ordered_steps(self):
        """Synthetic function with 2 extractable blocks → 6+ ordered steps."""
        target = "module.py::process_data"
        evidence = [
            _dep_clustering_evidence(
                target, "_compute_stats", ["data"], ["stats"], (10, 20), cc_reduction=5
            ),
            _dep_clustering_evidence(
                target,
                "_format_output",
                ["stats"],
                ["output"],
                (25, 35),
                cc_reduction=3,
            ),
            _purity_support(target),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert plan.source_function == target
        assert plan.source_file == "module.py"
        assert len(plan.steps) >= 6

        # Verify step ordering
        actions = [s.action for s in plan.steps]
        # create_function steps come first
        cf_indices = [i for i, a in enumerate(actions) if a == "create_function"]
        eb_indices = [i for i, a in enumerate(actions) if a == "extract_body"]
        uc_indices = [i for i, a in enumerate(actions) if a == "update_callers"]
        mt_indices = [i for i, a in enumerate(actions) if a == "migrate_tests"]

        assert cf_indices, "Must have create_function steps"
        assert eb_indices, "Must have extract_body steps"
        assert uc_indices, "Must have update_callers step"
        assert mt_indices, "Must have migrate_tests step"

        # Ordering: all create_function before update_callers, update_callers before migrate_tests
        assert max(cf_indices) < min(uc_indices)
        assert max(uc_indices) < min(mt_indices)

    def test_step_details_contain_parameters(self):
        """Create_function steps include parameter and output info."""
        target = "module.py::my_func"
        evidence = [
            _dep_clustering_evidence(
                target, "_helper", ["a", "b"], ["result"], (5, 15)
            ),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        cf_steps = [s for s in plan.steps if s.action == "create_function"]
        assert cf_steps
        detail = cf_steps[0].detail
        assert "parameters" in detail
        assert "a" in detail["parameters"]
        assert "b" in detail["parameters"]
        assert detail["proposed_name"] == "_helper"

    def test_extract_body_references_source_lines(self):
        """Extract_body steps reference the source line range."""
        target = "module.py::my_func"
        evidence = [
            _dep_clustering_evidence(target, "_helper", lines=(10, 25)),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        eb_steps = [s for s in plan.steps if s.action == "extract_body"]
        assert eb_steps
        assert eb_steps[0].detail["source_lines"] == [10, 25]


# ── Handler extraction plan tests ─────────────────────────────────────


class TestHandlerExtractionPlan:
    def test_handler_extraction_steps(self):
        """Handler prescriptions generate extract_handler steps."""
        target = "mcp_tools.py::register"
        evidence = [
            _handler_evidence(target, "run_sampling", "_impl_run_sampling", ["engine"]),
            _handler_evidence(
                target, "run_full", "_impl_run_full", ["engine", "state"]
            ),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="mcp_tools.py")

        handler_steps = [s for s in plan.steps if s.action == "extract_handler"]
        assert len(handler_steps) == 2

        # Each handler step has proper detail
        for step in handler_steps:
            assert "captured_variables" in step.detail
            assert "proposed_name" in step.detail
            assert "destination" in step.detail
            assert step.detail["destination"] == "module_level"

    def test_handler_plan_ends_with_update_and_migrate(self):
        """Handler plans end with update_callers and migrate_tests."""
        target = "tools.py::register"
        evidence = [_handler_evidence(target)]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="tools.py")

        actions = [s.action for s in plan.steps]
        assert "update_callers" in actions
        assert "migrate_tests" in actions
        # migrate_tests is last
        assert actions[-1] == "migrate_tests"

    def test_handler_no_captured_writes_safe_rationale(self):
        """Handler with no captured writes → safe extraction rationale."""
        target = "tools.py::register"
        evidence = [_handler_evidence(target, inputs=["engine"])]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="tools.py")

        handler_steps = [s for s in plan.steps if s.action == "extract_handler"]
        assert handler_steps
        assert "safe extraction" in handler_steps[0].rationale.lower()


# ── Warning generation tests ──────────────────────────────────────────


class TestWarningGeneration:
    def test_high_fan_in_warning(self):
        """Fan-in >= 5 generates a warning."""
        target = "module.py::my_func"
        evidence = [
            _purity_support(target),
            _fan_in_oppose(target, count=12),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert any("12 modules import" in w for w in plan.warnings)

    def test_cochange_warning(self):
        """Co-change coupling > 0.6 generates a warning."""
        target = "module.py::my_func"
        evidence = [
            _purity_support(target),
            _cochange_oppose(target, strength=0.75),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert any("co-change coupling" in w.lower() for w in plan.warnings)
        assert any("other_module.py" in w for w in plan.warnings)

    def test_no_warnings_without_opposing(self):
        """No opposing evidence → no warnings."""
        target = "module.py::my_func"
        evidence = [_purity_support(target)]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert plan.warnings == []

    def test_low_fan_in_no_warning(self):
        """Fan-in < 5 → no warning (below threshold)."""
        target = "module.py::my_func"
        evidence = [
            _purity_support(target),
            LensEvidence(
                lens=LensKind.FAN_IN,
                target=target,
                confidence=0.3,
                signal="oppose",
                detail="Fan-in=3",
                raw={"fan_in": 3},
            ),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert not any("modules import" in w for w in plan.warnings)


# ── Estimated impact tests ────────────────────────────────────────────


class TestEstimatedImpact:
    def test_cc_reduction_from_prescriptions(self):
        """CC reduction is summed from prescription expected_delta."""
        target = "module.py::my_func"
        evidence = [
            _dep_clustering_evidence(target, "_a", cc_reduction=5),
            _dep_clustering_evidence(
                target, "_b", cc_reduction=8, inputs=["z"], lines=(30, 40)
            ),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert plan.estimated_impact["CC_reduction"] == 13

    def test_fan_in_change(self):
        """Fan-in change reflects opposing fan-in count."""
        target = "module.py::my_func"
        evidence = [
            _purity_support(target),
            _fan_in_oppose(target, count=7),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert plan.estimated_impact["fan_in_change"] == -7

    def test_test_count_needed(self):
        """Test count equals number of extracted blocks."""
        target = "module.py::my_func"
        evidence = [
            _dep_clustering_evidence(target, "_a", lines=(5, 10)),
            _dep_clustering_evidence(target, "_b", inputs=["z"], lines=(15, 25)),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        assert plan.estimated_impact["test_count_needed"] == 2

    def test_handler_line_count_delta(self):
        """Handler extraction reports positive line_count_delta."""
        target = "tools.py::register"
        evidence = [
            _handler_evidence(target, "h1", "_impl_h1"),
            _handler_evidence(target, "h2", "_impl_h2"),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="tools.py")

        assert plan.estimated_impact["line_count_delta"] > 0
        assert "line_count_explanation" in plan.estimated_impact


# ── Pure function extraction tests ────────────────────────────────────


class TestPureFunctionPlan:
    def test_pure_function_generic_plan(self):
        """Pure function with no prescriptions → generic extraction steps."""
        target = "module.py::compute"
        evidence = [_purity_support(target)]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        actions = [s.action for s in plan.steps]
        assert "create_function" in actions
        assert "extract_body" in actions
        assert "update_callers" in actions
        assert "migrate_tests" in actions

    def test_pure_function_no_fan_in_no_update_imports(self):
        """Pure function without fan-in opposing evidence → no update_imports step."""
        target = "module.py::compute"
        evidence = [_purity_support(target)]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        actions = [s.action for s in plan.steps]
        assert "update_imports" not in actions


# ── Step ordering tests ───────────────────────────────────────────────


class TestStepOrdering:
    def test_canonical_order_maintained(self):
        """Steps follow canonical order even with mixed evidence."""
        target = "module.py::process"
        evidence = [
            _dep_clustering_evidence(target, "_helper", lines=(5, 15)),
            _fan_in_oppose(target, count=6),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        actions = [s.action for s in plan.steps]
        # Verify ordering constraints
        for i, action in enumerate(actions):
            for j, later_action in enumerate(actions[i + 1 :], i + 1):
                _assert_not_reversed(action, later_action)

    def test_orders_are_sequential(self):
        """Step order numbers are sequential starting from 1."""
        target = "module.py::f"
        evidence = [_purity_support(target)]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, source_file="module.py")

        orders = [s.order for s in plan.steps]
        assert orders == list(range(1, len(orders) + 1))


def _assert_not_reversed(earlier: str, later: str) -> None:
    """Assert that `later` action doesn't precede `earlier` in canonical order."""
    priority = {
        "create_function": 1,
        "extract_body": 2,
        "extract_handler": 3,
        "create_module": 4,
        "update_callers": 5,
        "update_imports": 6,
        "migrate_tests": 7,
    }
    e_pri = priority.get(earlier, 99)
    l_pri = priority.get(later, 99)
    assert l_pri >= e_pri, (
        f"'{later}' should not precede '{earlier}' in canonical order"
    )


# ── File-level split tests ────────────────────────────────────────────


class TestFileLevelSplit:
    def test_file_split_adds_create_module(self):
        """File-level SPLIT actionability adds create_module step."""
        target = "big_module.py"
        evidence = [
            LensEvidence(
                lens=LensKind.COHESION,
                target=target,
                confidence=0.8,
                signal="support",
                detail="File cohesion=0.2, components=4",
                raw={"score": 0.2, "component_count": 4},
            ),
        ]
        conv = _make_convergence(
            target=target,
            evidence=evidence,
            actionability=Actionability.SPLIT,
            target_type="file",
            split_proposals=[
                {"module_name": "big_module_helpers.py", "functions": ["a", "b"]}
            ],
        )
        plan = build_extraction_plan(conv, source_file="big_module.py")

        actions = [s.action for s in plan.steps]
        assert "create_module" in actions

        cm_step = next(s for s in plan.steps if s.action == "create_module")
        assert "big_module_helpers.py" in cm_step.target

    def test_file_split_default_module_name(self):
        """File split without explicit proposal uses default naming."""
        target = "utils.py"
        evidence = [
            LensEvidence(
                lens=LensKind.COHESION,
                target=target,
                confidence=0.7,
                signal="support",
                detail="Low cohesion",
                raw={},
            ),
        ]
        conv = _make_convergence(
            target=target,
            evidence=evidence,
            actionability=Actionability.SPLIT,
            target_type="file",
        )
        plan = build_extraction_plan(conv, source_file="utils.py")

        cm_steps = [s for s in plan.steps if s.action == "create_module"]
        assert cm_steps
        assert "utils_extracted" in cm_steps[0].target


# ── Batch extraction tests ────────────────────────────────────────────


class TestBatchExtraction:
    def test_batch_produces_plan_per_candidate(self):
        """build_batch_extraction_plan returns one plan per candidate."""
        candidates = [
            _make_convergence("m.py::f1", evidence=[_purity_support("m.py::f1")]),
            _make_convergence("m.py::f2", evidence=[_purity_support("m.py::f2")]),
        ]
        plans = build_batch_extraction_plan(candidates, source_file="m.py")
        assert len(plans) == 2
        assert plans[0].source_function == "m.py::f1"
        assert plans[1].source_function == "m.py::f2"

    def test_batch_empty(self):
        """Empty candidates → empty plans."""
        plans = build_batch_extraction_plan([])
        assert plans == []


# ── Convergence attachment tests ──────────────────────────────────────


class TestConvergenceAttachment:
    def test_plan_carries_convergence(self):
        """Plan carries the backing ConvergenceResult."""
        conv = _make_convergence()
        plan = build_extraction_plan(conv, source_file="m.py")
        assert plan.convergence is conv

    def test_to_dict_includes_convergence(self):
        """Serialized plan includes convergence data."""
        conv = _make_convergence()
        plan = build_extraction_plan(conv, source_file="m.py")
        d = plan.to_dict()
        assert "convergence" in d
        assert d["convergence"]["target"] == conv.target


# ── Source file inference tests ───────────────────────────────────────


class TestSourceFileInference:
    def test_infer_from_target(self):
        """Source file inferred from '::' target."""
        conv = _make_convergence(target="lintgate/utils.py::helper")
        plan = build_extraction_plan(conv)
        assert plan.source_file == "lintgate/utils.py"

    def test_explicit_source_file_overrides(self):
        """Explicit source_file takes precedence."""
        conv = _make_convergence(target="lintgate/utils.py::helper")
        plan = build_extraction_plan(conv, source_file="override.py")
        assert plan.source_file == "override.py"


# ── AST type inference tests ──────────────────────────────────────────


class TestASTTypeInference:
    def test_param_types_from_annotations(self):
        """Parameter types inferred from AST annotations."""
        code = "def f(x: int, y: str) -> bool:\n    return True\n"
        tree = ast.parse(code)
        target = "module.py::f"
        evidence = [
            _dep_clustering_evidence(target, "_helper", ["x", "y"], ["result"]),
        ]
        conv = _make_convergence(target=target, evidence=evidence)
        plan = build_extraction_plan(conv, ast_context=tree, source_file="module.py")

        cf_steps = [s for s in plan.steps if s.action == "create_function"]
        assert cf_steps
        types = cf_steps[0].detail.get("parameter_types", {})
        assert types.get("x") == "int"
        assert types.get("y") == "str"

    def test_return_type_inferred_from_outputs(self):
        """Return type varies by output count."""
        target = "m.py::f"

        # No outputs → None
        ev_no_out = [_dep_clustering_evidence(target, "_h", outputs=[])]
        conv = _make_convergence(target=target, evidence=ev_no_out)
        plan = build_extraction_plan(conv, source_file="m.py")
        cf = [s for s in plan.steps if s.action == "create_function"]
        assert cf[0].detail.get("return_type") == "None"

        # One output → Any
        ev_one = [
            _dep_clustering_evidence(target, "_h2", outputs=["r"], lines=(30, 40))
        ]
        conv2 = _make_convergence(target=target, evidence=ev_one)
        plan2 = build_extraction_plan(conv2, source_file="m.py")
        cf2 = [s for s in plan2.steps if s.action == "create_function"]
        assert cf2[0].detail.get("return_type") == "Any"

        # Two outputs → tuple
        ev_two = [
            _dep_clustering_evidence(target, "_h3", outputs=["a", "b"], lines=(50, 60))
        ]
        conv3 = _make_convergence(target=target, evidence=ev_two)
        plan3 = build_extraction_plan(conv3, source_file="m.py")
        cf3 = [s for s in plan3.steps if s.action == "create_function"]
        assert "tuple" in cf3[0].detail.get("return_type", "")
