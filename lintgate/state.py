"""State tracking for delta computation between runs.

Stores the last lint result per project so the agent reporter
can show "REGRESSION: +2 blocking issues" or "IMPROVEMENT: -1".

State is stored in ~/.claude/lintgate/state/ keyed by project path hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import AggregatedResult, LintIssue

STATE_DIR = Path.home() / ".claude" / "lintgate" / "state"
METRICS_DIR = Path.home() / ".claude" / "lintgate" / "metrics"
ISSUE_MEMORY_DIR = Path.home() / ".claude" / "lintgate" / "issue_memory"
VERSION_DIR = Path.home() / ".claude" / "lintgate" / "versioning"
VERSION_AUDIT_DIR = VERSION_DIR / "audits"
VERSION_EVENTS_DIR = VERSION_DIR / "events"
RUNS_DIR = Path.home() / ".claude" / "lintgate" / "runs"


def save_run(cwd: str, result: AggregatedResult) -> None:
    """Save current run metrics for future delta comparison."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / _project_hash(cwd)

    data = {
        "timestamp": time.time(),
        "project": cwd,
        "tier": result.tier_used,
        "blocking_count": len(result.blocking),
        "warning_count": len(result.warnings),
        "total_issues": result.metrics.get("total_issues", 0),
        "fixable_count": result.metrics.get("fixable_count", 0),
        "duration_ms": result.total_duration_ms,
    }

    with open(state_file, "w") as f:
        json.dump(data, f)


def load_last_run(cwd: str) -> dict[str, Any] | None:
    """Load last run metrics for delta comparison.

    Returns None if no previous run exists.
    """
    state_file = STATE_DIR / _project_hash(cwd)
    if not state_file.exists():
        return None

    try:
        with open(state_file) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


_run_id_counter = 0


def generate_run_id() -> str:
    """Generate a short, unique run ID based on timestamp + PID + counter."""
    global _run_id_counter  # noqa: PLW0603
    _run_id_counter += 1
    raw = f"{time.time_ns()}-{os.getpid()}-{_run_id_counter}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def save_run_details(run_id: str, data: dict[str, Any]) -> None:
    """Persist full lint run details keyed by run_id.

    Enables compact-first MCP responses with drill-down via lint_get_details.
    """
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        run_file = RUNS_DIR / f"{run_id}.json"
        payload = {"run_id": run_id, "timestamp": time.time(), **data}
        with open(run_file, "w") as f:
            json.dump(payload, f)

        # Prune old lint runs only: keep most recent 50.
        _prune_runs_dir(max_keep=50, run_type="lint")
    except OSError:
        pass  # Non-fatal


def load_run_details(run_id: str) -> dict[str, Any] | None:
    """Load full lint run details by run_id.

    Returns None if the run does not exist.
    """
    run_file = RUNS_DIR / f"{run_id}.json"
    return _load_json_file(run_file)


def save_controlplane_run(run_id: str, data: dict[str, Any]) -> None:
    """Persist full ControlPlane run details keyed by run_id.

    Uses cp_ prefix to avoid collision with lint run files.
    """
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        run_file = RUNS_DIR / f"cp_{run_id}.json"
        payload = {"run_id": run_id, "timestamp": time.time(), "type": "controlplane", **data}
        with open(run_file, "w") as f:
            json.dump(payload, f)
        _prune_runs_dir(max_keep=50, run_type="controlplane")
    except OSError:
        pass


def load_controlplane_run(run_id: str) -> dict[str, Any] | None:
    """Load full ControlPlane run details by run_id.

    Tries cp_ prefix first, then bare run_id for backward compat.
    """
    for prefix in ("cp_", ""):
        run_file = RUNS_DIR / f"{prefix}{run_id}.json"
        result = _load_json_file(run_file)
        if result is not None:
            return result
    return None


def _prune_runs_dir(max_keep: int = 50, run_type: str = "all") -> None:
    """Remove oldest run files to prevent unbounded growth.

    Args:
        max_keep: Maximum files to keep for the selected run type.
        run_type: "lint" (non-cp files), "controlplane" (cp_ files), or "all".
    """
    try:
        all_files = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if run_type == "lint":
            run_files = [p for p in all_files if not p.name.startswith("cp_")]
        elif run_type == "controlplane":
            run_files = [p for p in all_files if p.name.startswith("cp_")]
        else:
            run_files = all_files
        if len(run_files) > max_keep:
            for old_file in run_files[: len(run_files) - max_keep]:
                old_file.unlink(missing_ok=True)
    except OSError:
        pass


def log_metric(data: dict[str, Any]) -> None:
    """Log a metric event for later analysis.

    Follows the same JSONL pattern as semantic_preprocessor.py.
    Data is appended to a daily log file.
    """
    try:
        METRICS_DIR.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        metrics_file = METRICS_DIR / f"lintgate_{now.strftime('%Y%m%d')}.jsonl"

        # Remove caller-supplied timestamp to prevent override
        safe_data = {k: v for k, v in data.items() if k != "timestamp"}
        entry = {
            "timestamp": now.isoformat(),
            **safe_data,
        }

        with open(metrics_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Non-fatal: metric logging should never crash the tool


def log_feature_usage(
    feature: str,
    project: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log usage of an advanced subsystem feature for pruning decisions.

    Features tracked:
    - behavior_precheck: Proactive constraint check
    - prediction_tracking: Prediction registration/checking
    - living_context: Context patch generation/application
    - model_calibration: Model profile probe/submit
    - theory_extraction: Theory extraction/lookup
    - controlplane: Full mesh run
    - bootstrap: Context file generation

    This uses the same daily JSONL file as log_metric but with
    event="feature_usage" for easy filtering.
    """
    log_metric({
        "event": "feature_usage",
        "feature": feature,
        "project": project,
        **(metadata or {}),
    })


def _project_hash(cwd: str) -> str:
    """Generate a stable hash for a project path."""
    return hashlib.sha256(cwd.encode()).hexdigest()[:16]


def update_issue_memory(
    cwd: str,
    issues: list[LintIssue],
    top_n: int = 10,
) -> dict[str, Any]:
    """Update persistent issue memory and return recurrence summary.

    Tracks issue signatures across runs to highlight repeated mistakes.
    """
    ISSUE_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    memory_path = ISSUE_MEMORY_DIR / _project_hash(cwd)
    memory = _load_json_file(memory_path) or {"signatures": {}}
    signatures = memory.get("signatures", {})

    now = time.time()
    repeated_signatures: dict[str, int] = {}
    current_records: dict[str, dict[str, Any]] = {}

    for issue in issues:
        signature = _issue_signature(issue)
        entry = signatures.get(signature, {})
        previous_count = int(entry.get("count", 0))
        if previous_count > 0:
            repeated_signatures[signature] = previous_count

        updated_entry = {
            "count": previous_count + 1,
            "last_seen": now,
            "linter": issue.linter,
            "kind": issue.kind,
            "file": issue.file,
            "line": issue.line,
            "message": issue.message,
        }
        signatures[signature] = updated_entry
        current_records[signature] = updated_entry

    # Prevent unbounded growth: keep most recently seen signatures.
    max_entries = 10000
    if len(signatures) > max_entries:
        sorted_items = sorted(
            signatures.items(),
            key=lambda item: item[1].get("last_seen", 0),
            reverse=True,
        )
        signatures = dict(sorted_items[:max_entries])

    memory["updated_at"] = now
    memory["signatures"] = signatures
    with open(memory_path, "w") as f:
        json.dump(memory, f)

    sorted_repeated = sorted(
        (current_records[sig] for sig in repeated_signatures if sig in current_records),
        key=lambda item: item.get("count", 0),
        reverse=True,
    )

    top_repeated = sorted_repeated[:top_n] if top_n > 0 else sorted_repeated

    return {
        "repeated_issue_count": len(repeated_signatures),
        "unique_signatures_tracked": len(signatures),
        "top_repeated": top_repeated,
    }


def save_version_audit(cwd: str, audit: dict[str, Any]) -> None:
    """Persist the latest version audit snapshot for a project."""
    VERSION_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = VERSION_AUDIT_DIR / _project_hash(cwd)
    payload = {
        "timestamp": time.time(),
        "project": cwd,
        **audit,
    }
    with open(audit_path, "w") as f:
        json.dump(payload, f)


def load_last_version_audit(cwd: str) -> dict[str, Any] | None:
    """Load the latest version audit snapshot for a project."""
    audit_path = VERSION_AUDIT_DIR / _project_hash(cwd)
    if not audit_path.exists():
        return None
    return _load_json_file(audit_path)


def log_version_event(data: dict[str, Any]) -> None:
    """Append a version-audit event to a daily JSONL file."""
    VERSION_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    events_path = (
        VERSION_EVENTS_DIR / f"lintgate_versions_{datetime.now().strftime('%Y%m%d')}.jsonl"
    )
    entry = {
        "timestamp": datetime.now().isoformat(),
        **data,
    }
    with open(events_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _issue_signature(issue: LintIssue) -> str:
    """Build a stable signature for recurrence tracking."""
    return "|".join(
        [
            issue.linter,
            issue.kind,
            issue.file or "",
            str(issue.line or 0),
        ]
    )


def _load_json_file(path: Path) -> dict[str, Any] | None:
    """Load a JSON object from disk, returning None on failure."""
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None
