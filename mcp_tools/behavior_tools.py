"""Behavior tools — orthogonal behavioral supervision lenses.

Tools:
- hygiene_check: Command-class precondition checks (is venv active? lockfile fresh?)
- constraint_check: Constraint ledger, coverage gaps, uncertainty zones, similar failures
- prediction_register: Register falsifiable predictions for upcoming actions
- behavior_precheck: DEPRECATED compat wrapper — delegates to the three tools above
- global_memory_status: Cross-session behavioral analysis status
- global_memory_reset: Reset global behavior profile
"""

from __future__ import annotations

import os
from typing import Any


def register(mcp, helpers):
    """Register behavior tools on the shared MCP instance."""

    # ── hygiene_check ─────────────────────────────────────────────────

    @mcp.tool()
    def hygiene_check(
        path: str,
        planned_action: str,
    ) -> str:
        """Check command-class hygiene preconditions before executing.

        WHEN TO USE: Before running Bash commands that install packages,
        commit code, modify env files, or publish builds. Catches missing
        venv, stale lockfiles, unpinned versions, and staged secrets.

        This is the "professional instinct" layer — a senior engineer
        checks preconditions before running risky commands.

        Example: hygiene_check(path="/my/project", planned_action="pip install requests")

        Args:
            path: Project root path.
            planned_action: Free text describing the planned command.
        """
        import contextlib

        project_root = helpers["_validate_project_root"](path)

        # Telemetry: track hygiene_check usage
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

        output["next_actions"] = []
        if hygiene_result and hygiene_result.warnings:
            for w in hygiene_result.warnings[:2]:
                if w.actionability == "immediate":
                    output["next_actions"].append(
                        {
                            "action": f"Fix: {w.message[:80]}",
                            "priority": 1,
                        }
                    )

        return helpers["_json_dumps"](output)

    # ── constraint_check ──────────────────────────────────────────────

    @mcp.tool()
    def constraint_check(
        path: str,
        planned_action: str,
        known_constraints: list[str] | None = None,
    ) -> str:
        """Check planned action against the constraint ledger.

        WHEN TO USE: Before attempting a new approach or after failures.
        Declares your known constraints, then identifies coverage gaps,
        uncertainty zones, and similar past failures. This is the core
        "do you understand the constraint space?" check.

        Example: constraint_check(path="/my/project",
            planned_action="run pytest",
            known_constraints=["some tests may fail due to missing fixtures"])

        Args:
            path: Project root path.
            planned_action: Free text describing the planned action.
            known_constraints: Agent's self-reported constraints for this action.
        """
        import contextlib

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

        # Telemetry
        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("constraint_check", project_root)

        declared = known_constraints or []

        # Load config and session
        cp_config = load_controlplane_config(project_root)
        max_age = cp_config.session_max_age_hours if cp_config else 4.0
        session = get_or_create_session(project_root, max_age)
        compass = load_behavior_compass(session)

        # Track constraint_check invocation
        compass.constraint_check_count_session += 1

        # Extract command sig
        command_sig = normalize_command_sig(planned_action)

        # Find relevant hypotheses
        relevant = find_relevant_hypotheses(compass, command_sig, tool="Bash")
        if not relevant:
            relevant = find_relevant_hypotheses(compass)

        # Declare agent's constraints as hypotheses
        for claim in declared:
            add_declared_hypothesis(compass, claim, command_sig)

        # Compute coverage gap
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

        # Recompute coverage
        coverage = compute_coverage(compass)
        uncertainty = compute_uncertainty_zones(compass)

        # Find similar past failures
        similar_failures = []
        for a in compass.approaches:
            if a.outcome == "failed":
                binary = command_sig.split(":")[0] if ":" in command_sig else ""
                approach_binary = a.approach_sig.split(":")[0] if ":" in a.approach_sig else ""
                if binary and binary == approach_binary:
                    last_err = a.error_sigs[-1] if a.error_sigs else ""
                    similar_failures.append(
                        {
                            "sig": a.approach_sig,
                            "count": a.event_count,
                            "error": last_err[:80],
                        }
                    )

        # Build recommendation
        parts = []
        if coverage_gap > 0:
            parts.append(
                f"{coverage_gap} unverified constraint area{'s' if coverage_gap != 1 else ''}"
            )
        if recall < 1.0:
            parts.append(f"{recall:.0%} prediction recall")
        if uncertainty:
            parts.append(
                f"{len(uncertainty)} uncertainty zone{'s' if len(uncertainty) != 1 else ''}"
            )
        if similar_failures:
            parts.append(
                f"{len(similar_failures)} similar past failure{'s' if len(similar_failures) != 1 else ''}"
            )

        if parts:
            recommendation = (
                ". ".join(parts) + ". Consider researching uncertainty zones before acting."
            )
        else:
            recommendation = (
                "Good constraint coverage. Proceed with awareness of known constraints."
            )

        # Save compass updates
        save_behavior_compass(session, compass)
        save_session(session)

        # Build output
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

        # Cold-start: seed from theory profile when no relevant hypotheses exist
        if not relevant and compass.constraint_check_count_session <= 1:
            try:
                from lintgate.theory_extractor import extract_theory

                profile = extract_theory(project_root)
                theory_profile = profile.get("theory_profile", {})
                anti_patterns = theory_profile.get("anti_patterns", [])
                if anti_patterns:
                    theory_constraints = []
                    for entry in anti_patterns:
                        for claim in entry.get("claims", []):
                            if len(theory_constraints) < 5:
                                theory_constraints.append(claim[:120])
                    if theory_constraints:
                        output["theory_constraints"] = theory_constraints
                        output["hint"] = (
                            "Seeded from project theory. "
                            "Accuracy improves with session data."
                        )
            except Exception:
                pass

        # First-session guidance
        if compass.constraint_check_count_session == 1:
            output["first_session_hint"] = (
                "First constraint_check this session. Predictions and constraint "
                "tracking improve as you use constraint_check before taking actions. "
                "State your known constraints and register predictions for best results."
            )
            _bp_onboarding = helpers["_build_onboarding_status"](project_root)
            if _bp_onboarding.get("config_state") != "config_enabled":
                output["onboarding"] = _bp_onboarding

        # next_actions
        next_actions = []
        if coverage_gap > 0 or recall < 0.5:
            next_actions.append(
                {
                    "tool": "constraint_check",
                    "reason": "Re-run after researching uncertainty zones",
                    "priority": 1,
                }
            )
        if next_actions:
            output["next_actions"] = next_actions

        return helpers["_json_dumps"](output)

    # ── prediction_register ───────────────────────────────────────────

    @mcp.tool()
    def prediction_register(
        path: str,
        planned_action: str,
        prediction: str,
        prediction_type: str,
        prediction_value: str | int,
    ) -> str:
        """Register a falsifiable prediction for an upcoming action.

        WHEN TO USE: Before running a command whose outcome matters.
        Register what you expect to happen — the system will check the
        prediction against the actual outcome and track accuracy.

        Example: prediction_register(path="/my/project",
            planned_action="pytest tests/",
            prediction="Tests will pass",
            prediction_type="exit_code",
            prediction_value=0)

        Args:
            path: Project root path.
            planned_action: Free text describing the planned action.
            prediction: Free-text description of expected outcome.
            prediction_type: "exit_code", "error_signature", or "stdout_contains".
            prediction_value: The expected value for the prediction.
        """
        import contextlib
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

        # Telemetry
        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("prediction_register", project_root)

        # Validate prediction_type
        valid_types = {"exit_code", "error_signature", "stdout_contains"}
        if prediction_type not in valid_types:
            return helpers["_json_dumps"](
                {
                    "error": f"Invalid prediction_type: {prediction_type!r}",
                    "valid_types": sorted(valid_types),
                }
            )

        # Load session
        cp_config = load_controlplane_config(project_root)
        max_age = cp_config.session_max_age_hours if cp_config else 4.0
        session = get_or_create_session(project_root, max_age)
        compass = load_behavior_compass(session)

        command_sig = normalize_command_sig(planned_action)

        # Check if action involves Bash/execute
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
            return helpers["_json_dumps"](
                {
                    "status": "not_applicable",
                    "message": (
                        "Predictions apply to Bash/execute actions with "
                        "recognizable command signatures."
                    ),
                    "command_sig": command_sig,
                }
            )

        # Find relevant hypothesis to link
        relevant = find_relevant_hypotheses(compass, command_sig, tool="Bash")

        exp = PredictionExpectation(
            type=prediction_type,
            value=prediction_value,
        )
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

        # Save
        save_behavior_compass(session, compass)
        save_session(session)

        # Compute accuracy section
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

        # Recent outcomes
        recent_outcomes = compass.prediction_log[-5:] if compass.prediction_log else []
        if recent_outcomes:
            accuracy_section["recent_outcomes"] = [
                {
                    "id": o.get("prediction_id", "?"),
                    "status": o.get("status", "?"),
                }
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

        # next_actions: strong guidance per tool
        output["next_actions"] = [
            {
                "action": "Execute your planned action, then outcomes are checked automatically.",
                "priority": 1,
            },
        ]

        return helpers["_json_dumps"](output)

    # ── behavior_precheck (DEPRECATED compat wrapper) ─────────────────

    @mcp.tool()
    def behavior_precheck(
        path: str,
        planned_action: str,
        known_constraints: list[str] | None = None,
        prediction: str | None = None,
        prediction_type: str | None = None,
        prediction_value: str | int | None = None,
    ) -> str:
        """Check a planned action against known constraints before executing it.

        DEPRECATED: This tool combines three orthogonal concerns that are
        now separate tools. Prefer using:
        - hygiene_check(path, planned_action) — command-class preconditions
        - constraint_check(path, planned_action, known_constraints) — constraint ledger
        - prediction_register(path, planned_action, prediction, ...) — predictions

        This wrapper delegates to all three and merges results for backward
        compatibility. It will be removed in a future version.

        Example: behavior_precheck(path="/my/project", planned_action="run pytest",
            known_constraints=["some tests may fail due to missing fixtures"])

        Args:
            path: Project root path.
            planned_action: Free text describing the planned action.
            known_constraints: Agent's self-reported constraints for this action.
            prediction: Optional free-text description of expected outcome.
            prediction_type: Type of prediction: "exit_code", "error_signature", or "stdout_contains".
            prediction_value: The expected value for the prediction.
        """
        import contextlib
        import json

        project_root = helpers["_validate_project_root"](path)

        # Telemetry: track deprecated usage
        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("behavior_precheck_deprecated", project_root)

        # ── Delegate to constraint_check (primary concern) ──
        constraint_result_raw = constraint_check(
            path=path,
            planned_action=planned_action,
            known_constraints=known_constraints,
        )
        output = json.loads(constraint_result_raw)

        # ── Delegate to prediction_register if prediction provided ──
        prediction_registered = False
        _valid_prediction_types = {"exit_code", "error_signature", "stdout_contains"}
        if prediction:
            # Validate that prediction metadata is complete
            _pred_errors = []
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
                pred_result_raw = prediction_register(
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

        # ── Delegate to hygiene_check ──
        hygiene_result_raw = hygiene_check(
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

        # ── Deprecation notice ──
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

        return helpers["_json_dumps"](output)

    # ── global_memory_status ──────────────────────────────────────────

    @mcp.tool()
    def global_memory_status(path: str) -> str:
        """Show cross-session behavioral analysis status.

        Returns session count, learned patterns, calibration settings,
        and computed bias adjustments from accumulated behavioral data.

        Args:
            path: Project root path.
        """
        from lintgate.config import load_controlplane_config

        project_root = os.path.abspath(path)
        cp_config = load_controlplane_config(project_root)
        if cp_config is None:
            return helpers["_json_dumps"]({"error": "ControlPlane not configured"})

        from lintgate.controlplane.global_behavior_profile import (
            GLOBAL_PROFILE_PATH,
            load_global_profile,
        )

        profile = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)

        # Compute nudge acceptance rates
        nudge_rates: dict[str, Any] = {}
        for signal, outcomes in profile.nudge_outcomes.items():
            total = outcomes.get("accepted", 0) + outcomes.get("ignored", 0)
            if total > 0:
                nudge_rates[signal] = {
                    "accepted": outcomes.get("accepted", 0),
                    "ignored": outcomes.get("ignored", 0),
                    "acceptance_rate": round(outcomes["accepted"] / total, 2),
                }

        # Normalize intent ratios
        total_intents = sum(profile.intent_ratios.values()) or 1
        normalized_intents = {
            k: round(v / total_intents, 3)
            for k, v in sorted(profile.intent_ratios.items(), key=lambda x: -x[1])
        }

        output: dict[str, Any] = {
            "scope": "project",
            "scope_note": "Cross-session memory for this project (not cross-project)",
            "project_root": project_root,
            "enabled": cp_config.global_memory_enabled,
            "profile_path": str(GLOBAL_PROFILE_PATH),
            "session_count": profile.session_count,
            "updated_at": profile.updated_at,
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

        return helpers["_json_dumps"](output)

    @mcp.tool()
    def global_memory_reset(path: str) -> str:
        """Reset the global behavior profile. Useful after major workflow changes.

        Args:
            path: Project root path.
        """
        from lintgate.controlplane.global_behavior_profile import (
            GLOBAL_PROFILE_PATH,
            GlobalBehaviorProfile,
            save_global_profile,
        )

        project_root = os.path.abspath(path)
        save_global_profile(GlobalBehaviorProfile())
        return helpers["_json_dumps"](
            {
                "scope": "project",
                "scope_note": "Cross-session memory for this project (not cross-project)",
                "project_root": project_root,
                "status": "reset",
                "profile_path": str(GLOBAL_PROFILE_PATH),
                "message": "Global behavior profile has been reset to empty state.",
            }
        )

    return {
        "hygiene_check": hygiene_check,
        "constraint_check": constraint_check,
        "prediction_register": prediction_register,
        "behavior_precheck": behavior_precheck,
        "global_memory_status": global_memory_status,
        "global_memory_reset": global_memory_reset,
    }
