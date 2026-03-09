"""Intent bias scorer for the behavior channel.

Computes intent-based bias terms for detection rules using the agent's
intent history and optional global prior adjustments.

Extracted from behavior_scoring.py for module size compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.controlplane.behavior_compass import (
        BehaviorCompass,
    )

# ── Constants ────────────────────────────────────────────────────────────

_BIAS_CAP = 0.25


# ── IntentBiasScorer ─────────────────────────────────────────────────────


class IntentBiasScorer:
    """Compute intent-based bias terms for detection rules.

    Each method returns (bias_delta, matched_terms: list[str]).
    bias_delta is clamped to [-_BIAS_CAP, +_BIAS_CAP].
    unknown intent always contributes zero bias.
    """

    def __init__(
        self,
        compass: BehaviorCompass,
        bias_weights: dict[str, Any],
        global_priors: dict[str, Any] | None = None,
    ):
        self.compass = compass
        self.weights = bias_weights
        self.global_priors = global_priors or {}

        # Compute effective alpha from event_counter
        self._alpha = 0.0
        if self.global_priors.get("enabled"):
            from lintgate.controlplane.global_behavior_profile import compute_alpha

            self._alpha = compute_alpha(
                compass.event_counter,
                alpha_initial=self.global_priors.get("alpha", 0.6),
                decay_horizon=self.global_priors.get("decay_horizon", 50),
            )

        # Pre-computed global bias adjustments (signal_name -> delta)
        self._global_adjustments: dict[str, float] = self.global_priors.get(
            "computed_bias_adjustments", {}
        )

        # Pre-compute intent counts for the recent window (last 10)
        recent = compass.intent_history[-10:]
        self.recent_counts: dict[str, int] = {}
        for intent in recent:
            self.recent_counts[intent] = self.recent_counts.get(intent, 0) + 1
        self.recent_window = len(recent)

    def _effective_bias_weight(self, signal_name: str, config_key: str, default: float) -> float:
        """Merge project bias weight with global prior."""
        project = self.weights.get(config_key, default)
        global_adj = self._global_adjustments.get(signal_name, 0.0)
        effective = project + self._alpha * global_adj
        return float(max(0.0, min(_BIAS_CAP, effective)))

    def verification_debt_bias(self) -> tuple[float, list[str]]:
        """+ bias when execute streak >= threshold and verify_count == 0."""
        terms: list[str] = []
        verify_count = self.recent_counts.get("verify", 0)
        inspect_count = self.recent_counts.get("inspect", 0)

        streak = 0
        for intent in reversed(self.compass.intent_history):
            if intent in ("execute", "modify"):
                streak += 1
            else:
                break

        threshold = self.weights.get("verification_debt_streak", 8)
        if streak >= threshold and verify_count == 0 and inspect_count == 0:
            delta = self._effective_bias_weight("verification_debt", "verification_debt_bias", 0.20)
            terms.append(f"execute_streak={streak},verify=0,inspect=0")
            return (min(delta, _BIAS_CAP), terms)
        return (0.0, terms)

    def failure_amnesia_bias(self) -> tuple[float, list[str]]:
        """+ bias when repeated error with no verify/inspect between repeats."""
        terms: list[str] = []
        history = self.compass.action_history[-30:]
        if len(history) < 2:
            return (0.0, terms)

        latest = history[-1]
        latest_err = latest.get("err", "")
        if not latest_err:
            return (0.0, terms)

        for i in range(len(history) - 2, -1, -1):
            if history[i].get("err", "") == latest_err:
                between_count = len(history) - i - 1
                between = self.compass.intent_history[-between_count:] if between_count > 0 else []
                has_verify = any(intent in ("verify", "inspect") for intent in between)
                if not has_verify:
                    delta = self._effective_bias_weight(
                        "failure_amnesia", "failure_amnesia_bias", 0.15
                    )
                    terms.append("repeated_error,no_verify_between")
                    return (min(delta, _BIAS_CAP), terms)
                break
        return (0.0, terms)

    def serial_discovery_bias(self) -> tuple[float, list[str]]:
        """Soft nudge on first failure-sourced hypothesis + constraint_check_count == 0."""
        terms: list[str] = []
        if self.compass.constraint_check_count_session == 0:
            failure_hyps = [
                h
                for h in self.compass.hypotheses
                if h.source == "command_failure" and h.status in ("active", "confirmed")
            ]
            if len(failure_hyps) >= 1:
                delta = self._effective_bias_weight(
                    "serial_discovery", "serial_discovery_early_bias", 0.10
                )
                terms.append(f"failure_hyps={len(failure_hyps)},precheck=0")
                return (min(delta, _BIAS_CAP), terms)
        return (0.0, terms)

    def stale_model_bias(self) -> tuple[float, list[str]]:
        """Soft signal when approach_changes >= 2 and hyp_version unchanged."""
        terms: list[str] = []
        if len(self.compass.approaches) < 2:
            return (0.0, terms)

        sorted_approaches = sorted(self.compass.approaches, key=lambda a: a.started_at)
        max_streak = 1
        current = 1
        last_v = sorted_approaches[0].hyp_version_at_start
        for a in sorted_approaches[1:]:
            if a.hyp_version_at_start == last_v:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 1
                last_v = a.hyp_version_at_start

        threshold = self.weights.get("stale_model_approach_changes", 2)
        if max_streak >= threshold:
            delta = self._effective_bias_weight("stale_model", "stale_model_bias", 0.15)
            terms.append(f"approach_streak={max_streak},hyp_version_unchanged")
            return (min(delta, _BIAS_CAP), terms)
        return (0.0, terms)

    def build_evidence_trace(self) -> dict[str, Any]:
        """Build structured evidence trace for findings."""
        trace: dict[str, Any] = {
            "window": self.recent_window,
            "intent_counts": dict(self.recent_counts),
        }
        if self._alpha > 0:
            trace["global_alpha"] = round(self._alpha, 3)
            trace["global_adjustments_applied"] = {
                k: round(v, 3) for k, v in self._global_adjustments.items() if v != 0
            }
        return trace
