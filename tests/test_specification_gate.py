"""Tests for the optimization gate — thresholds, stop criteria, gate results."""

from __future__ import annotations

from lintgate.specification.optimization_gate import GATE_THRESHOLDS, check_gate
from lintgate.specification.types import (
    FunctionSpecification,
    SpecCore,
)


def _make_spec(
    spec_level: float = 0.0,
    sigma: int = 5,
    hints: list[str] | None = None,
) -> FunctionSpecification:
    return FunctionSpecification(
        function_key="mod::func",
        core=SpecCore(
            estimated_sigma=sigma,
            specification_level=spec_level,
        ),
        optimization_hints=hints or [],
    )


class TestGateThresholds:
    def test_thresholds_exist(self):
        assert "cacheable" in GATE_THRESHOLDS
        assert "parallelizable" in GATE_THRESHOLDS
        assert "foldable" in GATE_THRESHOLDS

    def test_cacheable_threshold(self):
        assert GATE_THRESHOLDS["cacheable"] == 0.6


class TestCheckGate:
    def test_no_hints_passes(self):
        spec = _make_spec(spec_level=0.0, hints=[])
        result = check_gate(spec)
        assert result.passed is True
        assert result.stop_criteria_met is True

    def test_hint_below_threshold(self):
        spec = _make_spec(spec_level=0.3, hints=["cacheable"])
        result = check_gate(spec)
        assert result.passed is False
        assert result.stop_criteria_met is False
        assert result.delta > 0
        assert result.gated_hints == ["cacheable"]

    def test_hint_above_threshold(self):
        spec = _make_spec(spec_level=0.7, hints=["cacheable"])
        result = check_gate(spec)
        assert result.passed is True
        assert result.stop_criteria_met is True
        assert result.delta == 0
        assert result.passed_hints == ["cacheable"]

    def test_multiple_hints_partial_pass(self):
        spec = _make_spec(spec_level=0.65, hints=["cacheable", "parallelizable"])
        result = check_gate(spec)
        # cacheable needs 0.6 (passes), parallelizable needs 0.7 (fails)
        assert result.passed is False
        assert "cacheable" in (result.passed_hints or [])
        assert "parallelizable" in (result.gated_hints or [])

    def test_all_hints_pass(self):
        spec = _make_spec(spec_level=0.9, hints=["cacheable", "parallelizable", "foldable"])
        result = check_gate(spec)
        assert result.passed is True
        assert result.stop_criteria_met is True

    def test_estimated_tests_remaining(self):
        spec = _make_spec(spec_level=0.3, sigma=10, hints=["cacheable"])
        result = check_gate(spec)
        assert result.estimated_tests_remaining > 0

    def test_estimated_tests_zero_when_met(self):
        spec = _make_spec(spec_level=0.8, sigma=10, hints=["cacheable"])
        result = check_gate(spec)
        assert result.estimated_tests_remaining == 0

    def test_function_key_preserved(self):
        spec = _make_spec(spec_level=0.5, hints=["foldable"])
        result = check_gate(spec)
        assert result.function_key == "mod::func"
