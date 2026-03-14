"""Tests for lintgate.linters.complexity_checker — radon CC/MI thresholds and suggestions."""

from __future__ import annotations

import pytest

from lintgate.linters.complexity_checker import (
    ComplexityChecker,
    _CC_THRESHOLDS,
    _MI_THRESHOLDS,
    _cc_suggestions,
)


# ── _CC_THRESHOLDS / _MI_THRESHOLDS ─────────────────────────────────


class TestThresholdConstants:
    def test_cc_thresholds_values(self):
        assert _CC_THRESHOLDS == {"relaxed": 25, "normal": 20, "strict": 15}

    def test_mi_thresholds_values(self):
        assert _MI_THRESHOLDS == {"relaxed": 5, "normal": 10, "strict": 20}

    def test_cc_strict_less_than_normal(self):
        assert _CC_THRESHOLDS["strict"] < _CC_THRESHOLDS["normal"]

    def test_mi_strict_greater_than_normal(self):
        # Higher MI threshold = stricter (MI is good when high)
        assert _MI_THRESHOLDS["strict"] > _MI_THRESHOLDS["normal"]


# ── _cc_suggestions ──────────────────────────────────────────────────


class TestCcSuggestions:
    def test_extreme_complexity_over_40(self):
        suggestions = _cc_suggestions("process", 45, 20)
        assert len(suggestions) >= 3  # 2 extreme + 1 universal
        assert any("extreme" in s.lower() for s in suggestions)
        assert any("dispatch" in s.lower() or "handler" in s.lower() for s in suggestions)
        # Universal guard-clause hint always present
        assert any("early-return" in s.lower() or "guard clause" in s.lower() for s in suggestions)

    def test_very_high_complexity_26_to_40(self):
        suggestions = _cc_suggestions("validate", 30, 20)
        assert len(suggestions) >= 2  # 1 very-high + 1 universal
        assert any("very high" in s.lower() for s in suggestions)
        assert any("extract" in s.lower() or "helper" in s.lower() for s in suggestions)

    def test_above_threshold_targeted(self):
        suggestions = _cc_suggestions("compute", 22, 20)
        assert len(suggestions) >= 2  # 1 targeted + 1 universal
        assert any("20" in s for s in suggestions)  # mentions threshold

    def test_at_threshold_only_universal(self):
        suggestions = _cc_suggestions("simple", 20, 20)
        # cc=20 with threshold=20 -> cc > threshold is False, cc > 25 is False, cc > 40 is False
        assert len(suggestions) == 1
        assert "guard clause" in suggestions[0].lower() or "early-return" in suggestions[0].lower()

    def test_below_threshold_only_universal(self):
        suggestions = _cc_suggestions("simple", 5, 20)
        assert len(suggestions) == 1

    def test_universal_hint_always_included(self):
        # Even at low complexity, the universal hint is present
        suggestions = _cc_suggestions("f", 1, 20)
        assert any("early-return" in s.lower() or "guard clause" in s.lower() for s in suggestions)

    def test_function_name_in_suggestions(self):
        suggestions = _cc_suggestions("my_func", 45, 20)
        assert any("my_func" in s for s in suggestions)
        suggestions2 = _cc_suggestions("another_func", 30, 20)
        assert any("another_func" in s for s in suggestions2)


# ── ComplexityChecker attributes ─────────────────────────────────────


class TestComplexityCheckerAttributes:
    def test_name(self):
        checker = ComplexityChecker()
        assert checker.name == "complexity_checker"

    def test_tier(self):
        checker = ComplexityChecker()
        assert checker.tier == 2

    def test_required_tool(self):
        checker = ComplexityChecker()
        assert checker.required_tool == "radon"

    def test_timeout_ms(self):
        checker = ComplexityChecker()
        assert checker.timeout_ms == 5000
