"""Comprehensive tests for lintgate/telemetry.py.

Covers all public functions and edge cases for telemetry aggregation:
  - compute_telemetry_summary
  - compute_feature_usage_summary
  - compute_quality_economics_summary
  - compute_token_economics_summary
  - compute_performance_economics_summary

Filesystem I/O is isolated via tmp_path and patching METRICS_DIR.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
from unittest.mock import patch

import pytest

from lintgate.telemetry import (
    compute_feature_usage_summary,
    compute_performance_economics_summary,
    compute_quality_economics_summary,
    compute_telemetry_summary,
    compute_token_economics_summary,
)

# ── helpers ───────────────────────────────────────────────────────────────


def _write_metrics(
    metrics_dir: Path,
    date_str: str,
    entries: list[dict[str, Any]],
) -> Path:
    """Write a JSONL metrics file for a given date."""
    filepath = metrics_dir / f"lintgate_{date_str}.jsonl"
    with open(filepath, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return filepath


def _today_str() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d")


def _yesterday_str() -> str:
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


def _old_date_str(days_ago: int = 60) -> str:
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")


# ── compute_telemetry_summary ─────────────────────────────────────────────


class TestComputeTelemetrySummary:
    """Tests for the main telemetry summary function."""

    def test_no_metrics_dir_returns_zero_summary(self, tmp_path: Path) -> None:
        """When METRICS_DIR does not exist, return zeroed summary."""
        fake_dir = tmp_path / "nonexistent"
        with patch("lintgate.telemetry.METRICS_DIR", fake_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 0
        assert result["total_issues_found"] == 0
        assert result["total_files_linted"] == 0
        assert result["fix_rate"] == 0.0
        assert result["trend"] == "no_data"
        assert result["period"] == "7d"

    def test_empty_metrics_dir_returns_zero_summary(self, tmp_path: Path) -> None:
        """When METRICS_DIR exists but is empty, return zeroed summary."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 0

    def test_aggregation_single_entry(self, tmp_path: Path) -> None:
        """Single run entry produces correct aggregation."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 3,
                    "warning_count": 2,
                    "info_count": 1,
                    "files_count": 10,
                    "duration_ms": 200,
                    "tier": "t1",
                    "output_mode": "compact",
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 1
        assert result["total_blocking_found"] == 3
        assert result["total_warnings_found"] == 2
        assert result["total_issues_found"] == 6  # 3+2+1
        assert result["total_files_linted"] == 10
        assert result["avg_duration_ms"] == 200.0
        assert result["fix_rate"] == 0.0  # blocking > 0
        assert result["tier_distribution"] == {"t1": 1}
        assert result["output_mode_distribution"] == {"compact": 1}
        assert result["tokens_per_run_estimate"] == 200  # compact = 200 tokens

    def test_aggregation_multiple_entries(self, tmp_path: Path) -> None:
        """Multiple entries are summed correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 2,
                    "warning_count": 1,
                    "info_count": 0,
                    "files_count": 5,
                    "duration_ms": 100,
                    "tier": "t1",
                    "output_mode": "full",
                },
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 3,
                    "duration_ms": 50,
                    "tier": "t2",
                    "output_mode": "standard",
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 2
        assert result["total_blocking_found"] == 2
        assert result["total_warnings_found"] == 1
        assert result["total_issues_found"] == 3
        assert result["total_files_linted"] == 8
        assert result["avg_duration_ms"] == 75.0
        assert result["fix_rate"] == 0.5  # 1 of 2 had 0 blocking
        assert result["clean_run_count"] == 1
        assert result["tier_distribution"] == {"t1": 1, "t2": 1}
        assert result["output_mode_distribution"] == {"full": 1, "standard": 1}
        # Token estimate: full=1500, standard=500 -> total=2000, avg=1000
        assert result["tokens_per_run_estimate"] == 1000

    def test_project_filter_excludes_other_projects(self, tmp_path: Path) -> None:
        """Only entries matching project_root are included."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj_a",
                    "blocking_count": 5,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
                {
                    "event": "mcp_lint_run",
                    "project": "/proj_b",
                    "blocking_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj_a")

        assert result["total_runs"] == 1
        assert result["total_blocking_found"] == 5

    def test_period_1d_filters_old_entries(self, tmp_path: Path) -> None:
        """1d period excludes entries from yesterday and earlier."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        _write_metrics(
            metrics_dir,
            _old_date_str(days_ago=5),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 99,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj", period="1d")

        assert result["total_runs"] == 1
        assert result["total_blocking_found"] == 1
        assert result["period"] == "1d"

    def test_period_all_includes_old_entries(self, tmp_path: Path) -> None:
        """'all' period includes entries from far in the past."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        _write_metrics(
            metrics_dir,
            _old_date_str(days_ago=60),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 2,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj", period="all")

        assert result["total_runs"] == 2
        assert result["total_blocking_found"] == 3

    def test_unknown_period_defaults_to_7d(self, tmp_path: Path) -> None:
        """Unknown period string falls back to 7 days."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj", period="bogus")

        assert result["total_runs"] == 1
        assert result["period"] == "bogus"

    def test_missing_fields_default_to_zero(self, tmp_path: Path) -> None:
        """Entries with missing count fields are treated as 0."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    # no blocking_count, warning_count, info_count, etc.
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 1
        assert result["total_blocking_found"] == 0
        assert result["total_warnings_found"] == 0
        assert result["total_issues_found"] == 0
        assert result["total_files_linted"] == 0
        assert result["avg_duration_ms"] == 0.0
        assert result["fix_rate"] == 1.0  # 0 blocking => clean run

    def test_repeated_issue_count_aggregated(self, tmp_path: Path) -> None:
        """repeated_issue_count is summed across entries."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                    "repeated_issue_count": 3,
                },
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                    "repeated_issue_count": 7,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["repeated_issue_count"] == 10

    def test_all_clean_runs_fix_rate_one(self, tmp_path: Path) -> None:
        """When all runs have 0 blocking issues, fix_rate is 1.0."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        entries = [
            {
                "event": "mcp_lint_run",
                "project": "/proj",
                "blocking_count": 0,
                "warning_count": 0,
                "info_count": 0,
                "files_count": 1,
                "duration_ms": 10,
            }
            for _ in range(5)
        ]
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["fix_rate"] == 1.0
        assert result["clean_run_count"] == 5

    def test_token_economics_included_when_has_data(self, tmp_path: Path) -> None:
        """When token economics has data, it's nested in the summary."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
                {
                    "event": "habit_mode_transition",
                    "project": "/proj",
                    "transition": "enter",
                    "habit_score": 0.8,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert "token_economics" in result
        assert result["token_economics"]["has_data"] is True

    def test_ignores_non_mcp_lint_run_events(self, tmp_path: Path) -> None:
        """Events that aren't mcp_lint_run are ignored by _load_entries."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "feature_usage",
                    "project": "/proj",
                    "feature": "constraint_check",
                },
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 1


# ── Trend computation (via compute_telemetry_summary) ─────────────────────


class TestTrendComputation:
    """Tests for trend direction and evidence, exercised through the public API."""

    def _make_entries(
        self,
        blocking_counts: list[int],
        project: str = "/proj",
    ) -> list[dict[str, Any]]:
        return [
            {
                "event": "mcp_lint_run",
                "project": project,
                "blocking_count": bc,
                "warning_count": 0,
                "info_count": 0,
                "files_count": 1,
                "duration_ms": 10,
            }
            for bc in blocking_counts
        ]

    def test_trend_improving_when_blockers_decrease(self, tmp_path: Path) -> None:
        """Second half has significantly fewer blockers -> improving."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        # First half: high blockers, second half: low blockers
        entries = self._make_entries([10, 10, 10, 10, 1, 1, 1, 1])
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["trend"] == "improving"
        assert "decreased" in result["trend_evidence"]["trend_explanation"].lower()

    def test_trend_degrading_when_blockers_increase(self, tmp_path: Path) -> None:
        """Second half has significantly more blockers -> degrading."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        entries = self._make_entries([1, 1, 1, 1, 10, 10, 10, 10])
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["trend"] == "degrading"
        assert "increased" in result["trend_evidence"]["trend_explanation"].lower()

    def test_trend_stable_when_blockers_similar(self, tmp_path: Path) -> None:
        """Similar blocker counts in both halves -> stable."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        entries = self._make_entries([5, 5, 5, 5, 5, 5, 5, 5])
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["trend"] == "stable"
        assert "stable" in result["trend_evidence"]["trend_explanation"].lower()

    def test_trend_no_data_with_few_entries(self, tmp_path: Path) -> None:
        """Fewer than 4 entries -> no_data trend."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        entries = self._make_entries([5, 5, 5])
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["trend"] == "no_data"
        assert "Insufficient data" in result["trend_evidence"]["trend_explanation"]

    def test_trend_evidence_includes_sample_size(self, tmp_path: Path) -> None:
        """Trend evidence includes avg_blockers and sample_size."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        entries = self._make_entries([4, 4, 4, 4, 2, 2, 2, 2])
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        evidence = result["trend_evidence"]
        assert evidence["avg_blockers_early"] == 4.0
        assert evidence["avg_blockers_recent"] == 2.0
        assert evidence["sample_size"] == 8

    def test_trend_zero_to_nonzero_degrading(self, tmp_path: Path) -> None:
        """Moving from 0 blockers to nonzero -> degrading."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        entries = self._make_entries([0, 0, 0, 0, 5, 5, 5, 5])
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["trend"] == "degrading"

    def test_trend_zero_to_zero_stable(self, tmp_path: Path) -> None:
        """All zero blockers -> stable."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        entries = self._make_entries([0, 0, 0, 0, 0, 0, 0, 0])
        _write_metrics(metrics_dir, _today_str(), entries)
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["trend"] == "stable"


# ── compute_feature_usage_summary ─────────────────────────────────────────


class TestComputeFeatureUsageSummary:
    """Tests for feature usage aggregation."""

    def test_no_data_returns_all_unused(self, tmp_path: Path) -> None:
        """No feature_usage events -> all tracked features listed as unused."""
        fake_dir = tmp_path / "nonexistent"
        with patch("lintgate.telemetry.METRICS_DIR", fake_dir):
            result = compute_feature_usage_summary("/proj")

        assert result["total_invocations"] == 0
        assert result["features"] == {}
        assert result["active_features"] == []
        assert len(result["unused_features"]) > 0

    def test_single_feature_counted(self, tmp_path: Path) -> None:
        """Single feature usage event is counted correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "feature_usage",
                    "project": "/proj",
                    "feature": "constraint_check",
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_feature_usage_summary("/proj")

        assert result["total_invocations"] == 1
        assert "constraint_check" in result["active_features"]
        assert result["features"]["constraint_check"]["invocations"] == 1
        assert result["features"]["constraint_check"]["pct_of_total"] == 100.0

    def test_multiple_features_counted(self, tmp_path: Path) -> None:
        """Multiple distinct features produce correct counts and percentages."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {"event": "feature_usage", "project": "/proj", "feature": "constraint_check"},
                {"event": "feature_usage", "project": "/proj", "feature": "constraint_check"},
                {"event": "feature_usage", "project": "/proj", "feature": "hygiene_check"},
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_feature_usage_summary("/proj")

        assert result["total_invocations"] == 3
        assert result["features"]["constraint_check"]["invocations"] == 2
        assert result["features"]["hygiene_check"]["invocations"] == 1
        assert result["features"]["constraint_check"]["pct_of_total"] == pytest.approx(
            66.7, abs=0.1
        )

    def test_unused_features_excludes_active(self, tmp_path: Path) -> None:
        """Active features are removed from unused list."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {"event": "feature_usage", "project": "/proj", "feature": "controlplane"},
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_feature_usage_summary("/proj")

        assert "controlplane" not in result["unused_features"]
        assert "controlplane" in result["active_features"]

    def test_project_filter_applied(self, tmp_path: Path) -> None:
        """Only entries matching project_root are counted."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {"event": "feature_usage", "project": "/proj_a", "feature": "bootstrap"},
                {"event": "feature_usage", "project": "/proj_b", "feature": "bootstrap"},
                {"event": "feature_usage", "project": "/proj_a", "feature": "controlplane"},
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_feature_usage_summary("/proj_a")

        assert result["total_invocations"] == 2
        assert result["features"]["bootstrap"]["invocations"] == 1
        assert result["features"]["controlplane"]["invocations"] == 1

    def test_no_project_filter_includes_all(self, tmp_path: Path) -> None:
        """None project_root includes entries from all projects."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {"event": "feature_usage", "project": "/proj_a", "feature": "bootstrap"},
                {"event": "feature_usage", "project": "/proj_b", "feature": "bootstrap"},
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_feature_usage_summary(None)

        assert result["total_invocations"] == 2
        assert result["features"]["bootstrap"]["projects"] == 2

    def test_projects_count_per_feature(self, tmp_path: Path) -> None:
        """projects count tracks distinct projects using each feature."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {"event": "feature_usage", "project": "/a", "feature": "bootstrap"},
                {"event": "feature_usage", "project": "/b", "feature": "bootstrap"},
                {"event": "feature_usage", "project": "/a", "feature": "bootstrap"},
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_feature_usage_summary(None)

        assert result["features"]["bootstrap"]["projects"] == 2
        assert result["features"]["bootstrap"]["invocations"] == 3


# ── compute_quality_economics_summary ─────────────────────────────────────


class TestComputeQualityEconomicsSummary:
    """Tests for quality gate economics aggregation."""

    def test_no_data_returns_empty_summary(self, tmp_path: Path) -> None:
        """No quality_gate events -> zeroed summary with has_data=False."""
        fake_dir = tmp_path / "nonexistent"
        with patch("lintgate.telemetry.METRICS_DIR", fake_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["has_data"] is False
        assert result["total_qg_runs"] == 0
        assert result["qg_pass_rate"] == 0.0
        assert result["coverage_trend"] == "no_data"
        assert result["time_to_green_ms"] is None

    def test_all_passes(self, tmp_path: Path) -> None:
        """All passes -> pass_rate 1.0."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 85.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 90.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["has_data"] is True
        assert result["total_qg_runs"] == 2
        assert result["qg_pass_count"] == 2
        assert result["qg_fail_count"] == 0
        assert result["qg_pass_rate"] == 1.0

    def test_mix_pass_fail(self, tmp_path: Path) -> None:
        """Mixed pass/fail produces correct rate."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 85.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": False,
                    "coverage_pct": 70.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": False,
                    "coverage_pct": 60.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["qg_pass_rate"] == pytest.approx(0.333, abs=0.001)
        assert result["qg_fail_count"] == 2

    def test_average_coverage(self, tmp_path: Path) -> None:
        """avg_coverage_pct is computed correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 90.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["avg_coverage_pct"] == 85.0

    def test_coverage_trend_improving(self, tmp_path: Path) -> None:
        """Coverage going up -> improving."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 70.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 70.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 85.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 85.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["coverage_trend"] == "improving"

    def test_coverage_trend_degrading(self, tmp_path: Path) -> None:
        """Coverage going down -> degrading."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 90.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 90.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 75.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 75.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["coverage_trend"] == "degrading"

    def test_coverage_trend_stable(self, tmp_path: Path) -> None:
        """Coverage flat -> stable."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.5,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.5,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["coverage_trend"] == "stable"

    def test_coverage_trend_no_data_too_few(self, tmp_path: Path) -> None:
        """Fewer than 4 coverage values -> no_data."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 90.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["coverage_trend"] == "no_data"

    def test_security_issues_summed(self, tmp_path: Path) -> None:
        """security_issues are summed across entries."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "security_issues": 2,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "security_issues": 3,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["total_security_issues"] == 5

    def test_fail_reasons_aggregated(self, tmp_path: Path) -> None:
        """qg_fail_reasons are aggregated by frequency."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": False,
                    "qg_fail_reasons": ["coverage_low", "security_hotspot"],
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": False,
                    "qg_fail_reasons": ["coverage_low"],
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["common_fail_reasons"]["coverage_low"] == 2
        assert result["common_fail_reasons"]["security_hotspot"] == 1

    def test_time_to_green_computed(self, tmp_path: Path) -> None:
        """Time from first failure to first subsequent pass is measured."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": False,
                    "timestamp": 1000.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "timestamp": 1005.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        # (1005.0 - 1000.0) * 1000 = 5000 ms
        assert result["time_to_green_ms"] == 5000

    def test_time_to_green_none_when_no_recovery(self, tmp_path: Path) -> None:
        """If there's no pass after a failure, time_to_green_ms is None."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": False,
                    "timestamp": 1000.0,
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": False,
                    "timestamp": 1005.0,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["time_to_green_ms"] is None

    def test_time_to_green_none_when_only_passes(self, tmp_path: Path) -> None:
        """If all entries are passes, time_to_green_ms is None."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {"event": "quality_gate", "project": "/proj", "qg_pass": True, "timestamp": 1000.0},
                {"event": "quality_gate", "project": "/proj", "qg_pass": True, "timestamp": 1005.0},
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["time_to_green_ms"] is None

    def test_missing_coverage_pct_excluded_from_avg(self, tmp_path: Path) -> None:
        """Entries without valid coverage_pct are excluded from the average."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.0,
                },
                {"event": "quality_gate", "project": "/proj", "qg_pass": True},  # no coverage_pct
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": "bad",
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_quality_economics_summary("/proj")

        assert result["avg_coverage_pct"] == 80.0  # only 1 valid entry


# ── compute_token_economics_summary ───────────────────────────────────────


class TestComputeTokenEconomicsSummary:
    """Tests for habit mode and token economics aggregation."""

    def test_no_data_returns_empty_summary(self, tmp_path: Path) -> None:
        """No token/habit events -> zeroed summary with has_data=False."""
        fake_dir = tmp_path / "nonexistent"
        with patch("lintgate.telemetry.METRICS_DIR", fake_dir):
            result = compute_token_economics_summary("/proj")

        assert result["has_data"] is False
        assert result["habit_mode_entries"] == 0
        assert result["compactions"] == 0
        assert result["token_estimate_events"] == 0
        assert result["runtime_state_writes"] == 0

    def test_habit_mode_transitions_counted(self, tmp_path: Path) -> None:
        """Enter and exit transitions are counted separately."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "habit_mode_transition",
                    "project": "/proj",
                    "transition": "enter",
                    "habit_score": 0.85,
                },
                {
                    "event": "habit_mode_transition",
                    "project": "/proj",
                    "transition": "exit",
                },
                {
                    "event": "habit_mode_transition",
                    "project": "/proj",
                    "transition": "enter",
                    "habit_score": 0.75,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_token_economics_summary("/proj")

        assert result["has_data"] is True
        assert result["habit_mode_entries"] == 2
        assert result["habit_mode_exits"] == 1
        assert result["avg_habit_score_at_entry"] == pytest.approx(0.8, abs=0.001)

    def test_compaction_aggregation(self, tmp_path: Path) -> None:
        """Compaction metrics are aggregated correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "habit_compact",
                    "project": "/proj",
                    "estimated_tokens_before": 5000,
                    "tool_calls_compacted": 10,
                },
                {
                    "event": "habit_compact",
                    "project": "/proj",
                    "estimated_tokens_before": 3000,
                    "tool_calls_compacted": 6,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_token_economics_summary("/proj")

        assert result["compactions"] == 2
        assert result["total_tokens_compacted"] == 8000
        assert result["avg_tokens_before_compaction"] == 4000.0
        assert result["avg_calls_per_compaction"] == 8.0

    def test_token_calibration_deltas(self, tmp_path: Path) -> None:
        """Token estimate deltas and factors are averaged."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "token_estimate",
                    "project": "/proj",
                    "delta": 10.0,
                    "new_factor": 1.05,
                    "source": "api",
                },
                {
                    "event": "token_estimate",
                    "project": "/proj",
                    "delta": -6.0,
                    "new_factor": 0.95,
                    "source": "heuristic",
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_token_economics_summary("/proj")

        assert result["token_estimate_events"] == 2
        assert result["api_calibration_events"] == 1
        assert result["avg_calibration_delta"] == 2.0  # (10 + -6) / 2
        assert result["avg_abs_calibration_delta"] == 8.0  # (10 + 6) / 2
        assert result["avg_calibration_factor"] == pytest.approx(1.0, abs=0.001)

    def test_runtime_state_writes(self, tmp_path: Path) -> None:
        """Runtime state write telemetry is computed correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "runtime_state_write",
                    "project": "/proj",
                    "success": True,
                    "skipped_by_cadence": False,
                    "lock_contention_count": 2,
                    "dynamic_status": "active",
                },
                {
                    "event": "runtime_state_write",
                    "project": "/proj",
                    "success": True,
                    "skipped_by_cadence": True,
                    "lock_contention_count": 0,
                    "dynamic_status": "active",
                },
                {
                    "event": "runtime_state_write",
                    "project": "/proj",
                    "success": False,
                    "skipped_by_cadence": False,
                    "lock_contention_count": 1,
                    "dynamic_status": "stale",
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_token_economics_summary("/proj")

        assert result["runtime_state_writes"] == 3
        assert result["runtime_write_success_rate"] == pytest.approx(0.667, abs=0.001)
        assert result["runtime_write_cadence_skips"] == 1
        assert result["runtime_write_lock_contention_avg"] == 1.0
        assert result["runtime_write_dynamic_status"] == {"active": 2, "stale": 1}

    def test_empty_dynamic_status_ignored(self, tmp_path: Path) -> None:
        """Entries with empty or missing dynamic_status are not counted."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "runtime_state_write",
                    "project": "/proj",
                    "success": True,
                    "dynamic_status": "",
                },
                {
                    "event": "runtime_state_write",
                    "project": "/proj",
                    "success": True,
                    # no dynamic_status key
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_token_economics_summary("/proj")

        assert result["runtime_write_dynamic_status"] == {}

    def test_no_habit_score_in_entry_excluded_from_avg(self, tmp_path: Path) -> None:
        """Entries without habit_score key are excluded from average."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "habit_mode_transition",
                    "project": "/proj",
                    "transition": "enter",
                    # no habit_score
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_token_economics_summary("/proj")

        assert result["habit_mode_entries"] == 1
        assert result["avg_habit_score_at_entry"] == 0.0


# ── compute_performance_economics_summary ─────────────────────────────────


class TestComputePerformanceEconomicsSummary:
    """Tests for performance/algebraic property telemetry."""

    def test_no_data_returns_empty_summary(self, tmp_path: Path) -> None:
        """No performance_analysis events -> zeroed summary."""
        fake_dir = tmp_path / "nonexistent"
        with patch("lintgate.telemetry.METRICS_DIR", fake_dir):
            result = compute_performance_economics_summary("/proj")

        assert result["has_data"] is False
        assert result["total_runs"] == 0
        assert result["total_pure_functions"] == 0
        assert result["total_properties_proven"] == 0
        assert result["total_performance_issues"] == 0
        assert result["avg_analysis_time_ms"] == 0.0

    def test_aggregation_single_entry(self, tmp_path: Path) -> None:
        """Single entry is aggregated correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "performance_analysis",
                    "project": "/proj",
                    "pure_functions": 5,
                    "properties_proven": 3,
                    "findings_count": 2,
                    "duration_ms": 150,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_performance_economics_summary("/proj")

        assert result["has_data"] is True
        assert result["total_runs"] == 1
        assert result["total_pure_functions"] == 5
        assert result["total_properties_proven"] == 3
        assert result["total_performance_issues"] == 2
        assert result["avg_analysis_time_ms"] == 150.0

    def test_aggregation_multiple_entries(self, tmp_path: Path) -> None:
        """Multiple entries are summed correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "performance_analysis",
                    "project": "/proj",
                    "pure_functions": 3,
                    "properties_proven": 1,
                    "findings_count": 0,
                    "duration_ms": 100,
                },
                {
                    "event": "performance_analysis",
                    "project": "/proj",
                    "pure_functions": 7,
                    "properties_proven": 4,
                    "findings_count": 3,
                    "duration_ms": 200,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_performance_economics_summary("/proj")

        assert result["total_runs"] == 2
        assert result["total_pure_functions"] == 10
        assert result["total_properties_proven"] == 5
        assert result["total_performance_issues"] == 3
        assert result["avg_analysis_time_ms"] == 150.0

    def test_missing_duration_excluded_from_avg(self, tmp_path: Path) -> None:
        """Entries without valid duration_ms are excluded from average."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "performance_analysis",
                    "project": "/proj",
                    "pure_functions": 1,
                    "properties_proven": 0,
                    "findings_count": 0,
                    "duration_ms": 200,
                },
                {
                    "event": "performance_analysis",
                    "project": "/proj",
                    "pure_functions": 1,
                    "properties_proven": 0,
                    "findings_count": 0,
                    # no duration_ms
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_performance_economics_summary("/proj")

        assert result["total_runs"] == 2
        assert result["avg_analysis_time_ms"] == 200.0  # only 1 valid entry

    def test_invalid_duration_type_excluded(self, tmp_path: Path) -> None:
        """Non-numeric duration_ms is excluded from average."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "performance_analysis",
                    "project": "/proj",
                    "pure_functions": 1,
                    "properties_proven": 0,
                    "findings_count": 0,
                    "duration_ms": "not_a_number",
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_performance_economics_summary("/proj")

        assert result["total_runs"] == 1
        assert result["avg_analysis_time_ms"] == 0.0


# ── File I/O edge cases (tested through public API) ──────────────────────


class TestFileIOEdgeCases:
    """Edge cases for JSONL file reading and date parsing."""

    def test_malformed_json_lines_skipped(self, tmp_path: Path) -> None:
        """Invalid JSON lines in metrics files are silently skipped."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        filepath = metrics_dir / f"lintgate_{_today_str()}.jsonl"
        with open(filepath, "w") as f:
            f.write("this is not json\n")
            f.write(
                json.dumps(
                    {
                        "event": "mcp_lint_run",
                        "project": "/proj",
                        "blocking_count": 1,
                        "warning_count": 0,
                        "info_count": 0,
                        "files_count": 1,
                        "duration_ms": 10,
                    }
                )
                + "\n"
            )
            f.write("{broken json\n")

        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 1

    def test_empty_lines_in_jsonl_skipped(self, tmp_path: Path) -> None:
        """Empty lines in JSONL files are silently skipped."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        filepath = metrics_dir / f"lintgate_{_today_str()}.jsonl"
        with open(filepath, "w") as f:
            f.write("\n")
            f.write("\n")
            f.write(
                json.dumps(
                    {
                        "event": "mcp_lint_run",
                        "project": "/proj",
                        "blocking_count": 0,
                        "warning_count": 0,
                        "info_count": 0,
                        "files_count": 1,
                        "duration_ms": 10,
                    }
                )
                + "\n"
            )
            f.write("\n")

        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 1

    def test_bad_date_filename_skipped(self, tmp_path: Path) -> None:
        """Files with unparseable date in filename are skipped."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        # Good file
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        # Bad filename
        bad_file = metrics_dir / "lintgate_notadate.jsonl"
        with open(bad_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "mcp_lint_run",
                        "project": "/proj",
                        "blocking_count": 99,
                        "warning_count": 0,
                        "info_count": 0,
                        "files_count": 1,
                        "duration_ms": 10,
                    }
                )
                + "\n"
            )

        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        # Only the good file should be read
        assert result["total_runs"] == 1
        assert result["total_blocking_found"] == 0

    def test_multiple_files_across_dates(self, tmp_path: Path) -> None:
        """Entries from multiple date files within window are combined."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        _write_metrics(
            metrics_dir,
            _yesterday_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 2,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj", period="7d")

        assert result["total_runs"] == 2
        assert result["total_blocking_found"] == 3

    def test_non_matching_glob_files_ignored(self, tmp_path: Path) -> None:
        """Files that don't match lintgate_*.jsonl pattern are ignored."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        # Write a file that doesn't match the glob pattern
        other_file = metrics_dir / "other_file.jsonl"
        with open(other_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "mcp_lint_run",
                        "project": "/proj",
                        "blocking_count": 99,
                    }
                )
                + "\n"
            )

        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 0

    def test_empty_metrics_file(self, tmp_path: Path) -> None:
        """An empty JSONL file produces no entries."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        filepath = metrics_dir / f"lintgate_{_today_str()}.jsonl"
        filepath.touch()  # empty file

        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["total_runs"] == 0


# ── Cross-function integration ────────────────────────────────────────────


class TestCrossFunctionIntegration:
    """Tests that verify different event types don't interfere with each other."""

    def test_mixed_events_in_same_file(self, tmp_path: Path) -> None:
        """Different event types in the same JSONL file are routed correctly."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 1,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
                {
                    "event": "feature_usage",
                    "project": "/proj",
                    "feature": "bootstrap",
                },
                {
                    "event": "quality_gate",
                    "project": "/proj",
                    "qg_pass": True,
                    "coverage_pct": 80.0,
                },
                {
                    "event": "performance_analysis",
                    "project": "/proj",
                    "pure_functions": 5,
                    "properties_proven": 2,
                    "findings_count": 1,
                    "duration_ms": 100,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            lint = compute_telemetry_summary("/proj")
            feat = compute_feature_usage_summary("/proj")
            quality = compute_quality_economics_summary("/proj")
            perf = compute_performance_economics_summary("/proj")

        assert lint["total_runs"] == 1
        assert feat["total_invocations"] == 1
        assert quality["total_qg_runs"] == 1
        assert perf["total_runs"] == 1

    def test_output_mode_defaults_to_full(self, tmp_path: Path) -> None:
        """Entries without output_mode default to 'full' for token estimation."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                    # no output_mode key
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        # Default output_mode = "full" = 1500 tokens
        assert result["tokens_per_run_estimate"] == 1500
        assert result["output_mode_distribution"] == {"full": 1}

    def test_tier_defaults_to_unknown(self, tmp_path: Path) -> None:
        """Entries without tier field default to 'unknown'."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        _write_metrics(
            metrics_dir,
            _today_str(),
            [
                {
                    "event": "mcp_lint_run",
                    "project": "/proj",
                    "blocking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "files_count": 1,
                    "duration_ms": 10,
                },
            ],
        )
        with patch("lintgate.telemetry.METRICS_DIR", metrics_dir):
            result = compute_telemetry_summary("/proj")

        assert result["tier_distribution"] == {"unknown": 1}
