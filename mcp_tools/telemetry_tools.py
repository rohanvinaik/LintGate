"""Telemetry tools — telemetry_summary ROI dashboard."""

from __future__ import annotations

import json


def register(mcp, helpers):
    """Register telemetry tools on the shared MCP instance."""

    @mcp.tool()
    def telemetry_summary(
        path: str,
        period: str = "7d",
    ) -> str:
        """ROI dashboard: code quality improvement vs token cost.

        Aggregates lint run metrics over a time window to show:
        total runs, issues found, fix rate, avg duration, token estimates,
        tier distribution, and quality trend.

        Args:
            path: Project root path.
            period: Time window — "1d", "7d", "30d", or "all".
        """
        import contextlib

        from lintgate.telemetry import compute_telemetry_summary

        project_root = helpers["_validate_project_root"](path)
        summary = compute_telemetry_summary(project_root, period=period)

        # Extend with feature-usage telemetry if available
        with contextlib.suppress(Exception):
            from lintgate.telemetry import compute_feature_usage_summary

            feature_usage = compute_feature_usage_summary(project_root, period=period)
            if feature_usage.get("total_invocations", 0) > 0:
                summary["feature_usage"] = feature_usage

        # Extend with quality economics if available
        with contextlib.suppress(Exception):
            from lintgate.telemetry import compute_quality_economics_summary

            quality_economics = compute_quality_economics_summary(
                project_root, period=period
            )
            if quality_economics.get("has_data", False):
                summary["quality_economics"] = quality_economics

        # Extend with performance economics if available
        with contextlib.suppress(Exception):
            from lintgate.telemetry import compute_performance_economics_summary

            performance_economics = compute_performance_economics_summary(
                project_root, period=period
            )
            if performance_economics.get("has_data", False):
                summary["performance_economics"] = performance_economics

        return json.dumps(summary, indent=2)

    return {"telemetry_summary": telemetry_summary}
