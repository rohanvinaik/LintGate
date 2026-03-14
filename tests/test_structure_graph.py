"""Tests for structure graph enrichment (Deliverable A).

Covers: reverse import graph, module fan-in, directed call graph,
function fan metrics, removal impact, and split-proposal annotation.
"""

from __future__ import annotations

import ast
import textwrap

from lintgate.channels.structure_graph import (
    annotate_proposals_with_fan_in,
    build_directed_call_graph,
    build_reverse_import_graph,
    compute_function_fan_metrics,
    compute_module_fan_in,
    compute_removal_impact,
)
from lintgate.types import Prescription

# ── Reverse Import Graph ─────────────────────────────────────────────────


class TestReverseImportGraph:
    def test_basic(self) -> None:
        """A imports B,C; B imports C → reverse: B←{A}, C←{A,B}."""
        forward = {
            "A": {"B", "C"},
            "B": {"C"},
        }
        reverse = build_reverse_import_graph(forward)
        assert reverse["B"] == {"A"}
        assert reverse["C"] == {"A", "B"}
        assert reverse["A"] == set()

    def test_empty(self) -> None:
        """Empty graph → empty reverse."""
        assert build_reverse_import_graph({}) == {}

    def test_single_node_no_edges(self) -> None:
        """Single module with no imports → appears with empty set."""
        forward: dict[str, set[str]] = {"A": set()}
        reverse = build_reverse_import_graph(forward)
        assert reverse == {"A": set()}

    def test_mutual_imports(self) -> None:
        """A↔B → both appear as importers of each other."""
        forward = {"A": {"B"}, "B": {"A"}}
        reverse = build_reverse_import_graph(forward)
        assert reverse["A"] == {"B"}
        assert reverse["B"] == {"A"}

    def test_all_modules_present(self) -> None:
        """All modules (keys and values) appear in reverse graph."""
        forward = {"A": {"B", "C"}, "D": {"C"}}
        reverse = build_reverse_import_graph(forward)
        # All four modules present
        assert set(reverse.keys()) == {"A", "B", "C", "D"}


# ── Module Fan-In ────────────────────────────────────────────────────────


class TestModuleFanIn:
    def test_counts_match_reverse(self) -> None:
        """Fan-in counts match reverse graph cardinalities."""
        reverse = {"A": set(), "B": {"A"}, "C": {"A", "B"}}
        file_map = {"A": "/a.py", "B": "/b.py", "C": "/c.py"}
        fan_in = compute_module_fan_in(reverse, file_map)
        assert fan_in == {"A": 0, "B": 1, "C": 2}

    def test_zero_for_leaf(self) -> None:
        """Module with no importers → fan-in 0."""
        reverse = {"leaf": set(), "root": {"leaf"}}
        file_map = {"leaf": "/leaf.py", "root": "/root.py"}
        fan_in = compute_module_fan_in(reverse, file_map)
        assert fan_in["leaf"] == 0

    def test_module_not_in_reverse(self) -> None:
        """Module in file_map but absent from reverse_graph → 0."""
        reverse = {"A": {"B"}}
        file_map = {"A": "/a.py", "B": "/b.py", "C": "/c.py"}
        fan_in = compute_module_fan_in(reverse, file_map)
        assert fan_in["C"] == 0

    def test_empty_inputs(self) -> None:
        assert compute_module_fan_in({}, {}) == {}


# ── Directed Call Graph ──────────────────────────────────────────────────


class TestDirectedCallGraph:
    def _parse(self, code: str) -> ast.Module:
        return ast.parse(textwrap.dedent(code))

    def test_basic_calls(self) -> None:
        """A calls B and C → calls[A]={B,C}, called_by[B]={A}, called_by[C]={A}."""
        tree = self._parse("""
            def A():
                B()
                C()

            def B():
                pass

            def C():
                pass
        """)
        calls, called_by = build_directed_call_graph(tree)
        assert calls["A"] == {"B", "C"}
        assert called_by["B"] == {"A"}
        assert called_by["C"] == {"A"}
        assert calls["B"] == set()
        assert calls["C"] == set()

    def test_self_ref_excluded(self) -> None:
        """Recursive call is not counted as an edge."""
        tree = self._parse("""
            def factorial(n):
                if n <= 1:
                    return 1
                return n * factorial(n - 1)
        """)
        calls, called_by = build_directed_call_graph(tree)
        assert calls["factorial"] == set()
        assert called_by["factorial"] == set()

    def test_bidirectional(self) -> None:
        """A calls B and B calls A → directed edges in both directions."""
        tree = self._parse("""
            def A():
                B()

            def B():
                A()
        """)
        calls, called_by = build_directed_call_graph(tree)
        assert calls["A"] == {"B"}
        assert calls["B"] == {"A"}
        assert called_by["A"] == {"B"}
        assert called_by["B"] == {"A"}

    def test_classes_included(self) -> None:
        """Class definitions are part of the call graph."""
        tree = self._parse("""
            class MyClass:
                pass

            def use_class():
                return MyClass()
        """)
        calls, called_by = build_directed_call_graph(tree)
        assert "MyClass" in calls["use_class"]

    def test_empty_file(self) -> None:
        tree = self._parse("")
        calls, called_by = build_directed_call_graph(tree)
        assert calls == {}
        assert called_by == {}


# ── Function Fan Metrics ─────────────────────────────────────────────────


class TestFunctionFanMetrics:
    def test_basic_metrics(self) -> None:
        calls = {"A": {"B", "C"}, "B": set(), "C": {"B"}}
        called_by = {"A": set(), "B": {"A", "C"}, "C": {"A"}}
        metrics = compute_function_fan_metrics(calls, called_by)
        assert metrics["A"] == {"fan_in": 0, "fan_out": 2}
        assert metrics["B"] == {"fan_in": 2, "fan_out": 0}
        assert metrics["C"] == {"fan_in": 1, "fan_out": 1}

    def test_empty(self) -> None:
        assert compute_function_fan_metrics({}, {}) == {}


# ── Removal Impact ───────────────────────────────────────────────────────


class TestRemovalImpact:
    def test_safe_to_remove(self) -> None:
        """0 importers → safe_to_remove=True."""
        reverse = {"leaf": set(), "root": {"leaf"}}
        result = compute_removal_impact("leaf", reverse)
        assert result["safe_to_remove"] is True
        assert result["importer_count"] == 0
        assert result["direct_importers"] == []

    def test_unsafe_to_remove(self) -> None:
        """3 importers → safe_to_remove=False."""
        reverse = {"core": {"A", "B", "C"}}
        result = compute_removal_impact("core", reverse)
        assert result["safe_to_remove"] is False
        assert result["importer_count"] == 3
        assert sorted(result["direct_importers"]) == ["A", "B", "C"]

    def test_unknown_module(self) -> None:
        """Module not in reverse graph → safe to remove (no importers known)."""
        result = compute_removal_impact("unknown", {})
        assert result["safe_to_remove"] is True
        assert result["importer_count"] == 0


# ── Split Proposal Annotation ───────────────────────────────────────────


class TestAnnotateProposals:
    def _make_proposal(self, target: str = "file.py") -> Prescription:
        return Prescription(
            kind="split_file",
            target=target,
            action="Split into separate module",
            source="static",
            confidence=0.55,
        )

    def test_high_fan_in_adds_caution(self) -> None:
        """fan_in >= 3 → caution note appended to action."""
        proposal = self._make_proposal()
        fan_in = {"mod": 5}
        annotate_proposals_with_fan_in([proposal], fan_in, "mod")
        assert "5 importers" in proposal.action
        assert proposal.expected_delta["importers_affected"] == 5

    def test_zero_fan_in_no_annotation(self) -> None:
        """fan_in = 0 → no caution note."""
        proposal = self._make_proposal()
        original_action = proposal.action
        fan_in = {"mod": 0}
        annotate_proposals_with_fan_in([proposal], fan_in, "mod")
        assert proposal.action == original_action
        assert proposal.expected_delta["importers_affected"] == 0

    def test_low_fan_in_no_caution(self) -> None:
        """fan_in = 2 (below threshold 3) → no caution note."""
        proposal = self._make_proposal()
        original_action = proposal.action
        fan_in = {"mod": 2}
        annotate_proposals_with_fan_in([proposal], fan_in, "mod")
        assert proposal.action == original_action
        assert proposal.expected_delta["importers_affected"] == 2

    def test_unknown_module(self) -> None:
        """Module not in fan_in dict → treated as 0."""
        proposal = self._make_proposal()
        annotate_proposals_with_fan_in([proposal], {}, "unknown")
        assert proposal.expected_delta["importers_affected"] == 0

    def test_no_duplicate_caution(self) -> None:
        """Calling twice doesn't duplicate the caution note."""
        proposal = self._make_proposal()
        fan_in = {"mod": 4}
        annotate_proposals_with_fan_in([proposal], fan_in, "mod")
        annotate_proposals_with_fan_in([proposal], fan_in, "mod")
        assert proposal.action.count("importers") == 1
