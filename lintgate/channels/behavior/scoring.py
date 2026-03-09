"""Behavioral signal scoring, coordination, and theory grounding.

Support classes and utilities for the behavior channel's detection rules.
Contains the signal coordinator, theory grounding, and re-exports the
intent bias scorer and error matching helpers from sub-modules.

Extracted from behavior_channel.py for module size compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.controlplane.behavior_types import (
        BehaviorCompass,
    )
    from lintgate.orchestration.attribution import SignalSourceDecomposition
    from lintgate.types import LintIssue

from lintgate.orchestration.authority import AuthorityEscalationEngine, AuthorityLevel

# ── Re-exports from sub-modules ──────────────────────────────────────────
# All public names remain importable from scoring for backward compat.
from ._error_utils import (  # noqa: F401
    _ERROR_EVIDENCE_PREFIXES,
    _ERROR_STOPWORDS,
    _error_like_match,
    _error_tokens,
    _extract_hypothesis_error_candidates,
    _normalize_error_text,
)
from ._intent_scorer import (  # noqa: F401
    _BIAS_CAP,
    IntentBiasScorer,
)

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
