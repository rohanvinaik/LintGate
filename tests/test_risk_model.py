"""Tests for the risk model — scoring and priority band classification."""

from __future__ import annotations

from lintgate.specification.risk_model import compute_risk_score


class TestRiskScoring:
    def test_pure_low_risk(self):
        result = compute_risk_score(
            is_pure=True,
            fan_in=0,
            fan_out=0,
            is_public=False,
            testability_score=1.0,
            regime="A",
        )
        assert result.risk_score < 0.4
        assert result.priority_band == "P2"

    def test_impure_public_high_fan(self):
        result = compute_risk_score(
            is_pure=False,
            fan_in=10,
            fan_out=10,
            is_public=True,
            testability_score=0.3,
            regime="B",
        )
        assert result.risk_score >= 0.7
        assert result.priority_band == "P0"

    def test_p1_band(self):
        result = compute_risk_score(
            is_pure=False,
            fan_in=3,
            fan_out=3,
            is_public=True,
            testability_score=0.8,
            regime="A",
        )
        assert 0.4 <= result.risk_score < 0.7
        assert result.priority_band == "P1"

    def test_risk_factors_populated(self):
        result = compute_risk_score(
            is_pure=False,
            fan_in=10,
            fan_out=0,
            is_public=True,
            testability_score=1.0,
            regime="A",
        )
        assert len(result.risk_factors) > 0

    def test_risk_capped_at_one(self):
        result = compute_risk_score(
            is_pure=False,
            fan_in=100,
            fan_out=100,
            is_public=True,
            testability_score=0.0,
            regime="B",
        )
        assert result.risk_score <= 1.0

    def test_all_benign(self):
        result = compute_risk_score(
            is_pure=True,
            fan_in=0,
            fan_out=0,
            is_public=False,
            testability_score=1.0,
            regime="A",
        )
        assert result.risk_score == 0.0
        assert result.priority_band == "P2"
        assert result.risk_factors == []
