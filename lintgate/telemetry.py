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
        summary: dict[str, Any] = {
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
        token_economics = compute_token_economics_summary(project_root, period=period)
        if token_economics.get("has_data", False):
            summary["token_economics"] = token_economics
        return summary

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

    summary = {
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
    token_economics = compute_token_economics_summary(project_root, period=period)
    if token_economics.get("has_data", False):
        summary["token_economics"] = token_economics
    return summary


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
    "habit_mode",
    "token_tracking",
}


def _load_feature_entries(
    days: int,
    project_root: str | None,
) -> list[dict[str, Any]]:
    """Load feature_usage events from daily JSONL files."""
    return _load_jsonl_entries(days, project_root, "feature_usage")


def compute_quality_economics_summary(
    project_root: str | None = None,
    period: str = "7d",
) -> dict[str, Any]:
    """Aggregate quality gate telemetry for economics tracking.

    Reads quality_gate events from JSONL metric files to produce:
    - Coverage trend over time (improving/stable/degrading)
    - QG pass rate per period
    - Security issue trend
    - Time-to-green (from first failure to passing QG)

    Args:
        project_root: Filter to a specific project (None = all projects).
        period: Time window — "1d", "7d", "30d", or "all".

    Returns:
        Dict with quality gate economics summary.
    """
    days = _PERIOD_MAP.get(period, 7)
    entries = _load_jsonl_entries(days, project_root, "quality_gate")

    if not entries:
        return {
            "period": period,
            "has_data": False,
            "total_qg_runs": 0,
            "qg_pass_count": 0,
            "qg_fail_count": 0,
            "qg_pass_rate": 0.0,
            "avg_coverage_pct": 0.0,
            "coverage_trend": "no_data",
            "total_security_issues": 0,
            "common_fail_reasons": {},
            "time_to_green_ms": None,
        }

    total = len(entries)
    passes = sum(1 for e in entries if e.get("qg_pass"))
    fails = total - passes

    # Coverage stats
    coverages = [
        float(e["coverage_pct"]) for e in entries if isinstance(e.get("coverage_pct"), (int, float))
    ]
    avg_coverage = round(sum(coverages) / len(coverages), 1) if coverages else 0.0
    coverage_trend = _compute_coverage_trend(coverages)

    # Security issues
    total_security = sum(int(e.get("security_issues", 0)) for e in entries)

    # Fail reason frequency
    fail_reasons: dict[str, int] = {}
    for e in entries:
        for reason in e.get("qg_fail_reasons", []):
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1

    # Time to green: from first failure to first subsequent pass
    time_to_green_ms = _compute_time_to_green(entries)

    return {
        "period": period,
        "has_data": True,
        "total_qg_runs": total,
        "qg_pass_count": passes,
        "qg_fail_count": fails,
        "qg_pass_rate": round(passes / max(total, 1), 3),
        "avg_coverage_pct": avg_coverage,
        "coverage_trend": coverage_trend,
        "total_security_issues": total_security,
        "common_fail_reasons": fail_reasons,
        "time_to_green_ms": time_to_green_ms,
    }


def _compute_coverage_trend(coverages: list[float]) -> str:
    """Compute trend from coverage values: improving/stable/degrading/no_data."""
    if len(coverages) < 4:
        return "no_data"
    mid = len(coverages) // 2
    avg_first = sum(coverages[:mid]) / mid
    avg_second = sum(coverages[mid:]) / (len(coverages) - mid)
    if avg_second > avg_first + 1.0:
        return "improving"
    elif avg_second < avg_first - 1.0:
        return "degrading"
    return "stable"


def _compute_time_to_green(entries: list[dict[str, Any]]) -> int | None:
    """Compute ms from first failure to first subsequent pass. None if N/A."""
    first_fail_ts: float | None = None
    for e in entries:
        ts = e.get("timestamp", 0.0)
        if not e.get("qg_pass"):
            if first_fail_ts is None:
                first_fail_ts = float(ts)
        elif first_fail_ts is not None:
            # Found a pass after a failure
            return int((float(ts) - first_fail_ts) * 1000)
    return None


def compute_token_economics_summary(
    project_root: str | None = None,
    period: str = "7d",
) -> dict[str, Any]:
    """Aggregate habit mode and token economics telemetry.

    Reads habit_mode_transition, habit_compact, token_estimate, and
    runtime_state_write metric events to produce a summary of habit mode
    usage, calibration quality, and runtime-state write cadence.

    Args:
        project_root: Filter to a specific project (None = all projects).
        period: Time window — "1d", "7d", "30d", or "all".

    Returns:
        Dict with habit mode entries, exits, compactions, token calibration
        quality, and runtime-state write telemetry.
    """
    days = _PERIOD_MAP.get(period, 7)

    transitions = _load_jsonl_entries(days, project_root, "habit_mode_transition")
    compactions = _load_jsonl_entries(days, project_root, "habit_compact")
    token_estimates = _load_jsonl_entries(days, project_root, "token_estimate")
    runtime_writes = _load_jsonl_entries(days, project_root, "runtime_state_write")

    if not transitions and not compactions and not token_estimates and not runtime_writes:
        return {
            "period": period,
            "has_data": False,
            "habit_mode_entries": 0,
            "habit_mode_exits": 0,
            "compactions": 0,
            "avg_habit_score_at_entry": 0.0,
            "total_tokens_compacted": 0,
            "avg_tokens_before_compaction": 0.0,
            "avg_calls_per_compaction": 0.0,
            "token_estimate_events": 0,
            "api_calibration_events": 0,
            "avg_calibration_delta": 0.0,
            "avg_abs_calibration_delta": 0.0,
            "avg_calibration_factor": 0.0,
            "runtime_state_writes": 0,
            "runtime_write_success_rate": 0.0,
            "runtime_write_cadence_skips": 0,
            "runtime_write_lock_contention_avg": 0.0,
            "runtime_write_dynamic_status": {},
        }

    entries = [t for t in transitions if t.get("transition") == "enter"]
    exits = [t for t in transitions if t.get("transition") == "exit"]

    entry_scores = [t.get("habit_score", 0.0) for t in entries if "habit_score" in t]
    avg_entry_score = sum(entry_scores) / max(len(entry_scores), 1) if entry_scores else 0.0

    total_tokens_compacted = sum(c.get("estimated_tokens_before", 0) for c in compactions)
    avg_tokens_before = total_tokens_compacted / len(compactions) if compactions else 0.0
    compaction_calls = [
        int(c.get("tool_calls_compacted", 0))
        for c in compactions
        if isinstance(c.get("tool_calls_compacted"), (int, float))
    ]
    avg_calls_per_compaction = (
        sum(compaction_calls) / len(compaction_calls) if compaction_calls else 0.0
    )

    deltas = [
        float(e.get("delta", 0.0))
        for e in token_estimates
        if isinstance(e.get("delta"), (int, float))
    ]
    new_factors = [
        float(e.get("new_factor"))
        for e in token_estimates
        if isinstance(e.get("new_factor"), (int, float))
    ]
    api_calibration_events = sum(1 for e in token_estimates if e.get("source") == "api")

    runtime_successes = sum(int(bool(e.get("success", 0))) for e in runtime_writes)
    runtime_write_success_rate = runtime_successes / len(runtime_writes) if runtime_writes else 0.0
    runtime_write_cadence_skips = sum(
        int(bool(e.get("skipped_by_cadence", 0))) for e in runtime_writes
    )
    lock_contention_values = [
        int(e.get("lock_contention_count", 0))
        for e in runtime_writes
        if isinstance(e.get("lock_contention_count"), (int, float))
    ]
    runtime_write_dynamic_status: dict[str, int] = {}
    for entry in runtime_writes:
        status = str(entry.get("dynamic_status", "") or "")
        if not status:
            continue
        runtime_write_dynamic_status[status] = runtime_write_dynamic_status.get(status, 0) + 1

    return {
        "period": period,
        "has_data": True,
        "habit_mode_entries": len(entries),
        "habit_mode_exits": len(exits),
        "compactions": len(compactions),
        "avg_habit_score_at_entry": round(avg_entry_score, 3),
        "total_tokens_compacted": total_tokens_compacted,
        "avg_tokens_before_compaction": round(avg_tokens_before, 1),
        "avg_calls_per_compaction": round(avg_calls_per_compaction, 2),
        "token_estimate_events": len(token_estimates),
        "api_calibration_events": api_calibration_events,
        "avg_calibration_delta": round(sum(deltas) / len(deltas), 1) if deltas else 0.0,
        "avg_abs_calibration_delta": (
            round(sum(abs(d) for d in deltas) / len(deltas), 1) if deltas else 0.0
        ),
        "avg_calibration_factor": (
            round(sum(new_factors) / len(new_factors), 6) if new_factors else 0.0
        ),
        "runtime_state_writes": len(runtime_writes),
        "runtime_write_success_rate": round(runtime_write_success_rate, 3),
        "runtime_write_cadence_skips": runtime_write_cadence_skips,
        "runtime_write_lock_contention_avg": (
            round(sum(lock_contention_values) / len(lock_contention_values), 3)
            if lock_contention_values
            else 0.0
        ),
        "runtime_write_dynamic_status": runtime_write_dynamic_status,
    }
