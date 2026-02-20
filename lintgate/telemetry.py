"""Phase 2B: Telemetry aggregation for ROI tracking.

Reads daily JSONL metric files from ~/.claude/lintgate/metrics/ and
computes aggregate summaries: issues found, issues fixed, token estimates,
trends, and fix rates.

All read-only — never modifies metric files.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .state import METRICS_DIR

_PERIOD_MAP = {
    "1d": 1,
    "7d": 7,
    "30d": 30,
    "all": 3650,  # ~10 years
}


def compute_telemetry_summary(
    project_root: str,
    period: str = "7d",
) -> dict[str, Any]:
    """Aggregate metrics into an ROI summary.

    Args:
        project_root: Project path to filter metrics by.
        period: Time window — "1d", "7d", "30d", or "all".

    Returns:
        Dict with total_runs, issues_found, fix_rate, trends, etc.
    """
    days = _PERIOD_MAP.get(period, 7)
    entries = _load_entries(days, project_root)

    if not entries:
        return {
            "period": period,
            "total_runs": 0,
            "total_files_linted": 0,
            "total_issues_found": 0,
            "total_blocking_found": 0,
            "total_warnings_found": 0,
            "clean_run_count": 0,
            "avg_duration_ms": 0,
            "tokens_per_run_estimate": 0,
            "fix_rate": 0.0,
            "tier_distribution": {},
            "trend": "no_data",
        }

    total_runs = len(entries)
    total_blocking = sum(e.get("blocking_count", 0) for e in entries)
    total_warnings = sum(e.get("warning_count", 0) for e in entries)
    total_info = sum(e.get("info_count", 0) for e in entries)
    total_issues = total_blocking + total_warnings + total_info
    total_files = sum(e.get("files_count", 0) for e in entries)
    total_duration = sum(e.get("duration_ms", 0) for e in entries)
    total_repeated = sum(e.get("repeated_issue_count", 0) for e in entries)

    # Tier distribution
    tier_dist: dict[str, int] = {}
    for e in entries:
        tier = e.get("tier", "unknown")
        tier_dist[tier] = tier_dist.get(tier, 0) + 1

    # Output mode distribution
    mode_dist: dict[str, int] = {}
    for e in entries:
        mode = e.get("output_mode", "full")
        mode_dist[mode] = mode_dist.get(mode, 0) + 1

    # Trend: compare first half vs second half of period
    trend = _compute_trend(entries)

    # Token estimate: compact ~200, standard ~500, full ~1500
    token_map = {"compact": 200, "standard": 500, "full": 1500}
    total_tokens = sum(token_map.get(e.get("output_mode", "full"), 1500) for e in entries)

    # Fix rate: ratio of runs with 0 blocking to total runs
    clean_runs = sum(1 for e in entries if e.get("blocking_count", 0) == 0)
    fix_rate = clean_runs / total_runs if total_runs > 0 else 0.0

    return {
        "period": period,
        "total_runs": total_runs,
        "total_files_linted": total_files,
        "total_issues_found": total_issues,
        "total_blocking_found": total_blocking,
        "total_warnings_found": total_warnings,
        "avg_duration_ms": round(total_duration / max(total_runs, 1), 1),
        "tokens_per_run_estimate": round(total_tokens / max(total_runs, 1)),
        "total_tokens_estimate": total_tokens,
        "fix_rate": round(fix_rate, 3),
        "clean_run_count": clean_runs,
        "repeated_issue_count": total_repeated,
        "tier_distribution": tier_dist,
        "output_mode_distribution": mode_dist,
        "trend": trend,
    }


def _load_jsonl_entries(
    days: int,
    project_root: str | None,
    event_type: str,
) -> list[dict[str, Any]]:
    """Load entries from daily JSONL files, filtered by event type and project.

    Shared implementation for both lint-run and feature-usage loading.
    """
    if not METRICS_DIR.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days)
    entries: list[dict[str, Any]] = []

    for metrics_file in sorted(METRICS_DIR.glob("lintgate_*.jsonl")):
        file_date = _parse_metrics_file_date(metrics_file)
        if file_date is None or file_date < cutoff:
            continue

        for entry in _read_jsonl_file(metrics_file):
            if entry.get("event") != event_type:
                continue
            if project_root and entry.get("project") != project_root:
                continue
            entries.append(entry)

    return entries


def _parse_metrics_file_date(metrics_file: Any) -> datetime | None:
    """Parse date from metric filename (lintgate_YYYYMMDD.jsonl)."""
    date_part = metrics_file.stem.replace("lintgate_", "")
    try:
        return datetime.strptime(date_part, "%Y%m%d")
    except ValueError:
        return None


def _read_jsonl_file(metrics_file: Any) -> list[dict[str, Any]]:
    """Read all valid JSON entries from a JSONL file."""
    entries: list[dict[str, Any]] = []
    try:
        with open(metrics_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _load_entries(days: int, project_root: str | None) -> list[dict[str, Any]]:
    """Load metric entries from daily JSONL files within the time window."""
    return _load_jsonl_entries(days, project_root, "mcp_lint_run")


def _compute_trend(entries: list[dict[str, Any]]) -> str:
    """Compare blocking counts: first half vs second half of entries.

    Returns: "improving", "stable", "degrading", or "no_data".
    """
    if len(entries) < 4:
        return "no_data"

    mid = len(entries) // 2
    first_half = entries[:mid]
    second_half = entries[mid:]

    avg_first = sum(e.get("blocking_count", 0) for e in first_half) / len(first_half)
    avg_second = sum(e.get("blocking_count", 0) for e in second_half) / len(second_half)

    if avg_second < avg_first * 0.8:
        return "improving"
    elif avg_second > avg_first * 1.2:
        return "degrading"
    return "stable"


def compute_feature_usage_summary(
    project_root: str | None = None,
    period: str = "7d",
) -> dict[str, Any]:
    """Aggregate feature usage telemetry for data-driven pruning decisions.

    Reads feature_usage events from the same JSONL metric files and produces
    per-feature usage counts. This enables answering: "Are predictions,
    living context, model profiles actually being used?"

    Args:
        project_root: Filter to a specific project (None = all projects).
        period: Time window — "1d", "7d", "30d", or "all".

    Returns:
        Dict with per-feature counts, total invocations, and active features.
    """
    days = _PERIOD_MAP.get(period, 7)
    entries = _load_feature_entries(days, project_root)

    if not entries:
        return {
            "period": period,
            "total_invocations": 0,
            "features": {},
            "active_features": [],
            "unused_features": _ALL_TRACKED_FEATURES.copy(),
        }

    # Count per feature
    feature_counts: dict[str, int] = {}
    feature_projects: dict[str, set[str]] = {}
    for e in entries:
        feat = e.get("feature", "unknown")
        feature_counts[feat] = feature_counts.get(feat, 0) + 1
        proj = e.get("project", "")
        if proj:
            feature_projects.setdefault(feat, set()).add(proj)

    total = sum(feature_counts.values())
    active = sorted(feature_counts.keys())
    unused = sorted(_ALL_TRACKED_FEATURES - set(active))

    features_detail: dict[str, dict[str, Any]] = {}
    for feat in sorted(feature_counts.keys()):
        features_detail[feat] = {
            "invocations": feature_counts[feat],
            "projects": len(feature_projects.get(feat, set())),
            "pct_of_total": round(feature_counts[feat] / max(total, 1) * 100, 1),
        }

    return {
        "period": period,
        "total_invocations": total,
        "features": features_detail,
        "active_features": active,
        "unused_features": unused,
    }


# All features we track — used to identify unused ones
_ALL_TRACKED_FEATURES = {
    "behavior_precheck_deprecated",
    "constraint_check",
    "hygiene_check",
    "prediction_register",
    "prediction_tracking",
    "living_context",
    "model_calibration",
    "theory_extraction",
    "controlplane",
    "bootstrap",
}


def _load_feature_entries(
    days: int,
    project_root: str | None,
) -> list[dict[str, Any]]:
    """Load feature_usage events from daily JSONL files."""
    return _load_jsonl_entries(days, project_root, "feature_usage")
