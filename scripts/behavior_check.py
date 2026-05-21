#!/usr/bin/env python3
"""Behavioral supervision checks — standalone.

Commands:
    hygiene PATH --action "..."
    constraint PATH --action "..." [--known-constraint "..." ...]
    predict PATH --action "..." --prediction "..." --type exit_code --value 0
    precheck PATH --action "..." [--known-constraint "..." ...]
                   [--prediction "..." --type ... --value ...]
    memory-status PATH
    memory-reset PATH
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import uuid
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from scripts._common import emit, emit_error, validate_project_root

_VALID_PREDICTION_TYPES = {"exit_code", "error_signature", "stdout_contains"}


def _build_onboarding_status(project_root: str) -> dict[str, Any]:
    """Minimal onboarding status — mirrors mcp_server._build_onboarding_status.

    Four config states: no_config, config_no_controlplane_section,
    config_disabled, config_enabled. Kept local to avoid cross-module import
    during subprocess start-up.
    """
    from lintgate.config import load_controlplane_config

    config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
    config_file_exists = os.path.exists(config_path)
    cp_config = load_controlplane_config(project_root)
    has_controlplane_section = False
    if config_file_exists:
        with contextlib.suppress(Exception):
            import yaml as _yaml

            with open(config_path) as _f:
                _raw = _yaml.safe_load(_f) or {}
            has_controlplane_section = bool(
                isinstance(_raw, dict) and isinstance(_raw.get("controlplane"), dict)
            )

    status: dict[str, Any] = {
        "config_found": config_file_exists,
        "config_path_checked": config_path,
        "controlplane_enabled": cp_config.enabled if cp_config else False,
        "automatic_hook_active": cp_config.enabled if cp_config else False,
        "using_default_config": cp_config is None,
    }

    if not config_file_exists:
        status["config_state"] = "no_config"
    elif cp_config is None and not has_controlplane_section:
        status["config_state"] = "config_no_controlplane_section"
    elif cp_config is not None and not cp_config.enabled:
        status["config_state"] = "config_disabled"
    else:
        status["config_state"] = "config_enabled"
    return status


# ── shared helpers (behavior-specific) ──────────────────────────────────────


def _build_constraint_recommendation(
    coverage_gap: int,
    recall: float,
    uncertainty: list[Any],
    similar_failures: list[dict[str, Any]],
) -> str:
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


def _find_similar_failures(approaches: list[Any], command_sig: str) -> list[dict[str, Any]]:
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
                {"sig": a.approach_sig, "count": a.event_count, "error": last_err[:80]}
            )
    return similar


def _compute_coverage_gap(
    declared: list[str], relevant: list[Any]
) -> tuple[int, float, set[str]]:
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


def _seed_theory_constraints(project_root: str, output: dict[str, Any]) -> None:
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


# ── compute functions (return dicts, not JSON) ──────────────────────────────


def compute_hygiene(project_root: str, planned_action: str) -> tuple[dict[str, Any], str, list]:
    """Run hygiene checks for a planned action. Returns (output, summary, next_actions)."""
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
                    NextAction(tool="terminal", reason=f"Fix: {w.message[:80]}", priority=1)
                )
    na_serialized = serialize_next_actions(_na_list)
    output["next_actions"] = na_serialized

    status = output.get("status", "unknown")
    if status == "warnings":
        n_warn = len(output.get("warnings", []))
        summary = (
            f"Hygiene: {n_warn} warning(s) for {output.get('command_class', 'unknown')} "
            f"action. {output.get('recommendation', '')}"
        )
    elif status == "pass":
        summary = (
            f"Hygiene: pass ({output.get('command_class', 'unknown')}). "
            f"{output.get('message', '')}"
        )
    else:
        summary = f"Hygiene: {output.get('message', 'no checks applicable')}"
    return output, summary, na_serialized


def compute_constraint(
    project_root: str,
    planned_action: str,
    known_constraints: list[str] | None = None,
) -> tuple[dict[str, Any], str, list]:
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
        coverage_gap, recall, uncertainty, similar_failures
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
        _bp_onboarding = _build_onboarding_status(project_root)
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
    na_serialized = serialize_next_actions(_cc_actions) if _cc_actions else []
    if na_serialized:
        output["next_actions"] = na_serialized

    cov = output.get("coverage", {})
    n_relevant = cov.get("relevant_hypotheses", 0)
    gap = cov.get("coverage_gap", 0)
    recall_pct = f"{cov.get('prediction_recall', 1.0):.0%}"
    n_uncertain = len(output.get("uncertainty_zones", []))
    n_similar = len(output.get("similar_failures", []))
    summary = (
        f"Constraints: {n_relevant} relevant, gap={gap}, recall={recall_pct}, "
        f"{n_uncertain} uncertainty zones, {n_similar} similar failures. "
        f"{recommendation}"
    )
    return output, summary, na_serialized


def compute_predict(
    project_root: str,
    planned_action: str,
    prediction: str,
    prediction_type: str,
    prediction_value: str | int,
) -> tuple[dict[str, Any] | None, str, list, dict[str, Any] | None]:
    """Register a prediction. Returns (output, summary, next_actions, error_dict).

    If invalid, returns (None, "", [], error_dict). The error_dict is suitable
    for emission as a plain JSON (no disk persistence).
    """
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

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("prediction_register", project_root)

    if prediction_type not in _VALID_PREDICTION_TYPES:
        return (
            None,
            "",
            [],
            {
                "error": f"Invalid prediction_type: {prediction_type!r}",
                "valid_types": sorted(_VALID_PREDICTION_TYPES),
            },
        )

    cp_config = load_controlplane_config(project_root)
    max_age = cp_config.session_max_age_hours if cp_config else 4.0
    session = get_or_create_session(project_root, max_age)
    compass = load_behavior_compass(session)

    command_sig = normalize_command_sig(planned_action)

    _is_bash_action = any(
        kw in planned_action.lower()
        for kw in (
            "bash", "execute", "run", "command", "shell", "npm", "pip",
            "git", "make", "pytest", "python", "uv",
        )
    )

    if not _is_bash_action or not command_sig or command_sig == "unknown:unknown":
        return (
            None,
            "",
            [],
            {
                "status": "not_applicable",
                "message": (
                    "Predictions apply to Bash/execute actions with "
                    "recognizable command signatures."
                ),
                "command_sig": command_sig,
            },
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

    na_serialized = serialize_next_actions(
        [
            NextAction(
                tool="terminal",
                reason="Execute your planned action, then outcomes are checked automatically.",
                priority=1,
            ),
        ]
    )
    output["next_actions"] = na_serialized

    acc_str = ""
    if accuracy_section.get("accuracy") is not None:
        acc_str = f", accuracy={accuracy_section['accuracy']}"
    summary = (
        f"Prediction '{pred_obj.prediction_id}' registered: "
        f"{prediction_type}={prediction_value} for {command_sig}. "
        f"{accuracy_section.get('pending_count', 0)} pending, "
        f"{accuracy_section.get('checked_count', 0)} checked{acc_str}."
    )
    return output, summary, na_serialized, None


def compute_precheck(
    project_root: str,
    planned_action: str,
    known_constraints: list[str] | None = None,
    prediction: str | None = None,
    prediction_type: str | None = None,
    prediction_value: str | int | None = None,
) -> tuple[dict[str, Any], str, list]:
    """Deprecated aggregator — calls constraint, predict, hygiene compute fns."""
    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("behavior_precheck_deprecated", project_root)

    # constraint_check path
    c_output = compute_constraint(project_root, planned_action, known_constraints)[0]

    # Undo the double-count: constraint_check incremented the counter,
    # but this wrapper call should not count as a separate invocation.
    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            load_behavior_compass,
        )

        _session = get_or_create_session(project_root, 4.0)
        _compass = load_behavior_compass(_session)
        if _compass.constraint_check_count_session > 0:
            _compass.constraint_check_count_session -= 1

    output = dict(c_output)
    # Strip next_actions carried by constraint_check output (will be rebuilt below)
    prev_na = output.pop("next_actions", None)

    prediction_registered = False
    if prediction:
        _pred_errors: list[str] = []
        if not prediction_type:
            _pred_errors.append("prediction_type is required when prediction is provided")
        elif prediction_type not in _VALID_PREDICTION_TYPES:
            _pred_errors.append(
                f"prediction_type {prediction_type!r} invalid, "
                f"must be one of: {sorted(_VALID_PREDICTION_TYPES)}"
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
            _p_res = compute_predict(
                project_root,
                planned_action,
                prediction,
                prediction_type,  # type: ignore[arg-type]
                prediction_value,  # type: ignore[arg-type]
            )
            p_output, p_err = _p_res[0], _p_res[3]
            if p_err is not None:
                output["prediction_error"] = {"errors": [p_err.get("error", "unknown")]}
            else:
                prediction_registered = bool(p_output and p_output.get("status") == "registered")
                if p_output and "prediction_tracking" in p_output:
                    output["prediction_tracking"] = p_output["prediction_tracking"]

    if prediction_registered:
        output.setdefault("prediction_tracking", {})["prediction_registered"] = True

    h_output = compute_hygiene(project_root, planned_action)[0]
    if h_output.get("status") == "warnings":
        output["hygiene"] = {
            "command_class": h_output.get("command_class"),
            "warnings": h_output.get("warnings", []),
            "recommendation": h_output.get("recommendation", ""),
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

    cov = output.get("coverage", {})
    gap = cov.get("coverage_gap", 0)
    has_hygiene = "hygiene" in output
    pred_ok = output.get("prediction_tracking", {}).get("prediction_registered", False)
    parts = [f"gap={gap}"]
    if has_hygiene:
        parts.append(f"{len(output['hygiene'].get('warnings', []))} hygiene warnings")
    if pred_ok:
        parts.append("prediction registered")
    summary = f"Behavior precheck (DEPRECATED): {', '.join(parts)}. Use orthogonal tools instead."

    na_serialized = prev_na or []
    if na_serialized:
        output["next_actions"] = na_serialized
    return output, summary, na_serialized


def compute_memory_status(project_root: str) -> dict[str, Any] | None:
    """Returns output dict, or None if controlplane not configured (emit error)."""
    from lintgate.config import load_controlplane_config

    cp_config = load_controlplane_config(project_root)
    if cp_config is None:
        return None

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

    return {
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


def compute_memory_reset(project_root: str) -> dict[str, Any]:
    from lintgate.controlplane.global_behavior_profile import (
        GLOBAL_PROFILE_PATH,
        GlobalBehaviorProfile,
        save_global_profile,
    )

    save_global_profile(GlobalBehaviorProfile())
    return {
        "scope": "project",
        "scope_note": "Cross-session memory for this project (not cross-project)",
        "project_root": project_root,
        "status": "reset",
        "profile_path": str(GLOBAL_PROFILE_PATH),
        "message": "Global behavior profile has been reset to empty state.",
    }


# ── command handlers ────────────────────────────────────────────────────────


def cmd_hygiene(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    output, summary, na = compute_hygiene(project_root, args.action)
    emit(output, "hygiene_check", project_root, summary, next_actions=na or None)


def cmd_constraint(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    output, summary, na = compute_constraint(project_root, args.action, args.known_constraint)
    emit(output, "constraint_check", project_root, summary, next_actions=na or None)


def cmd_predict(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    pv: str | int = args.value
    if args.type == "exit_code":
        try:
            pv = int(args.value)
        except (TypeError, ValueError):
            pass
    output, summary, na, err = compute_predict(
        project_root, args.action, args.prediction, args.type, pv
    )
    if err is not None:
        print(json.dumps(err, separators=(",", ":"), default=str))
        return
    assert output is not None
    emit(output, "prediction_register", project_root, summary, next_actions=na or None)


def cmd_precheck(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    pv: str | int | None = args.value
    if pv is not None and args.type == "exit_code":
        try:
            pv = int(pv)
        except (TypeError, ValueError):
            pass
    output, summary, na = compute_precheck(
        project_root,
        args.action,
        args.known_constraint,
        args.prediction,
        args.type,
        pv,
    )
    emit(output, "behavior_precheck", project_root, summary, next_actions=na or None)


def cmd_memory_status(args: argparse.Namespace) -> None:
    project_root = os.path.abspath(args.path)
    output = compute_memory_status(project_root)
    if output is None:
        emit_error("ControlPlane not configured")
    profile = output  # type: ignore[assignment]
    n_signals = len(profile.get("signal_priors", {}))
    n_nudges = len(profile.get("nudge_outcomes", {}))
    n_intents = len(profile.get("intent_ratios_normalized", {}))
    enabled_str = "enabled" if profile.get("enabled") else "disabled"
    summary = (
        f"Global memory ({enabled_str}): {profile.get('session_count', 0)} sessions, "
        f"{n_signals} signal priors, {n_nudges} nudge outcomes, {n_intents} intent types."
    )
    emit(profile, "global_memory_status", project_root, summary)


def cmd_memory_reset(args: argparse.Namespace) -> None:
    project_root = os.path.abspath(args.path)
    output = compute_memory_reset(project_root)
    # Historical contract: returned plain json.dumps(dict) — preserve by emitting
    # the dict as-is (not wrapped in a disk envelope).
    print(json.dumps(output, separators=(",", ":"), default=str))


def main() -> None:
    parser = argparse.ArgumentParser(prog="behavior_check", description="Behavioral supervision checks")
    sub = parser.add_subparsers(dest="command", required=True)

    p_hyg = sub.add_parser("hygiene", help="Hygiene precheck for a planned action")
    p_hyg.add_argument("path")
    p_hyg.add_argument("--action", required=True)

    p_con = sub.add_parser("constraint", help="Constraint ledger check")
    p_con.add_argument("path")
    p_con.add_argument("--action", required=True)
    p_con.add_argument("--known-constraint", action="append", default=[])

    p_pred = sub.add_parser("predict", help="Register a falsifiable prediction")
    p_pred.add_argument("path")
    p_pred.add_argument("--action", required=True)
    p_pred.add_argument("--prediction", required=True)
    p_pred.add_argument("--type", required=True)
    p_pred.add_argument("--value", required=True)

    p_pre = sub.add_parser("precheck", help="DEPRECATED aggregator")
    p_pre.add_argument("path")
    p_pre.add_argument("--action", required=True)
    p_pre.add_argument("--known-constraint", action="append", default=[])
    p_pre.add_argument("--prediction")
    p_pre.add_argument("--type")
    p_pre.add_argument("--value")

    p_ms = sub.add_parser("memory-status", help="Cross-session behavioral memory status")
    p_ms.add_argument("path")

    p_mr = sub.add_parser("memory-reset", help="Reset global behavior profile")
    p_mr.add_argument("path")

    args = parser.parse_args()
    {
        "hygiene": cmd_hygiene,
        "constraint": cmd_constraint,
        "predict": cmd_predict,
        "precheck": cmd_precheck,
        "memory-status": cmd_memory_status,
        "memory-reset": cmd_memory_reset,
    }[args.command](args)


if __name__ == "__main__":
    main()
