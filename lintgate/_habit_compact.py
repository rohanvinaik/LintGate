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

    Args:
        state: Current habit mode state.
        project_root: Project path.
        session_memory: Serialized SessionMemory dict (optional).
        compass: Serialized BehaviorCompass dict (optional).
        last_lint_run: Last lint run details dict (optional).
        theory_pack: Theory pack from build_theory_pack (optional).
        issue_memory: Issue memory data (optional).
        token_estimate: Token tracker usage summary (optional).

    Returns:
        Structured snapshot dict ready for injection post-compaction.
    """
    snapshot: dict[str, Any] = {}

    # 1. Mode (50 tokens — fixed structure, never exceeds)
    snapshot["mode"] = {
        "active": state.active,
        "habit_score": round(state.habit_score, 2),
        "declared": state.declared,
        "compaction_number": state.compaction_count + 1,
    }

    # 2. Active context (100 tokens — cap at 10 files)
    active_files = state.active_files[:10]
    # Truncate to basenames if too many chars
    total_chars = sum(len(f) for f in active_files)
    if total_chars > 400:
        active_files = [f.rsplit("/", 1)[-1] for f in active_files]
    snapshot["active_context"] = {
        "files": active_files,
        "last_test_status": state.last_test_status,
    }

    # 3. Theory digest (1500 tokens — already capped by build_theory_pack)
    if theory_pack:
        snapshot["theory_digest"] = theory_pack
    else:
        snapshot["theory_digest"] = None

    # 4. Lint state (400 tokens — cap at 5 blocking issues)
    if last_lint_run:
        blocking_issues = []
        for issue in last_lint_run.get("issues", [])[:5]:
            msg = str(issue.get("message", ""))[:80]
            blocking_issues.append(
                {
                    "file": issue.get("file", ""),
                    "line": issue.get("line"),
                    "kind": issue.get("kind", ""),
                    "message": msg,
                }
            )
        snapshot["lint_state"] = {
            "blocking_count": last_lint_run.get("blocking_count", 0),
            "warning_count": last_lint_run.get("warning_count", 0),
            "issues": blocking_issues,
        }
    else:
        snapshot["lint_state"] = None

    # 5. Behavioral trajectory (300 tokens — top 3 constraints, top 2 errors)
    if compass:
        hypotheses = compass.get("hypotheses", [])
        # Sort by confidence desc, take top 3
        top_hyps = sorted(hypotheses, key=lambda h: h.get("confidence", 0), reverse=True)[:3]
        hyp_summaries = [
            {"claim": h.get("claim", "")[:80], "confidence": h.get("confidence", 0)}
            for h in top_hyps
        ]
        # Top 2 errors from error_memory
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
        # Prediction accuracy
        coverage = compass.get("coverage", {})
        snapshot["behavioral_trajectory"] = {
            "top_constraints": hyp_summaries,
            "top_errors": error_summaries,
            "prediction_recall": coverage.get("prediction_recall", 0.0),
        }
    else:
        snapshot["behavioral_trajectory"] = None

    # 6. Recurring issues (150 tokens — top 3)
    if issue_memory:
        recurrent = issue_memory.get("recurrent_issues", [])[:3]
        snapshot["recurring_issues"] = recurrent
    else:
        snapshot["recurring_issues"] = None

    # 7. Session history (100 tokens — last 2 snapshots)
    if session_memory:
        snapshots = session_memory.get("snapshots", [])
        recent = snapshots[-2:] if len(snapshots) >= 2 else snapshots
        snapshot["session_history"] = [
            {
                "coherence": s.get("coherence_state", ""),
                "blocking": s.get("blocking_count", 0),
                "findings": s.get("finding_count", 0),
            }
            for s in recent
        ]
    else:
        snapshot["session_history"] = None

    # 8. Coherence trajectory (30 tokens — last 3 states)
    if session_memory:
        trajectory = session_memory.get("coherence_trajectory", [])
        snapshot["coherence_trajectory"] = trajectory[-3:]
    else:
        snapshot["coherence_trajectory"] = None

    # 9. Token state (50 tokens — fixed structure)
    if token_estimate:
        snapshot["token_state"] = {
            "estimated_used": token_estimate.get("estimated_tokens_used", 0),
            "tool_calls": token_estimate.get("tool_call_count", 0),
            "lines_written": token_estimate.get("lines_written", 0),
        }
    else:
        snapshot["token_state"] = None

    # 10. Tool guidance (300 tokens — max 4 injections)
    injections = _build_tool_injections(state, compass, last_lint_run, session_memory)
    snapshot["tool_guidance"] = injections

    # Focus directive
    focus_files = ", ".join(state.active_files[:3]) if state.active_files else "none"
    test_str = f"Test: {state.last_test_status}." if state.last_test_status else ""
    snapshot["focus_directive"] = f"You are in Habit Mode. Focus: [{focus_files}]. {test_str}"

    # Enforce hard cap
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
    if last_lint_run:
        blocking = last_lint_run.get("blocking_count", 0)
        if blocking > 3:
            injections.append(
                {
                    "tool": "lint_fix",
                    "priority": 1,
                    "reason": f"{blocking} blocking lint issues. Auto-fix safe issues."[:80],
                }
            )

    # Priority 2: recent coherence = "systemic" -> suggest controlplane_run
    if session_memory:
        trajectory = session_memory.get("coherence_trajectory", [])
        if trajectory and trajectory[-1] == "systemic":
            injections.append(
                {
                    "tool": "controlplane_run",
                    "priority": 2,
                    "reason": "Systemic coherence state detected. Run full analysis."[:80],
                }
            )

    # Priority 1: 2+ recent failed approaches -> suggest constraint_check
    if compass:
        approaches = compass.get("approaches", [])
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
