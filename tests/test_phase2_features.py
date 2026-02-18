"""Phase 2A/2B: Path policies and telemetry tests.

Tests:
- Path policy config parsing
- Telemetry aggregation over empty and populated metrics
- Telemetry period filtering
- Trend computation
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from lintgate.config import load_config
from lintgate.telemetry import _compute_trend, compute_telemetry_summary
from lintgate.types import ProjectConfig


# ── Phase 2A: Path Policies ─────────────────────────────────────────────


class TestPathPolicies:
    def test_default_config_has_empty_policies(self) -> None:
        config = ProjectConfig()
        assert config.path_policies == []

    def test_yaml_path_policies_parsed(self, tmp_path: Path) -> None:
        """path_policies section in lintgate.yaml is parsed correctly."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config_file = claude_dir / "lintgate.yaml"
        config_file.write_text(
            "path_policies:\n"
            "  - glob: 'src/**'\n"
            "    tier: 3\n"
            "    strictness: strict\n"
            "  - glob: 'tests/**'\n"
            "    tier: 1\n"
            "    strictness: relaxed\n"
            "    include_info: false\n"
        )
        config = load_config(str(tmp_path))
        assert len(config.path_policies) == 2
        assert config.path_policies[0]["glob"] == "src/**"
        assert config.path_policies[0]["tier"] == 3
        assert config.path_policies[0]["strictness"] == "strict"
        assert config.path_policies[1]["glob"] == "tests/**"
        assert config.path_policies[1]["tier"] == 1
        assert config.path_policies[1]["include_info"] is False

    def test_empty_policies_section_ok(self, tmp_path: Path) -> None:
        """Missing or empty path_policies section doesn't crash."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config_file = claude_dir / "lintgate.yaml"
        config_file.write_text("languages:\n  - python\n")
        config = load_config(str(tmp_path))
        assert config.path_policies == []


# ── Phase 2B: Telemetry ─────────────────────────────────────────────────


class TestTelemetryEmpty:
    def test_empty_metrics_returns_zeros(self, tmp_path: Path) -> None:
        with patch("lintgate.telemetry.METRICS_DIR", tmp_path):
            summary = compute_telemetry_summary("/tmp/nonexistent", period="7d")
        assert summary["total_runs"] == 0
        assert summary["total_issues_found"] == 0
        assert summary["fix_rate"] == 0.0
        assert summary["trend"] == "no_data"

    def test_period_is_included(self, tmp_path: Path) -> None:
        with patch("lintgate.telemetry.METRICS_DIR", tmp_path):
            for period in ("1d", "7d", "30d", "all"):
                summary = compute_telemetry_summary("/tmp/proj", period=period)
                assert summary["period"] == period


class TestTelemetryWithData:
    @staticmethod
    def _write_metrics(metrics_dir: Path, project: str, entries: list[dict]) -> None:
        metrics_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        metrics_file = metrics_dir / f"lintgate_{today}.jsonl"
        with open(metrics_file, "w") as f:
            for entry in entries:
                full = {
                    "timestamp": datetime.now().isoformat(),
                    "event": "mcp_lint_run",
                    "project": project,
                    **entry,
                }
                f.write(json.dumps(full) + "\n")

    def test_aggregation_with_data(self, tmp_path: Path) -> None:
        project = "/tmp/test_proj"
        entries = [
            {"blocking_count": 2, "warning_count": 3, "info_count": 1, "files_count": 5, "duration_ms": 100, "tier": "tier_2_manual"},
            {"blocking_count": 0, "warning_count": 1, "info_count": 0, "files_count": 3, "duration_ms": 80, "tier": "tier_0_debounced"},
            {"blocking_count": 1, "warning_count": 0, "info_count": 2, "files_count": 4, "duration_ms": 150, "tier": "tier_2_manual"},
        ]
        self._write_metrics(tmp_path, project, entries)

        with patch("lintgate.telemetry.METRICS_DIR", tmp_path):
            summary = compute_telemetry_summary(project, period="1d")

        assert summary["total_runs"] == 3
        assert summary["total_blocking_found"] == 3
        assert summary["total_warnings_found"] == 4
        assert summary["total_issues_found"] == 3 + 4 + 3  # blocking + warning + info
        assert summary["total_files_linted"] == 12
        assert summary["clean_run_count"] == 1  # only second entry has blocking=0
        assert "tier_2_manual" in summary["tier_distribution"]
        assert summary["tier_distribution"]["tier_2_manual"] == 2

    def test_fix_rate_calculation(self, tmp_path: Path) -> None:
        project = "/tmp/fix_test"
        entries = [
            {"blocking_count": 0, "warning_count": 0, "info_count": 0, "files_count": 1, "duration_ms": 50},
            {"blocking_count": 0, "warning_count": 1, "info_count": 0, "files_count": 2, "duration_ms": 60},
            {"blocking_count": 1, "warning_count": 0, "info_count": 0, "files_count": 1, "duration_ms": 70},
            {"blocking_count": 0, "warning_count": 0, "info_count": 0, "files_count": 1, "duration_ms": 40},
        ]
        self._write_metrics(tmp_path, project, entries)

        with patch("lintgate.telemetry.METRICS_DIR", tmp_path):
            summary = compute_telemetry_summary(project, period="1d")

        # 3 out of 4 runs have 0 blocking = 75% clean rate
        assert summary["fix_rate"] == 0.75

    def test_filters_by_project(self, tmp_path: Path) -> None:
        """Only entries for the requested project are counted."""
        self._write_metrics(tmp_path, "/tmp/proj_a", [
            {"blocking_count": 1, "warning_count": 0, "info_count": 0, "files_count": 1, "duration_ms": 50},
        ])
        # Manually append entries for proj_b to the same file
        today = datetime.now().strftime("%Y%m%d")
        metrics_file = tmp_path / f"lintgate_{today}.jsonl"
        with open(metrics_file, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "event": "mcp_lint_run",
                "project": "/tmp/proj_b",
                "blocking_count": 5,
                "warning_count": 0,
                "info_count": 0,
                "files_count": 1,
                "duration_ms": 50,
            }) + "\n")

        with patch("lintgate.telemetry.METRICS_DIR", tmp_path):
            summary = compute_telemetry_summary("/tmp/proj_a", period="1d")

        assert summary["total_runs"] == 1
        assert summary["total_blocking_found"] == 1


class TestTrend:
    def test_few_entries_returns_no_data(self) -> None:
        assert _compute_trend([{}, {}, {}]) == "no_data"

    def test_improving_trend(self) -> None:
        entries = [
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 1},
            {"blocking_count": 1},
            {"blocking_count": 0},
            {"blocking_count": 0},
        ]
        assert _compute_trend(entries) == "improving"

    def test_degrading_trend(self) -> None:
        entries = [
            {"blocking_count": 0},
            {"blocking_count": 0},
            {"blocking_count": 0},
            {"blocking_count": 0},
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
            {"blocking_count": 5},
        ]
        assert _compute_trend(entries) == "degrading"

    def test_stable_trend(self) -> None:
        entries = [
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
            {"blocking_count": 2},
        ]
        assert _compute_trend(entries) == "stable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
