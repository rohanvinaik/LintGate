"""Behavior tools — behavior_precheck, global_memory_status, global_memory_reset."""

from __future__ import annotations

import os
from typing import Any


def register(mcp, helpers):
    """Register behavior tools on the shared MCP instance."""

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

        WHEN TO USE: Before running Bash commands or making significant changes.
        State what you plan to do and what constraints you know about — this tool
        identifies coverage gaps, uncertainty zones, and similar past failures.

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
        from lintgate.config import load_controlplane_config
        from lintgate.controlplane.behavior_compass import (
            Prediction,
            PredictionExpectation,
            add_declared_hypothesis,
            compute_coverage,
            compute_prediction_accuracy,
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

        # Telemetry: track behavior_precheck usage
        import contextlib

        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("behavior_precheck", project_root)

        declared = known_constraints or []

        # Load config and session
        cp_config = load_controlplane_config(project_root)
        max_age = cp_config.session_max_age_hours if cp_config else 4.0
        session = get_or_create_session(project_root, max_age)
        compass = load_behavior_compass(session)

        # v2: Track precheck invocation
        compass.precheck_count_session += 1

        # Extract command sig from planned_action (best-effort)
        command_sig = normalize_command_sig(planned_action)

        # Find relevant hypotheses
        relevant = find_relevant_hypotheses(compass, command_sig, tool="Bash")

        # If no scoped hypotheses found, fall back to all active
        if not relevant:
            relevant = find_relevant_hypotheses(compass)

        # Agent-declared constraints: add as hypotheses if new
        for claim in declared:
            add_declared_hypothesis(compass, claim, command_sig)

        # Register prediction if provided and action involves Bash/execute
        _is_bash_action = any(
            kw in planned_action.lower()
            for kw in ("bash", "execute", "run", "command", "shell", "npm", "pip", "git", "make")
        )
        prediction_registered = False
        _valid_prediction_types = {"exit_code", "error_signature", "stdout_contains"}
        if (
            prediction
            and prediction_type
            and prediction_value is not None
            and _is_bash_action
            and prediction_type in _valid_prediction_types
            and command_sig
            and command_sig != "unknown:unknown"
        ):
            import uuid

            exp = PredictionExpectation(
                type=prediction_type,
                value=prediction_value,
            )
            # Link to most relevant hypothesis if available
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
            prediction_registered = True

        # Compute coverage gap
        matched_relevant_ids: set[str] = set()
        for claim in declared:
            # Check if any relevant hypothesis matches (keyword overlap)
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

        # Find similar past failures (one-liner per failure for compact output)
        similar_failures = []
        for a in compass.approaches:
            if a.outcome == "failed":
                # Check if approach sig overlaps with planned action
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

        # Save compass updates (new declared hypotheses)
        save_behavior_compass(session, compass)
        save_session(session)

        # Build output
        output: dict[str, Any] = {
            "constraint_ledger": [
                {"claim": h.claim[:100], "confidence": round(h.confidence, 2), "source": h.source}
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

        # First-session guidance when precheck_count_session was just incremented to 1
        if compass.precheck_count_session == 1:
            output["first_session_hint"] = (
                "First precheck this session — predictions and constraint tracking "
                "improve as you use behavior_precheck before taking actions. "
                "State your known constraints and register predictions for best results."
            )
            _bp_onboarding = helpers["_build_onboarding_status"](project_root)
            if _bp_onboarding.get("config_state") != "config_enabled":
                output["onboarding"] = _bp_onboarding

        # Prediction tracking section
        pred_accuracy = compute_prediction_accuracy(compass)
        checked_count = len(
            [e for e in compass.prediction_log if e.get("status") in ("confirmed", "falsified")]
        )
        prediction_section: dict[str, Any] = {
            "pending_count": len(compass.pending_predictions),
            "checked_count": checked_count,
            "prediction_registered": prediction_registered,
        }
        if pred_accuracy is not None:
            prediction_section["accuracy"] = round(pred_accuracy, 2)
        else:
            prediction_section["accuracy"] = None
            if checked_count > 0:
                prediction_section["accuracy_note"] = (
                    f"Need {5 - checked_count} more checked predictions for accuracy"
                )
        # Recent prediction outcomes (last 5)
        recent_outcomes = compass.prediction_log[-5:] if compass.prediction_log else []
        if recent_outcomes:
            prediction_section["recent_outcomes"] = [
                {"id": o.get("prediction_id", "?"), "status": o.get("status", "?")}
                for o in recent_outcomes
            ]
        output["prediction_tracking"] = prediction_section

        next_actions = []
        if coverage_gap > 0 or recall < 0.5:
            next_actions.append(
                {
                    "tool": "behavior_precheck",
                    "reason": "Re-run after researching uncertainty zones",
                    "priority": 1,
                }
            )

        if next_actions:
            output["next_actions"] = next_actions

        return helpers["_json_dumps"](output)

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

        save_global_profile(GlobalBehaviorProfile())
        return helpers["_json_dumps"](
            {
                "status": "reset",
                "profile_path": str(GLOBAL_PROFILE_PATH),
                "message": "Global behavior profile has been reset to empty state.",
            }
        )

    return {
        "behavior_precheck": behavior_precheck,
        "global_memory_status": global_memory_status,
        "global_memory_reset": global_memory_reset,
    }
