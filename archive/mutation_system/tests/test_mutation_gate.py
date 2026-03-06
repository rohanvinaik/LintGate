"""Tests for mutation × algebra gate (#207).

Covers:
- specification_strength property
- is_gateable with SAMPLED+HIGH
- classify_properties depth/confidence/assertion-aware gate
- MUTCH004 finding emission
"""

from __future__ import annotations

import ast

import pytest

from lintgate.channels.mutation_channel import MutationChannel
from lintgate.linters.performance_checks.algebra_types import PropertyKind, PurityResult
from lintgate.linters.performance_checks.properties import classify_properties
from lintgate.mutation.state import (
    ConfidenceLevel,
    CoverageDepth,
    FunctionMutationState,
)

# ── helpers ─────────────────────────────────────────────────────────────


def _make_node(code: str = "def f(x): return x + 1") -> ast.FunctionDef:
    return ast.parse(code).body[0]


def _make_purity(name: str = "f", confidence: float = 0.7) -> PurityResult:
    return PurityResult(name, name, 1, True, confidence, (), 1, None)


def _pure_prop(props):
    return next(p for p in props.properties if p.kind == PropertyKind.PURE)


# ── specification_strength ──────────────────────────────────────────────


class TestSpecificationStrength:
    def test_all_assertion_kills(self):
        s = FunctionMutationState(
            "f", "a.py", "h", "t", killed_by_assertion=10, killed_by_crash=0
        )
        assert s.specification_strength == 1.0

    def test_all_crash_kills(self):
        s = FunctionMutationState(
            "f", "a.py", "h", "t", killed_by_assertion=0, killed_by_crash=10
        )
        assert s.specification_strength == 0.0

    def test_mixed_kills(self):
        s = FunctionMutationState(
            "f", "a.py", "h", "t", killed_by_assertion=3, killed_by_crash=7
        )
        assert s.specification_strength == pytest.approx(0.3)

    def test_no_kills(self):
        s = FunctionMutationState(
            "f", "a.py", "h", "t", killed_by_assertion=0, killed_by_crash=0
        )
        assert s.specification_strength == 0.0

    def test_half_and_half(self):
        s = FunctionMutationState(
            "f", "a.py", "h", "t", killed_by_assertion=5, killed_by_crash=5
        )
        assert s.specification_strength == pytest.approx(0.5)


# ── is_gateable (#207 update) ───────────────────────────────────────────


class TestIsGateableUpdated:
    def test_profiled_always_gateable(self):
        s = FunctionMutationState("f", "a.py", "h", "t", depth=CoverageDepth.PROFILED)
        assert s.is_gateable is True

    def test_sampled_high_gateable(self):
        s = FunctionMutationState(
            "f",
            "a.py",
            "h",
            "t",
            depth=CoverageDepth.SAMPLED,
            confidence=ConfidenceLevel.HIGH,
        )
        assert s.is_gateable is True

    def test_sampled_medium_not_gateable(self):
        s = FunctionMutationState(
            "f",
            "a.py",
            "h",
            "t",
            depth=CoverageDepth.SAMPLED,
            confidence=ConfidenceLevel.MEDIUM,
        )
        assert s.is_gateable is False

    def test_sampled_low_not_gateable(self):
        s = FunctionMutationState(
            "f",
            "a.py",
            "h",
            "t",
            depth=CoverageDepth.SAMPLED,
            confidence=ConfidenceLevel.LOW,
        )
        assert s.is_gateable is False

    def test_none_not_gateable(self):
        s = FunctionMutationState("f", "a.py", "h", "t", depth=CoverageDepth.NONE)
        assert s.is_gateable is False


# ── classify_properties: hard gate (gateable data) ──────────────────────


class TestHardGate:
    """Tests with PROFILED depth (gateable) — confidence and hints are modified."""

    def test_high_survival_gates_confidence_and_hints(self):
        """Gateable + survival > 50% → confidence=0.1, hints=[]."""
        state = FunctionMutationState(
            "f",
            "f.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=3,
            survived=7,
            killed_by_assertion=2,
            killed_by_crash=1,
        )
        props = classify_properties(_make_node(), _make_purity(confidence=0.9), state)
        assert _pure_prop(props).confidence == 0.1
        assert "[MUTATION GATED" in _pure_prop(props).evidence
        assert len(props.optimization_hints) == 0

    def test_low_spec_strength_audit_mode_preserves_hints(self):
        """Gateable + spec_strength < 50% + audit mode + survival ≤ 20% → verified, hints preserved."""
        state = FunctionMutationState(
            "f",
            "f.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=8,
            survived=2,
            killed_by_assertion=2,
            killed_by_crash=6,  # spec=25%, survival=20%
        )
        props = classify_properties(_make_node(), _make_purity(confidence=0.9), state)
        # In audit mode (default), spec_strength alone no longer gates when survival ≤ 0.2
        assert _pure_prop(props).confidence >= 0.9
        assert len(props.optimization_hints) > 0

    def test_moderate_survival_penalizes_and_filters_hints(self):
        """Gateable + 20% < survival ≤ 50% + spec ≥ 50% → only cacheable."""
        state = FunctionMutationState(
            "f",
            "f.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=7,
            survived=3,
            killed_by_assertion=5,
            killed_by_crash=2,  # spec≈71%
        )
        props = classify_properties(_make_node(), _make_purity(confidence=0.8), state)
        assert _pure_prop(props).confidence == pytest.approx(0.4)
        assert "[MUTATION PENALIZED" in _pure_prop(props).evidence
        assert "cacheable" in props.optimization_hints
        assert "parallelizable" not in props.optimization_hints

    def test_low_survival_verifies_and_preserves_hints(self):
        """Gateable + survival ≤ 20% + spec ≥ 50% → verified, all hints kept."""
        state = FunctionMutationState(
            "f",
            "f.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=9,
            survived=1,
            killed_by_assertion=8,
            killed_by_crash=1,  # spec≈89%
        )
        props = classify_properties(_make_node(), _make_purity(confidence=0.7), state)
        assert _pure_prop(props).confidence >= 0.9
        assert "[MUTATION VERIFIED" in _pure_prop(props).evidence
        assert "cacheable" in props.optimization_hints


# ── classify_properties: advisory (non-gateable data) ───────────────────


class TestAdvisoryGate:
    """Tests with non-gateable data — hints are NOT modified."""

    def test_advisory_high_survival_preserves_hints(self):
        """Non-gateable + high survival → advisory prefix, hints untouched."""
        state = FunctionMutationState(
            "f",
            "f.py",
            "h",
            "t",
            depth=CoverageDepth.SAMPLED,
            confidence=ConfidenceLevel.LOW,
            total=10,
            killed=3,
            survived=7,
        )
        props = classify_properties(_make_node(), _make_purity(confidence=0.9), state)
        assert "ADVISORY" in _pure_prop(props).evidence
        assert "cacheable" in props.optimization_hints  # NOT suppressed

    def test_advisory_low_survival_verified(self):
        """Non-gateable + survival ≤ 20% → verified, confidence boosted."""
        state = FunctionMutationState(
            "f",
            "f.py",
            "h",
            "t",
            depth=CoverageDepth.SAMPLED,
            confidence=ConfidenceLevel.LOW,
            total=10,
            killed=9,
            survived=1,
        )
        props = classify_properties(_make_node(), _make_purity(confidence=0.7), state)
        assert _pure_prop(props).confidence >= 0.9
        assert "[MUTATION VERIFIED" in _pure_prop(props).evidence

    def test_no_mutation_state_preserves_defaults(self):
        """None mutation state → no prefix, default confidence."""
        props = classify_properties(_make_node(), _make_purity(confidence=0.7), None)
        assert _pure_prop(props).confidence == 0.7
        assert "[MUTATION" not in _pure_prop(props).evidence
        assert "cacheable" in props.optimization_hints


# ── MUTCH004 finding emission ───────────────────────────────────────────


class TestMUTCH004:
    def test_emitted_low_spec_strength(self):
        ch = MutationChannel()
        state = FunctionMutationState(
            "add",
            "logic.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=8,
            survived=2,
            killed_by_assertion=2,
            killed_by_crash=6,  # spec=25%
        )
        issues = ch._check_mutch004(
            {"logic.py::add": state},
            {"logic.py::add": ("cacheable", "parallelizable")},
        )
        assert len(issues) == 1
        assert issues[0].kind == "MUTCH004"
        assert issues[0].severity == "informational"
        assert "spec_strength=25%" in issues[0].message
        assert issues[0].evidence["specification_strength"] == pytest.approx(0.25)
        assert issues[0].evidence["optimization_hints"] == [
            "cacheable",
            "parallelizable",
        ]

    def test_not_emitted_sufficient_spec(self):
        ch = MutationChannel()
        state = FunctionMutationState(
            "add",
            "logic.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=8,
            survived=2,
            killed_by_assertion=6,
            killed_by_crash=2,  # spec=75%
        )
        issues = ch._check_mutch004(
            {"logic.py::add": state},
            {"logic.py::add": ("cacheable",)},
        )
        assert len(issues) == 0

    def test_not_emitted_no_mutation_data(self):
        ch = MutationChannel()
        issues = ch._check_mutch004({}, {"logic.py::add": ("cacheable",)})
        assert len(issues) == 0

    def test_not_emitted_zero_total(self):
        ch = MutationChannel()
        state = FunctionMutationState(
            "add",
            "logic.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=0,
        )
        issues = ch._check_mutch004(
            {"logic.py::add": state},
            {"logic.py::add": ("cacheable",)},
        )
        assert len(issues) == 0

    def test_not_emitted_no_hints(self):
        ch = MutationChannel()
        state = FunctionMutationState(
            "add",
            "logic.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=8,
            survived=2,
            killed_by_assertion=2,
            killed_by_crash=6,
        )
        issues = ch._check_mutch004({"logic.py::add": state}, {})
        assert len(issues) == 0

    def test_boundary_spec_50_not_emitted(self):
        """spec_strength exactly 0.5 → NOT emitted (threshold is <0.5)."""
        ch = MutationChannel()
        state = FunctionMutationState(
            "add",
            "logic.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=8,
            survived=2,
            killed_by_assertion=4,
            killed_by_crash=4,  # spec=50%
        )
        issues = ch._check_mutch004(
            {"logic.py::add": state},
            {"logic.py::add": ("cacheable",)},
        )
        assert len(issues) == 0

    def test_includes_suggestions(self):
        ch = MutationChannel()
        state = FunctionMutationState(
            "add",
            "logic.py",
            "h",
            "t",
            depth=CoverageDepth.PROFILED,
            total=10,
            killed=8,
            survived=2,
            killed_by_assertion=1,
            killed_by_crash=7,
        )
        issues = ch._check_mutch004(
            {"logic.py::add": state},
            {"logic.py::add": ("cacheable",)},
        )
        assert len(issues) == 1
        assert any("mutation_prescribe" in s for s in issues[0].suggestions)


# ── Phase 2: resolve_gate_status ──────────────────────────────────────


class TestResolveGateStatus:
    """Tests for resolve_gate_status."""

    def test_audit_always_pass(self):
        """Audit mode always returns pass with multiplier 1.0."""
        from lintgate.mutation.prescriptions import resolve_gate_status
        assert resolve_gate_status(0.0, "audit") == ("pass", 1.0)
        assert resolve_gate_status(0.5, "audit") == ("pass", 1.0)
        assert resolve_gate_status(1.0, "audit") == ("pass", 1.0)

    def test_graduated_boundaries(self):
        """Graduated mode: spec<0.1->warn*0.2, spec<0.3->warn*0.5, spec>=0.3->pass."""
        from lintgate.mutation.prescriptions import resolve_gate_status
        assert resolve_gate_status(0.05, "graduated") == ("warn", 0.2)
        assert resolve_gate_status(0.2, "graduated") == ("warn", 0.5)
        assert resolve_gate_status(0.3, "graduated") == ("pass", 1.0)
        assert resolve_gate_status(0.5, "graduated") == ("pass", 1.0)

    def test_strict_boundary(self):
        """Strict mode: spec<0.5->fail*0.0, spec>=0.5->pass."""
        from lintgate.mutation.prescriptions import resolve_gate_status
        assert resolve_gate_status(0.3, "strict") == ("fail", 0.0)
        assert resolve_gate_status(0.49, "strict") == ("fail", 0.0)
        assert resolve_gate_status(0.5, "strict") == ("pass", 1.0)

    def test_unknown_mode(self):
        """Unknown enforcement mode defaults to pass."""
        from lintgate.mutation.prescriptions import resolve_gate_status
        assert resolve_gate_status(0.1, "unknown_mode") == ("pass", 1.0)


# ── Phase 2: MUTCH004 graduated gating ────────────────────────────────


class TestMUTCH004Phase2:
    """Phase 2 graduated gating tests."""

    def test_graduated_warn_emits_warning_with_reduced_confidence(self):
        """Graduated mode with low spec -> warning severity + reduced confidence."""
        channel = MutationChannel()
        state = FunctionMutationState(
            "test_func", "test.py", "h", "t",
            depth=CoverageDepth.PROFILED,
            total=10, killed=8, survived=2,
            killed_by_assertion=1, killed_by_crash=7,  # spec=0.125 -> <0.3 -> warn*0.5
        )
        hints = {"test.py::test_func": ("cacheable",)}
        issues = channel._check_mutch004({"test.py::test_func": state}, hints, "graduated")
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].evidence["gate_status"] == "warn"
        assert issues[0].evidence["enforcement_mode"] == "graduated"

    def test_strict_fail_emits_warning_with_zero_confidence(self):
        """Strict mode with spec<0.5 -> warning severity + zero adjusted confidence."""
        channel = MutationChannel()
        state = FunctionMutationState(
            "test_func", "test.py", "h", "t",
            depth=CoverageDepth.PROFILED,
            total=10, killed=8, survived=2,
            killed_by_assertion=2, killed_by_crash=6,  # spec=0.25
        )
        hints = {"test.py::test_func": ("cacheable",)}
        issues = channel._check_mutch004({"test.py::test_func": state}, hints, "strict")
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].evidence["gate_status"] == "fail"
        assert issues[0].evidence["adjusted_confidence"] == 0.0

    def test_audit_preserves_phase1_behavior(self):
        """Audit mode preserves Phase 1 behavior: informational, original confidence."""
        channel = MutationChannel()
        state = FunctionMutationState(
            "test_func", "test.py", "h", "t",
            depth=CoverageDepth.PROFILED,
            total=10, killed=8, survived=2,
            killed_by_assertion=2, killed_by_crash=6,  # spec=0.25
        )
        hints = {"test.py::test_func": ("cacheable",)}
        issues = channel._check_mutch004({"test.py::test_func": state}, hints, "audit")
        assert len(issues) == 1
        assert issues[0].severity == "informational"
        assert issues[0].confidence == 0.85

    def test_sufficient_spec_not_emitted(self):
        """spec_strength >= 0.5 -> no finding emitted in any mode."""
        channel = MutationChannel()
        state = FunctionMutationState(
            "test_func", "test.py", "h", "t",
            depth=CoverageDepth.PROFILED,
            total=10, killed=8, survived=2,
            killed_by_assertion=6, killed_by_crash=2,  # spec=0.75
        )
        hints = {"test.py::test_func": ("cacheable",)}
        for mode in ("audit", "graduated", "strict"):
            issues = channel._check_mutch004({"test.py::test_func": state}, hints, mode)
            assert len(issues) == 0, f"Should not emit for mode={mode}"
