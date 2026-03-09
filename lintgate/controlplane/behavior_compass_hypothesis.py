"""Hypothesis management for the behavioral compass.

Handles hypothesis matching, testing against outcomes, confidence
strengthening/weakening, decay, eviction, coverage computation,
uncertainty zone detection, and precheck support.

Extracted from behavior_compass.py for module size compliance.
"""

from __future__ import annotations

import time
from typing import Any

from lintgate.orchestration.attribution import SignalSourceDecomposition

from .behavior_types import (
    DEFAULT_HYPOTHESIS_CONFIG,
    MAX_EVIDENCE_ITEMS,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    make_hypothesis_id,
)

# Private alias for internal use
_make_hypothesis_id = make_hypothesis_id


def _hypothesis_matches_sig(hyp: BehaviorHypothesis, command_sig: str, binary: str) -> bool:
    """Check if a hypothesis applies to a given command signature."""
    for sig_pattern in hyp.applies_to_sigs:
        if sig_pattern == command_sig:
            return True
        if sig_pattern.endswith(":*") and sig_pattern[:-2] == binary:
            return True
    return False


def _test_hypotheses(
    compass: BehaviorCompass,
    command_sig: str,
    exit_code: int | None,
    error_sig: str,
    now: float,
    cfg: dict[str, Any],
) -> None:
    """Test active hypotheses against an observed outcome."""
    binary = command_sig.split(":")[0] if ":" in command_sig else command_sig

    for hyp in compass.hypotheses:
        if hyp.status == "expired":
            continue

        if not _hypothesis_matches_sig(hyp, command_sig, binary):
            continue

        hyp.last_tested = now

        if exit_code is not None and exit_code != 0 and error_sig:
            _strengthen_hypothesis(hyp, f"Error confirmed: {error_sig[:60]}", cfg)
        elif exit_code == 0:
            _weaken_hypothesis(
                hyp, f"Success on {command_sig} at event {compass.event_counter}", cfg
            )


def _strengthen_hypothesis(
    hyp: BehaviorHypothesis,
    evidence: str,
    cfg: dict[str, Any],
) -> None:
    """Apply strengthening evidence to a hypothesis."""
    hyp.confidence = min(1.0, hyp.confidence + cfg["strengthen_delta"])
    hyp.evidence_for.append(evidence)
    if len(hyp.evidence_for) > MAX_EVIDENCE_ITEMS:
        hyp.evidence_for = hyp.evidence_for[-MAX_EVIDENCE_ITEMS:]
    if (
        hyp.confidence >= cfg["promote_threshold"]
        and len(hyp.evidence_for) >= cfg["min_evidence_for_promote"]
    ):
        hyp.status = "confirmed"


def _weaken_hypothesis(
    hyp: BehaviorHypothesis,
    evidence: str,
    cfg: dict[str, Any],
) -> None:
    """Apply weakening evidence to a hypothesis."""
    hyp.confidence = max(0.0, hyp.confidence - cfg["weaken_delta"])
    hyp.evidence_against.append(evidence)
    if len(hyp.evidence_against) > MAX_EVIDENCE_ITEMS:
        hyp.evidence_against = hyp.evidence_against[-MAX_EVIDENCE_ITEMS:]
    if hyp.confidence <= 0.0:
        hyp.status = "expired"
    elif hyp.confidence < 0.3:
        hyp.status = "weakened"


def update_hypothesis(
    compass: BehaviorCompass,
    hyp_id: str,
    direction: str,
    evidence: str,
    *,
    now: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Strengthen or weaken a hypothesis based on new evidence."""
    if now is None:
        now = time.time()
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG

    for hyp in compass.hypotheses:
        if hyp.id != hyp_id:
            continue
        hyp.last_tested = now
        hyp.last_decay = now

        if direction == "strengthen":
            _strengthen_hypothesis(hyp, evidence, cfg)
        elif direction == "weaken":
            _weaken_hypothesis(hyp, evidence, cfg)
        compass.hypothesis_version += 1
        break


def decay_stale(
    compass: BehaviorCompass,
    now: float,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Apply confidence decay to hypotheses not tested recently."""
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG
    decay_rate = cfg["decay_per_hour"]

    for hyp in compass.hypotheses:
        if hyp.status == "expired":
            continue
        decay_anchor = max(hyp.last_tested, hyp.last_decay)
        if decay_anchor <= 0.0:
            decay_anchor = hyp.last_tested
        hours_stale = (now - decay_anchor) / 3600.0
        if hours_stale <= 0.0:
            continue
        hyp.confidence = max(0.0, hyp.confidence - decay_rate * hours_stale)
        hyp.last_decay = now
        if hyp.confidence <= 0.0:
            hyp.status = "expired"
            compass.hypothesis_version += 1


def evict_overflow(
    compass: BehaviorCompass,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Evict lowest-confidence + oldest hypotheses when over cap."""
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG
    max_active = cfg["max_active"]

    compass.hypotheses = [h for h in compass.hypotheses if h.status != "expired"]
    if len(compass.hypotheses) <= max_active:
        return

    compass.hypotheses.sort(key=lambda h: (h.confidence, h.created_at))
    compass.hypotheses = compass.hypotheses[-(max_active):]


# ── Coverage and uncertainty ─────────────────────────────────────────────


def compute_coverage(
    compass: BehaviorCompass,
    cfg: dict[str, Any] | None = None,
) -> CoverageMetrics:
    """Recompute coverage metrics from current compass state."""
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG
    promote_threshold = cfg["promote_threshold"]

    active_hyps = [h for h in compass.hypotheses if h.status in ("active", "confirmed")]
    verified = sum(1 for h in active_hyps if h.confidence >= promote_threshold)
    predicted = sum(
        1
        for h in active_hyps
        if h.source == "precheck_declared" and h.confidence >= promote_threshold
    )

    total_approaches = len(compass.approaches)
    successful = sum(1 for a in compass.approaches if a.outcome == "success")
    success_rate = successful / total_approaches if total_approaches > 0 else 0.0

    recent = compass.action_history[-10:]
    bash_count = sum(1 for e in recent if e.get("tool") == "Bash")
    read_count = sum(1 for e in recent if e.get("tool") in ("Read", "Grep", "Glob"))

    surprise = sum(1 for h in active_hyps if h.source == "command_failure" and h.confidence >= 0.5)
    total_discovered = predicted + surprise
    recall = predicted / total_discovered if total_discovered > 0 else 0.0

    return CoverageMetrics(
        constraints_verified=verified,
        constraints_predicted=predicted,
        approaches_attempted=total_approaches,
        approach_success_rate=success_rate,
        bash_count_recent=bash_count,
        read_count_recent=read_count,
        prediction_recall=recall,
    )


def _find_uncovered_approaches(compass: BehaviorCompass) -> list[str]:
    """Find failed approaches with no explaining hypothesis."""
    zones: list[str] = []
    for approach in compass.approaches:
        if approach.outcome != "failed":
            continue
        binary = approach.approach_sig.split(":")[0]
        covered = any(
            _hypothesis_matches_sig(hyp, approach.approach_sig, binary)
            for hyp in compass.hypotheses
            if hyp.status != "expired"
        )
        if not covered and approach.error_sigs:
            last_err = approach.error_sigs[-1]
            zones.append(
                f"Failed approach '{approach.approach_sig}' has no constraint hypothesis "
                f"(last error: {last_err[:60]})"
            )
    return zones


def _find_low_confidence_hypotheses(compass: BehaviorCompass) -> list[str]:
    """Find hypotheses with confidence below 0.4."""
    return [
        f"Low-confidence hypothesis: {hyp.claim[:80]} (confidence: {hyp.confidence:.2f})"
        for hyp in compass.hypotheses
        if hyp.status != "expired" and 0.0 < hyp.confidence < 0.4
    ]


def _find_conflicting_hypotheses(compass: BehaviorCompass) -> list[str]:
    """Find hypotheses with both supporting and contradicting evidence."""
    return [
        f"Conflicting evidence for: {hyp.claim[:80]} "
        f"({len(hyp.evidence_for)} for, {len(hyp.evidence_against)} against)"
        for hyp in compass.hypotheses
        if hyp.status != "expired" and hyp.evidence_for and hyp.evidence_against
    ]


def compute_uncertainty_zones(compass: BehaviorCompass) -> list[str]:
    """Identify areas with lowest confidence or missing coverage."""
    zones: list[str] = []
    zones.extend(_find_uncovered_approaches(compass))
    zones.extend(_find_low_confidence_hypotheses(compass))
    zones.extend(_find_conflicting_hypotheses(compass))
    return zones[:5]  # Cap at 5 for token efficiency


# ── Precheck support ─────────────────────────────────────────────────────


def find_relevant_hypotheses(
    compass: BehaviorCompass,
    command_sig: str | None = None,
    tool: str | None = None,
) -> list[BehaviorHypothesis]:
    """Filter hypotheses by applicability for precheck recall computation."""
    results = []
    binary = ""
    if command_sig and ":" in command_sig:
        binary = command_sig.split(":")[0]

    for hyp in compass.hypotheses:
        if hyp.status == "expired":
            continue

        if not command_sig and not tool:
            results.append(hyp)
            continue

        matched = False
        if command_sig:
            matched = _hypothesis_matches_sig(hyp, command_sig, binary)
        if not matched and tool and tool in hyp.applies_to_tools:
            matched = True

        if matched:
            results.append(hyp)

    return results


def add_declared_hypothesis(
    compass: BehaviorCompass,
    claim: str,
    command_sig: str | None = None,
    *,
    now: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> BehaviorHypothesis:
    """Add a hypothesis declared by the agent via precheck.

    These start at confidence 0.5 (higher than auto-generated 0.3)
    and have source="precheck_declared".
    """
    if now is None:
        now = time.time()
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG

    sig = command_sig or "unknown:unknown"
    hyp_id = _make_hypothesis_id(claim, sig)

    for existing in compass.hypotheses:
        if existing.id == hyp_id:
            update_hypothesis(
                compass,
                hyp_id,
                "strengthen",
                "Re-declared via precheck",
                now=now,
                cfg=cfg,
            )
            return existing

    binary = sig.split(":")[0] if ":" in sig else sig
    # Agent declaration boosts theory and pattern
    decomp = SignalSourceDecomposition(signal_name=claim, theory_score=0.8, pattern_score=0.2)
    hypothesis = BehaviorHypothesis(
        id=hyp_id,
        claim=claim,
        confidence=0.5,
        evidence_for=[f"Declared via precheck: {decomp.to_summary()}"],
        created_at=now,
        last_tested=now,
        last_decay=now,
        source="precheck_declared",
        applies_to_sigs=[f"{binary}:*"] if binary != "unknown" else [],
        applies_to_tools=["Bash"],
        trust_score=0.7,  # Declarations start with higher trust
    )
    compass.hypotheses.append(hypothesis)
    evict_overflow(compass, cfg)
    return hypothesis
