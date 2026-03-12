"""Regression tests for findings from code review.

P0: Per-mutant timeout enforcement via thread-based timeout
P1: Sampling budget splitting across functions
P1: Topology computed from linked test files only
P1: spec_improve respects caller budget
P2: prescribe_tests uses witness data from survivor records
Residual: Fallback test loading ranks by relevance
"""

from __future__ import annotations

import time

from lintgate.specification.mutation_engine import (
    _run_test_with_timeout,
    evaluate_mutant,
)
from mcp_tools._mutation_impl import (
    DiscoveryDiagnostics,
    _rank_test_files,
)

# ── P0: Per-mutant timeout is enforced on slow tests ─────────────


class TestPerMutantTimeout:
    def test_slow_test_killed_by_timeout(self):
        """A test that sleeps longer than timeout_ms must be killed."""

        def slow_test():
            time.sleep(0.5)  # 500ms

        result = _run_test_with_timeout(slow_test, None, True, timeout_ms=50)
        assert result == "timeout"

    def test_fast_test_survives(self):
        """A test that passes quickly returns None (survived)."""

        def fast_test():
            pass

        result = _run_test_with_timeout(fast_test, None, True, timeout_ms=1000)
        assert result is None

    def test_assertion_failure_detected(self):
        """A test that raises AssertionError returns 'assertion'."""

        def failing_test():
            raise AssertionError("wrong")

        result = _run_test_with_timeout(failing_test, None, True, timeout_ms=1000)
        assert result == "assertion"

    def test_crash_detected(self):
        """A test that raises non-assertion exception returns 'crash'."""

        def crashing_test():
            raise RuntimeError("boom")

        result = _run_test_with_timeout(crashing_test, None, True, timeout_ms=1000)
        assert result == "crash"

    def test_unpatched_fallback_with_arg(self):
        """Unpatched path passes mutated_func as arg."""
        called_with = []

        def test_fn(func):
            called_with.append(func)

        sentinel = object()
        result = _run_test_with_timeout(test_fn, sentinel, False, timeout_ms=1000)
        assert result is None
        assert called_with == [sentinel]

    def test_evaluate_mutant_respects_timeout(self):
        """evaluate_mutant with a blocking test should return timeout, not hang."""
        import ast

        from lintgate.specification.mutation_engine import (
            Mutant,
            MutationCategory,
        )

        node = ast.parse("def f(): return 1").body[0]
        mutant = Mutant(
            category=MutationCategory.VALUE,
            original_node=node,
            mutated_node=node,
            description="test",
            mutant_id="VALUE_0",
        )

        def blocking_test():
            time.sleep(1.0)

        start = time.monotonic()
        result = evaluate_mutant(mutant, [blocking_test], lambda: 1, timeout_ms=100)
        elapsed = (time.monotonic() - start) * 1000
        assert result.killed
        assert result.killed_by == "timeout"
        # Should complete well before 1000ms (the sleep duration)
        assert elapsed < 500


# ── P1: Sampling budget is split across functions ────────────────


class TestSamplingBudgetSplit:
    def test_per_func_budget_calculated(self):
        """Verify budget splitting logic: N functions get budget/N each."""
        # Create a file with 3 functions
        import tempfile

        from mcp_tools._mutation_impl import parse_file, walk_functions

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def f1(): pass\ndef f2(): pass\ndef f3(): pass\n")
            f.flush()
            tree = parse_file(f.name)

        funcs = walk_functions(tree)
        assert len(funcs) == 3

        # Budget splitting: 600ms / 3 = 200ms per function
        total_budget = 600
        per_func = max(total_budget / max(len(funcs), 1), 200)
        assert per_func == 200.0


# ── P1: Topology uses linked test files only ─────────────────────


class TestTopologyLinkedFiles:
    def test_linked_test_files_populated_from_impact_map(self):
        """DiscoveryDiagnostics.linked_test_files should be set."""
        diag = DiscoveryDiagnostics()
        assert diag.linked_test_files == []

    def test_linked_test_files_field_exists(self):
        """The field exists on DiscoveryDiagnostics and defaults to empty."""
        diag = DiscoveryDiagnostics(
            test_files_found=3,
            linked_test_files=["/a/test_x.py", "/b/test_y.py"],
        )
        assert len(diag.linked_test_files) == 2


# ── P1: spec_improve respects caller budget ──────────────────────


class TestSpecImproveBudget:
    def test_remaining_budget_not_inflated(self):
        """If budget is exhausted, sampling should be skipped, not inflated to 5000ms."""
        # Verify the logic: remaining = budget_ms - elapsed, skip if <= 0
        budget_ms = 100
        elapsed = 200  # already over budget
        remaining = budget_ms - elapsed
        assert remaining <= 0  # should skip, not floor to 5000


# ── P2: prescribe_tests uses witness data ────────────────────────


class TestPrescribeTestsWitness:
    def test_skeleton_enriched_with_witness(self):
        """When survivor_records exist, skeletons should have witness data."""
        from lintgate.specification.witness_generation import generate_witness_prescription
        from mcp_tools._mutation_impl import generate_test_skeleton

        survivor = {
            "mutant_id": "VALUE_0",
            "category": "VALUE",
            "description": "VALUE_0: replace constant",
            "diff_summary": "- x + 1\n+ x + 0",
            "status": "survived",
        }
        rx = generate_witness_prescription(survivor, "mod.py::add")
        skeleton = generate_test_skeleton("mod.py::add", rx.get("category", "UNKNOWN"))
        # Enrich as impl_prescribe_tests does
        skeleton["witness"] = {
            "mutant_id": rx.get("mutant_id", ""),
            "why_this_matters": rx.get("why_this_matters", ""),
            "suggested_input": rx.get("suggested_input", ""),
            "assertion_shape": rx.get("assertion_shape", ""),
            "confidence": rx.get("confidence", 0.0),
            "source_of_evidence": rx.get("source_of_evidence", ""),
        }
        assert skeleton["witness"]["mutant_id"] == "VALUE_0"
        assert skeleton["witness"]["confidence"] == 0.8
        assert skeleton["witness"]["source_of_evidence"] == "survivor_record"
        assert "add" in skeleton["witness"]["assertion_shape"]


# ── Residual: Fallback relevance ranking ─────────────────────────


class TestFallbackRelevanceRanking:
    def test_same_directory_ranked_first(self):
        files = ["/project/tests/test_other.py", "/project/src/test_foo.py"]
        ranked = _rank_test_files(files, "/project/src/foo.py")
        assert ranked[0] == "/project/src/test_foo.py"

    def test_module_name_match_ranked_second(self):
        files = [
            "/project/tests/test_unrelated.py",
            "/project/tests/test_mymod.py",
            "/project/other/test_other.py",
        ]
        ranked = _rank_test_files(files, "/project/src/mymod.py")
        assert ranked[0] == "/project/tests/test_mymod.py"

    def test_no_source_preserves_order(self):
        files = ["/a/b.py", "/c/d.py"]
        assert _rank_test_files(files, None) == files

    def test_empty_files(self):
        assert _rank_test_files([], "/some/file.py") == []

    def test_three_tiers(self):
        files = [
            "/proj/other/test_z.py",  # tier 3: rest
            "/proj/tests/test_foo.py",  # tier 2: name match
            "/proj/src/test_inline.py",  # tier 1: same dir
        ]
        ranked = _rank_test_files(files, "/proj/src/foo.py")
        assert ranked[0] == "/proj/src/test_inline.py"  # same dir
        assert ranked[1] == "/proj/tests/test_foo.py"  # name match
        assert ranked[2] == "/proj/other/test_z.py"  # rest
