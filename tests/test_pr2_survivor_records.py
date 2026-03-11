"""PR2: Survivor records as first-class data tests.

Validates that profiling produces per-mutant survivor/killed records
with stable IDs, diff summaries, and enough detail for grounded prescriptions.
"""

from __future__ import annotations

import ast

from lintgate.specification.mutant_reporting import (
    build_killed_record,
    build_survivor_record,
)
from lintgate.specification.mutation_engine import (
    Mutant,
    MutantResult,
    MutationCategory,
    ProfilingResult,
    generate_mutants,
    run_function_profiling,
)


def _parse_func(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    msg = "No function found"
    raise ValueError(msg)


# ── Mutant ID generation ─────────────────────────────────────────


class TestMutantId:
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


# ── Survivor / killed record building ────────────────────────────


class TestBuildSurvivorRecord:
    def test_basic_structure(self):
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
        # Should contain both - and + lines
        assert "-" in record["diff_summary"]
        assert "+" in record["diff_summary"]


class TestBuildKilledRecord:
    def test_basic_structure(self):
        mutant = Mutant(
            category=MutationCategory.BOUNDARY,
            original_node=ast.parse("x < 10").body[0],
            mutated_node=ast.parse("x <= 10").body[0],
            description="BOUNDARY_0: off-by-one",
            location=7,
            mutant_id="BOUNDARY_0",
        )
        result = MutantResult(
            mutant=mutant, killed=True, killed_by="assertion",
            test_name="test_boundary", elapsed_ms=5.0,
        )
        record = build_killed_record(result)

        assert record["mutant_id"] == "BOUNDARY_0"
        assert record["category"] == "BOUNDARY"
        assert record["status"] == "killed"
        assert record["killed_by"] == "assertion"
        assert record["killed_by_test"] == "test_boundary"


# ── Profiling produces records ───────────────────────────────────


class TestProfilingRecords:
    def test_profiling_includes_survivor_records(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        # Weak test — doesn't check return value, so mutant survives
        def weak_test(mutated_func):
            mutated_func(5)

        result = run_function_profiling(
            func, "test.py::f", {MutationCategory.VALUE},
            [weak_test], original,
        )
        assert result.total_survived >= 1
        assert len(result.survivor_records) >= 1
        rec = result.survivor_records[0]
        assert "mutant_id" in rec
        assert rec["status"] == "survived"
        assert "diff_summary" in rec

    def test_profiling_includes_killed_records(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def test_fn(mutated_func):
            assert mutated_func(0) == 1

        result = run_function_profiling(
            func, "test.py::f", {MutationCategory.VALUE},
            [test_fn], original,
        )
        assert result.total_killed >= 1
        assert len(result.killed_records) >= 1
        rec = result.killed_records[0]
        assert "mutant_id" in rec
        assert rec["status"] == "killed"
        assert rec["killed_by"] in ("assertion", "crash", "timeout")

    def test_records_appear_in_to_dict(self):
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def weak_test(mutated_func):
            mutated_func(5)

        result = run_function_profiling(
            func, "test.py::f", {MutationCategory.VALUE},
            [weak_test], original,
        )
        d = result.to_dict()
        if result.total_survived > 0:
            assert "survivor_records" in d
        if result.total_killed > 0:
            assert "killed_records" in d

    def test_empty_records_not_in_dict(self):
        """When no survivors/killed, records shouldn't appear in dict."""
        result = ProfilingResult()
        d = result.to_dict()
        assert "survivor_records" not in d
        assert "killed_records" not in d
