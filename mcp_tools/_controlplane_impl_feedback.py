"""ControlPlane feedback and repairs implementation.

Extracted from controlplane_tools.py to keep the register() module under 400 lines.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

# ── controlplane_agent_feedback helpers ─────────────────────────────────


def _record_disagreement(session, run_id, disagreement, actions_taken):
    """Record a disagreement in the session."""
    session.agent_disagreements.append(
        {
            "run_id": run_id or "unknown",
            "disagreement": disagreement,
            "timestamp": time.time(),
        }
    )
    actions_taken.append(f"Recorded disagreement: {disagreement[:100]}")


def _process_accepted_constraints(session, accepted_constraints, actions_taken):
    """Accept constraints and collect their rule texts for patch generation."""
    from lintgate.controlplane.constraint_proposer import update_constraint_status

    accepted_rules: list[str] = []
    for key in accepted_constraints or []:
        if not update_constraint_status(session, key, "accepted"):
            actions_taken.append(f"Constraint not found: {key}")
            continue
        actions_taken.append(f"Accepted constraint: {key}")
        for p in session.proposed_constraints:
            if p.get("pattern_key") == key and p.get("status") == "accepted":
                rule_text = p.get("proposed_rule", "")
                if rule_text:
                    accepted_rules.append(rule_text)
                break
    return accepted_rules


def _process_rejected_constraints(session, rejected_constraints, actions_taken):
    """Reject constraints and record actions."""
    from lintgate.controlplane.constraint_proposer import update_constraint_status

    for key in rejected_constraints or []:
        if update_constraint_status(session, key, "rejected"):
            actions_taken.append(f"Rejected constraint: {key}")
        else:
            actions_taken.append(f"Constraint not found: {key}")


def _generate_living_context_patches(session, project_root, accepted_rules, actions_taken):
    """Generate context patches for accepted constraints if living context is enabled."""
    from lintgate.config import load_controlplane_config

    cp_config = load_controlplane_config(project_root)
    if not (cp_config and cp_config.inquiry.living_context and accepted_rules):
        return

    from lintgate.context_bootstrap import generate_context_patch

    for rule_text in accepted_rules:
        patch = generate_context_patch(
            project_root,
            trigger="constraint_accepted",
            evidence={"rule": rule_text, "rationale": "Accepted via agent feedback"},
        )
        if patch is not None:
            session.pending_patches.append(patch.to_dict())
            actions_taken.append(f"Generated context patch: {patch.patch_id}")


def _build_feedback_result(
    session,
    actions_taken: list[str],
    tuned_results: list[str],
    rejected_tunings: list[dict],
) -> dict[str, Any]:
    """Assemble the agent feedback response dict."""
    result: dict[str, Any] = {
        "session_id": session.session_id,
        "actions_taken": actions_taken,
        "total_disagreements": len(session.agent_disagreements),
        "proposed_constraints": len(session.proposed_constraints),
        "active_proposals": sum(
            1 for c in session.proposed_constraints if c.get("status") == "proposed"
        ),
    }
    if tuned_results:
        result["tuned"] = tuned_results
    if rejected_tunings:
        result["rejected_tunings"] = rejected_tunings
    return result


def _process_tuned_findings(
    tuned_findings: list[dict],
    project_root: str,
    actions_taken: list[str],
) -> tuple[list[str], list[dict]]:
    """Process signal tuning requests from agent feedback."""
    from lintgate.signal_tunings import VALID_ACTIONS, apply_tuning

    tuned: list[str] = []
    rejected: list[dict] = []

    for tf in tuned_findings:
        sig = tf.get("signature", "")
        action = tf.get("action", "")
        rationale = tf.get("rationale", "")

        if not sig:
            rejected.append({"signature": sig, "reason": "missing signature"})
            continue
        if action not in VALID_ACTIONS:
            rejected.append({"signature": sig, "reason": f"invalid action: {action}"})
            continue
        if action != "reset" and not rationale:
            rejected.append({"signature": sig, "reason": "rationale required for tuning"})
            continue

        result = apply_tuning(project_root, sig, action, rationale, tf.get("recurrence_count", 0))
        if result.get("error"):
            rejected.append({"signature": sig, "reason": result["error"]})
        else:
            tuned.append(sig)
            actions_taken.append(f"Tuned finding: {sig} ({action})")

    return tuned, rejected


def _process_test_failure_classifications(
    classifications: list[dict],
    session,
    actions_taken: list[str],
) -> None:
    """Record structured test failure classifications in session memory."""
    from lintgate.controlplane.session_memory import record_test_failure_classification

    valid_types = {"stale_test", "known_regression", "flaky", "out_of_scope"}

    for entry in classifications:
        fp = entry.get("fingerprint", "")
        classification = entry.get("classification", "")
        rationale = entry.get("rationale", "")

        if not fp:
            continue
        if classification not in valid_types:
            actions_taken.append(
                f"Rejected classification for {fp}: invalid type '{classification}'"
            )
            continue

        record_test_failure_classification(session, fp, classification, rationale)
        actions_taken.append(f"Classified test failure {fp} as {classification}")


def _impl_controlplane_agent_feedback(
    path,
    run_id,
    disagreement,
    accepted_constraints,
    rejected_constraints,
    helpers,
    *,
    tuned_findings=None,
    test_failure_classifications=None,
):
    """Core implementation of controlplane_agent_feedback."""
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = helpers["_validate_project_root"](path)
    session = get_or_create_session(project_root)
    actions_taken: list[str] = []

    if disagreement:
        _record_disagreement(session, run_id, disagreement, actions_taken)

    accepted_rules = _process_accepted_constraints(session, accepted_constraints, actions_taken)
    _process_rejected_constraints(session, rejected_constraints, actions_taken)
    _generate_living_context_patches(session, project_root, accepted_rules, actions_taken)

    tuned_results: list[str] = []
    rejected_tunings: list[dict] = []
    if tuned_findings:
        tuned_results, rejected_tunings = _process_tuned_findings(
            tuned_findings,
            project_root,
            actions_taken,
        )

    if test_failure_classifications:
        _process_test_failure_classifications(test_failure_classifications, session, actions_taken)

    save_session(session)
    result = _build_feedback_result(session, actions_taken, tuned_results, rejected_tunings)
    return json.dumps(result, indent=2)


# ── controlplane_apply_repairs helpers ──────────────────────────────────


def _select_repair_source(session, run_id):
    """Resolve the repair source snapshot and persisted run details."""
    from lintgate.state import load_controlplane_run

    if run_id:
        for snapshot in reversed(session.snapshots):
            if snapshot.run_id == run_id:
                return snapshot, load_controlplane_run(run_id), []

        run_details = load_controlplane_run(run_id)
        if run_details is not None:
            return None, run_details, []

        return None, None, [
            {
                "reason": "run_not_found",
                "detail": f"Run {run_id} is not available in session memory or persisted state.",
            }
        ]

    if not session.snapshots:
        return None, None, [
            {
                "reason": "no_snapshots",
                "detail": "No ControlPlane snapshots in session. Run controlplane_run first.",
            }
        ]

    latest = session.snapshots[-1]
    return latest, load_controlplane_run(latest.run_id) if latest.run_id else None, []


def _collect_pending_repairs(session, action_ids, safe_only, run_id=None):
    """Collect pending repairs from a specific run or the latest session snapshot.

    Returns (pending_repairs, skipped_diagnostics) where skipped_diagnostics
    is a list of per-repair status dicts explaining why each repair was not
    included in the pending set.
    """
    snapshot, run_details, diagnostics = _select_repair_source(session, run_id)
    if diagnostics:
        return [], diagnostics

    all_repairs = _load_all_repairs(snapshot, run_details=run_details)
    proposed_ids = (
        set(snapshot.repairs_proposed)
        if snapshot is not None
        else {repair.get("action_id", "") for repair in all_repairs if repair.get("action_id")}
    )
    source_run_id = run_id or (snapshot.run_id if snapshot is not None else "")

    if not all_repairs:
        return [], [
            {
                "reason": "no_repairs_in_run",
                "detail": f"Run {source_run_id or 'latest'} contains no repair actions.",
            }
        ]

    if not proposed_ids:
        return [], [
            {
                "reason": "no_proposed_repairs",
                "detail": f"Run {source_run_id or 'latest'} has no proposed repairs.",
            }
        ]

    pending: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for repair in all_repairs:
        repair_id = repair.get("action_id", "")
        if repair_id not in proposed_ids:
            continue
        outcome = session.repair_outcomes.get(repair_id, "pending")
        if outcome != "pending":
            skipped.append(
                {
                    "action_id": repair_id,
                    "status": "skipped",
                    "reason": "already_executed",
                    "detail": f"Previous outcome: {outcome}",
                }
            )
            continue
        if action_ids and repair_id not in action_ids:
            skipped.append(
                {"action_id": repair_id, "status": "skipped", "reason": "not_in_action_ids"}
            )
            continue
        if safe_only and not repair.get("safe", True):
            skipped.append(
                {
                    "action_id": repair_id,
                    "status": "skipped",
                    "reason": "safe_only_filter",
                    "detail": f"Repair kind '{repair.get('kind')}' excluded by safe_only=True",
                }
            )
            continue
        pending.append(repair)
    return pending, skipped


def _load_all_repairs(snapshot, *, run_details=None):
    """Load repair details from persisted run or fallback to snapshot catalog."""
    if run_details is None and snapshot is not None and getattr(snapshot, "run_id", ""):
        from lintgate.state import load_controlplane_run

        run_details = load_controlplane_run(snapshot.run_id)

    all_repairs: list[dict[str, Any]] = []

    if run_details:
        for ch_data in run_details.get("channels", {}).values():
            all_repairs.extend(ch_data.get("repairs", []))
        return all_repairs

    if snapshot is None:
        return all_repairs

    # Fallback: reconstruct from snapshot's compact repair catalog
    for aid, meta in getattr(snapshot, "repair_catalog", {}).items():
        all_repairs.append(
            {
                "action_id": aid,
                "kind": meta.get("kind", "command"),
                "summary": meta.get("summary", ""),
                "safe": meta.get("safe", "true") == "true",
                "channel": meta.get("channel", ""),
                "payload": dict(meta.get("payload", {})),
            }
        )
    return all_repairs


def _execute_single_repair(repair, project_root, session):
    """Execute a single command repair. Returns a result dict."""
    from lintgate.controlplane.session_memory import report_repair_outcome

    action_id = repair.get("action_id")

    if repair.get("kind") == "safe_delete":
        return _execute_safe_delete(repair, project_root, session)

    if repair.get("kind") != "command":
        return {"action_id": action_id, "status": "skipped", "reason": "not a command"}

    payload = repair.get("payload", {})
    command = payload.get("command", "")
    cwd = payload.get("cwd", project_root)

    if not command:
        return {"action_id": action_id, "status": "skipped", "reason": "empty command"}

    try:
        import shlex

        proc = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
        status = "ok" if proc.returncode == 0 else "error"
        report_repair_outcome(session, action_id or "", "applied" if status == "ok" else "ignored")
        return {
            "action_id": action_id,
            "command": command,
            "status": status,
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip()[-300:] if proc.stderr else None,
        }
    except subprocess.TimeoutExpired:
        return {"action_id": action_id, "command": command, "status": "timeout"}
    except OSError as e:
        return {
            "action_id": action_id,
            "command": command,
            "status": "error",
            "error": str(e),
        }


def _execute_safe_delete(repair, project_root, session):
    """Execute a safe_delete repair — removes a file after validation."""
    from lintgate.controlplane.session_memory import report_repair_outcome

    action_id = repair.get("action_id")
    payload = repair.get("payload", {})
    target_path = payload.get("target_path", "")

    if not target_path:
        return {"action_id": action_id, "status": "skipped", "reason": "no target_path"}

    # Safety: must be under project root and in a test directory
    abs_target = os.path.abspath(target_path)
    abs_root = os.path.abspath(project_root)
    if not abs_target.startswith(abs_root + os.sep):
        return {
            "action_id": action_id,
            "status": "blocked",
            "reason": "target outside project root",
        }

    rel_path = os.path.relpath(abs_target, abs_root)
    if not any(part.startswith("test") for part in Path(rel_path).parts):
        return {
            "action_id": action_id,
            "status": "blocked",
            "reason": "target not in test directory",
        }

    if not os.path.exists(abs_target):
        return {"action_id": action_id, "status": "skipped", "reason": "file already deleted"}

    try:
        os.remove(abs_target)
        report_repair_outcome(session, action_id or "", "applied")
        return {"action_id": action_id, "status": "ok", "deleted": rel_path}
    except OSError as e:
        return {"action_id": action_id, "status": "error", "error": str(e)}


def _impl_controlplane_apply_repairs(path, action_ids, safe_only, helpers, *, run_id=None):
    """Core implementation of controlplane_apply_repairs."""
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = helpers["_validate_project_root"](path)
    session = get_or_create_session(project_root)

    pending_repairs, skip_diagnostics = _collect_pending_repairs(
        session,
        action_ids,
        safe_only,
        run_id=run_id,
    )

    results = [_execute_single_repair(repair, project_root, session) for repair in pending_repairs]

    save_session(session)

    # Aggregate execution outcomes
    succeeded = sum(1 for r in results if r.get("status") == "ok")
    failed = sum(1 for r in results if r.get("status") in ("error", "timeout"))
    skipped_in_exec = sum(1 for r in results if r.get("status") in ("skipped", "blocked"))

    # Aggregate skip reasons across both collection-phase and execution-phase skips
    skipped_by_reason: dict[str, int] = {}
    for sd in skip_diagnostics:
        reason = sd.get("reason", "unknown")
        skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
    for r in results:
        if r.get("status") in ("skipped", "blocked"):
            reason = r.get("reason", "unknown")
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1

    pending_remaining = sum(1 for v in session.repair_outcomes.values() if v == "pending")

    response: dict[str, Any] = {
        "run_id": run_id,
        "summary": {
            "total_proposed": len(pending_repairs) + len(skip_diagnostics),
            "collected": len(pending_repairs),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": len(skip_diagnostics) + skipped_in_exec,
        },
        "repairs_executed": len(results),
        "results": results,
        "pending_remaining": pending_remaining,
    }
    if skipped_by_reason:
        response["summary"]["skipped_by_reason"] = skipped_by_reason
    if skip_diagnostics:
        response["skipped"] = skip_diagnostics

    return json.dumps(response, indent=2)
