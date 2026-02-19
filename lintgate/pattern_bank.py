"""Anti-pattern bank — categorical pattern tracking across lint runs.

Tracks issue patterns by (linter, kind) — ignoring file and line — to detect
when the same *category* of error keeps recurring. This is the anti-tail-chasing
mechanism: if the agent keeps producing F821 errors across different files, the
bank alerts that this is a systemic problem, not an isolated typo.

Design decisions:
- Alert-only: patterns trigger PATTERN ALERT messages but do NOT auto-promote
  severity. Auto-escalation creates noisy hard-fail loops on style codes.
  Future: config-gated escalation allowlist behind explicit opt-in.
- Persistent: stored on disk per project in ~/.claude/lintgate/pattern_bank/
  so patterns survive across process invocations (hook is process-per-run).
- Bounded: rolling window of last N runs prevents unbounded growth.

Reuses:
- state._project_hash() for project-path hashing
- state._load_json_file() for safe JSON loading
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import LintIssue

PATTERN_BANK_DIR = Path.home() / ".claude" / "lintgate" / "pattern_bank"
_MAX_RUN_HISTORY = 10  # Keep per-pattern history for this many runs
_ALERT_THRESHOLD_SINGLE_RUN = 3  # >=N of same kind in one run → note
_ALERT_THRESHOLD_RECENT_RUNS = 3  # >=N of last 5 runs with same kind → ALERT
_RECENT_WINDOW = 5  # Look at last N runs for recurrence


@dataclass
class PatternAlert:
    """A pattern that has been flagged for agent attention."""

    linter: str
    kind: str
    count_this_run: int
    files_this_run: int
    recent_run_count: int  # How many of last _RECENT_WINDOW runs had this pattern
    total_count: int  # Lifetime count across all runs
    alert_reason: str  # "single_run_volume" or "recurring_across_runs"


def update_pattern_bank(
    cwd: str,
    issues: list[LintIssue],
) -> dict[str, Any]:
    """Track categorical patterns and return alert report.

    Args:
        cwd: Project root path
        issues: All issues from current lint run

    Returns:
        Dict with alerted_patterns and top_categories for the reporter.
    """
    PATTERN_BANK_DIR.mkdir(parents=True, exist_ok=True)
    bank_path = PATTERN_BANK_DIR / _project_hash(cwd)
    bank = _load_bank(bank_path)

    now = time.time()
    run_id = str(int(now * 1000))  # Unique run identifier

    # Track global run history so we can compute "N of last M runs" correctly.
    # Without this, we only know when a pattern appeared — not how many clean
    # runs happened between appearances — leading to false "recurring" alerts.
    global_runs: list[str] = bank.get("global_run_ids", [])
    global_runs.append(run_id)
    if len(global_runs) > _MAX_RUN_HISTORY:
        global_runs = global_runs[-_MAX_RUN_HISTORY:]
    bank["global_run_ids"] = global_runs

    # The recent window is the last N global runs (including clean ones)
    recent_global_ids = set(global_runs[-_RECENT_WINDOW:])

    # Group issues by (linter, kind)
    current_patterns: dict[str, dict[str, Any]] = {}
    for issue in issues:
        key = f"{issue.linter}|{issue.kind}"
        if key not in current_patterns:
            current_patterns[key] = {
                "linter": issue.linter,
                "kind": issue.kind,
                "count": 0,
                "files": set(),
            }
        current_patterns[key]["count"] += 1
        if issue.file:
            current_patterns[key]["files"].add(issue.file)

    # Update bank with current run data
    patterns = bank.get("patterns", {})
    alerts: list[dict[str, Any]] = []

    for key, current in current_patterns.items():
        entry = patterns.get(
            key,
            {
                "linter": current["linter"],
                "kind": current["kind"],
                "total_count": 0,
                "first_seen": now,
                "run_history": [],
            },
        )

        # Append this run to history
        entry["run_history"].append(
            {
                "run_id": run_id,
                "timestamp": now,
                "count": current["count"],
                "files": len(current["files"]),
            }
        )

        # Trim history to max
        if len(entry["run_history"]) > _MAX_RUN_HISTORY:
            entry["run_history"] = entry["run_history"][-_MAX_RUN_HISTORY:]

        entry["total_count"] = entry.get("total_count", 0) + current["count"]
        entry["last_seen"] = now

        # Count how many of the last N *global* runs this pattern appeared in.
        # This correctly accounts for clean runs between appearances.
        pattern_run_ids = {r["run_id"] for r in entry["run_history"]}
        recent_run_count = len(pattern_run_ids & recent_global_ids)

        alert_reason = None
        if current["count"] >= _ALERT_THRESHOLD_SINGLE_RUN:
            alert_reason = "single_run_volume"
        if recent_run_count >= _ALERT_THRESHOLD_RECENT_RUNS:
            alert_reason = "recurring_across_runs"

        if alert_reason:
            alerts.append(
                {
                    "linter": current["linter"],
                    "kind": current["kind"],
                    "count_this_run": current["count"],
                    "files_this_run": len(current["files"]),
                    "recent_run_count": recent_run_count,
                    "total_count": entry["total_count"],
                    "alert_reason": alert_reason,
                }
            )

        patterns[key] = entry

    # Save updated bank
    bank["patterns"] = patterns
    bank["updated_at"] = now
    bank["last_run_id"] = run_id

    try:
        with open(bank_path, "w") as f:
            json.dump(bank, f)
    except OSError:
        pass  # Non-fatal — pattern tracking is observability, not correctness

    # Sort alerts by severity (recurring > volume, then by count)
    alerts.sort(
        key=lambda a: (
            0 if a["alert_reason"] == "recurring_across_runs" else 1,
            -a["count_this_run"],
        )
    )

    # Build top categories (all patterns from this run, sorted by count)
    top_categories = sorted(
        [
            {
                "linter": v["linter"],
                "kind": v["kind"],
                "count": v["count"],
                "files": len(v["files"]),
            }
            for v in current_patterns.values()
        ],
        key=lambda x: -x["count"],
    )[:10]

    return {
        "alerted_patterns": alerts,
        "top_categories": top_categories,
        "total_pattern_keys_tracked": len(patterns),
    }


def _project_hash(cwd: str) -> str:
    """Generate a stable hash for a project path."""
    import hashlib

    return hashlib.sha256(cwd.encode()).hexdigest()[:16]


def _load_bank(path: Path) -> dict[str, Any]:
    """Load pattern bank from disk."""
    try:
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"patterns": {}}
