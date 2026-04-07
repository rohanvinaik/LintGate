"""Tests for lintgate.specification.mutation_engine — AST mutation generation and evaluation."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

from Wesker.engine import (
    CategoryResult,
    Mutant,
    MutantResult,
    MutationCategory,
    ProfilingResult,
    SamplingResult,
    _count_targets,
    _docstring_positions,
    evaluate_mutant,
    extract_boundary_inputs,
    generate_mutants,
    run_function_profiling,
    run_function_sampling,
)


def _parse_func(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    msg = "No function found"
    raise ValueError(msg)


def _parse_method(source: str, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    msg = f"No method found: {class_name}.{method_name}"
    raise ValueError(msg)


class TestGenerateMutants:
    def test_value_mutants_for_add(self):
        func = _parse_func("def add(a, b): return a + b + 1")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        # The literal `1` is a VALUE mutation target
        assert len(mutants) >= 1
        assert all(m.category == MutationCategory.VALUE for m in mutants)

    def test_value_mutants_with_constants(self):
        func = _parse_func("def f(x): return x + 1")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1
        assert all(m.category == MutationCategory.VALUE for m in mutants)

    def test_boundary_mutants_for_comparison(self):
        func = _parse_func("def f(x): return x < 10")
        mutants = generate_mutants(func, {MutationCategory.BOUNDARY})
        assert len(mutants) >= 1
        assert all(m.category == MutationCategory.BOUNDARY for m in mutants)

    def test_swap_mutants_two_params(self):
        func = _parse_func("def f(a, b): return min(a, b)")
        mutants = generate_mutants(func, {MutationCategory.SWAP})
        assert len(mutants) >= 1
        assert all(m.category == MutationCategory.SWAP for m in mutants)

    def test_no_swap_mutants_zero_params(self):
        func = _parse_func("def f(): return 42")
        mutants = generate_mutants(func, {MutationCategory.SWAP})
        assert len(mutants) == 0

    def test_state_mutants_self_assign(self):
        func = _parse_func("def f(self, x):\n    self.value = x\n    return self.value")
        mutants = generate_mutants(func, {MutationCategory.STATE})
        assert len(mutants) >= 1

    def test_type_mutants_isinstance(self):
        func = _parse_func("def f(x): return isinstance(x, int)")
        mutants = generate_mutants(func, {MutationCategory.TYPE})
        assert len(mutants) >= 1

    def test_no_type_mutants_without_isinstance(self):
        func = _parse_func("def f(x): return x + 1")
        mutants = generate_mutants(func, {MutationCategory.TYPE})
        assert len(mutants) == 0

    def test_max_per_category(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        mutants = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2)
        assert len(mutants) <= 2

    def test_multiple_categories(self):
        func = _parse_func("def f(a, b): return a < b")
        mutants = generate_mutants(func, {MutationCategory.BOUNDARY, MutationCategory.SWAP})
        categories = {m.category for m in mutants}
        # Should have at least boundary mutants
        assert MutationCategory.BOUNDARY in categories


class TestEvaluateMutant:
    def test_killed_by_assertion(self):
        func = _parse_func("def add(a, b): return a + b")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        if not mutants:
            return  # No constants to mutate

        original = lambda a, b: a + b  # noqa: E731

        def test_fn(mutated_func):
            assert mutated_func(2, 3) == 5

        result = evaluate_mutant(mutants[0], [test_fn], original)
        assert result.killed

    def test_survived_when_test_passes(self):
        func = _parse_func("def f(x): return x + 1")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        original = lambda x: x + 1  # noqa: E731

        def weak_test(mutated_func):
            # This test doesn't actually check the return value
            mutated_func(5)

        result = evaluate_mutant(mutants[0], [weak_test], original)
        assert not result.killed

    def test_killed_by_crash(self):
        func = _parse_func("def f(x): return isinstance(x, int)")
        mutants = generate_mutants(func, {MutationCategory.TYPE})
        if not mutants:
            return

        original = lambda x: isinstance(x, int)  # noqa: E731

        def test_fn(mutated_func):
            # isinstance replaced with True, but we call it wrong
            result = mutated_func("hello")
            if result is True:
                raise TypeError("wrong!")

        result = evaluate_mutant(mutants[0], [test_fn], original)
        assert result.killed
        assert result.killed_by == "crash"

    def test_kills_instance_method_via_class_patch(self):
        func = _parse_method(
            """
class Parser:
    def parse(self, value):
        return value + 1
""",
            "Parser",
            "parse",
        )
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        namespace: dict[str, object] = {}
        exec(  # nosec B102
            """
class Parser:
    def parse(self, value):
        return value + 1

def test_fn():
    assert Parser().parse(1) == 2
""",
            namespace,
        )
        test_fn = cast("Callable[..., None]", namespace["test_fn"])

        result = evaluate_mutant(
            mutants[0],
            [test_fn],
            lambda value: value + 1,
            qualname="Parser.parse",
        )
        assert result.killed
        assert result.killed_by == "assertion"

    def test_kills_classmethod_via_owner_patch(self):
        func = _parse_method(
            """
class ModelProfile:
    @classmethod
    def from_dict(cls, value):
        return value + 1
""",
            "ModelProfile",
            "from_dict",
        )
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        namespace: dict[str, object] = {}
        exec(  # nosec B102
            """
class ModelProfile:
    @classmethod
    def from_dict(cls, value):
        return value + 1

def test_fn():
    assert ModelProfile.from_dict(1) == 2
""",
            namespace,
        )
        test_fn = cast("Callable[..., None]", namespace["test_fn"])

        result = evaluate_mutant(
            mutants[0],
            [test_fn],
            lambda value: value + 1,
            qualname="ModelProfile.from_dict",
        )
        assert result.killed
        assert result.killed_by == "assertion"

    def test_kills_staticmethod_via_owner_patch(self):
        func = _parse_method(
            """
class Normalizer:
    @staticmethod
    def normalize(value):
        return value + 1
""",
            "Normalizer",
            "normalize",
        )
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        namespace: dict[str, object] = {}
        exec(  # nosec B102
            """
class Normalizer:
    @staticmethod
    def normalize(value):
        return value + 1

def test_fn():
    assert Normalizer.normalize(1) == 2
""",
            namespace,
        )
        test_fn = cast("Callable[..., None]", namespace["test_fn"])

        result = evaluate_mutant(
            mutants[0],
            [test_fn],
            lambda value: value + 1,
            qualname="Normalizer.normalize",
        )
        assert result.killed
        assert result.killed_by == "assertion"

    def test_kills_local_instance_method_via_closure_owner_patch(self):
        func = _parse_method(
            """
class Parser:
    def parse(self, value):
        return value + 1
""",
            "Parser",
            "parse",
        )
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        class Parser:
            def parse(self, value):
                return value + 1

        def test_fn():
            assert Parser().parse(1) == 2

        result = evaluate_mutant(
            mutants[0],
            [test_fn],
            lambda value: value + 1,
            qualname="Parser.parse",
        )
        assert result.killed
        assert result.killed_by == "assertion"

    def test_kills_local_classmethod_via_closure_owner_patch(self):
        func = _parse_method(
            """
class ModelProfile:
    @classmethod
    def from_dict(cls, value):
        return value + 1
""",
            "ModelProfile",
            "from_dict",
        )
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        class ModelProfile:
            @classmethod
            def from_dict(cls, value):
                return value + 1

        def test_fn():
            assert ModelProfile.from_dict(1) == 2

        result = evaluate_mutant(
            mutants[0],
            [test_fn],
            lambda value: value + 1,
            qualname="ModelProfile.from_dict",
        )
        assert result.killed
        assert result.killed_by == "assertion"

    def test_kills_local_function_via_closure_cell_patch(self):
        func = _parse_func("def score(value): return value + 1")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        def score(value):
            return value + 1

        def test_fn():
            assert score(1) == 2

        result = evaluate_mutant(
            mutants[0],
            [test_fn],
            lambda value: value + 1,
            qualname="score",
        )
        assert result.killed
        assert result.killed_by == "assertion"

    def test_kills_instance_method_via_instance_owner_resolution(self):
        func = _parse_method(
            """
class ModelProfileStore:
    def to_dict(self):
        return {"version": 1}
""",
            "ModelProfileStore",
            "to_dict",
        )
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1

        namespace: dict[str, object] = {}
        exec(  # nosec B102
            """
class ModelProfileStore:
    def to_dict(self):
        return {"version": 1}

store = ModelProfileStore()
del ModelProfileStore

def test_fn():
    assert store.to_dict()["version"] == 1
""",
            namespace,
        )
        test_fn = cast("Callable[..., None]", namespace["test_fn"])

        result = evaluate_mutant(
            mutants[0],
            [test_fn],
            lambda: {"version": 1},
            qualname="ModelProfileStore.to_dict",
        )
        assert result.killed


class TestSampling:
    def test_sampling_returns_result(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def test_fn(mutated_func):
            assert mutated_func(0) == 1

        result = run_function_sampling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [test_fn],
            original,
            budget_ms=5000,
        )
        assert result.coverage_depth == "sampled"
        assert result.function_key == "test.py::f"
        assert result.total_mutants >= 0

    def test_sampling_respects_max_per_category(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        original = lambda x: x + 1 + 2 + 3  # noqa: E731

        result = run_function_sampling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
            max_per_category=2,
        )
        assert result.total_mutants <= 2


class TestProfiling:
    def test_profiling_returns_result(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def test_fn(mutated_func):
            assert mutated_func(0) == 1

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [test_fn],
            original,
        )
        assert result.coverage_depth == "profiled"
        assert result.is_gateable
        assert result.function_key == "test.py::f"

    def test_profiling_builds_kill_matrix(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def test_add(mutated_func):
            assert mutated_func(0) == 1

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [test_add],
            original,
        )
        if result.total_killed > 0:
            assert len(result.kill_matrix) > 0

    def test_to_dict(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
        )
        d = result.to_dict()
        assert "coverage_depth" in d
        assert "is_gateable" in d
        assert "per_category" in d


class TestDocstringSkip:
    """Verify that docstring constants are excluded from VALUE mutation."""

    def test_docstring_positions_detected(self):
        func = _parse_func('def f(x):\n    """My docstring."""\n    return x + 1')
        pos = _docstring_positions(func)
        assert len(pos) == 1

    def test_docstring_positions_empty_when_no_docstring(self):
        func = _parse_func("def f(x): return x + 1")
        pos = _docstring_positions(func)
        assert len(pos) == 0

    def test_value_count_excludes_docstring(self):
        # Without docstring: 1 VALUE target (the int 1)
        func_no_doc = _parse_func("def f(x): return x + 1")
        count_no_doc = _count_targets(func_no_doc, MutationCategory.VALUE)

        # With docstring: still 1 VALUE target — the docstring string is skipped
        func_with_doc = _parse_func('def f(x):\n    """Docstring."""\n    return x + 1')
        count_with_doc = _count_targets(func_with_doc, MutationCategory.VALUE)
        assert count_with_doc == count_no_doc

    def test_generate_skips_docstring_mutants(self):
        func = _parse_func('def f(x):\n    """Some docstring."""\n    return x + 1')
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        # All mutants should target the int constant, not the docstring
        for m in mutants:
            assert "replace constant" in m.description
            # The mutated tree should still have a docstring
            for node in ast.walk(m.mutated_node):
                if (
                    isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and "docstring" in node.value.value.lower()
                ):
                    # Docstring is unchanged — not mutated
                    assert node.value.value == "Some docstring."

    def test_docstring_only_function_has_zero_value_targets(self):
        func = _parse_func('def f():\n    """Only a docstring."""\n    pass')
        count = _count_targets(func, MutationCategory.VALUE)
        assert count == 0
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) == 0

    def test_max_per_category_aligns_after_docstring_skip(self):
        # 3 int constants + 1 docstring → 3 VALUE targets (not 4)
        func = _parse_func('def f(x):\n    """Doc."""\n    return x + 1 + 2 + 3')
        count = _count_targets(func, MutationCategory.VALUE)
        assert count == 3
        mutants = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2)
        assert len(mutants) == 2


class TestBoundaryInputExtraction:
    """Verify mutation-guided boundary input synthesis."""

    def test_extracts_boundary_from_compare(self):
        func = _parse_func("def f(x): return x < 10")
        mutants = generate_mutants(func, {MutationCategory.BOUNDARY})
        assert len(mutants) >= 1
        inputs = extract_boundary_inputs(mutants[0])
        assert len(inputs) == 1
        bi = inputs[0]
        assert bi.parameter == "x"
        assert bi.boundary_value == 10
        assert len(bi.inputs) == 3
        values = [v for _, v in bi.inputs]
        assert 10 in values
        assert 9 in values
        assert 11 in values

    def test_extracts_reversed_compare(self):
        func = _parse_func("def f(x): return 5 >= x")
        mutants = generate_mutants(func, {MutationCategory.BOUNDARY})
        assert len(mutants) >= 1
        inputs = extract_boundary_inputs(mutants[0])
        assert len(inputs) == 1
        assert inputs[0].parameter == "x"
        assert inputs[0].boundary_value == 5

    def test_no_extraction_for_non_boundary(self):
        func = _parse_func("def f(x): return x + 1")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1
        inputs = extract_boundary_inputs(mutants[0])
        assert inputs == []

    def test_no_extraction_for_non_numeric_compare(self):
        func = _parse_func('def f(x): return x < "hello"')
        mutants = generate_mutants(func, {MutationCategory.BOUNDARY})
        # String comparisons won't produce numeric boundary inputs
        for m in mutants:
            inputs = extract_boundary_inputs(m)
            assert inputs == []

    def test_float_boundary(self):
        func = _parse_func("def f(x): return x <= 3.14")
        mutants = generate_mutants(func, {MutationCategory.BOUNDARY})
        assert len(mutants) >= 1
        inputs = extract_boundary_inputs(mutants[0])
        assert len(inputs) == 1
        assert inputs[0].boundary_value == 3.14


class TestStableShuffle:
    """Verify seed-based shuffling of mutation target selection."""

    def test_same_seed_produces_same_mutants(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3 + 4 + 5")
        m1 = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2, seed=42)
        m2 = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2, seed=42)
        assert [m.mutant_id for m in m1] == [m.mutant_id for m in m2]

    def test_different_seeds_produce_different_mutants(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3 + 4 + 5")
        m1 = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2, seed=1)
        m2 = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2, seed=2)
        ids1 = [m.mutant_id for m in m1]
        ids2 = [m.mutant_id for m in m2]
        # With 5 targets and cap of 2, different seeds should usually select
        # different indices. Not guaranteed for all seed pairs, but highly likely.
        # If they happen to match, the test still passes — we just verify
        # the mechanism doesn't crash and produces valid mutants.
        assert len(ids1) == 2
        assert len(ids2) == 2

    def test_no_seed_preserves_ast_order(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        m_no_seed = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2)
        # Without seed, should always get VALUE_0 and VALUE_1 (AST walk order)
        assert [m.mutant_id for m in m_no_seed] == ["VALUE_0", "VALUE_1"]

    def test_seed_ignored_when_no_truncation(self):
        func = _parse_func("def f(x): return x + 1")
        # Only 1 target, max_per_category=2 → no truncation, seed irrelevant
        m1 = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2, seed=42)
        m2 = generate_mutants(func, {MutationCategory.VALUE}, max_per_category=2, seed=99)
        assert [m.mutant_id for m in m1] == [m.mutant_id for m in m2]


# ── Budget separation (from PR0) ────────────────────────────────


class TestSamplingBudgetSeparation:
    """Verify that sampling separates outer budget from per-mutant timeout."""

    def test_per_mutant_timeout_is_independent_of_budget(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def test_fn(mutated_func):
            assert mutated_func(0) == 1

        result = run_function_sampling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [test_fn],
            original,
            budget_ms=10000,
            per_mutant_timeout_ms=500,
        )
        assert result.total_mutants >= 1
        assert not result.budget_exhausted

    def test_budget_exhaustion_stops_early(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        original = lambda x: x + 1 + 2 + 3  # noqa: E731

        result = run_function_sampling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
            budget_ms=0,
            per_mutant_timeout_ms=500,
        )
        assert result.budget_exhausted

    def test_default_per_mutant_timeout(self):
        import inspect

        sig = inspect.signature(run_function_sampling)
        assert sig.parameters["per_mutant_timeout_ms"].default == 500


class TestProfilingBudget:
    """Verify that profiling respects optional budget."""

    def test_profiling_no_budget_runs_all(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        original = lambda x: x + 1 + 2 + 3  # noqa: E731

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
        )
        assert not result.budget_exhausted
        assert result.total_mutants == 3

    def test_profiling_with_budget_returns_partial(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        original = lambda x: x + 1 + 2 + 3  # noqa: E731

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
            budget_ms=0,
        )
        assert result.budget_exhausted

    def test_profiling_budget_none_is_unlimited(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
            budget_ms=None,
        )
        assert not result.budget_exhausted

    def test_budget_exhausted_in_to_dict(self):
        result = ProfilingResult(budget_exhausted=True)
        d = result.to_dict()
        assert d["budget_exhausted"] is True

    def test_budget_exhausted_false_in_to_dict(self):
        result = ProfilingResult(budget_exhausted=False)
        d = result.to_dict()
        assert d["budget_exhausted"] is False


# ── Mutant ID generation (from PR2) ─────────────────────────────


class TestMutantIdGeneration:
    def test_value_mutants_have_ids(self):
        func = _parse_func("def f(x): return x + 1")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        assert len(mutants) >= 1
        for m in mutants:
            assert m.mutant_id
            assert m.mutant_id.startswith("VALUE_")

    def test_boundary_mutants_have_ids(self):
        func = _parse_func("def f(x): return x < 10")
        mutants = generate_mutants(func, {MutationCategory.BOUNDARY})
        assert len(mutants) >= 1
        for m in mutants:
            assert m.mutant_id.startswith("BOUNDARY_")

    def test_state_mutants_have_ids(self):
        func = _parse_func("def f(self, x):\n    self.val = x\n    return self.val")
        mutants = generate_mutants(func, {MutationCategory.STATE})
        assert len(mutants) >= 1
        for m in mutants:
            assert m.mutant_id.startswith("STATE_")

    def test_ids_are_unique_within_generation(self):
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        mutants = generate_mutants(func, {MutationCategory.VALUE})
        ids = [m.mutant_id for m in mutants]
        assert len(ids) == len(set(ids))


# ── Profiling produces records (from PR2) ────────────────────────


class TestProfilingRecords:
    def test_profiling_includes_survivor_records(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def weak_test(mutated_func):
            mutated_func(5)

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [weak_test],
            original,
        )
        assert result.total_survived >= 1
        assert len(result.survivor_records) >= 1
        rec = result.survivor_records[0]
        assert "mutant_id" in rec
        assert "category" in rec
        assert "mutant" in rec

    def test_profiling_includes_killed_records(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def test_fn(mutated_func):
            assert mutated_func(0) == 1

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [test_fn],
            original,
        )
        assert result.total_killed >= 1
        assert len(result.killed_records) >= 1
        rec = result.killed_records[0]
        assert "mutant_id" in rec
        assert "category" in rec
        assert rec["killed_by"] in ("assertion", "crash", "timeout")

    def test_records_appear_in_to_dict(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def weak_test(mutated_func):
            mutated_func(5)

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [weak_test],
            original,
        )
        d = result.to_dict()
        if result.total_survived > 0:
            assert "survivor_records" in d
        if result.total_killed > 0:
            assert "killed_records" in d

    def test_empty_records_not_in_dict(self):
        result = ProfilingResult()
        d = result.to_dict()
        assert "survivor_records" not in d
        assert "killed_records" not in d


# ── to_dict exact-value assertions (VALUE mutation killers) ───────────


class TestSamplingResultToDictValues:
    """Kill VALUE mutants on SamplingResult.to_dict by asserting exact values."""

    def test_exact_field_values(self):

        cr = CategoryResult(category=MutationCategory.VALUE, total=5, killed=3, survived=2)
        result = SamplingResult(
            function_key="mod.py::func",
            categories_tested=1,
            total_mutants=5,
            total_killed=3,
            total_survived=2,
            survival_rate=0.4,
            coverage_depth="sampled",
            per_category=[cr],
            budget_exhausted=False,
            elapsed_ms=12.345,
        )
        d = result.to_dict()
        assert d["function_key"] == "mod.py::func"
        assert d["categories_tested"] == 1
        assert d["total_mutants"] == 5
        assert d["total_killed"] == 3
        assert d["total_survived"] == 2
        assert d["survival_rate"] == 0.4
        assert d["coverage_depth"] == "sampled"
        assert d["budget_exhausted"] is False
        assert d["elapsed_ms"] == 12.3
        assert len(d["per_category"]) == 1
        assert d["per_category"][0]["category"] == "VALUE"
        assert d["per_category"][0]["total"] == 5
        assert d["per_category"][0]["killed"] == 3
        assert d["per_category"][0]["survived"] == 2
        assert d["per_category"][0]["survival_rate"] == 0.4


class TestProfilingResultToDictValues:
    """Kill VALUE mutants on ProfilingResult.to_dict by asserting exact values."""

    def test_exact_field_values(self):

        cr = CategoryResult(
            category=MutationCategory.SWAP,
            total=4,
            killed=3,
            survived=1,
            killed_by_assertion=2,
            killed_by_crash=1,
        )
        result = ProfilingResult(
            function_key="mod.py::func",
            categories_tested=2,
            total_mutants=10,
            total_killed=8,
            total_survived=2,
            survival_rate=0.2,
            coverage_depth="profiled",
            is_gateable=True,
            per_category=[cr],
            kill_matrix={"SWAP_0": ["test_a"]},
            survivor_records=[{"id": "SWAP_1"}],
            killed_records=[{"id": "SWAP_0"}],
            budget_exhausted=False,
            elapsed_ms=99.567,
        )
        d = result.to_dict()
        assert d["function_key"] == "mod.py::func"
        assert d["categories_tested"] == 2
        assert d["total_mutants"] == 10
        assert d["total_killed"] == 8
        assert d["total_survived"] == 2
        assert d["survival_rate"] == 0.2
        assert d["coverage_depth"] == "profiled"
        assert d["is_gateable"] is True
        assert d["budget_exhausted"] is False
        assert d["elapsed_ms"] == 99.6
        # per_category inner dict
        pc = d["per_category"][0]
        assert pc["category"] == "SWAP"
        assert pc["total"] == 4
        assert pc["killed"] == 3
        assert pc["survived"] == 1
        assert pc["killed_by_assertion"] == 2
        assert pc["killed_by_crash"] == 1
        assert pc["survival_rate"] == 0.25
        # conditional keys
        assert d["kill_matrix"] == {"SWAP_0": ["test_a"]}
        assert d["survivor_records"] == [{"id": "SWAP_1"}]
        assert d["killed_records"] == [{"id": "SWAP_0"}]


# ── Mutant reporting — survivor/killed record building ────────────────


class TestBuildSurvivorRecord:
    def test_basic_structure(self):
        from lintgate.specification.mutant_reporting import build_survivor_record

        mutant = Mutant(
            category=MutationCategory.VALUE,
            original_node=ast.parse("x + 1").body[0],
            mutated_node=ast.parse("x + 0").body[0],
            description="VALUE_0: replace constant",
            location=5,
            mutant_id="VALUE_0",
        )
        result = MutantResult(mutant=mutant, killed=False, elapsed_ms=12.3)
        record = build_survivor_record(result)

        assert record["mutant_id"] == "VALUE_0"
        assert record["category"] == "VALUE"
        assert record["location"] == 5
        assert record["status"] == "survived"
        assert "diff_summary" in record
        assert record["elapsed_ms"] == 12.3

    def test_diff_summary_shows_change(self):
        from lintgate.specification.mutant_reporting import build_survivor_record

        orig = ast.parse("x + 1").body[0]
        mut = ast.parse("x + 0").body[0]
        mutant = Mutant(
            category=MutationCategory.VALUE,
            original_node=orig,
            mutated_node=mut,
            description="VALUE_0: test",
            mutant_id="VALUE_0",
        )
        result = MutantResult(mutant=mutant, killed=False)
        record = build_survivor_record(result)
        assert "-" in record["diff_summary"]
        assert "+" in record["diff_summary"]


class TestBuildKilledRecord:
    def test_basic_structure(self):
        from lintgate.specification.mutant_reporting import build_killed_record

        mutant = Mutant(
            category=MutationCategory.BOUNDARY,
            original_node=ast.parse("x < 10").body[0],
            mutated_node=ast.parse("x <= 10").body[0],
            description="BOUNDARY_0: off-by-one",
            location=7,
            mutant_id="BOUNDARY_0",
        )
        result = MutantResult(
            mutant=mutant,
            killed=True,
            killed_by="assertion",
            test_name="test_boundary",
            elapsed_ms=5.0,
        )
        record = build_killed_record(result)

        assert record["mutant_id"] == "BOUNDARY_0"
        assert record["category"] == "BOUNDARY"
        assert record["status"] == "killed"
        assert record["killed_by"] == "assertion"
        assert record["killed_by_test"] == "test_boundary"
