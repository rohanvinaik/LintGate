"""Behavioral signal scoring, coordination, and theory grounding.

Support classes and utilities for the behavior channel's detection rules.
Contains the intent bias scorer, signal coordinator, theory grounding,
and error matching helpers.

Extracted from behavior_channel.py for module size compliance.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.controlplane.behavior_compass import (
        BehaviorCompass,
    )
    from lintgate.orchestration.attribution import SignalSourceDecomposition
    from lintgate.types import LintIssue

from lintgate.orchestration.authority import AuthorityEscalationEngine, AuthorityLevel

# ── Theory Grounding ─────────────────────────────────────────────────

# Maps each behavioral signal to the theory facets and keywords most
# relevant to it. Used by _ground_finding_in_theory to pull specific
# claims from the project's theory profile into hook messages.
SIGNAL_THEORY_MAP: dict[str, dict[str, Any]] = {
    "approach_cycling": {
        "facets": ["problem_solving", "alignment"],
        "keywords": ["approach", "strategy", "heuristic"],
    },
    "failure_amnesia": {
        "facets": ["alignment", "anti_patterns"],
        "keywords": ["learn", "error", "constraint"],
    },
    "premature_action": {
        "facets": ["problem_solving"],
        "keywords": ["verify", "understand", "plan"],
    },
    "brute_force_escalation": {
        "facets": ["problem_solving", "anti_patterns"],
        "keywords": ["escalate", "complexity", "decompose"],
    },
    "verification_debt": {
        "facets": ["alignment"],
        "keywords": ["verify", "validate", "correct"],
    },
    "stale_model": {
        "facets": ["core_theory", "alignment"],
        "keywords": ["update", "model", "understand"],
    },
    "serial_discovery": {
        "facets": ["problem_solving"],
        "keywords": ["predict", "proactive", "enumerate"],
    },
    "tool_repetition": {
        "facets": ["problem_solving", "anti_patterns"],
        "keywords": ["repetition", "stuck", "progress"],
    },
    "consecutive_failures": {
        "facets": ["problem_solving"],
        "keywords": ["failure", "constraint", "pause"],
    },
    "integration_verification_debt": {
        "facets": ["alignment", "anti_patterns"],
        "keywords": ["integration", "composition", "sheaf", "wiring"],
    },
}

_THEORY_CODA_MAX_CHARS = 150


def _ground_finding_in_theory(
    finding: LintIssue,
    signal_name: str,
    theory_profile: dict[str, Any] | None,
) -> tuple[str, float] | None:
    """Append a theory coda to a behavioral finding's message.

    Pulls 1-2 short claims from the project's theory profile that are
    relevant to the signal. Returns the coda text (for dedup tracking)
    or None if no grounding was applied.
    """
    if not theory_profile or signal_name not in SIGNAL_THEORY_MAP:
        return None

    from lintgate.theory_extractor import get_theory_context_from_profile

    mapping = SIGNAL_THEORY_MAP[signal_name]
    facets = mapping.get("facets", [])
    keywords = mapping.get("keywords", [])

    # Query each facet and collect best claims
    all_claims: list[dict[str, Any]] = []
    for facet in facets:
        result = get_theory_context_from_profile(
            theory_profile,
            facet=facet,
            keywords=keywords,
            max_claims=2,
        )
        all_claims.extend(result.get("claims", []))

    if not all_claims:
        return None

    # Deduplicate and sort by relevance
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in all_claims:
        if c["claim"] not in seen:
            seen.add(c["claim"])
            unique.append(c)
    unique.sort(key=lambda c: -c.get("relevance_score", 0))

    # Build coda: cap at ~150 chars total, 1-2 claims
    coda_parts: list[str] = []
    total_len = 0
    for claim in unique[:2]:
        text = claim["claim"]
        if len(text) > 80:
            text = text[:77] + "..."
        if total_len + len(text) > _THEORY_CODA_MAX_CHARS:
            break
        coda_parts.append(f"'{text}'")
        total_len += len(text)

    if not coda_parts:
        return None

    coda = f" Theory: {'; '.join(coda_parts)}."
    finding.message = finding.message.rstrip() + coda

    max_score = max(c.get("relevance_score", 0) for c in unique[:2])

    if not finding.evidence:
        finding.evidence = {}
    finding.evidence["theory_context"] = [c["claim"] for c in unique[:2]]

    return coda, float(max_score)


# ── Intent Bias Scorer ─────────────────────────────────────────────────

_BIAS_CAP = 0.25


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

        # Pre-computed global bias adjustments (signal_name → delta)
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


# ── Signal Coordinator ─────────────────────────────────────────────────


class SignalCoordinator:
    """Manages cooldown, precheck nudge dedup, and escalation."""

    _PRECHECK_PRIORITY: dict[str, int] = {
        "approach_cycling": 1,
        "failure_amnesia": 2,
        "brute_force_escalation": 3,
        "consecutive_failures": 4,
        "verification_debt": 5,
        "stale_model": 6,
        "serial_discovery_early": 7,
    }

    def __init__(
        self,
        compass: BehaviorCompass,
        thresholds: dict[str, Any],
        theory_profile: dict[str, Any] | None = None,
        recent_codas: dict[str, str] | None = None,
    ):
        self.compass = compass
        self.thresholds = thresholds
        self.findings: list[LintIssue] = []
        self.next_actions: list[dict[str, Any]] = []
        self._pending_precheck: dict[str, Any] | None = None
        self._pending_priority: int = 999
        self._nudge_signals: list[str] = []
        self.run_fire_counts: dict[str, int] = {}
        self.suppressed_nudge_count = 0
        self.authority_engine = AuthorityEscalationEngine()
        self._theory_profile = theory_profile
        self._recent_codas: dict[str, str] = recent_codas or {}
        self._new_codas: dict[str, str] = {}

    def can_fire(self, signal_name: str) -> bool:
        last = self.compass.last_fired.get(signal_name)
        if last is None:
            return True
        cooldown = self.thresholds.get("signal_cooldown", 10)
        return bool((self.compass.event_counter - last) >= cooldown)

    def record_firing(self, signal_name: str) -> None:
        self.compass.last_fired[signal_name] = self.compass.event_counter
        self.compass.signal_fire_counts[signal_name] = (
            self.compass.signal_fire_counts.get(signal_name, 0) + 1
        )
        self.run_fire_counts[signal_name] = self.run_fire_counts.get(signal_name, 0) + 1

    def _apply_theory_coda(self, signal_name: str, finding: LintIssue) -> float:
        """Apply theory grounding to a finding. Returns the theory score."""
        if self._theory_profile is None:
            return 0.0
        result = _ground_finding_in_theory(finding, signal_name, self._theory_profile)
        if result is None:
            return 0.0
        coda, theory_score = result
        if not coda:
            return theory_score
        prev_coda = self._recent_codas.get(signal_name)
        if prev_coda == coda:
            finding.message = finding.message[: -len(coda)]
            if finding.evidence:
                finding.evidence.pop("theory_context", None)
        else:
            self._new_codas[signal_name] = coda
        return theory_score

    def _apply_attribution(
        self,
        finding: LintIssue,
        signal_name: str,
        decomposition: SignalSourceDecomposition,
    ) -> None:
        """Apply decomposition attribution and theory grounding to a finding."""
        theory_score = self._apply_theory_coda(signal_name, finding)
        decomposition.theory_score = max(decomposition.theory_score, theory_score)
        finding.confidence = round(decomposition.total_confidence, 2)
        if not finding.evidence:
            finding.evidence = {}
        finding.evidence["attribution"] = {
            "pattern": decomposition.pattern_score,
            "theory": decomposition.theory_score,
            "outcome": decomposition.outcome_score,
            "coherence": decomposition.coherence_score,
        }
        summary = decomposition.to_summary()
        if summary:
            finding.message += f" ({summary})"

    _AUTHORITY_SEVERITY_MAP: dict[AuthorityLevel, str] = {
        AuthorityLevel.INTERVENTION: "blocking",
        AuthorityLevel.WARNING: "warning",
        AuthorityLevel.NUDGE: "warning",
    }

    def _apply_authority_severity(
        self,
        finding: LintIssue,
        signal_name: str,
        is_hard: bool,
        decomposition: SignalSourceDecomposition | None,
    ) -> None:
        """Calculate authority level and map to finding severity."""
        fire_count = self.compass.signal_fire_counts.get(signal_name, 0)
        significance = decomposition.total_confidence if decomposition else 0.5
        model_risk = "structural" if self._theory_profile else "moderate"
        compliance = self.compass.compliance_rate

        auth_level = self.authority_engine.calculate_authority(
            significance=significance,
            recurrence_count=fire_count,
            model_risk=model_risk,
            compliance_rate=compliance,
        )

        if not finding.evidence:
            finding.evidence = {}
        finding.evidence["authority"] = {
            "level": auth_level.value,
            "reason": self.authority_engine.get_escalation_reason(
                auth_level, significance, fire_count, compliance
            ),
        }

        finding.severity = self._AUTHORITY_SEVERITY_MAP.get(auth_level, "informational")
        if auth_level == AuthorityLevel.WARNING and is_hard:
            finding.message = f"[persistent] {finding.message}"

    def add_finding(
        self,
        signal_name: str,
        finding: LintIssue,
        is_hard: bool,
        precheck_nudge: dict[str, Any] | None = None,
        decomposition: SignalSourceDecomposition | None = None,
    ) -> None:
        if not self.can_fire(signal_name):
            self.suppressed_nudge_count += 1
            return
        self.record_firing(signal_name)

        if decomposition:
            self._apply_attribution(finding, signal_name, decomposition)
        else:
            self._apply_theory_coda(signal_name, finding)

        self._apply_authority_severity(finding, signal_name, is_hard, decomposition)
        self.findings.append(finding)

        if precheck_nudge:
            self._nudge_signals.append(signal_name)
            p = self._PRECHECK_PRIORITY.get(signal_name, 999)
            if p < self._pending_priority:
                self._pending_precheck = precheck_nudge
                self._pending_priority = p

    def register_nudge_only(self, signal_name: str, nudge: dict[str, Any]) -> None:
        if not self.can_fire(signal_name):
            return
        self.record_firing(signal_name)
        self._nudge_signals.append(signal_name)
        p = self._PRECHECK_PRIORITY.get(signal_name, 999)
        if p < self._pending_priority:
            self._pending_precheck = nudge
            self._pending_priority = p

    def finalize(self) -> tuple[list[LintIssue], list[dict[str, Any]], list[str], int]:
        if self._pending_precheck:
            self.next_actions.append(self._pending_precheck)
        return (
            self.findings,
            self.next_actions,
            self._nudge_signals,
            self.suppressed_nudge_count,
        )


# ── Error Matching Helpers ─────────────────────────────────────────────

_ERROR_EVIDENCE_PREFIXES = ("exit!=0 with:", "confirmed by:", "re-observed:")
_ERROR_STOPWORDS = {
    "error",
    "failed",
    "failure",
    "exit",
    "code",
    "status",
    "with",
    "from",
    "during",
    "while",
    "the",
    "and",
    "for",
    "this",
}


def _normalize_error_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _error_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) >= 3 and t not in _ERROR_STOPWORDS
    }


def _error_like_match(candidate: str, latest: str) -> bool:
    """Robust similarity check between stored evidence and latest error."""
    cand_norm = _normalize_error_text(candidate)
    latest_norm = _normalize_error_text(latest)
    if not cand_norm or not latest_norm:
        return False

    if cand_norm == latest_norm and len(cand_norm) >= 7:
        return True

    shorter, longer = (
        (cand_norm, latest_norm) if len(cand_norm) <= len(latest_norm) else (latest_norm, cand_norm)
    )
    if len(shorter) >= 12 and shorter in longer:
        return True

    overlap = _error_tokens(cand_norm) & _error_tokens(latest_norm)
    return len(overlap) >= 2


def _extract_hypothesis_error_candidates(evidence_for: list[str]) -> list[str]:
    """Extract likely error-signature strings from hypothesis evidence entries."""
    candidates: list[str] = []
    for ev in evidence_for:
        txt = ev.strip()
        lowered = txt.lower()
        for prefix in _ERROR_EVIDENCE_PREFIXES:
            if lowered.startswith(prefix):
                extracted = txt[len(prefix) :].strip()
                if extracted:
                    candidates.append(extracted)
                break
    return candidates
