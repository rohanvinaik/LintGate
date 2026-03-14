"""Implementation functions for behavior_tools.py.

Extracted to keep the register() module under the 400-line structural limit.
Each ``_impl_*`` function receives ``helpers`` as its first argument; the thin
The ``mcp.tool`` wrappers in ``behavior_tools.register()`` simply forward.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any

# ── helpers ────────────────────────────────────────────────────────────────


def _build_constraint_recommendation(
    coverage_gap: int,
    recall: float,
    uncertainty: list[Any],
    similar_failures: list[dict[str, Any]],
) -> str:
    """Build the human-readable recommendation string."""
    parts: list[str] = []
    if coverage_gap > 0:
        parts.append(f"{coverage_gap} unverified constraint area{'s' if coverage_gap != 1 else ''}")
    if recall < 1.0:
        parts.append(f"{recall:.0%} prediction recall")
    if uncertainty:
        parts.append(f"{len(uncertainty)} uncertainty zone{'s' if len(uncertainty) != 1 else ''}")
    if similar_failures:
        parts.append(
            f"{len(similar_failures)} similar past failure{'s' if len(similar_failures) != 1 else ''}"
        )
    if parts:
        return ". ".join(parts) + ". Consider researching uncertainty zones before acting."
    return "Good constraint coverage. Proceed with awareness of known constraints."


def _find_similar_failures(
    approaches: list[Any],
    command_sig: str,
) -> list[dict[str, Any]]:
    """Find past failed approaches matching the current command binary."""
    # Hoist constant out of loop to avoid repeated string scan (PERF001).
    binary = command_sig.split(":")[0] if ":" in command_sig else ""
    similar: list[dict[str, Any]] = []
    if not binary:
        return similar
    for a in approaches:
        if a.outcome != "failed":
            continue
        approach_binary = a.approach_sig.split(":")[0] if ":" in a.approach_sig else ""
        if binary == approach_binary:
            last_err = a.error_sigs[-1] if a.error_sigs else ""
            similar.append(
                {
                    "sig": a.approach_sig,
                    "count": a.event_count,
                    "error": last_err[:80],
                }
            )
    return similar


def _compute_coverage_gap(
    declared: list[str],
    relevant: list[Any],
) -> tuple[int, float, set[str]]:
    """Compute coverage gap and recall between declared and relevant hypotheses."""
    matched_relevant_ids: set[str] = set()
    for claim in declared:
        claim_words = set(claim.lower().split())
        for h in relevant:
            hyp_words = set(h.claim.lower().split())
            if len(claim_words & hyp_words) >= 2:
                matched_relevant_ids.add(h.id)
                break
    agent_matched = len(matched_relevant_ids)
    coverage_gap = max(0, len(relevant) - agent_matched)
    recall = agent_matched / len(relevant) if relevant else 1.0
    return coverage_gap, recall, matched_relevant_ids


def _seed_theory_constraints(
    project_root: str,
    output: dict[str, Any],
) -> None:
    """Cold-start: seed from theory profile when no relevant hypotheses exist."""
    try:
        from lintgate.theory_extractor import extract_theory

        profile = extract_theory(project_root)
        theory_profile = profile.get("theory_profile", {})
        anti_patterns = theory_profile.get("anti_patterns", [])
        if anti_patterns:
            theory_constraints: list[str] = []
            for entry in anti_patterns:
                for claim in entry.get("claims", []):
                    if len(theory_constraints) < 5:
                        theory_constraints.append(claim[:120])
            if theory_constraints:
                output["theory_constraints"] = theory_constraints
                output["hint"] = "Seeded from project theory. Accuracy improves with session data."
    except Exception:
        pass


# ── tool implementations ──────────────────────────────────────────────────


def impl_hygiene_check(helpers: dict[str, Any], path: str, planned_action: str) -> str:
    project_root = helpers["_validate_project_root"](path)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("hygiene_check", project_root)

    output: dict[str, Any] = {}

    hygiene_result = None
    with contextlib.suppress(Exception):
        from lintgate.hygiene import classify_and_check

        hygiene_result = classify_and_check(planned_action, project_root)

    if hygiene_result is None:
        output["status"] = "no_checks_applicable"
        output["message"] = "No hygiene checks applicable for this command class."
    elif not hygiene_result.warnings:
        output["status"] = "pass"
        output["command_class"] = hygiene_result.command_class
        output["message"] = hygiene_result.recommendation
    else:
        output["status"] = "warnings"
        output["command_class"] = hygiene_result.command_class
        output["warnings"] = [
            {
                "check": w.check,
                "message": w.message,
                "confidence": round(w.confidence, 2),
                "actionability": w.actionability,
            }
            for w in hygiene_result.warnings
        ]
        output["recommendation"] = hygiene_result.recommendation

    from lintgate.next_action import NextAction, serialize_next_actions

    _na_list: list[NextAction] = []
    if hygiene_result and hygiene_result.warnings:
        for w in hygiene_result.warnings[:2]:
            if w.actionability == "immediate":
                _na_list.append(
                    NextAction(
                        tool="terminal",
                        reason=f"Fix: {w.message[:80]}",
                        priority=1,
                    )
                )
    output["next_actions"] = serialize_next_actions(_na_list)

    return helpers["_json_dumps"](output)  # type: ignore[no-any-return]


def impl_constraint_check(
    helpers: dict[str, Any],
    path: str,
    planned_action: str,
    known_constraints: list[str] | None = None,
) -> str:
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.behavior_compass import (
        add_declared_hypothesis,
        compute_coverage,
        compute_uncertainty_zones,
        find_relevant_hypotheses,
        normalize_command_sig,
    )
    from lintgate.controlplane.session_memory import (
        get_or_create_session,
        load_behavior_compass,
        save_behavior_compass,
        save_session,
    )

    project_root = helpers["_validate_project_root"](path)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("constraint_check", project_root)

    declared = known_constraints or []

    cp_config = load_controlplane_config(project_root)
    max_age = cp_config.session_max_age_hours if cp_config else 4.0
    session = get_or_create_session(project_root, max_age)
    compass = load_behavior_compass(session)

    compass.constraint_check_count_session += 1

    command_sig = normalize_command_sig(planned_action)

    relevant = find_relevant_hypotheses(compass, command_sig, tool="Bash")
    if not relevant:
        relevant = find_relevant_hypotheses(compass)

    for claim in declared:
        add_declared_hypothesis(compass, claim, command_sig)

    coverage_gap, recall, _ = _compute_coverage_gap(declared, relevant)
    coverage = compute_coverage(compass)
    uncertainty = compute_uncertainty_zones(compass)
    similar_failures = _find_similar_failures(compass.approaches, command_sig)
    recommendation = _build_constraint_recommendation(
        coverage_gap,
        recall,
        uncertainty,
        similar_failures,
    )

    save_behavior_compass(session, compass)
    save_session(session)

    output: dict[str, Any] = {
        "constraint_ledger": [
            {
                "claim": h.claim[:100],
                "confidence": round(h.confidence, 2),
                "source": h.source,
            }
            for h in relevant[:8]
        ],
        "coverage": {
            "constraints_verified": coverage.constraints_verified,
            "agent_reported": len(declared),
            "relevant_hypotheses": len(relevant),
            "coverage_gap": coverage_gap,
            "prediction_recall": round(recall, 2),
        },
        "uncertainty_zones": uncertainty[:3],
        "similar_failures": similar_failures[:5],
        "recommendation": recommendation,
    }

    if not relevant and compass.constraint_check_count_session <= 1:
        _seed_theory_constraints(project_root, output)

    if compass.constraint_check_count_session == 1:
        output["first_session_hint"] = (
            "First constraint_check this session. Predictions and constraint "
            "tracking improve as you use constraint_check before taking actions. "
            "State your known constraints and register predictions for best results."
        )
        _bp_onboarding = helpers["_build_onboarding_status"](project_root)
        if _bp_onboarding.get("config_state") != "config_enabled":
            output["onboarding"] = _bp_onboarding

    from lintgate.next_action import NextAction, serialize_next_actions

    _cc_actions: list[NextAction] = []
    if coverage_gap > 0 or recall < 0.5:
        _cc_actions.append(
            NextAction(
                tool="constraint_check",
                args={"path": project_root, "planned_action": planned_action},
                reason="Re-run after researching uncertainty zones",
                priority=1,
            )
        )
    if _cc_actions:
        output["next_actions"] = serialize_next_actions(_cc_actions)

    return helpers["_json_dumps"](output)  # type: ignore[no-any-return]


def impl_prediction_register(
    helpers: dict[str, Any],
    path: str,
    planned_action: str,
    prediction: str,
    prediction_type: str,
    prediction_value: str | int,
) -> str:
    import uuid

    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.behavior_compass import (
        Prediction,
        PredictionExpectation,
        compute_prediction_accuracy,
        find_relevant_hypotheses,
        normalize_command_sig,
    )
    from lintgate.controlplane.session_memory import (
        get_or_create_session,
        load_behavior_compass,
        save_behavior_compass,
        save_session,
    )

    project_root = helpers["_validate_project_root"](path)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("prediction_register", project_root)

    valid_types = {"exit_code", "error_signature", "stdout_contains"}
    if prediction_type not in valid_types:
        return helpers["_json_dumps"](  # type: ignore[no-any-return]
            {
                "error": f"Invalid prediction_type: {prediction_type!r}",
                "valid_types": sorted(valid_types),
            }
        )

    cp_config = load_controlplane_config(project_root)
    max_age = cp_config.session_max_age_hours if cp_config else 4.0
    session = get_or_create_session(project_root, max_age)
    compass = load_behavior_compass(session)

    command_sig = normalize_command_sig(planned_action)

    _is_bash_action = any(
        kw in planned_action.lower()
        for kw in (
            "bash",
            "execute",
            "run",
            "command",
            "shell",
            "npm",
            "pip",
            "git",
            "make",
            "pytest",
            "python",
            "uv",
        )
    )

    if not _is_bash_action or not command_sig or command_sig == "unknown:unknown":
        return helpers["_json_dumps"](  # type: ignore[no-any-return]
            {
                "status": "not_applicable",
                "message": (
                    "Predictions apply to Bash/execute actions with "
                    "recognizable command signatures."
                ),
                "command_sig": command_sig,
            }
        )

    relevant = find_relevant_hypotheses(compass, command_sig, tool="Bash")

    exp = PredictionExpectation(type=prediction_type, value=prediction_value)
    linked_hyp_id = relevant[0].id if relevant else None

    pred_obj = Prediction(
        prediction_id=uuid.uuid4().hex[:8],
        claim=prediction,
        expected=exp,
        declared_at_event=compass.event_counter,
        declared_sig=command_sig,
        linked_hypothesis_id=linked_hyp_id,
    )
    compass.pending_predictions.append(pred_obj)

    save_behavior_compass(session, compass)
    save_session(session)

    pred_accuracy = compute_prediction_accuracy(compass)
    checked_count = len(
        [e for e in compass.prediction_log if e.get("status") in ("confirmed", "falsified")]
    )
    accuracy_section: dict[str, Any] = {
        "pending_count": len(compass.pending_predictions),
        "checked_count": checked_count,
    }
    if pred_accuracy is not None:
        accuracy_section["accuracy"] = round(pred_accuracy, 2)
    elif checked_count > 0:
        accuracy_section["accuracy_note"] = (
            f"Need {5 - checked_count} more checked predictions for accuracy"
        )

    recent_outcomes = compass.prediction_log[-5:] if compass.prediction_log else []
    if recent_outcomes:
        accuracy_section["recent_outcomes"] = [
            {"id": o.get("prediction_id", "?"), "status": o.get("status", "?")}
            for o in recent_outcomes
        ]

    output: dict[str, Any] = {
        "status": "registered",
        "prediction_id": pred_obj.prediction_id,
        "command_sig": command_sig,
        "prediction_type": prediction_type,
        "prediction_value": prediction_value,
        "linked_hypothesis_id": linked_hyp_id,
        "prediction_tracking": accuracy_section,
    }

    from lintgate.next_action import NextAction, serialize_next_actions

    output["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="terminal",
                reason="Execute your planned action, then outcomes are checked automatically.",
                priority=1,
            ),
        ]
    )

    return helpers["_json_dumps"](output)  # type: ignore[no-any-return]


def impl_behavior_precheck(
    helpers: dict[str, Any],
    tools: dict[str, Any],
    path: str,
    planned_action: str,
    known_constraints: list[str] | None = None,
    prediction: str | None = None,
    prediction_type: str | None = None,
    prediction_value: str | int | None = None,
) -> str:
    import json

    project_root = helpers["_validate_project_root"](path)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("behavior_precheck_deprecated", project_root)

    constraint_result_raw = tools["constraint_check"](
        path=path,
        planned_action=planned_action,
        known_constraints=known_constraints,
    )
    output = json.loads(constraint_result_raw)

    prediction_registered = False
    _valid_prediction_types = {"exit_code", "error_signature", "stdout_contains"}
    if prediction:
        _pred_errors: list[str] = []
        if not prediction_type:
            _pred_errors.append("prediction_type is required when prediction is provided")
        elif prediction_type not in _valid_prediction_types:
            _pred_errors.append(
                f"prediction_type {prediction_type!r} invalid, "
                f"must be one of: {sorted(_valid_prediction_types)}"
            )
        if prediction_value is None:
            _pred_errors.append("prediction_value is required when prediction is provided")

        if _pred_errors:
            output["prediction_error"] = {
                "errors": _pred_errors,
                "hint": (
                    "Prediction was not registered. Provide all three: "
                    "prediction, prediction_type, prediction_value."
                ),
            }
        else:
            pred_result_raw = tools["prediction_register"](
                path=path,
                planned_action=planned_action,
                prediction=prediction,
                prediction_type=prediction_type,
                prediction_value=prediction_value,
            )
            pred_result = json.loads(pred_result_raw)
            prediction_registered = pred_result.get("status") == "registered"
            if "prediction_tracking" in pred_result:
                output["prediction_tracking"] = pred_result["prediction_tracking"]

    if prediction_registered:
        output.setdefault("prediction_tracking", {})["prediction_registered"] = True

    hygiene_result_raw = tools["hygiene_check"](
        path=path,
        planned_action=planned_action,
    )
    hygiene_result = json.loads(hygiene_result_raw)
    if hygiene_result.get("status") == "warnings":
        output["hygiene"] = {
            "command_class": hygiene_result.get("command_class"),
            "warnings": hygiene_result.get("warnings", []),
            "recommendation": hygiene_result.get("recommendation", ""),
        }

    output["deprecation"] = {
        "message": (
            "behavior_precheck is deprecated. Use the orthogonal tools instead: "
            "hygiene_check, constraint_check, prediction_register."
        ),
        "migration": {
            "hygiene_check": "hygiene_check(path, planned_action)",
            "constraint_check": "constraint_check(path, planned_action, known_constraints)",
            "prediction_register": (
                "prediction_register(path, planned_action, prediction, "
                "prediction_type, prediction_value)"
            ),
        },
    }

    return helpers["_json_dumps"](output)  # type: ignore[no-any-return]


def impl_global_memory_status(helpers: dict[str, Any], path: str) -> str:
    from lintgate.config import load_controlplane_config

    project_root = os.path.abspath(path)
    cp_config = load_controlplane_config(project_root)
    if cp_config is None:
        return helpers["_json_dumps"]({"error": "ControlPlane not configured"})  # type: ignore[no-any-return]

    from lintgate.controlplane.global_behavior_profile import (
        GLOBAL_PROFILE_PATH,
        load_global_profile,
    )

    profile = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)

    nudge_rates: dict[str, Any] = {}
    for signal, outcomes in profile.nudge_outcomes.items():
        total = outcomes.get("accepted", 0) + outcomes.get("ignored", 0)
        if total > 0:
            nudge_rates[signal] = {
                "accepted": outcomes.get("accepted", 0),
                "ignored": outcomes.get("ignored", 0),
                "acceptance_rate": round(outcomes["accepted"] / total, 2),
            }

    total_intents = sum(profile.intent_ratios.values()) or 1
    normalized_intents = {
        k: round(v / total_intents, 3)
        for k, v in sorted(profile.intent_ratios.items(), key=lambda x: -x[1])
    }

    transfer_info: dict[str, Any] = {}
    from lintgate.controlplane.session_memory import load_session

    session = load_session(project_root)
    if session:
        transfer_info = {
            "latest_transfer_packet": session.latest_transfer_packet,
            "packet_age_hours": round((time.time() - session.last_active) / 3600, 2)
            if session.latest_transfer_packet
            else None,
            "resolutions_available": len(session.resolution_repertoire),
            "suppressed_nudges": session.delivery_health_summary.get("skipped", 0)
            if hasattr(session, "delivery_health_summary")
            else 0,
        }

    output: dict[str, Any] = {
        "scope": "project",
        "scope_note": "Cross-session memory for this project (not cross-project)",
        "project_root": project_root,
        "enabled": cp_config.global_memory_enabled,
        "profile_path": str(GLOBAL_PROFILE_PATH),
        "session_count": profile.session_count,
        "updated_at": profile.updated_at,
        "transfer_telemetry": transfer_info,
        "signal_priors": profile.signal_priors,
        "intent_ratios_normalized": normalized_intents,
        "nudge_outcomes": nudge_rates,
        "computed_bias_adjustments": {
            k: round(v, 4) for k, v in profile.computed_bias_adjustments.items()
        },
        "alpha_config": {
            "initial": cp_config.global_memory_alpha,
            "decay_horizon": cp_config.global_memory_decay_horizon,
            "ttl_days": cp_config.global_memory_ttl_days,
        },
    }

    return helpers["_json_dumps"](output)  # type: ignore[no-any-return]


def impl_global_memory_reset(helpers: dict[str, Any], path: str) -> str:
    from lintgate.controlplane.global_behavior_profile import (
        GLOBAL_PROFILE_PATH,
        GlobalBehaviorProfile,
        save_global_profile,
    )

    project_root = os.path.abspath(path)
    save_global_profile(GlobalBehaviorProfile())
    return helpers["_json_dumps"](  # type: ignore[no-any-return]
        {
            "scope": "project",
            "scope_note": "Cross-session memory for this project (not cross-project)",
            "project_root": project_root,
            "status": "reset",
            "profile_path": str(GLOBAL_PROFILE_PATH),
            "message": "Global behavior profile has been reset to empty state.",
        }
    )
