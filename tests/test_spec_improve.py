"""Tests for the one-shot spec_improve tool.

Validates the diagnose → profile → prescribe pipeline and action plan
generation.
"""

from __future__ import annotations

from mcp_tools._mutation_tools_impl import _build_action_plan

# ── Action plan builder ──────────────────────────────────────────


class TestBuildActionPlan:
    def test_empty_targets(self):
        plan = _build_action_plan([], [])
        assert plan == []

    def test_targets_without_prescriptions(self):
        targets = [
            ("mod.py::f", {"specification_level": 0.2, "phase": "bulk"}),
        ]
        plan = _build_action_plan(targets, [])
        assert len(plan) == 1
        assert plan[0]["function"] == "mod.py::f"
        assert plan[0]["next_test"] is None
        assert "note" in plan[0]

    def test_targets_with_matching_prescriptions(self):
        targets = [
            ("mod.py::f", {"specification_level": 0.3, "phase": "transition"}),
        ]
        prescriptions = [
            {
                "function": "mod.py::f",
                "category": "VALUE",
                "why_this_matters": "Constant replacement survived",
                "assertion_shape": "assert f(x) == expected",
                "confidence": 0.8,
            },
        ]
        plan = _build_action_plan(targets, prescriptions)
        assert len(plan) == 1
        assert plan[0]["next_test"] is not None
        assert plan[0]["next_test"]["category"] == "VALUE"
        assert plan[0]["next_test"]["confidence"] == 0.8
        assert plan[0]["total_prescriptions"] == 1

    def test_multiple_prescriptions_takes_first(self):
        targets = [
            ("mod.py::f", {"specification_level": 0.1, "phase": "bulk"}),
        ]
        prescriptions = [
            {
                "function": "mod.py::f",
                "category": "VALUE",
                "why_this_matters": "First",
                "assertion_shape": "assert f(x) == 1",
                "confidence": 0.9,
            },
            {
                "function": "mod.py::f",
                "category": "SWAP",
                "why_this_matters": "Second",
                "assertion_shape": "assert f(a, b) != f(b, a)",
                "confidence": 0.7,
            },
        ]
        plan = _build_action_plan(targets, prescriptions)
        assert plan[0]["next_test"]["category"] == "VALUE"
        assert plan[0]["total_prescriptions"] == 2

    def test_prescriptions_for_different_functions(self):
        targets = [
            ("mod.py::f", {"specification_level": 0.2, "phase": "bulk"}),
            ("mod.py::g", {"specification_level": 0.4, "phase": "transition"}),
        ]
        prescriptions = [
            {
                "function": "mod.py::g",
                "category": "BOUNDARY",
                "why_this_matters": "Off-by-one",
                "assertion_shape": "",
                "confidence": 0.85,
            },
        ]
        plan = _build_action_plan(targets, prescriptions)
        assert len(plan) == 2
        assert plan[0]["next_test"] is None  # f has no prescriptions
        assert plan[1]["next_test"]["category"] == "BOUNDARY"

    def test_spec_level_in_output(self):
        targets = [
            ("mod.py::f", {"specification_level": 0.123456, "phase": "bulk"}),
        ]
        plan = _build_action_plan(targets, [])
        assert plan[0]["spec_level"] == 0.123

    def test_fallback_prescription_keys(self):
        """Prescriptions may use 'kind' and 'description' instead of category-specific keys."""
        targets = [
            ("mod.py::f", {"specification_level": 0.3, "phase": "transition"}),
        ]
        prescriptions = [
            {
                "function": "mod.py::f",
                "kind": "boundary_test",
                "description": "Add boundary check",
                "confidence": 0.6,
            },
        ]
        plan = _build_action_plan(targets, prescriptions)
        assert plan[0]["next_test"]["category"] == "boundary_test"
        assert "boundary" in plan[0]["next_test"]["why"].lower()
