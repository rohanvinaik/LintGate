"""Habit Mode compaction — structured snapshot building.

Produces JSON snapshots for context window compaction with per-section
token budgets and deterministic truncation.
"""

from __future__ import annotations

import json
from typing import Any

from lintgate._habit_types import SNAPSHOT_MAX_CHARS, HabitModeState

# ── Compaction Strategy ──────────────────────────────────────────────

# Per-section token budgets (approximate)
_SECTION_BUDGETS = {
    "mode": 50,
    "active_context": 100,
    "theory_digest": 1500,
    "lint_state": 400,
    "behavioral_trajectory": 300,
    "recurring_issues": 150,
    "session_history": 100,
    "coherence_trajectory": 30,
    "token_state": 50,
    "tool_guidance": 300,
}

# Truncation order: low priority -> high priority (truncated first)
_TRUNCATION_ORDER = [
    "session_history",
    "recurring_issues",
    "behavioral_trajectory",
    "lint_state",
    "coherence_trajectory",
    # These are NEVER truncated:
    # "mode", "active_context", "token_state", "tool_guidance", "theory_digest"
]


def _build_active_context(state: HabitModeState) -> dict[str, Any]:
    """Build active context section (100 tokens — cap at 10 files)."""
    active_files = state.active_files[:10]
    if sum(len(f) for f in active_files) > 400:
        active_files = [f.rsplit("/", 1)[-1] for f in active_files]
    return {"files": active_files, "last_test_status": state.last_test_status}


def _build_lint_section(last_lint_run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build lint state section (400 tokens — cap at 5 blocking issues)."""
    if not last_lint_run:
        return None
    blocking_issues = [
        {
            "file": issue.get("file", ""),
            "line": issue.get("line"),
            "kind": issue.get("kind", ""),
            "message": str(issue.get("message", ""))[:80],
        }
        for issue in last_lint_run.get("issues", [])[:5]
    ]
    return {
        "blocking_count": last_lint_run.get("blocking_count", 0),
        "warning_count": last_lint_run.get("warning_count", 0),
        "issues": blocking_issues,
    }


def _build_behavioral_section(compass: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build behavioral trajectory section (300 tokens — top 3 constraints, top 2 errors)."""
    if not compass:
        return None
    hypotheses = compass.get("hypotheses", [])
    top_hyps = sorted(hypotheses, key=lambda h: h.get("confidence", 0), reverse=True)[:3]
    hyp_summaries = [
        {"claim": h.get("claim", "")[:80], "confidence": h.get("confidence", 0)} for h in top_hyps
    ]
    error_mem = compass.get("error_memory", {})
    top_errors = sorted(
        error_mem.items(),
        key=lambda kv: kv[1].get("count", 0) if isinstance(kv[1], dict) else 0,
        reverse=True,
    )[:2]
    error_summaries = [
        {"sig": k[:60], "count": v.get("count", 0) if isinstance(v, dict) else 0}
        for k, v in top_errors
    ]
    coverage = compass.get("coverage", {})
    return {
        "top_constraints": hyp_summaries,
        "top_errors": error_summaries,
        "prediction_recall": coverage.get("prediction_recall", 0.0),
    }


def _build_session_history(session_memory: dict[str, Any] | None) -> list[dict] | None:
    """Build session history section (100 tokens — last 2 snapshots)."""
    if not session_memory:
        return None
    snapshots = session_memory.get("snapshots", [])
    recent = snapshots[-2:] if len(snapshots) >= 2 else snapshots
    return [
        {
            "coherence": s.get("coherence_state", ""),
            "blocking": s.get("blocking_count", 0),
            "findings": s.get("finding_count", 0),
        }
        for s in recent
    ]


def _build_token_section(token_estimate: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build token state section (50 tokens — fixed structure)."""
    if not token_estimate:
        return None
    return {
        "estimated_used": token_estimate.get("estimated_tokens_used", 0),
        "tool_calls": token_estimate.get("tool_call_count", 0),
        "lines_written": token_estimate.get("lines_written", 0),
    }


def build_compaction_snapshot(
    state: HabitModeState,
    project_root: str,
    *,
    session_memory: dict[str, Any] | None = None,
    compass: dict[str, Any] | None = None,
    last_lint_run: dict[str, Any] | None = None,
    theory_pack: dict[str, Any] | None = None,
    issue_memory: dict[str, Any] | None = None,
    token_estimate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured compaction snapshot.

    Produces a JSON document with 10 sections, subject to hard per-section
    token budgets and deterministic truncation if snapshot exceeds
    SNAPSHOT_MAX_CHARS.
    """
    focus_files = ", ".join(state.active_files[:3]) if state.active_files else "none"
    test_str = f"Test: {state.last_test_status}." if state.last_test_status else ""

    snapshot: dict[str, Any] = {
        "mode": {
            "active": state.active,
            "habit_score": round(state.habit_score, 2),
            "declared": state.declared,
            "compaction_number": state.compaction_count + 1,
        },
        "active_context": _build_active_context(state),
        "theory_digest": theory_pack or None,
        "lint_state": _build_lint_section(last_lint_run),
        "behavioral_trajectory": _build_behavioral_section(compass),
        "recurring_issues": issue_memory.get("recurrent_issues", [])[:3] if issue_memory else None,
        "session_history": _build_session_history(session_memory),
        "coherence_trajectory": (
            session_memory.get("coherence_trajectory", [])[-3:] if session_memory else None
        ),
        "token_state": _build_token_section(token_estimate),
        "tool_guidance": _build_tool_injections(state, compass, last_lint_run, session_memory),
        "focus_directive": f"You are in Habit Mode. Focus: [{focus_files}]. {test_str}",
    }

    _enforce_snapshot_cap(snapshot)
    return snapshot


def _enforce_snapshot_cap(snapshot: dict[str, Any]) -> None:
    """Truncate sections in priority order if snapshot exceeds SNAPSHOT_MAX_CHARS."""
    serialized = json.dumps(snapshot, separators=(",", ":"))
    if len(serialized) <= SNAPSHOT_MAX_CHARS:
        return

    # Truncate in reverse priority order
    for section_key in _TRUNCATION_ORDER:
        if section_key in snapshot and snapshot[section_key] is not None:
            snapshot[section_key] = None
            serialized = json.dumps(snapshot, separators=(",", ":"))
            if len(serialized) <= SNAPSHOT_MAX_CHARS:
                return


def _build_tool_injections(
    state: HabitModeState,
    compass: dict[str, Any] | None,
    last_lint_run: dict[str, Any] | None,
    session_memory: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build conditional tool injection recommendations.

    Based on behavioral metrics, suggest underused LintGate tools.
    Max 4 injections, 80 char reasons.
    """
    injections: list[dict[str, Any]] = []

    # Priority 1: edit_streak > 5 AND no test -> suggest prediction_register
    if state.signals.edit_streak > 5 and not state.signals.test_in_last_n:
        injections.append(
            {
                "tool": "prediction_register",
                "priority": 1,
                "reason": "5+ edits without test. Register a prediction before running tests."[:80],
            }
        )

    # Priority 1: blocking lint issues > 3 -> suggest lint_fix
    blocking = (last_lint_run or {}).get("blocking_count", 0)
    if blocking > 3:
        injections.append(
            {
                "tool": "lint_fix",
                "priority": 1,
                "reason": f"{blocking} blocking lint issues. Auto-fix safe issues."[:80],
            }
        )

    # Priority 2: recent coherence = "systemic" -> suggest controlplane_run
    trajectory = (session_memory or {}).get("coherence_trajectory", [])
    if trajectory and trajectory[-1] == "systemic":
        injections.append(
            {
                "tool": "controlplane_run",
                "priority": 2,
                "reason": "Systemic coherence state detected. Run full analysis."[:80],
            }
        )

    # Priority 1: 2+ recent failed approaches -> suggest constraint_check
    approaches = (compass or {}).get("approaches", [])
    recent_failed = sum(
        1 for a in approaches[-5:] if isinstance(a, dict) and a.get("outcome") == "failed"
    )
    if recent_failed >= 2:
        injections.append(
            {
                "tool": "constraint_check",
                "priority": 1,
                "reason": f"{recent_failed} recent failed approaches. Check constraints."[:80],
            }
        )

    # Priority 3: always include habit_status reference
    if len(injections) < 4:
        injections.append(
            {
                "tool": "habit_status",
                "priority": 3,
                "reason": "Check habit mode state and token economics."[:80],
            }
        )

    # Cap at 4
    injections.sort(key=lambda x: x.get("priority", 3))
    return injections[:4]
