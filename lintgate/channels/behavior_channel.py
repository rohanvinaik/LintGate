"""Behavior channel — supervision for agent behavioral drift.

Detects anti-patterns in tool-use sequences that indicate the agent's
problem-solving strategy is diverging from effective reasoning:

Hard signals (severity="warning", participate in coherence):
1. approach_cycling: Repeatedly trying failed approaches without updating model
2. failure_amnesia: Repeating the same error without incorporating prior lessons
3. brute_force_escalation: More approaches tried than constraints understood

Soft signals (severity="informational", coherence-neutral):
4. premature_action: Acting faster than understanding (high bash:read ratio)
5. serial_discovery: All constraints discovered reactively, none predicted
6. tool_repetition: Same command signature repeated excessively
7. verification_debt: Long execute/modify streak with no verify/inspect
8. stale_model: Approach changes without hypothesis model updates

v2 additions:
- Intent bias layer: 6-category intent taxonomy (inspect, modify, verify,
  execute, meta, unknown) biases signal confidence via deltas (not hard gating)
- Signal coordinator: per-signal cooldown, precheck nudge dedup, escalation
- Evidence traces: every finding carries intent_counts, bias terms, score_delta

This channel is ADVISORY and read-only for session state:
- Receives compass via event.raw_input["behavior_compass"]
- Returns compass delta in ChannelResult.metrics["behavior_compass_delta"]
- Hook applies delta after mesh completes (avoids race with parallel channels)
"""

from __future__ import annotations

import re
import time
from typing import Any

from lintgate.controlplane.behavior_compass import (
    DEFAULT_THRESHOLDS,
    BehaviorCompass,
    error_memory_key,
)
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import LintIssue

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
}

_THEORY_CODA_MAX_CHARS = 150


def _ground_finding_in_theory(
    finding: LintIssue,
    signal_name: str,
    theory_profile: dict[str, Any] | None,
) -> str | None:
    """Append a theory coda to a behavioral finding's message.

    Pulls 1-2 short claims from the project's theory profile that are
    relevant to the signal. Returns the coda text (for dedup tracking)
    or None if no grounding was applied.

    Args:
        finding: The LintIssue to augment (modified in place).
        signal_name: The signal name (key into SIGNAL_THEORY_MAP).
        theory_profile: Pre-extracted theory profile dict, or None.

    Returns:
        The coda text string, or None if no coda was added.
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
        # Truncate individual claim if too long
        if len(text) > 80:
            text = text[:77] + "..."
        if total_len + len(text) > _THEORY_CODA_MAX_CHARS:
            break
        coda_parts.append(f"'{text}'")
        total_len += len(text)

    if not coda_parts:
        return None

    coda = f" Theory: {'; '.join(coda_parts)}."

    # Append to finding message
    finding.message = finding.message.rstrip() + coda

    # Store in evidence
    if not finding.evidence:
        finding.evidence = {}
    finding.evidence["theory_context"] = [c["claim"] for c in unique[:2]]

    return coda


# ── Intent Bias Scorer ─────────────────────────────────────────────────

_BIAS_CAP = 0.25


class _IntentBiasScorer:
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
        """Merge project bias weight with global prior.

        effective = project_weight + alpha * global_adjustment
        Clamped to [0, _BIAS_CAP].
        """
        project = self.weights.get(config_key, default)
        global_adj = self._global_adjustments.get(signal_name, 0.0)
        effective = project + self._alpha * global_adj
        return max(0.0, min(_BIAS_CAP, effective))

    def verification_debt_bias(self) -> tuple[float, list[str]]:
        """+ bias when execute streak >= threshold and verify_count == 0."""
        terms: list[str] = []
        verify_count = self.recent_counts.get("verify", 0)
        inspect_count = self.recent_counts.get("inspect", 0)

        # Count consecutive execute/modify from end of intent_history
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

        # Walk backwards to find matching error
        for i in range(len(history) - 2, -1, -1):
            if history[i].get("err", "") == latest_err:
                # Check intents between match and latest
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


class _SignalCoordinator:
    """Manages cooldown, precheck nudge dedup, and escalation.

    - Per-signal cooldown: each signal can only fire once per N events
    - Precheck nudge dedup: only the highest-priority nudge per execution
    - Escalation: after N firings, soft signals promote to warning,
      hard signals get [persistent] tag
    """

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
        self._nudge_signals: list[str] = []  # Signals that produced nudges
        self.run_fire_counts: dict[str, int] = {}  # Per-execution signal firings
        # Architecture of Inquiry: theory grounding
        self._theory_profile = theory_profile
        self._recent_codas: dict[str, str] = recent_codas or {}
        self._new_codas: dict[str, str] = {}  # Codas generated this run

    def can_fire(self, signal_name: str) -> bool:
        """Check if a signal is past its cooldown."""
        last = self.compass.last_fired.get(signal_name)
        if last is None:
            return True
        cooldown = self.thresholds.get("signal_cooldown", 10)
        return (self.compass.event_counter - last) >= cooldown

    def record_firing(self, signal_name: str) -> None:
        """Record that a signal fired at the current event_counter."""
        self.compass.last_fired[signal_name] = self.compass.event_counter
        self.compass.signal_fire_counts[signal_name] = (
            self.compass.signal_fire_counts.get(signal_name, 0) + 1
        )
        self.run_fire_counts[signal_name] = self.run_fire_counts.get(signal_name, 0) + 1

    def add_finding(
        self,
        signal_name: str,
        finding: LintIssue,
        is_hard: bool,
        precheck_nudge: dict[str, Any] | None = None,
    ) -> None:
        """Add a finding with cooldown check, escalation, and nudge dedup."""
        if not self.can_fire(signal_name):
            return
        self.record_firing(signal_name)

        # Escalation: after N firings, promote severity
        fire_count = self.compass.signal_fire_counts.get(signal_name, 0)
        threshold = self.thresholds.get("escalation_threshold", 3)
        if fire_count >= threshold:
            if is_hard:
                finding.message = f"[persistent] {finding.message}"
            else:
                finding.severity = "warning"

        # Theory grounding: append relevant theory claims to finding message
        if self._theory_profile is not None:
            coda = _ground_finding_in_theory(finding, signal_name, self._theory_profile)
            if coda is not None:
                prev_coda = self._recent_codas.get(signal_name)
                if prev_coda == coda:
                    # Dedup: same coda as last run for this signal — strip it
                    finding.message = finding.message[: -len(coda)]
                    finding.evidence.pop("theory_context", None)
                else:
                    self._new_codas[signal_name] = coda

        self.findings.append(finding)

        # Precheck nudge dedup: highest priority wins
        if precheck_nudge:
            self._nudge_signals.append(signal_name)
            p = self._PRECHECK_PRIORITY.get(signal_name, 999)
            if p < self._pending_priority:
                self._pending_precheck = precheck_nudge
                self._pending_priority = p

    def register_nudge_only(self, signal_name: str, nudge: dict[str, Any]) -> None:
        """For trigger-only signals that produce no finding."""
        if not self.can_fire(signal_name):
            return
        self.record_firing(signal_name)
        self._nudge_signals.append(signal_name)
        p = self._PRECHECK_PRIORITY.get(signal_name, 999)
        if p < self._pending_priority:
            self._pending_precheck = nudge
            self._pending_priority = p

    def finalize(self) -> tuple[list[LintIssue], list[dict[str, Any]], list[str]]:
        """Return accumulated findings, at most one precheck nudge, and nudge signals."""
        if self._pending_precheck:
            self.next_actions.append(self._pending_precheck)
        return self.findings, self.next_actions, self._nudge_signals


_ERROR_EVIDENCE_PREFIXES = (
    "exit!=0 with:",
    "confirmed by:",
    "re-observed:",
)
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

    # Exact matches are strong unless the string is too short/generic.
    if cand_norm == latest_norm and len(cand_norm) >= 7:
        return True

    # Handle truncation: one normalized message may be a long prefix of the other.
    shorter, longer = (
        (cand_norm, latest_norm) if len(cand_norm) <= len(latest_norm) else (latest_norm, cand_norm)
    )
    if len(shorter) >= 12 and shorter in longer:
        return True

    # Fall back to token overlap for paraphrased but semantically similar signatures.
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


# ── BehaviorChannel ────────────────────────────────────────────────────


class BehaviorChannel:
    """Supervision channel for agent behavioral drift detection.

    Advisory only — behavioral findings are weather-style observations,
    not judgments. Hard signals trigger constraint_check nudges.
    """

    name = "behavior"
    timeout_ms = 500  # Lightweight — no subprocess calls
    blocking_capable = False  # Advisory only

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run when behavior compass data is available in the event."""
        if event.surface == "mcp":
            return True
        # Run when compass data has been injected by the hook
        return "behavior_compass" in event.raw_input

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute behavioral drift detection against compass state."""
        start = time.perf_counter()

        # Deserialize compass from event (read-only — no session I/O)
        compass_data = event.raw_input.get("behavior_compass", {})
        compass = BehaviorCompass.from_dict(compass_data)

        # Load thresholds from config, falling back to defaults.
        channel_config = config.channels.get("behavior", None)
        thresholds = dict(DEFAULT_THRESHOLDS)
        settings = getattr(channel_config, "settings", {}) if channel_config else {}
        if isinstance(settings, dict):
            nested = settings.get("thresholds", {})
            if isinstance(nested, dict):
                for key, value in nested.items():
                    if key in DEFAULT_THRESHOLDS:
                        thresholds[key] = value
            for key, value in settings.items():
                if key in DEFAULT_THRESHOLDS:
                    thresholds[key] = value
        # Allow threshold overrides via event.raw_input for MCP surface
        if "behavior_thresholds" in event.raw_input:
            thresholds.update(event.raw_input["behavior_thresholds"])

        # Load intent config from settings
        bias_weights = settings.get("bias_weights", {}) if isinstance(settings, dict) else {}

        # Load global behavior priors (if injected by hook/MCP)
        global_priors = event.raw_input.get("behavior_global_priors")

        # Architecture of Inquiry: load theory profile for grounding
        theory_profile = (
            event.raw_input.get("theory_profile")
            if config.inquiry.theory_grounded_signals
            else None
        )
        recent_codas = compass_data.get("_theory_recent_codas", {})

        # Create scorer and coordinator
        scorer = _IntentBiasScorer(compass, bias_weights, global_priors=global_priors)
        coord = _SignalCoordinator(
            compass,
            thresholds,
            theory_profile=theory_profile,
            recent_codas=recent_codas,
        )

        # Run all 9 detection rules
        _detect_approach_cycling(compass, thresholds, coord, scorer)
        _detect_failure_amnesia(compass, thresholds, coord, scorer)
        _detect_brute_force_escalation(compass, thresholds, coord, scorer)
        _detect_premature_action(compass, thresholds, coord, scorer)
        _detect_serial_discovery(compass, thresholds, coord, scorer)
        _detect_tool_repetition(compass, thresholds, coord, scorer)
        _detect_consecutive_failures(compass, thresholds, coord, scorer)
        _detect_verification_debt(compass, thresholds, coord, scorer)
        _detect_stale_model(compass, thresholds, coord, scorer)

        # Prediction accuracy modulation (only with ≥5 checked predictions)
        if config.inquiry.prediction_tracking:
            from lintgate.controlplane.behavior_compass import compute_prediction_accuracy

            pred_accuracy = compute_prediction_accuracy(compass)
            if pred_accuracy is not None:
                for finding in coord.findings:
                    if pred_accuracy > 0.70 and finding.severity == "informational":
                        # High accuracy → agent predicts well, soften soft signals
                        if finding.confidence is not None:
                            finding.confidence = round(max(0.0, finding.confidence - 0.15), 2)
                    elif pred_accuracy < 0.30 and finding.confidence is not None:
                        # Low accuracy → agent predicts poorly, amplify signals
                        finding.confidence = round(min(1.0, finding.confidence + 0.15), 2)

        findings, next_actions, nudge_signals = coord.finalize()

        # Compute nudge outcomes for global profile:
        # If previous nudge signals exist and a precheck happened since, → "accepted"
        # If previous nudge signals exist and no precheck → "ignored"
        nudge_outcomes: dict[str, str] = {}
        if compass.pending_nudge_signals:
            precheck_delta = compass.constraint_check_count_session - compass.pending_nudge_constraint_check_count
            outcome = "accepted" if precheck_delta > 0 else "ignored"
            for sig in compass.pending_nudge_signals:
                nudge_outcomes[sig] = outcome
            compass.nudge_outcomes.update(nudge_outcomes)

        # Update pending nudge signals for the next cycle
        compass.pending_nudge_signals = list(nudge_signals)
        compass.pending_nudge_constraint_check_count = compass.constraint_check_count_session

        # Global profile intent delta is per-run, not rolling-window cumulative.
        intent_delta: dict[str, int] = {}
        if event.surface == "hook" and compass.action_history:
            latest_intent = compass.action_history[-1].get("intent")
            if isinstance(latest_intent, str) and latest_intent:
                intent_delta[latest_intent] = 1

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = "fail" if findings else "pass"

        # Severity: highest among findings, but soft signals alone → informational
        has_hard = any(f.severity == "warning" for f in findings)
        if has_hard:
            severity = "warning"
        elif findings:
            severity = "informational"
        else:
            severity = "none"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=[],  # Behavior channel doesn't propose code repairs
            metrics={
                "alert_count": len(findings),
                "hard_alerts": sum(1 for f in findings if f.severity == "warning"),
                "soft_alerts": sum(1 for f in findings if f.severity == "informational"),
                "next_actions": next_actions,
                "compass_summary": {
                    "hypotheses_active": sum(
                        1 for h in compass.hypotheses if h.status in ("active", "confirmed")
                    ),
                    "approaches_total": len(compass.approaches),
                    "prediction_recall": compass.coverage.prediction_recall,
                },
                "behavior_compass_delta": {
                    "last_fired": compass.last_fired,
                    "signal_fire_counts": compass.signal_fire_counts,
                    "early_nudge_emitted": compass.early_nudge_emitted,
                    "pending_nudge_signals": compass.pending_nudge_signals,
                    "pending_nudge_constraint_check_count": compass.pending_nudge_constraint_check_count,
                    "nudge_outcomes": compass.nudge_outcomes,
                    "_theory_recent_codas": coord._new_codas,
                },
                "intent_summary": scorer.build_evidence_trace(),
                "global_profile_delta": {
                    "signal_fire_counts": dict(coord.run_fire_counts),
                    "intent_summary": intent_delta,
                    "nudge_outcomes": nudge_outcomes,
                },
            },
            duration_ms=elapsed_ms,
        )


# ── Detection Rules ─────────────────────────────────────────────────────
# Each rule checks compass state against thresholds and uses coordinator.
# Hard signals: severity="warning". Soft signals: severity="informational".


def _detect_approach_cycling(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect repeated failed approaches within a time window.

    Hard signal: 3+ failed approaches in 30 minutes.
    """
    count_threshold = thresholds.get("approach_cycling_count", 3)
    window_min = thresholds.get("approach_cycling_window_min", 30)

    if not compass.approaches:
        return

    now = compass.action_history[-1]["ts"] if compass.action_history else time.time()
    cutoff = now - (window_min * 60)

    recent_failed = [
        a for a in compass.approaches if a.outcome == "failed" and a.last_event >= cutoff
    ]

    if len(recent_failed) >= count_threshold:
        window_actual = int((now - min(a.started_at for a in recent_failed)) / 60)
        sigs = ", ".join(a.approach_sig for a in recent_failed[:4])

        evidence = scorer.build_evidence_trace()
        coord.add_finding(
            "approach_cycling",
            LintIssue(
                linter="behavior_channel",
                kind="approach_cycling",
                message=(
                    f"{len(recent_failed)} approaches attempted in {window_actual}min, "
                    f"all failed. Constraint space may be wider than current model. "
                    f"Approaches: {sigs}"
                ),
                severity="warning",
                evidence=evidence,
            ),
            is_hard=True,
            precheck_nudge={
                "tool": "constraint_check",
                "reason": "approach_cycling detected — enumerate constraints before next attempt",
            },
        )


def _detect_failure_amnesia(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect repeated error signatures — multi-source.

    Hard signal. Source 1: action_history repeated error_sig.
    Source 2: session-level error_memory aggregate (cross-window).
    Source 3: hypothesis evidence_for matching latest error.
    """
    lookback = thresholds.get("failure_amnesia_lookback", 30)
    recent = compass.action_history[-lookback:]

    bias, bias_terms = scorer.failure_amnesia_bias()
    evidence = scorer.build_evidence_trace()
    evidence["matched_bias_terms"] = bias_terms
    evidence["score_delta"] = bias

    # Source 1: action_history repeated error_sig
    error_counts: dict[str, list[dict[str, Any]]] = {}
    for event in recent:
        err = event.get("err", "")
        if err:
            error_counts.setdefault(err, []).append(event)

    for err_sig, events in error_counts.items():
        if len(events) >= 2:
            first_ts = events[0].get("ts", 0)
            last_ts = events[-1].get("ts", 0)
            gap_min = int((last_ts - first_ts) / 60) if last_ts > first_ts else 0

            evidence["source"] = "action_history"
            coord.add_finding(
                "failure_amnesia",
                LintIssue(
                    linter="behavior_channel",
                    kind="failure_amnesia",
                    message=(
                        f"Error signature '{err_sig[:80]}' seen {len(events)} times "
                        f"in action history (first {gap_min}min ago). "
                        "Known constraint may not be incorporated into approach."
                    ),
                    severity="warning",
                    evidence=evidence,
                ),
                is_hard=True,
                precheck_nudge={
                    "tool": "constraint_check",
                    "reason": f"failure_amnesia: '{err_sig[:60]}' repeated — check constraint ledger",
                },
            )
            return  # Report most significant amnesia only

    latest_err = recent[-1].get("err", "") if recent else ""
    if not latest_err:
        return

    # Source 2: persistent error-memory aggregate (covers beyond action_history window)
    key = error_memory_key(latest_err)
    if key:
        mem = compass.error_memory.get(key)
        if mem and int(mem.get("count", 0)) >= 2:
            first_ts = float(mem.get("first_seen", 0.0))
            last_ts = float(mem.get("last_seen", 0.0))
            gap_min = int((last_ts - first_ts) / 60) if last_ts > first_ts else 0
            seen = int(mem.get("count", 0))
            evidence["source"] = "error_memory"
            coord.add_finding(
                "failure_amnesia",
                LintIssue(
                    linter="behavior_channel",
                    kind="failure_amnesia",
                    message=(
                        f"Error signature '{latest_err[:80]}' seen {seen} times "
                        f"across session memory (first {gap_min}min ago). "
                        "Known constraint may not be incorporated into approach."
                    ),
                    severity="warning",
                    evidence=evidence,
                ),
                is_hard=True,
                precheck_nudge={
                    "tool": "constraint_check",
                    "reason": f"failure_amnesia: '{latest_err[:60]}' repeated across session",
                },
            )
            return

    # Source 3: hypothesis evidence matching latest error
    for hyp in compass.hypotheses:
        if hyp.status in ("expired",):
            continue
        for candidate_err in _extract_hypothesis_error_candidates(hyp.evidence_for):
            if _error_like_match(candidate_err, latest_err):
                evidence["source"] = "hypothesis_evidence"
                coord.add_finding(
                    "failure_amnesia",
                    LintIssue(
                        linter="behavior_channel",
                        kind="failure_amnesia",
                        message=(
                            f"Latest error matches existing hypothesis "
                            f"'{hyp.claim[:60]}' (confidence {hyp.confidence:.2f}). "
                            "Known constraint may not be incorporated."
                        ),
                        severity="warning",
                        evidence=evidence,
                    ),
                    is_hard=True,
                    precheck_nudge={
                        "tool": "constraint_check",
                        "reason": f"failure_amnesia: error matches hypothesis '{hyp.id}'",
                    },
                )
                return


def _detect_brute_force_escalation(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect more approaches than constraints understood.

    Hard signal: approaches_attempted > constraints_verified + gap.
    """
    gap_threshold = thresholds.get("brute_force_approach_gap", 0)

    approaches = compass.coverage.approaches_attempted
    constraints = compass.coverage.constraints_verified
    gap = approaches - constraints

    if approaches > 0 and gap > gap_threshold:
        evidence = scorer.build_evidence_trace()
        coord.add_finding(
            "brute_force_escalation",
            LintIssue(
                linter="behavior_channel",
                kind="brute_force_escalation",
                message=(
                    f"{approaches} approaches tried, only {constraints} constraints verified. "
                    f"Approach-to-constraint gap: {gap}. "
                    "Strategy may be brute-forcing rather than understanding."
                ),
                severity="warning",
                evidence=evidence,
            ),
            is_hard=True,
            precheck_nudge={
                "tool": "constraint_check",
                "reason": "brute_force_escalation — approaches outpacing constraint understanding",
            },
        )


def _detect_premature_action(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect high action-to-research ratio with high failure rate.

    Soft signal: bash:read ratio > 3:1 AND failure rate > 50%.
    Triggers precheck nudge only at extreme ratio (> 5:1).
    """
    ratio_threshold = thresholds.get("premature_action_ratio", 3.0)
    failure_threshold = thresholds.get("premature_action_failure_rate", 0.5)

    bash_count = compass.coverage.bash_count_recent
    read_count = compass.coverage.read_count_recent

    if bash_count == 0:
        return

    ratio = bash_count / max(read_count, 1)

    # Compute recent failure rate from action history
    recent = compass.action_history[-10:]
    bash_events = [e for e in recent if e.get("tool") == "Bash"]
    if not bash_events:
        return

    failures = sum(1 for e in bash_events if (e.get("exit") or 0) != 0)
    failure_rate = failures / len(bash_events)

    if ratio > ratio_threshold and failure_rate > failure_threshold:
        evidence = scorer.build_evidence_trace()
        nudge = None
        if ratio > 5.0:
            nudge = {
                "tool": "constraint_check",
                "reason": f"extreme premature_action: {ratio:.1f}:1 bash:read ratio",
            }

        coord.add_finding(
            "premature_action",
            LintIssue(
                linter="behavior_channel",
                kind="premature_action",
                message=(
                    f"{bash_count} of last {len(recent)} actions were Bash commands "
                    f"({failures} failed, {failure_rate:.0%} failure rate). "
                    "Research-to-action ratio suggests acting ahead of understanding."
                ),
                severity="informational",
                evidence=evidence,
            ),
            is_hard=False,
            precheck_nudge=nudge,
        )


def _detect_serial_discovery(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect constraints discovered reactively — two-stage.

    Stage 1 (early nudge): 1+ failure-sourced hypothesis + constraint_check_count=0
    Stage 2 (existing): 3+ failure-sourced hypotheses, 0 from precheck
    """
    active_hyps = [h for h in compass.hypotheses if h.status in ("active", "confirmed")]

    failure_sourced = sum(1 for h in active_hyps if h.source == "command_failure")
    precheck_sourced = sum(1 for h in active_hyps if h.source == "precheck_declared")

    # Stage 1: Early nudge (one-time)
    if (
        failure_sourced >= 1
        and compass.constraint_check_count_session == 0
        and not compass.early_nudge_emitted
    ):
        bias, bias_terms = scorer.serial_discovery_bias()
        evidence = scorer.build_evidence_trace()
        evidence["matched_bias_terms"] = bias_terms
        evidence["score_delta"] = bias
        evidence["stage"] = 1

        coord.add_finding(
            "serial_discovery_early",
            LintIssue(
                linter="behavior_channel",
                kind="serial_discovery",
                message=(
                    f"{failure_sourced} constraint(s) discovered through failure, "
                    "0 predicted via constraint_check. Consider proactive constraint enumeration "
                    "with constraint_check."
                ),
                severity="informational",
                confidence=round(min(1.0, max(0.0, bias)), 2),
                evidence=evidence,
            ),
            is_hard=False,
            precheck_nudge={
                "tool": "constraint_check",
                "reason": "serial_discovery_early — first failure-sourced constraint, no constraint_check used",
            },
        )
        compass.early_nudge_emitted = True

    # Stage 2: Existing logic (3+ failure-sourced, 0 precheck)
    if failure_sourced >= 3 and precheck_sourced == 0:
        evidence = scorer.build_evidence_trace()
        evidence["stage"] = 2
        coord.add_finding(
            "serial_discovery",
            LintIssue(
                linter="behavior_channel",
                kind="serial_discovery",
                message=(
                    f"{failure_sourced} constraints discovered through failure, "
                    "0 predicted via constraint_check. All learning is reactive — "
                    "consider proactive constraint enumeration."
                ),
                severity="informational",
                evidence=evidence,
            ),
            is_hard=False,
        )


def _detect_tool_repetition(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect same command signature repeated excessively.

    Soft signal: 4+ of the same sig in 30 minutes.
    """
    count_threshold = thresholds.get("tool_repetition_count", 4)
    window_min = thresholds.get("tool_repetition_window_min", 30)

    if not compass.action_history:
        return

    now = compass.action_history[-1]["ts"]
    cutoff = now - (window_min * 60)

    # Count sigs in window
    sig_counts: dict[str, int] = {}
    for event in compass.action_history:
        if event.get("ts", 0) < cutoff:
            continue
        sig = event.get("sig", "")
        if sig:
            sig_counts[sig] = sig_counts.get(sig, 0) + 1

    for sig, count in sig_counts.items():
        if count >= count_threshold:
            evidence = scorer.build_evidence_trace()
            coord.add_finding(
                "tool_repetition",
                LintIssue(
                    linter="behavior_channel",
                    kind="tool_repetition",
                    message=(
                        f"Command '{sig}' executed {count} times in "
                        f"{window_min}min window. Repeated tool use without "
                        "progress may indicate stuck approach."
                    ),
                    severity="informational",
                    evidence=evidence,
                ),
                is_hard=False,
            )
            break  # Report most frequent only


def _detect_consecutive_failures(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect consecutive Bash failures as precheck trigger.

    Not a standalone finding — just triggers a precheck nudge.
    """
    threshold = thresholds.get("consecutive_bash_failures", 3)

    if not compass.action_history:
        return

    # Count consecutive failures from end of history
    consecutive = 0
    for event in reversed(compass.action_history):
        if event.get("tool") != "Bash":
            continue
        if (event.get("exit") or 0) != 0:
            consecutive += 1
        else:
            break

    if consecutive >= threshold:
        coord.register_nudge_only(
            "consecutive_failures",
            {
                "tool": "constraint_check",
                "reason": f"{consecutive} consecutive Bash failures — pause and check constraints",
            },
        )


def _detect_verification_debt(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect long execute/modify streak with no verify/inspect intent.

    Soft signal: 8+ consecutive execute/modify intents.
    Uses intent bias to compute confidence.
    """
    bias, terms = scorer.verification_debt_bias()

    # Count consecutive execute/modify from end of intent_history
    streak = 0
    for intent in reversed(compass.intent_history):
        if intent in ("execute", "modify"):
            streak += 1
        else:
            break

    threshold = thresholds.get("verification_debt_streak", 8)
    if streak < threshold:
        return

    score = min(1.0, max(0.0, bias))
    evidence = scorer.build_evidence_trace()
    evidence["matched_bias_terms"] = terms
    evidence["score_delta"] = bias
    evidence["execute_streak"] = streak

    coord.add_finding(
        "verification_debt",
        LintIssue(
            linter="behavior_channel",
            kind="verification_debt",
            message=(
                f"{streak} execute/modify actions without verification checkpoint. "
                "Consider verifying downstream acceptance."
            ),
            severity="informational",
            confidence=round(score, 2),
            evidence=evidence,
        ),
        is_hard=False,
        precheck_nudge={
            "tool": "constraint_check",
            "reason": f"verification_debt: {streak} actions without verification",
        },
    )


def _detect_stale_model(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
) -> None:
    """Detect approach changes without hypothesis model updates.

    Soft signal: 2+ approaches created at the same hypothesis_version.
    """
    bias, terms = scorer.stale_model_bias()
    if not terms:  # bias function already checks threshold
        return

    # Re-extract max_streak for message
    sorted_approaches = sorted(compass.approaches, key=lambda a: a.started_at)
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

    evidence = scorer.build_evidence_trace()
    evidence["matched_bias_terms"] = terms
    evidence["score_delta"] = bias
    evidence["approach_streak_at_same_version"] = max_streak

    coord.add_finding(
        "stale_model",
        LintIssue(
            linter="behavior_channel",
            kind="stale_model",
            message=(
                f"{max_streak} approach changes without constraint model updates. "
                "Hypothesis set unchanged. Consider using constraint_check."
            ),
            severity="informational",
            confidence=round(min(1.0, max(0.0, bias)), 2),
            evidence=evidence,
        ),
        is_hard=False,
    )
