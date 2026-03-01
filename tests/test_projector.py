"""Tests for B2: Post-Extraction Projector."""

from __future__ import annotations

import ast

from lintgate.convergence.extraction_plan import (
    ExtractionPlan,
    ExtractionStep,
)
from lintgate.convergence.projector import (
    ProjectedOpportunity,
    _analyze_projected_purity,
    _is_directly_testable,
    _is_numeric_heavy,
    build_projected_ast,
    project_post_extraction,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_plan(
    steps: list[ExtractionStep],
    source_function: str = "module.py::process",
    source_file: str = "module.py",
) -> ExtractionPlan:
    return ExtractionPlan(
        source_function=source_function,
        source_file=source_file,
        steps=steps,
    )


def _create_function_step(
    proposed_name: str = "_compute",
    params: list[str] | None = None,
    source_lines: list[int] | None = None,
    order: int = 1,
) -> ExtractionStep:
    return ExtractionStep(
        order=order,
        action="create_function",
        target=proposed_name,
        detail={
            "proposed_name": proposed_name,
            "parameters": params or ["x", "y"],
            "outputs": ["result"],
            "source_lines": source_lines or [],
        },
    )


def _handler_step(
    handler_name: str = "handle",
    proposed_name: str = "_impl_handle",
    captured: list[str] | None = None,
    captured_writes: list[str] | None = None,
    source_lines: list[int] | None = None,
    order: int = 1,
) -> ExtractionStep:
    return ExtractionStep(
        order=order,
        action="extract_handler",
        target=handler_name,
        detail={
            "handler_name": handler_name,
            "proposed_name": proposed_name,
            "captured_variables": captured or ["engine"],
            "captured_writes": captured_writes or [],
            "source_lines": source_lines or [],
            "destination": "module_level",
        },
    )


# ── ProjectedOpportunity tests ────────────────────────────────────────


class TestProjectedOpportunity:
    def test_to_dict(self):
        opp = ProjectedOpportunity(
            function_id="_compute",
            opportunity="cacheable",
            confidence=0.81,
            precondition="requires extraction",
            evidence=["projected_pure"],
        )
        d = opp.to_dict()
        assert d["function_id"] == "_compute"
        assert d["opportunity"] == "cacheable"
        assert d["confidence"] == 0.81
        assert d["precondition"] == "requires extraction"
        assert "projected_pure" in d["evidence"]

    def test_confidence_rounding(self):
        opp = ProjectedOpportunity(
            function_id="f",
            opportunity="cacheable",
            confidence=0.123456789,
            precondition="",
        )
        assert opp.to_dict()["confidence"] == 0.123


# ── Pure function extraction → cacheable ──────────────────────────────


class TestCacheableProjection:
    def test_pure_extracted_function_is_cacheable(self):
        """Extracting a pure computation → cacheable opportunity."""
        # Build projected AST with pure body
        pure_code = "def _compute_result(data):\n    return sum(x * 2 for x in data)\n"
        pure_tree = ast.parse(pure_code)
        pure_func = pure_tree.body[0]

        # Directly test purity
        purity = _analyze_projected_purity(pure_func)
        assert purity.is_pure

    def test_project_finds_cacheable(self):
        """Full projection pipeline identifies cacheable opportunity."""
        # Build a plan with a create_function step that has source AST
        pure_code = """\
def _compute(x, y):
    return x + y
"""
        tree = ast.parse(pure_code)
        step = _create_function_step("_compute", ["x", "y"], [2, 2])
        plan = _make_plan([step])

        opportunities = project_post_extraction(plan, tree)

        cacheable = [o for o in opportunities if o.opportunity == "cacheable"]
        assert len(cacheable) >= 1
        assert cacheable[0].function_id == "_compute"
        assert cacheable[0].confidence > 0
        assert "projected_pure" in cacheable[0].evidence

    def test_impure_function_not_cacheable(self):
        """Function with IO is NOT cacheable."""
        impure_code = """\
def _log_data(data):
    print(data)
    return len(data)
"""
        tree = ast.parse(impure_code)
        step = _create_function_step("_log_data", ["data"], [2, 3])
        plan = _make_plan([step])

        opportunities = project_post_extraction(plan, tree)

        cacheable = [o for o in opportunities if o.opportunity == "cacheable"]
        assert len(cacheable) == 0


# ── Parallelizable detection ─────────────────────────────────────────


class TestParallelizableProjection:
    def test_two_pure_functions_parallelizable(self):
        """Two independent pure extracted functions → parallelizable."""
        code = """\
def _compute_a(x):
    return x * 2

def _compute_b(y):
    return y + 1
"""
        tree = ast.parse(code)
        step_a = _create_function_step("_compute_a", ["x"], [2, 2], order=1)
        step_b = _create_function_step("_compute_b", ["y"], [5, 5], order=2)
        plan = _make_plan([step_a, step_b])

        opportunities = project_post_extraction(plan, tree)

        parallel = [o for o in opportunities if o.opportunity == "parallelizable"]
        assert len(parallel) >= 1
        assert "no_shared_mutable_state" in parallel[0].evidence

    def test_single_function_not_parallelizable(self):
        """Single function → no parallelizable opportunity."""
        code = "def _f(x):\n    return x\n"
        tree = ast.parse(code)
        step = _create_function_step("_f", ["x"], [1, 2])
        plan = _make_plan([step])

        opportunities = project_post_extraction(plan, tree)

        parallel = [o for o in opportunities if o.opportunity == "parallelizable"]
        assert len(parallel) == 0


# ── Directly testable detection ───────────────────────────────────────


class TestDirectlyTestableProjection:
    def test_handler_no_writes_is_testable(self):
        """Handler with no captured writes → directly testable."""
        code = "def _impl_handle(engine):\n    return engine.run()\n"
        tree = ast.parse(code)
        step = _handler_step("handle", "_impl_handle", ["engine"], [])
        plan = _make_plan([step])

        opportunities = project_post_extraction(plan, tree)

        testable = [o for o in opportunities if o.opportunity == "directly_testable"]
        assert len(testable) >= 1
        assert testable[0].confidence >= 0.7

    def test_handler_with_writes_lower_confidence(self):
        """Handler with captured writes → not directly testable."""
        step = _handler_step("handle", "_impl_handle", ["engine"], ["state"])
        assert not _is_directly_testable(step)

    def test_create_function_with_params_is_testable(self):
        """Block extraction with explicit params → directly testable."""
        step = _create_function_step("_helper", ["a", "b"])
        assert _is_directly_testable(step)


# ── JIT candidate detection ──────────────────────────────────────────


class TestJITCandidateProjection:
    def test_numeric_heavy_pure_function(self):
        """Pure function with numeric operations → jit_candidate."""
        code = """\
def _compute(x, y, z):
    a = x * y + z
    b = a ** 2 - x
    c = b / (y + 1)
    return a + b + c
"""
        tree = ast.parse(code)
        step = _create_function_step("_compute", ["x", "y", "z"], [2, 5])
        plan = _make_plan([step])

        opportunities = project_post_extraction(plan, tree)

        jit = [o for o in opportunities if o.opportunity == "jit_candidate"]
        assert len(jit) >= 1
        assert "numeric_operations" in jit[0].evidence

    def test_non_numeric_not_jit(self):
        """Non-numeric pure function → no jit_candidate."""
        code = "def _format(s):\n    return s.strip().upper()\n"
        tree = ast.parse(code)
        step = _create_function_step("_format", ["s"], [1, 2])
        plan = _make_plan([step])

        opportunities = project_post_extraction(plan, tree)

        jit = [o for o in opportunities if o.opportunity == "jit_candidate"]
        assert len(jit) == 0


# ── Build projected AST tests ────────────────────────────────────────


class TestBuildProjectedAST:
    def test_create_function_ast(self):
        """build_projected_ast creates valid function node for create_function step."""
        step = _create_function_step("_helper", ["a", "b"])
        func = build_projected_ast(step)

        assert func is not None
        assert func.name == "_helper"
        assert len(func.args.args) == 2
        assert func.args.args[0].arg == "a"
        assert func.args.args[1].arg == "b"

    def test_handler_ast(self):
        """build_projected_ast creates valid function node for extract_handler step."""
        step = _handler_step("handle", "_impl_handle", ["engine", "state"])
        func = build_projected_ast(step)

        assert func is not None
        assert func.name == "_impl_handle"
        assert len(func.args.args) == 2

    def test_non_extraction_step_returns_none(self):
        """Non-extraction steps return None."""
        step = ExtractionStep(order=1, action="update_callers", target="f")
        assert build_projected_ast(step) is None

    def test_body_extraction_from_source(self):
        """When source AST is provided, body is extracted by line range."""
        code = """\
def process(data):
    x = data[0]
    y = data[1]
    return x + y
"""
        tree = ast.parse(code)
        step = _create_function_step("_helper", ["data"], [2, 4])
        func = build_projected_ast(step, tree)

        assert func is not None
        # Should have extracted statements from the source
        assert len(func.body) >= 1


# ── Numeric detection tests ───────────────────────────────────────────


class TestNumericDetection:
    def test_numeric_heavy(self):
        code = "def f(x, y):\n    return x * y + x - y / 2\n"
        tree = ast.parse(code)
        assert _is_numeric_heavy(tree.body[0])

    def test_not_numeric(self):
        code = "def f(s):\n    return s.upper()\n"
        tree = ast.parse(code)
        assert not _is_numeric_heavy(tree.body[0])


# ── Integration with ExtractionPlan ───────────────────────────────────


class TestPlanIntegration:
    def test_opportunities_attached_to_plan(self):
        """project_post_extraction results can be attached to ExtractionPlan."""
        code = "def _compute(x):\n    return x * 2\n"
        tree = ast.parse(code)
        step = _create_function_step("_compute", ["x"], [1, 2])
        plan = _make_plan([step])

        opportunities = project_post_extraction(plan, tree)
        plan.post_extraction_opportunities = opportunities

        d = plan.to_dict()
        if opportunities:
            assert "post_extraction_opportunities" in d

    def test_empty_plan_no_opportunities(self):
        """Plan with no extraction steps → no opportunities."""
        plan = _make_plan(
            [
                ExtractionStep(order=1, action="update_callers", target="f"),
            ]
        )
        opportunities = project_post_extraction(plan)
        assert opportunities == []

    def test_plan_to_dict_without_opportunities(self):
        """Plan without opportunities omits the field from dict."""
        plan = _make_plan([])
        d = plan.to_dict()
        assert "post_extraction_opportunities" not in d
