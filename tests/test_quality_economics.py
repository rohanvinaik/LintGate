"""Tests for quality economics telemetry functions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lintgate.telemetry import (
    _compute_coverage_trend,
    _compute_time_to_green,
    compute_quality_economics_summary,
)


class TestComputeCoverageTrend:
    """Verify _compute_coverage_trend logic."""

    def test_no_data_few_entries(self):
        assert _compute_coverage_trend([80.0, 81.0]) == "no_data"

    def test_no_data_empty(self):
        assert _compute_coverage_trend([]) == "no_data"

    def test_stable(self):
        coverages = [80.0, 80.5, 79.5, 80.0, 80.2, 80.1]
        assert _compute_coverage_trend(coverages) == "stable"

    def test_improving(self):
        coverages = [70.0, 71.0, 75.0, 80.0, 82.0, 85.0]
        assert _compute_coverage_trend(coverages) == "improving"

    def test_degrading(self):
        coverages = [85.0, 84.0, 82.0, 75.0, 73.0, 70.0]
        assert _compute_coverage_trend(coverages) == "degrading"


class TestComputeTimeToGreen:
    """Verify _compute_time_to_green logic."""

    def test_no_failures(self):
        entries = [
            {"qg_pass": True, "timestamp": 1000.0},
            {"qg_pass": True, "timestamp": 2000.0},
        ]
        assert _compute_time_to_green(entries) is None

    def test_only_failures(self):
        entries = [
            {"qg_pass": False, "timestamp": 1000.0},
            {"qg_pass": False, "timestamp": 2000.0},
        ]
        assert _compute_time_to_green(entries) is None

    def test_failure_then_pass(self):
        entries = [
            {"qg_pass": False, "timestamp": 1000.0},
            {"qg_pass": False, "timestamp": 1500.0},
            {"qg_pass": True, "timestamp": 2000.0},
        ]
        result = _compute_time_to_green(entries)
        assert result == 1000000  # (2000 - 1000) * 1000 ms

    def test_pass_then_failure_then_pass(self):
        entries = [
            {"qg_pass": True, "timestamp": 500.0},
            {"qg_pass": False, "timestamp": 1000.0},
            {"qg_pass": True, "timestamp": 1200.0},
        ]
        result = _compute_time_to_green(entries)
        assert result == 200000  # (1200 - 1000) * 1000 ms


class TestComputeQualityEconomicsSummary:
    """Verify compute_quality_economics_summary aggregation."""

    def test_no_data(self):
        """No quality_gate events → empty summary."""
        with patch(
            "lintgate.telemetry._load_jsonl_entries",
            return_value=[],
        ):
            result = compute_quality_economics_summary("/tmp/test", "7d")
            assert result["has_data"] is False
            assert result["total_qg_runs"] == 0
            assert result["qg_pass_rate"] == 0.0

    def test_with_data(self):
        """Quality gate events produce correct aggregates."""
        entries = [
            {
                "event": "quality_gate",
                "qg_pass": False,
                "coverage_pct": 78.0,
                "security_issues": 1,
                "qg_fail_reasons": [
                    "coverage_below_threshold",
                    "security_vulnerability",
                ],
                "timestamp": 1000.0,
            },
            {
                "event": "quality_gate",
                "qg_pass": False,
                "coverage_pct": 79.5,
                "security_issues": 1,
                "qg_fail_reasons": ["coverage_below_threshold"],
                "timestamp": 2000.0,
            },
            {
                "event": "quality_gate",
                "qg_pass": True,
                "coverage_pct": 82.0,
                "security_issues": 0,
                "qg_fail_reasons": [],
                "timestamp": 3000.0,
            },
        ]

        with patch(
            "lintgate.telemetry._load_jsonl_entries",
            return_value=entries,
        ):
            result = compute_quality_economics_summary("/tmp/test", "7d")
            assert result["has_data"] is True
            assert result["total_qg_runs"] == 3
            assert result["qg_pass_count"] == 1
            assert result["qg_fail_count"] == 2
            assert result["qg_pass_rate"] == pytest.approx(1 / 3, abs=0.01)
            assert result["avg_coverage_pct"] == pytest.approx(79.8, abs=0.1)
            assert result["total_security_issues"] == 2
            assert result["common_fail_reasons"]["coverage_below_threshold"] == 2
            assert result["common_fail_reasons"]["security_vulnerability"] == 1
            # Time to green: 1000.0 → 3000.0 = 2000s = 2_000_000ms
            assert result["time_to_green_ms"] == 2000000
