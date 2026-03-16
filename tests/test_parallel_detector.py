"""Tests for parallel_detector: call-site parallelization opportunity detection."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.performance_checks.algebra_types import PurityResult
from lintgate.linters.performance_checks.parallel_detector import (
    ParallelOpportunity,
    _check_async_func,
    _check_comprehension,
    _check_for_loop,
    _check_if_branches,
    _get_assign_target_names,
    _get_await_target_name,
    _get_referenced_names,
    _is_pure,
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


# ── Direct unit tests for internal functions ────────────────────────


class TestIsPure:
    def test_known_pure(self):
        pr = {"f": _make_pure("f")}
        assert _is_pure("f", pr) is True

    def test_known_impure(self):
        pr = {"f": _make_impure("f")}
        assert _is_pure("f", pr) is False

    def test_unknown_returns_none(self):
        assert _is_pure("missing", {}) is None


class TestToDict:
    def test_all_fields_present(self):
        opp = ParallelOpportunity(
            pattern="PARALLEL_ASYNC",
            file="a.py",
            line=5,
            callee="fetch",
            confidence=0.7777,
            constraints=["c1", "c2"],
            detail="some detail",
        )
        d = opp.to_dict()
        assert d == {
            "pattern": "PARALLEL_ASYNC",
            "file": "a.py",
            "line": 5,
            "callee": "fetch",
            "confidence": 0.78,
            "constraints": ["c1", "c2"],
            "detail": "some detail",
        }

    def test_confidence_rounds_down(self):
        opp = ParallelOpportunity(
            pattern="PARALLEL_MAP",
            file="",
            line=1,
            callee="f",
            confidence=0.844,
            constraints=[],
            detail="",
        )
        assert opp.to_dict()["confidence"] == 0.84

    def test_empty_constraints(self):
        opp = ParallelOpportunity(
            pattern="PARALLEL_BEAM",
            file="",
            line=1,
            callee="g",
            confidence=1.0,
            constraints=[],
            detail="d",
        )
        assert opp.to_dict()["constraints"] == []

    def test_file_empty_string(self):
        opp = ParallelOpportunity(
            pattern="PARALLEL_MAP",
            file="",
            line=0,
            callee="h",
            confidence=0.0,
            constraints=[],
            detail="",
        )
        assert opp.to_dict()["file"] == ""
        assert opp.to_dict()["line"] == 0


class TestCheckForLoopDirect:
    def _get_for_node(self, source: str) -> ast.For:
        tree = _parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                return node
        raise AssertionError("No for-loop found")

    def test_pure_callee_returns_opportunity(self):
        src = """\
        for x in items:
            r = transform(x)
        """
        node = self._get_for_node(src)
        purity = {"transform": _make_pure("transform")}
        results = _check_for_loop(node, purity, "f.py")
        assert len(results) == 1
        assert results[0].pattern == "PARALLEL_MAP"
        assert results[0].callee == "transform"
        assert results[0].confidence == 0.85
        assert results[0].file == "f.py"
        assert results[0].constraints == ["callee must be pure"]

    def test_impure_callee_skipped(self):
        src = """\
        for x in items:
            r = write(x)
        """
        node = self._get_for_node(src)
        purity = {"write": _make_impure("write")}
        assert _check_for_loop(node, purity, "") == []

    def test_unknown_purity_lower_confidence(self):
        src = """\
        for x in items:
            r = mystery(x)
        """
        node = self._get_for_node(src)
        results = _check_for_loop(node, {}, "")
        assert len(results) == 1
        assert results[0].confidence == 0.5
        assert "purity of callee is unknown" in results[0].constraints

    def test_no_assignment_in_body(self):
        src = """\
        for x in items:
            print(x)
        """
        node = self._get_for_node(src)
        assert _check_for_loop(node, {}, "") == []

    def test_assignment_not_call(self):
        src = """\
        for x in items:
            r = x + 1
        """
        node = self._get_for_node(src)
        assert _check_for_loop(node, {}, "") == []

    def test_multiple_assignments(self):
        src = """\
        for x in items:
            a = f(x)
            b = g(x)
        """
        node = self._get_for_node(src)
        purity = {"f": _make_pure("f"), "g": _make_pure("g")}
        results = _check_for_loop(node, purity, "")
        assert len(results) == 2
        callees = {r.callee for r in results}
        assert callees == {"f", "g"}

    def test_detail_mentions_callee(self):
        src = """\
        for x in items:
            r = compute(x)
        """
        node = self._get_for_node(src)
        purity = {"compute": _make_pure("compute")}
        results = _check_for_loop(node, purity, "")
        assert "compute" in results[0].detail


class TestCheckComprehensionDirect:
    def _get_comp_node(self, source: str):
        tree = _parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                return node
        raise AssertionError("No comprehension found")

    def test_pure_list_comp(self):
        src = "[f(x) for x in items]"
        node = self._get_comp_node(src)
        purity = {"f": _make_pure("f")}
        results = _check_comprehension(node, purity, "c.py")
        assert len(results) == 1
        assert results[0].callee == "f"
        assert results[0].confidence == 0.85
        assert results[0].file == "c.py"

    def test_impure_comp_returns_empty(self):
        src = "[write(x) for x in items]"
        node = self._get_comp_node(src)
        purity = {"write": _make_impure("write")}
        assert _check_comprehension(node, purity, "") == []

    def test_unknown_purity_comp(self):
        src = "[mystery(x) for x in items]"
        node = self._get_comp_node(src)
        results = _check_comprehension(node, {}, "")
        assert len(results) == 1
        assert results[0].confidence == 0.5
        assert "purity of callee is unknown" in results[0].constraints

    def test_non_call_elt_returns_empty(self):
        src = "[x + 1 for x in items]"
        node = self._get_comp_node(src)
        assert _check_comprehension(node, {}, "") == []

    def test_set_comp(self):
        src = "{g(x) for x in items}"
        node = self._get_comp_node(src)
        purity = {"g": _make_pure("g")}
        results = _check_comprehension(node, purity, "")
        assert len(results) == 1
        assert results[0].callee == "g"


class TestGetAwaitTargetNameDirect:
    def _get_stmt(self, source: str) -> ast.stmt:
        tree = _parse(source)
        # Get the first statement inside the async function body
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                return node.body[0]
        raise AssertionError("No async function found")

    def test_valid_await_assign(self):
        src = """\
        async def f():
            x = await fetch(1)
        """
        stmt = self._get_stmt(src)
        assert _get_await_target_name(stmt) == "fetch"

    def test_non_assign_returns_none(self):
        src = """\
        async def f():
            await fetch(1)
        """
        stmt = self._get_stmt(src)
        assert _get_await_target_name(stmt) is None

    def test_non_await_assign_returns_none(self):
        src = """\
        async def f():
            x = compute(1)
        """
        stmt = self._get_stmt(src)
        assert _get_await_target_name(stmt) is None

    def test_await_non_call_returns_none(self):
        src = """\
        async def f():
            x = await some_coroutine
        """
        stmt = self._get_stmt(src)
        assert _get_await_target_name(stmt) is None


class TestGetAssignTargetNamesDirect:
    def _get_assign(self, source: str) -> ast.Assign:
        tree = _parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                return node
        raise AssertionError("No Assign found")

    def test_single_name(self):
        stmt = self._get_assign("x = 1")
        assert _get_assign_target_names(stmt) == {"x"}

    def test_tuple_unpack(self):
        stmt = self._get_assign("a, b = 1, 2")
        assert _get_assign_target_names(stmt) == {"a", "b"}

    def test_list_unpack(self):
        stmt = self._get_assign("[a, b] = [1, 2]")
        assert _get_assign_target_names(stmt) == {"a", "b"}

    def test_attribute_target_ignored(self):
        stmt = self._get_assign("self.x = 1")
        assert _get_assign_target_names(stmt) == set()

    def test_nested_tuple(self):
        stmt = self._get_assign("(a, (b, c)) = (1, (2, 3))")
        # Only top-level elts are checked — b,c are in a nested tuple
        names = _get_assign_target_names(stmt)
        assert "a" in names


class TestGetReferencedNamesDirect:
    def test_simple_expression(self):
        tree = ast.parse("x + y", mode="eval")
        names = _get_referenced_names(tree)
        assert "x" in names
        assert "y" in names

    def test_nested_call(self):
        tree = ast.parse("f(a, b)", mode="eval")
        names = _get_referenced_names(tree)
        assert "f" in names
        assert "a" in names
        assert "b" in names

    def test_no_names(self):
        tree = ast.parse("1 + 2", mode="eval")
        names = _get_referenced_names(tree)
        assert names == set()

    def test_attribute_access(self):
        tree = ast.parse("obj.method(x)", mode="eval")
        names = _get_referenced_names(tree)
        assert "obj" in names
        assert "x" in names


class TestCheckAsyncFuncDirect:
    def _get_async_func(self, source: str) -> ast.AsyncFunctionDef:
        tree = _parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                return node
        raise AssertionError("No async function found")

    def test_two_independent_awaits(self):
        src = """\
        async def f():
            a = await fetch_a(1)
            b = await fetch_b(2)
        """
        node = self._get_async_func(src)
        results = _check_async_func(node, "test.py")
        assert len(results) == 1
        assert results[0].pattern == "PARALLEL_ASYNC"
        assert "fetch_a" in results[0].callee
        assert "fetch_b" in results[0].callee
        assert results[0].confidence == 0.75
        assert results[0].file == "test.py"

    def test_dependent_awaits_no_result(self):
        src = """\
        async def f():
            a = await fetch_a(1)
            b = await fetch_b(a)
        """
        node = self._get_async_func(src)
        assert _check_async_func(node, "") == []

    def test_single_await_no_result(self):
        src = """\
        async def f():
            a = await fetch(1)
        """
        node = self._get_async_func(src)
        assert _check_async_func(node, "") == []

    def test_three_independent_awaits(self):
        src = """\
        async def f():
            a = await svc_a(1)
            b = await svc_b(2)
            c = await svc_c(3)
        """
        node = self._get_async_func(src)
        results = _check_async_func(node, "")
        assert len(results) == 1
        assert "svc_a" in results[0].callee
        assert "svc_b" in results[0].callee
        assert "svc_c" in results[0].callee

    def test_non_await_stmts_ignored(self):
        src = """\
        async def f():
            x = 1
            a = await fetch_a(2)
            b = await fetch_b(3)
        """
        node = self._get_async_func(src)
        results = _check_async_func(node, "")
        assert len(results) == 1

    def test_detail_suggests_gather(self):
        src = """\
        async def f():
            a = await fa(1)
            b = await fb(2)
        """
        node = self._get_async_func(src)
        results = _check_async_func(node, "")
        assert "asyncio.gather()" in results[0].detail


class TestCheckIfBranchesDirect:
    def _get_if_node(self, source: str) -> ast.If:
        tree = _parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                return node
        raise AssertionError("No If found")

    def test_two_pure_branches(self):
        src = """\
        if c:
            r = f(x)
        elif d:
            r = g(x)
        """
        node = self._get_if_node(src)
        purity = {"f": _make_pure("f"), "g": _make_pure("g")}
        results = _check_if_branches(node, purity, "b.py")
        assert len(results) == 1
        assert results[0].pattern == "PARALLEL_BEAM"
        assert "f" in results[0].callee
        assert "g" in results[0].callee
        assert results[0].confidence == 0.6
        assert results[0].file == "b.py"

    def test_single_branch_no_result(self):
        src = """\
        if c:
            r = f(x)
        """
        node = self._get_if_node(src)
        purity = {"f": _make_pure("f")}
        assert _check_if_branches(node, purity, "") == []

    def test_impure_branch_excluded(self):
        src = """\
        if c:
            r = f(x)
        elif d:
            r = impure(x)
        """
        node = self._get_if_node(src)
        purity = {"f": _make_pure("f"), "impure": _make_impure("impure")}
        # Only 1 pure callee — needs >=2
        assert _check_if_branches(node, purity, "") == []

    def test_multi_stmt_branch_skipped(self):
        src = """\
        if c:
            r = f(x)
            s = g(x)
        elif d:
            r = h(x)
        """
        node = self._get_if_node(src)
        purity = {"f": _make_pure("f"), "g": _make_pure("g"), "h": _make_pure("h")}
        # First branch has 2 statements — skipped
        assert _check_if_branches(node, purity, "") == []

    def test_else_branch_included(self):
        src = """\
        if c:
            r = f(x)
        else:
            r = g(x)
        """
        node = self._get_if_node(src)
        purity = {"f": _make_pure("f"), "g": _make_pure("g")}
        results = _check_if_branches(node, purity, "")
        assert len(results) == 1
        assert "f" in results[0].callee
        assert "g" in results[0].callee

    def test_three_elif_branches(self):
        src = """\
        if c:
            r = a(x)
        elif d:
            r = b(x)
        elif e:
            r = c_fn(x)
        """
        node = self._get_if_node(src)
        purity = {
            "a": _make_pure("a"),
            "b": _make_pure("b"),
            "c_fn": _make_pure("c_fn"),
        }
        results = _check_if_branches(node, purity, "")
        assert len(results) == 1
        assert "a" in results[0].callee
        assert "b" in results[0].callee
        assert "c_fn" in results[0].callee
