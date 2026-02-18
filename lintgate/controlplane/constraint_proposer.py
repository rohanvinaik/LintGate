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
        "template": "Enumerate constraints before attempting new approach. Use behavior_precheck to state known constraints and identify gaps.",
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
        "template": "Update constraint model between approach changes. Use behavior_precheck before switching strategies.",
        "rationale_template": "stale_model detected in {count} of last {window} mesh runs",
        "base_confidence": 0.5,
    },
}


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "pattern_key": self.pattern_key,
            "proposed_rule": self.proposed_rule,
            "rule_type": self.rule_type,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProposedConstraint:
        return cls(
            source=data.get("source", "pattern_bank"),
            pattern_key=data.get("pattern_key", ""),
            proposed_rule=data.get("proposed_rule", ""),
            rule_type=data.get("rule_type", ""),
            rationale=data.get("rationale", ""),
            confidence=data.get("confidence", 0.0),
            status=data.get("status", "proposed"),
        )


def propose_constraints_from_patterns(
    pattern_report: dict[str, Any],
    session: SessionMemory | None = None,
    existing_rules: list[str] | None = None,
    threshold: int = _DEFAULT_PROPOSAL_THRESHOLD,
) -> list[ProposedConstraint]:
    """Generate constraint proposals from recurring pattern bank alerts.

    Args:
        pattern_report: Output from update_pattern_bank() with alerted_patterns.
        session: Active session memory (for dedup and status tracking).
        existing_rules: Current LINTGATE_FORBID/REQUIRE rules in CLAUDE.md.
        threshold: Minimum recent_run_count to trigger proposal.

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

    # Build set of existing rule texts (dedup against CLAUDE.md)
    existing_rule_texts = set(existing_rules)

    proposals: list[ProposedConstraint] = []

    for alert in alerts:
        linter = alert.get("linter", "")
        kind = alert.get("kind", "")
        pattern_key = f"{linter}|{kind}"

        # Only act on recurring-across-runs alerts
        if alert.get("alert_reason") != "recurring_across_runs":
            continue

        recent_count = alert.get("recent_run_count", 0)

        # Must meet threshold
        if recent_count < threshold:
            continue

        # Skip already-proposed patterns in this session
        if pattern_key in already_proposed:
            continue

        # Look up constraint template — check behavioral map first for behavior_channel
        template_data = None
        if linter == "behavior_channel":
            template_data = _BEHAVIOR_CONSTRAINT_MAP.get(kind)
        if template_data is None:
            template_data = _PATTERN_CONSTRAINT_MAP.get(kind)
        if template_data is None:
            # Generic fallback for unknown kinds
            template_data = _build_generic_template(linter, kind)

        # Build the proposal
        rule_text = template_data["template"]

        # Skip if this rule already exists
        if rule_text in existing_rule_texts:
            continue

        rationale = template_data["rationale_template"].format(
            count=recent_count,
            window=5,  # _RECENT_WINDOW from pattern_bank
        )

        # Confidence scales with recurrence — more runs = higher confidence
        base_conf = template_data.get("base_confidence", 0.5)
        # Scale: 3/5 runs → base, 5/5 runs → base + 0.15
        recurrence_bonus = min((recent_count - 3) * 0.075, 0.15) if recent_count > 3 else 0.0
        confidence = min(base_conf + recurrence_bonus, 1.0)

        proposal = ProposedConstraint(
            source="pattern_bank",
            pattern_key=pattern_key,
            proposed_rule=rule_text,
            rule_type=template_data["rule_type"],
            rationale=rationale,
            confidence=round(confidence, 3),
            status="proposed",
        )

        proposals.append(proposal)
        already_proposed.add(pattern_key)

    # Sort by confidence descending
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
