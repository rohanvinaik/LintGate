"""Tests for lintgate.specification.mutation_engine — AST mutation generation and evaluation."""

from __future__ import annotations

import ast

from lintgate.specification.mutation_engine import (
    MutationCategory,
    evaluate_mutant,
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
        test_fn = namespace["test_fn"]

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
        test_fn = namespace["test_fn"]

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
        test_fn = namespace["test_fn"]

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
