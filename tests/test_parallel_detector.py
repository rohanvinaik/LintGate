"""Tests for parallel_detector: call-site parallelization opportunity detection."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.performance_checks.algebra_types import PurityResult
from lintgate.linters.performance_checks.parallel_detector import (
    ParallelOpportunity,
    detect_parallel_opportunities,
)


def _make_pure(name: str, line: int = 1) -> PurityResult:
    """Create a PurityResult marking a function as pure."""
    return PurityResult(
        function_name=name,
        qualified_name=name,
        line=line,
        is_pure=True,
        confidence=0.9,
        side_effects=(),
        parameter_count=1,
        return_annotation=None,
    )


def _make_impure(name: str, line: int = 1) -> PurityResult:
    """Create a PurityResult marking a function as impure."""
    from lintgate.linters.performance_checks.algebra_types import SideEffect

    return PurityResult(
        function_name=name,
        qualified_name=name,
        line=line,
        is_pure=False,
        confidence=1.0,
        side_effects=(
            SideEffect(
                kind="io_call",
                node_type="Call",
                line=line,
                detail=f"Calls impure function: {name}",
            ),
        ),
        parameter_count=1,
        return_annotation=None,
    )


def _parse(source: str) -> ast.AST:
    return ast.parse(textwrap.dedent(source))


# ── PARALLEL_MAP: for-loop ────────────────────────────────────────────


class TestForLoopWithPureCall:
    def test_for_loop_with_pure_call_detected(self):
        source = """\
        def process(items):
            for x in items:
                result = transform(x)
        """
        tree = _parse(source)
        purity = {"transform": _make_pure("transform")}
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) >= 1
        opp = parallel_maps[0]
        assert opp.callee == "transform"
        assert opp.confidence == 0.85
        assert opp.file == "test.py"
        assert "callee must be pure" in opp.constraints

    def test_for_loop_with_impure_call_not_detected(self):
        source = """\
        def process(items):
            for x in items:
                result = write_to_db(x)
        """
        tree = _parse(source)
        purity = {"write_to_db": _make_impure("write_to_db")}
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) == 0

    def test_for_loop_unknown_purity_lower_confidence(self):
        source = """\
        def process(items):
            for x in items:
                result = unknown_func(x)
        """
        tree = _parse(source)
        # No purity info for unknown_func
        opps = detect_parallel_opportunities(tree, {}, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) >= 1
        assert parallel_maps[0].confidence == 0.5
        assert "purity of callee is unknown" in parallel_maps[0].constraints


# ── PARALLEL_MAP: comprehension ──────────────────────────────────────


class TestComprehensionWithPureCall:
    def test_list_comprehension_with_pure_call_detected(self):
        source = """\
        def process(items):
            return [transform(x) for x in items]
        """
        tree = _parse(source)
        purity = {"transform": _make_pure("transform")}
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) >= 1
        opp = parallel_maps[0]
        assert opp.callee == "transform"
        assert opp.confidence == 0.85

    def test_set_comprehension_with_pure_call_detected(self):
        source = """\
        def process(items):
            return {normalize(x) for x in items}
        """
        tree = _parse(source)
        purity = {"normalize": _make_pure("normalize")}
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) >= 1
        assert parallel_maps[0].callee == "normalize"

    def test_generator_expression_with_pure_call_detected(self):
        source = """\
        def process(items):
            return sum(compute(x) for x in items)
        """
        tree = _parse(source)
        purity = {"compute": _make_pure("compute")}
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) >= 1
        assert parallel_maps[0].callee == "compute"

    def test_comprehension_with_impure_call_not_detected(self):
        source = """\
        def process(items):
            return [send_request(x) for x in items]
        """
        tree = _parse(source)
        purity = {"send_request": _make_impure("send_request")}
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) == 0


# ── PARALLEL_ASYNC ───────────────────────────────────────────────────


class TestAsyncIndependentAwaits:
    def test_async_independent_awaits_detected(self):
        source = """\
        async def fetch_all():
            a = await fetch_user(1)
            b = await fetch_order(2)
        """
        tree = _parse(source)
        opps = detect_parallel_opportunities(tree, {}, "test.py")

        async_opps = [o for o in opps if o.pattern == "PARALLEL_ASYNC"]
        assert len(async_opps) == 1
        opp = async_opps[0]
        assert "fetch_user" in opp.callee
        assert "fetch_order" in opp.callee
        assert opp.confidence == 0.75

    def test_async_dependent_awaits_not_detected(self):
        source = """\
        async def fetch_chain():
            user = await fetch_user(1)
            orders = await fetch_orders(user)
        """
        tree = _parse(source)
        opps = detect_parallel_opportunities(tree, {}, "test.py")

        async_opps = [o for o in opps if o.pattern == "PARALLEL_ASYNC"]
        assert len(async_opps) == 0

    def test_async_single_await_not_detected(self):
        source = """\
        async def fetch_one():
            result = await fetch_user(1)
        """
        tree = _parse(source)
        opps = detect_parallel_opportunities(tree, {}, "test.py")

        async_opps = [o for o in opps if o.pattern == "PARALLEL_ASYNC"]
        assert len(async_opps) == 0


# ── PARALLEL_BEAM ────────────────────────────────────────────────────


class TestIndependentBranchComputations:
    def test_independent_branch_computations_detected(self):
        source = """\
        def dispatch(kind, x):
            if kind == "a":
                r = compute_a(x)
            elif kind == "b":
                r = compute_b(x)
        """
        tree = _parse(source)
        purity = {
            "compute_a": _make_pure("compute_a"),
            "compute_b": _make_pure("compute_b"),
        }
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        beam_opps = [o for o in opps if o.pattern == "PARALLEL_BEAM"]
        assert len(beam_opps) == 1
        opp = beam_opps[0]
        assert "compute_a" in opp.callee
        assert "compute_b" in opp.callee
        assert opp.confidence == 0.6

    def test_branches_with_impure_calls_not_detected(self):
        source = """\
        def dispatch(kind, x):
            if kind == "a":
                r = compute_a(x)
            elif kind == "b":
                r = write_db(x)
        """
        tree = _parse(source)
        purity = {
            "compute_a": _make_pure("compute_a"),
            "write_db": _make_impure("write_db"),
        }
        opps = detect_parallel_opportunities(tree, purity, "test.py")

        beam_opps = [o for o in opps if o.pattern == "PARALLEL_BEAM"]
        # Only one pure callee — need at least 2 for PARALLEL_BEAM
        assert len(beam_opps) == 0


# ── Edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_tree_returns_empty_list(self):
        tree = ast.parse("")
        opps = detect_parallel_opportunities(tree, {}, "")
        assert opps == []

    def test_no_false_positive_on_stateful_loop(self):
        """A loop with an accumulator should not be flagged as PARALLEL_MAP."""
        source = """\
        def process(items):
            total = 0
            for x in items:
                total += x
        """
        tree = _parse(source)
        opps = detect_parallel_opportunities(tree, {}, "test.py")

        parallel_maps = [o for o in opps if o.pattern == "PARALLEL_MAP"]
        assert len(parallel_maps) == 0

    def test_to_dict_round_trips(self):
        opp = ParallelOpportunity(
            pattern="PARALLEL_MAP",
            file="test.py",
            line=10,
            callee="transform",
            confidence=0.853,
            constraints=["callee must be pure"],
            detail="test detail",
        )
        d = opp.to_dict()
        assert d["confidence"] == 0.85  # rounded to 2 decimals
        assert d["pattern"] == "PARALLEL_MAP"
        assert d["constraints"] == ["callee must be pure"]
