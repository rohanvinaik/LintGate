"""Constraint proposer — pattern bank → theory refinement feedback loop.

When the pattern bank detects recurring anti-patterns (same linter/kind
appearing across multiple runs), the constraint proposer translates
these observations into enforceable rules:

  LINTGATE_FORBID_REGEX  — ban patterns that keep causing errors
  LINTGATE_REQUIRE_REGEX — require patterns that prevent recurring issues
  Theory notes           — advisory observations for CLAUDE.md refinement

This closes the loop: findings → pattern bank → constraint proposals →
theory extractor rules → reduced future findings.

Design:
- Proposals are stored in session memory, not auto-applied
- Deduplication against existing rules prevents noise
- Confidence tracks how strongly the data supports the proposal
- Agent can accept/reject via controlplane_agent_feedback MCP tool
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session_memory import SessionMemory
    from .types import ControlPlaneConfig

# Minimum number of recent runs a pattern must appear in to trigger a proposal
_DEFAULT_PROPOSAL_THRESHOLD = 5

# Mapping from recurring pattern categories to proposed constraint templates
_PATTERN_CONSTRAINT_MAP: dict[str, dict[str, Any]] = {
    # Undefined names — the agent keeps referencing symbols that don't exist
    "F821": {
        "rule_type": "LINTGATE_FORBID_REGEX",
        "template": "# LINTGATE_FORBID_REGEX: undefined name pattern — avoid referencing undefined symbols",
        "rationale_template": "ruff/F821 (undefined name) appeared in {count} of last {window} runs",
        "base_confidence": 0.8,
    },
    # Unused imports — agent imports then doesn't use
    "F401": {
        "rule_type": "theory_note",
        "template": "The agent tends to import modules without using them. Clean up imports after generating code.",
        "rationale_template": "ruff/F401 (unused import) appeared in {count} of last {window} runs",
        "base_confidence": 0.6,
    },
    # Missing return type annotations
    "return-value": {
        "rule_type": "LINTGATE_REQUIRE_REGEX",
        "template": "# LINTGATE_REQUIRE_REGEX: def.*->  — add return type annotations to function definitions",
        "rationale_template": "mypy/return-value appeared in {count} of last {window} runs",
        "base_confidence": 0.7,
    },
    # High complexity
    "complexity": {
        "rule_type": "theory_note",
        "template": "Functions frequently exceed complexity thresholds. Prefer smaller, composable functions.",
        "rationale_template": "radon/complexity appeared in {count} of last {window} runs",
        "base_confidence": 0.5,
    },
    # Security issues
    "B101": {
        "rule_type": "LINTGATE_FORBID_REGEX",
        "template": "# LINTGATE_FORBID_REGEX: assert\\s  — do not use assert for runtime validation (use if/raise)",
        "rationale_template": "bandit/B101 (assert used) appeared in {count} of last {window} runs",
        "base_confidence": 0.7,
    },
}

# Mapping from recurring behavioral pattern kinds to constraint templates.
# Behavioral patterns are keyed by their behavior_channel finding kind,
# not by linter|kind — they promote when the same behavioral anti-pattern
# recurs across mesh runs.
_BEHAVIOR_CONSTRAINT_MAP: dict[str, dict[str, Any]] = {
    "approach_cycling": {
        "rule_type": "theory_note",
        "template": "Enumerate constraints before attempting new approach. Use constraint_check to state known constraints and identify gaps.",
        "rationale_template": "approach_cycling detected in {count} of last {window} mesh runs",
        "base_confidence": 0.6,
    },
    "failure_amnesia": {
        "rule_type": "theory_note",
        "template": "Check constraint ledger before repeating similar commands. Prior failures may contain constraints not yet incorporated.",
        "rationale_template": "failure_amnesia detected in {count} of last {window} mesh runs",
        "base_confidence": 0.7,
    },
    "premature_action": {
        "rule_type": "theory_note",
        "template": "Research system constraints before multi-step execution. Read documentation and inspect state before acting.",
        "rationale_template": "premature_action detected in {count} of last {window} mesh runs",
        "base_confidence": 0.5,
    },
    "brute_force_escalation": {
        "rule_type": "theory_note",
        "template": "More approaches have been tried than constraints understood. Pause to build a constraint model before trying another approach.",
        "rationale_template": "brute_force_escalation detected in {count} of last {window} mesh runs",
        "base_confidence": 0.65,
    },
    "verification_debt": {
        "rule_type": "theory_note",
        "template": "Verify downstream acceptance before long build sequences. Intersperse diagnostic checks.",
        "rationale_template": "verification_debt detected in {count} of last {window} mesh runs",
        "base_confidence": 0.55,
    },
    "stale_model": {
        "rule_type": "theory_note",
        "template": "Update constraint model between approach changes. Use constraint_check before switching strategies.",
        "rationale_template": "stale_model detected in {count} of last {window} mesh runs",
        "base_confidence": 0.5,
    },
}


@dataclass
class TheoryCoherenceResult:
    """Result of checking a constraint proposal against the project's theory profile.

    Metadata-only: does not auto-adjust confidence. The heuristic contradiction
    detection will have false positives, so we start with observation and let
    results accumulate before trusting them for confidence modulation.
    """

    aligned: bool | None = None  # True=aligned, False=contradicting, None=no signal
    supporting_claims: list[str] = field(default_factory=list)
    contradicting_claims: list[str] = field(default_factory=list)
    coherence_score: float = 0.0  # -1.0 (full contradiction) to +1.0 (full alignment)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aligned": self.aligned,
            "supporting_claims": self.supporting_claims,
            "contradicting_claims": self.contradicting_claims,
            "coherence_score": self.coherence_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheoryCoherenceResult:
        return cls(
            aligned=data.get("aligned"),
            supporting_claims=data.get("supporting_claims", []),
            contradicting_claims=data.get("contradicting_claims", []),
            coherence_score=data.get("coherence_score", 0.0),
        )


@dataclass
class ProposedConstraint:
    """A constraint proposed from recurring pattern observation."""

    source: str = "pattern_bank"  # Where the proposal originated
    pattern_key: str = ""  # "ruff|F821" — the triggering pattern
    proposed_rule: str = ""  # The rule text (FORBID/REQUIRE/note)
    rule_type: str = ""  # "LINTGATE_FORBID_REGEX", "LINTGATE_REQUIRE_REGEX", "theory_note"
    rationale: str = ""  # Human-readable explanation
    confidence: float = 0.0  # 0.0-1.0 based on recurrence strength
    status: str = "proposed"  # proposed | accepted | rejected
    # Architecture of Inquiry: theory coherence metadata (no confidence adjustment)
    theory_coherence: TheoryCoherenceResult | None = None
    drift_warning: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "pattern_key": self.pattern_key,
            "proposed_rule": self.proposed_rule,
            "rule_type": self.rule_type,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "status": self.status,
            "theory_coherence": self.theory_coherence.to_dict() if self.theory_coherence else None,
            "drift_warning": self.drift_warning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProposedConstraint:
        tc_data = data.get("theory_coherence")
        tc = TheoryCoherenceResult.from_dict(tc_data) if isinstance(tc_data, dict) else None
        return cls(
            source=data.get("source", "pattern_bank"),
            pattern_key=data.get("pattern_key", ""),
            proposed_rule=data.get("proposed_rule", ""),
            rule_type=data.get("rule_type", ""),
            rationale=data.get("rationale", ""),
            confidence=data.get("confidence", 0.0),
            status=data.get("status", "proposed"),
            theory_coherence=tc,
            drift_warning=data.get("drift_warning", False),
        )


# ── Theory Coherence Check ───────────────────────────────────────────

# Words to exclude when extracting meaningful keywords
_COHERENCE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "have",
    "been",
    "will",
    "should",
    "must",
    "not",
    "are",
    "was",
    "were",
    "has",
    "had",
    "can",
    "may",
    "use",
    "used",
    "using",
    "when",
    "where",
    "which",
    "what",
    "into",
    "than",
    "then",
    "also",
    "each",
    "more",
    "some",
    "any",
    "all",
    "runs",
    "last",
    "detected",
    "appeared",
    "check",
    "avoid",
    "before",
}

# Polarity indicators for contradiction detection
_POSITIVE_POLARITY = {
    "do",
    "use",
    "prefer",
    "recommend",
    "require",
    "ensure",
    "always",
    "should",
    "must",
}
_NEGATIVE_POLARITY = {
    "dont",
    "don't",
    "avoid",
    "never",
    "forbid",
    "ban",
    "not",
    "no",
    "prevent",
    "stop",
}


def _extract_coherence_keywords(text: str) -> list[str]:
    """Extract meaningful 4+ char words from text, minus stopwords."""
    import re

    words = re.findall(r"[a-z]{4,}", text.lower())
    return [w for w in words if w not in _COHERENCE_STOPWORDS]


def _dominant_polarity(c_lower, p_lower):
    p_words = set(p_lower.split())
    c_words = set(c_lower.split())

    p_pos_count = len(p_words & _POSITIVE_POLARITY)
    p_neg_count = len(p_words & _NEGATIVE_POLARITY)
    c_pos_count = len(c_words & _POSITIVE_POLARITY)
    c_neg_count = len(c_words & _NEGATIVE_POLARITY)

    # Determine dominant polarity for each text
    # If both have same dominant polarity → not contradicting
    p_dominant = (
        "positive"
        if p_pos_count > p_neg_count
        else ("negative" if p_neg_count > p_pos_count else "neutral")
    )
    c_dominant = (
        "positive"
        if c_pos_count > c_neg_count
        else ("negative" if c_neg_count > c_pos_count else "neutral")
    )
    return c_dominant, p_dominant


def _is_contradicting(proposal_text: str, claim_text: str) -> bool:
    """Heuristic: does the proposal forbid what the claim endorses, or vice versa?

    Conservative: only flags clear contradictions where noun overlap exists
    and polarity is reversed.
    """
    p_lower = proposal_text.lower()
    c_lower = claim_text.lower()

    # Extract meaningful nouns (4+ chars, not stopwords/polarity words)
    all_polarity = _POSITIVE_POLARITY | _NEGATIVE_POLARITY
    p_nouns = {w for w in _extract_coherence_keywords(p_lower) if w not in all_polarity}
    c_nouns = {w for w in _extract_coherence_keywords(c_lower) if w not in all_polarity}

    # Need at least 1 overlapping concept noun
    overlap = p_nouns & c_nouns
    if not overlap:
        return False

    # Check polarity: texts must have DIFFERENT dominant polarity
    c_dominant, p_dominant = _dominant_polarity(c_lower, p_lower)

    # Contradiction requires opposite dominant polarity
    if p_dominant == "neutral" or c_dominant == "neutral":
        return False
    return p_dominant != c_dominant


def check_theory_coherence(
    proposal: ProposedConstraint,
    theory_profile: dict[str, Any] | None,
) -> TheoryCoherenceResult | None:
    """Check a constraint proposal against the theory profile.

    Metadata-only: returns alignment/contradiction data without modifying
    the proposal's confidence. The heuristic may produce false positives,
    so we start with observation before trusting it for modulation.

    Args:
        proposal: The constraint proposal to check.
        theory_profile: Pre-extracted theory profile, or None.

    Returns:
        TheoryCoherenceResult with supporting/contradicting claims, or None
        if no theory profile available.
    """
    if not theory_profile:
        return None

    from lintgate.theory_extractor import get_theory_context_from_profile

    # Extract keywords from proposal's rule text and rationale
    combined_text = f"{proposal.proposed_rule} {proposal.rationale}"
    keywords = _extract_coherence_keywords(combined_text)

    if not keywords:
        return None

    # Query theory profile for relevant claims
    result = get_theory_context_from_profile(
        theory_profile,
        keywords=keywords[:5],  # Cap keywords to avoid over-broad matching
        max_claims=10,
    )

    claims = result.get("claims", [])
    if not claims:
        return TheoryCoherenceResult(aligned=None, coherence_score=0.0)

    supporting: list[str] = []
    contradicting: list[str] = []

    for claim_data in claims:
        claim_text = claim_data.get("claim", "")
        if _is_contradicting(combined_text, claim_text):
            contradicting.append(claim_text)
        else:
            supporting.append(claim_text)

    # Compute score: normalized difference
    total = len(supporting) + len(contradicting)
    if total == 0:
        score = 0.0
        aligned = None
    else:
        score = (len(supporting) - len(contradicting)) / total
        aligned = score > 0 if score != 0 else None

    return TheoryCoherenceResult(
        aligned=aligned,
        supporting_claims=supporting,
        contradicting_claims=contradicting,
        coherence_score=round(score, 3),
    )


def _resolve_constraint_template(linter: str, kind: str) -> dict[str, Any]:
    """Resolve constraint template from behavioral → pattern → generic maps."""
    if linter == "behavior_channel":
        template_data = _BEHAVIOR_CONSTRAINT_MAP.get(kind)
        if template_data is not None:
            return template_data
    template_data = _PATTERN_CONSTRAINT_MAP.get(kind)
    if template_data is not None:
        return template_data
    return _build_generic_template(linter, kind)


def _compute_proposal_confidence(template_data: dict[str, Any], recent_count: int) -> float:
    """Compute confidence scaling with recurrence count."""
    base_conf = template_data.get("base_confidence", 0.5)
    recurrence_bonus = min((recent_count - 3) * 0.075, 0.15) if recent_count > 3 else 0.0
    return float(min(base_conf + recurrence_bonus, 1.0))


def _apply_coherence_check(
    proposal: ProposedConstraint,
    config: ControlPlaneConfig | None,
    session: SessionMemory | None,
) -> None:
    """Apply theory coherence check to a proposal if enabled."""
    if config is None or not config.inquiry.theory_coherence_check:
        return
    if session is None:
        return
    theory_profile = getattr(session, "theory_profile_cache", None)
    if theory_profile is None:
        return
    coherence = check_theory_coherence(proposal, theory_profile)
    if coherence is not None:
        proposal.theory_coherence = coherence
        if coherence.contradicting_claims:
            proposal.drift_warning = True


def propose_constraints_from_patterns(
    pattern_report: dict[str, Any],
    session: SessionMemory | None = None,
    existing_rules: list[str] | None = None,
    threshold: int = _DEFAULT_PROPOSAL_THRESHOLD,
    config: ControlPlaneConfig | None = None,
) -> list[ProposedConstraint]:
    """Generate constraint proposals from recurring pattern bank alerts.

    Args:
        pattern_report: Output from update_pattern_bank() with alerted_patterns.
        session: Active session memory (for dedup and status tracking).
        existing_rules: Current LINTGATE_FORBID/REQUIRE rules in CLAUDE.md.
        threshold: Minimum recent_run_count to trigger proposal.
        config: ControlPlane config (used to gate inquiry features like coherence).

    Returns:
        List of ProposedConstraint objects. Empty if no patterns qualify.
    """
    existing_rules = existing_rules or []
    alerts = pattern_report.get("alerted_patterns", [])

    if not alerts:
        return []

    # Build set of already-proposed pattern keys (dedup within session)
    already_proposed: set[str] = set()
    if session is not None:
        for constraint in session.proposed_constraints:
            already_proposed.add(constraint.get("pattern_key", ""))

    existing_rule_texts = set(existing_rules)
    proposals: list[ProposedConstraint] = []

    for alert in alerts:
        linter = alert.get("linter", "")
        kind = alert.get("kind", "")
        pattern_key = f"{linter}|{kind}"

        if alert.get("alert_reason") != "recurring_across_runs":
            continue

        recent_count = alert.get("recent_run_count", 0)
        if recent_count < threshold:
            continue

        if pattern_key in already_proposed:
            continue

        template_data = _resolve_constraint_template(linter, kind)
        rule_text = template_data["template"]

        if rule_text in existing_rule_texts:
            continue

        rationale = template_data["rationale_template"].format(
            count=recent_count,
            window=5,
        )
        confidence = _compute_proposal_confidence(template_data, recent_count)

        proposal = ProposedConstraint(
            source="pattern_bank",
            pattern_key=pattern_key,
            proposed_rule=rule_text,
            rule_type=template_data["rule_type"],
            rationale=rationale,
            confidence=round(confidence, 3),
            status="proposed",
        )

        _apply_coherence_check(proposal, config, session)

        proposals.append(proposal)
        already_proposed.add(pattern_key)

    proposals.sort(key=lambda p: -p.confidence)
    return proposals


def store_proposals_in_session(
    session: SessionMemory,
    proposals: list[ProposedConstraint],
) -> None:
    """Store newly proposed constraints in session memory.

    Appends proposal dicts to session.proposed_constraints.
    Does not overwrite existing proposals (by pattern_key).
    """
    existing_keys = {c.get("pattern_key") for c in session.proposed_constraints}

    for p in proposals:
        if p.pattern_key not in existing_keys:
            session.proposed_constraints.append(p.to_dict())
            existing_keys.add(p.pattern_key)


def update_constraint_status(
    session: SessionMemory,
    pattern_key: str,
    new_status: str,
) -> bool:
    """Update the status of a proposed constraint.

    Args:
        session: Active session memory.
        pattern_key: The pattern key to update (e.g. "ruff|F821").
        new_status: New status ("accepted" or "rejected").

    Returns:
        True if the constraint was found and updated.
    """
    for constraint in session.proposed_constraints:
        if constraint.get("pattern_key") == pattern_key:
            constraint["status"] = new_status
            return True
    return False


# ── Helpers ──────────────────────────────────────────────────────────


def _build_generic_template(linter: str, kind: str) -> dict[str, Any]:
    """Build a generic constraint template for unmapped patterns."""
    return {
        "rule_type": "theory_note",
        "template": f"Recurring {linter}/{kind} issues detected. Review and address the root cause.",
        "rationale_template": f"{linter}/{kind} appeared in {{count}} of last {{window}} runs",
        "base_confidence": 0.4,
    }
