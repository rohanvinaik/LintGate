"""Coverage tests for mcp_tools/telemetry_tools.py.

Exercises the register() function and the telemetry_summary MCP tool,
including feature_usage and quality_economics extension paths.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.telemetry_tools import register


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_helpers(tmp_path: Path) -> dict:
    """Build a minimal helpers dict that validates against tmp_path."""
    return {
        "_validate_project_root": lambda path, **kw: str(tmp_path),
    }


def _register_tools(tmp_path: Path) -> dict:
    """Register tools on a mock MCP and return the tool function dict."""
    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = _make_helpers(tmp_path)
    return register(mcp, helpers)


# ── register() ───────────────────────────────────────────────────────────


class TestRegister:
    def test_register_returns_telemetry_summary(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        assert set(tools.keys()) == {"telemetry_summary"}

    def test_register_value_is_callable(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        assert callable(tools["telemetry_summary"])


# ── telemetry_summary — base summary ─────────────────────────────────────


class TestTelemetrySummaryBase:
    def test_returns_valid_json(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {
            "period": "7d",
            "total_runs": 0,
            "total_issues_found": 0,
            "fix_rate": 0.0,
            "trend": "no_data",
        }
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        assert result["period"] == "7d"
        assert result["total_runs"] == 0

    def test_default_period_is_7d(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 5}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ) as mock_fn:
            tools["telemetry_summary"](path=str(tmp_path))
        mock_fn.assert_called_once_with(str(tmp_path), period="7d")

    def test_custom_period_passes_through(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "30d", "total_runs": 42}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ) as mock_fn:
            result = json.loads(
                tools["telemetry_summary"](path=str(tmp_path), period="30d")
            )
        mock_fn.assert_called_once_with(str(tmp_path), period="30d")
        assert result["period"] == "30d"

    def test_period_1d(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "1d", "total_runs": 3}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ):
            result = json.loads(
                tools["telemetry_summary"](path=str(tmp_path), period="1d")
            )
        assert result["period"] == "1d"

    def test_period_all(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "all", "total_runs": 100}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ):
            result = json.loads(
                tools["telemetry_summary"](path=str(tmp_path), period="all")
            )
        assert result["total_runs"] == 100


# ── telemetry_summary — feature_usage extension ─────────────────────────


class TestTelemetrySummaryFeatureUsage:
    def test_feature_usage_included_when_positive(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 10}
        mock_feature = {"total_invocations": 5, "features": {"bootstrap": 3}}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ), patch(
            "lintgate.telemetry.compute_feature_usage_summary",
            return_value=mock_feature,
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        assert "feature_usage" in result
        assert result["feature_usage"]["total_invocations"] == 5

    def test_feature_usage_omitted_when_zero(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 10}
        mock_feature = {"total_invocations": 0, "features": {}}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ), patch(
            "lintgate.telemetry.compute_feature_usage_summary",
            return_value=mock_feature,
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        assert "feature_usage" not in result

    def test_feature_usage_exception_suppressed(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 10}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ), patch(
            "lintgate.telemetry.compute_feature_usage_summary",
            side_effect=RuntimeError("broken"),
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        # Should still return base summary without feature_usage
        assert "feature_usage" not in result
        assert result["total_runs"] == 10


# ── telemetry_summary — quality_economics extension ──────────────────────


class TestTelemetrySummaryQualityEconomics:
    def test_quality_economics_included_when_has_data(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 10}
        mock_qe = {"has_data": True, "qg_pass_rate": 0.85}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ), patch(
            "lintgate.telemetry.compute_quality_economics_summary",
            return_value=mock_qe,
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        assert "quality_economics" in result
        assert result["quality_economics"]["qg_pass_rate"] == 0.85

    def test_quality_economics_omitted_when_no_data(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 10}
        mock_qe = {"has_data": False, "total_qg_runs": 0}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ), patch(
            "lintgate.telemetry.compute_quality_economics_summary",
            return_value=mock_qe,
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        assert "quality_economics" not in result

    def test_quality_economics_exception_suppressed(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 10}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ), patch(
            "lintgate.telemetry.compute_quality_economics_summary",
            side_effect=ImportError("missing"),
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        assert "quality_economics" not in result
        assert result["total_runs"] == 10


# ── telemetry_summary — combined extensions ──────────────────────────────


class TestTelemetrySummaryCombined:
    def test_both_extensions_present(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_summary = {"period": "7d", "total_runs": 20}
        mock_feature = {"total_invocations": 8, "features": {"bootstrap": 5}}
        mock_qe = {"has_data": True, "qg_pass_rate": 0.9}
        with patch(
            "lintgate.telemetry.compute_telemetry_summary",
            return_value=mock_summary,
        ), patch(
            "lintgate.telemetry.compute_feature_usage_summary",
            return_value=mock_feature,
        ), patch(
            "lintgate.telemetry.compute_quality_economics_summary",
            return_value=mock_qe,
        ):
            result = json.loads(tools["telemetry_summary"](path=str(tmp_path)))
        assert "feature_usage" in result
        assert "quality_economics" in result
        assert result["total_runs"] == 20
