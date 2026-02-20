"""Deterministic model calibration probe.

5 multiple-choice questions, each with 4 choices (A-D). Each choice maps
deterministically to signal_risk adjustments via a static scoring matrix.
No LLM inference, no raw text storage.

The probe reveals the model's behavioral tendencies:
- Q1: Approach to failures → approach_cycling, failure_amnesia risk
- Q2: Verification habits → verification_debt, premature_action risk
- Q3: Constraint discovery style → serial_discovery, brute_force_escalation risk
- Q4: Model updating behavior → stale_model, approach_cycling risk
- Q5: Tool use patterns → tool_repetition, consecutive_failures risk

Scoring: Each answer contributes float deltas to signal_risk entries.
The final signal_risk vector is clamped to [0.0, 1.0] per signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model_profiles import ModelProfile

PROBE_VERSION = 1
SUPPORTED_PROBE_SETS = {"quick"}


@dataclass
class ProbeQuestion:
    """A single probe question with multiple-choice answers."""

    id: str
    question: str
    choices: dict[str, str]  # "A" -> description
    signal_map: dict[str, dict[str, float]]  # choice -> {signal: delta}


# ── Probe Question Bank ──────────────────────────────────────────────

PROBE_QUESTIONS: list[ProbeQuestion] = [
    ProbeQuestion(
        id="q1_failure_response",
        question=(
            "When a Bash command fails with an error you've seen before, "
            "what is your most likely next action?"
        ),
        choices={
            "A": "Try a variation of the same command immediately",
            "B": "Read the error output carefully, then try a different approach",
            "C": "Search for the error in project docs before retrying",
            "D": "Run a diagnostic command to understand the root cause",
        },
        signal_map={
            "A": {
                "approach_cycling": 0.3,
                "failure_amnesia": 0.4,
                "brute_force_escalation": 0.2,
            },
            "B": {"approach_cycling": 0.1, "failure_amnesia": 0.1},
            "C": {"serial_discovery": -0.1},
            "D": {"premature_action": -0.2, "serial_discovery": -0.2},
        },
    ),
    ProbeQuestion(
        id="q2_verification_habits",
        question=(
            "After making 5 consecutive edits to fix a bug, "
            "when do you run the test suite?"
        ),
        choices={
            "A": "After each individual edit",
            "B": "After every 2-3 edits",
            "C": "After all edits are complete",
            "D": "Only when specifically asked to verify",
        },
        signal_map={
            "A": {"verification_debt": -0.3, "premature_action": -0.1},
            "B": {"verification_debt": -0.1},
            "C": {"verification_debt": 0.3, "premature_action": 0.2},
            "D": {"verification_debt": 0.5, "premature_action": 0.3},
        },
    ),
    ProbeQuestion(
        id="q3_constraint_discovery",
        question=(
            "You're working on an unfamiliar codebase. "
            "How do you discover constraints and requirements?"
        ),
        choices={
            "A": "Start implementing and learn from errors",
            "B": "Read the main config and test files first",
            "C": "Use behavior_precheck to enumerate constraints upfront",
            "D": "Scan docs, tests, and config before any edits",
        },
        signal_map={
            "A": {
                "serial_discovery": 0.4,
                "brute_force_escalation": 0.3,
                "premature_action": 0.3,
            },
            "B": {"serial_discovery": 0.1},
            "C": {"serial_discovery": -0.3, "brute_force_escalation": -0.2},
            "D": {"serial_discovery": -0.2, "premature_action": -0.3},
        },
    ),
    ProbeQuestion(
        id="q4_model_updating",
        question=(
            "After 3 failed approaches to the same problem, "
            "what do you typically do?"
        ),
        choices={
            "A": "Try a 4th approach with minor variations",
            "B": "Step back and re-read error output from all 3 attempts",
            "C": "Run behavior_precheck to check constraint coverage",
            "D": "Abandon the current strategy entirely and start fresh",
        },
        signal_map={
            "A": {
                "stale_model": 0.4,
                "approach_cycling": 0.3,
                "brute_force_escalation": 0.2,
            },
            "B": {"stale_model": 0.1, "failure_amnesia": -0.1},
            "C": {"stale_model": -0.3, "approach_cycling": -0.2},
            "D": {"stale_model": 0.2, "approach_cycling": 0.1},
        },
    ),
    ProbeQuestion(
        id="q5_tool_patterns",
        question=(
            "When a complex command works on one file but fails on another, "
            "what is your immediate reaction?"
        ),
        choices={
            "A": "Run the same command again to see if it's transient",
            "B": "Compare the two files to understand the difference",
            "C": "Modify the command slightly and retry",
            "D": "Check what preconditions the command requires",
        },
        signal_map={
            "A": {"tool_repetition": 0.4, "consecutive_failures": 0.3},
            "B": {"tool_repetition": -0.1, "premature_action": -0.2},
            "C": {"approach_cycling": 0.2, "tool_repetition": 0.1},
            "D": {"serial_discovery": -0.2, "premature_action": -0.2},
        },
    ),
]


# ── Signal-to-Anti-Pattern Mapping ───────────────────────────────────

_SIGNAL_ANTI_PATTERN_MAP: dict[str, str] = {
    "approach_cycling": (
        "Do not try a 4th approach without first enumerating all known constraints "
        "and verifying which ones the new approach actually addresses."
    ),
    "failure_amnesia": (
        "Do not re-attempt an approach that already failed unless the conditions "
        "that caused the failure have changed."
    ),
    "serial_discovery": (
        "Do not discover constraints one-at-a-time through failure — enumerate "
        "the full constraint space upfront by reading before acting."
    ),
    "premature_action": (
        "Do not act before understanding — convert unbounded aesthetic tasks "
        "into bounded checklists before beginning work."
    ),
    "verification_debt": (
        "Do not defer verification — run tests after every 2-3 edits, not "
        "only at the end of a long editing sequence."
    ),
    "stale_model": (
        "Do not keep using the same mental model after it has been falsified — "
        "update your hypothesis after each failed approach."
    ),
    "tool_repetition": (
        "Do not repeat the same command hoping for a different result — vary "
        "your approach after 2 repetitions of the same tool signature."
    ),
    "brute_force_escalation": (
        "Do not treat N instances of the same root cause as N separate problems — "
        "cluster issues by shared fix before diving into individual repairs."
    ),
    "consecutive_failures": (
        "Do not ignore layered signals (e.g., file-too-long + too-many-methods + "
        "clustered type errors) that compose into a single structural diagnosis."
    ),
}

_SIGNAL_DISPOSITION_MAP: dict[str, str] = {
    "approach_cycling": (
        "MUST run `behavior_precheck` before attempting a 3rd approach "
        "(model profile indicates high approach-cycling risk)."
    ),
    "verification_debt": (
        "MUST verify after every 3 edits, not just at the end "
        "(model profile indicates high verification-debt risk)."
    ),
    "premature_action": (
        "MUST read relevant code before any Bash command "
        "(model profile indicates premature-action risk)."
    ),
    "serial_discovery": (
        "MUST use `behavior_precheck` proactively at session start "
        "(model profile indicates reactive constraint discovery)."
    ),
    "failure_amnesia": (
        "MUST review error signatures from prior attempts before retrying "
        "(model profile indicates failure-amnesia risk)."
    ),
    "stale_model": (
        "MUST update hypothesis model after each failed approach "
        "(model profile indicates stale-model risk)."
    ),
    "tool_repetition": (
        "MUST vary approach after 2 repetitions of the same command "
        "(model profile indicates tool-repetition risk)."
    ),
}


# ── Public API ───────────────────────────────────────────────────────


def get_probe_questions(probe_set: str = "quick") -> list[dict[str, Any]]:
    """Return probe questions formatted for MCP response.

    Returns list of {id, question, choices} dicts.
    Does NOT include signal_map (internal scoring detail).
    """
    normalized = probe_set.strip().lower() if isinstance(probe_set, str) else ""
    if normalized not in SUPPORTED_PROBE_SETS:
        msg = (
            f"Unsupported probe_set: {probe_set!r}. "
            f"Supported: {sorted(SUPPORTED_PROBE_SETS)}"
        )
        raise ValueError(msg)

    return [
        {
            "id": q.id,
            "question": q.question,
            "choices": q.choices,
        }
        for q in PROBE_QUESTIONS
    ]


def score_probe_responses(
    responses: dict[str, str],
) -> tuple[dict[str, float], float]:
    """Score probe responses into a signal_risk vector.

    Args:
        responses: {question_id: choice_letter} e.g. {"q1_failure_response": "A"}

    Returns:
        (signal_risk, confidence)
        signal_risk: {signal_name: risk_level} clamped to [0.0, 1.0]
        confidence: 0.0-1.0 based on completeness
    """
    signal_risk: dict[str, float] = {}
    answered = 0
    question_index = {q.id: q for q in PROBE_QUESTIONS}

    for qid, choice in responses.items():
        q = question_index.get(qid)
        if q is None:
            continue
        choice = choice.strip().upper()
        if choice not in q.signal_map:
            continue
        answered += 1
        for signal, delta in q.signal_map[choice].items():
            signal_risk[signal] = signal_risk.get(signal, 0.0) + delta

    # Clamp to [0.0, 1.0]
    for signal in signal_risk:
        signal_risk[signal] = max(0.0, min(1.0, signal_risk[signal]))

    # Confidence: based on completeness
    total_questions = len(PROBE_QUESTIONS)
    if total_questions == 0:
        return signal_risk, 0.0

    completeness = answered / total_questions
    # 5/5 = 0.80, 4/5 = 0.71, 3/5 = 0.62 (just above 0.55 threshold)
    confidence = min(1.0, 0.35 + (completeness * 0.45))

    return signal_risk, round(confidence, 3)


def _derive_custom_anti_patterns(
    signal_risk: dict[str, float],
    max_items: int = 5,
) -> list[str]:
    """Select anti-patterns prioritized by highest-risk signals."""
    ranked = sorted(signal_risk.items(), key=lambda x: -x[1])
    patterns: list[str] = []
    for signal, risk in ranked:
        if risk < 0.1:
            continue
        if signal in _SIGNAL_ANTI_PATTERN_MAP:
            patterns.append(_SIGNAL_ANTI_PATTERN_MAP[signal])
        if len(patterns) >= max_items:
            break
    return patterns


def _derive_custom_dispositions(
    signal_risk: dict[str, float],
    threshold: float = 0.3,
    max_items: int = 4,
) -> list[str]:
    """Generate guardrail dispositions for high-risk signals."""
    ranked = sorted(signal_risk.items(), key=lambda x: -x[1])
    dispositions: list[str] = []
    for signal, risk in ranked:
        if risk < threshold:
            continue
        if signal in _SIGNAL_DISPOSITION_MAP:
            dispositions.append(_SIGNAL_DISPOSITION_MAP[signal])
        if len(dispositions) >= max_items:
            break
    return dispositions


def build_profile_from_probe(
    model_key: str,
    responses: dict[str, str],
) -> ModelProfile:
    """Create a ModelProfile from probe responses.

    Scores, derives custom anti-patterns and dispositions, constructs profile.
    """
    from .model_profiles import ModelProfile, resolve_model_key

    canonical = resolve_model_key(model_key)
    if canonical is None:
        msg = f"Cannot resolve model key: {model_key!r}"
        raise ValueError(msg)

    signal_risk, confidence = score_probe_responses(responses)
    custom_anti_patterns = _derive_custom_anti_patterns(signal_risk)
    custom_dispositions = _derive_custom_dispositions(signal_risk)

    return ModelProfile(
        model_key=canonical,
        probe_version=PROBE_VERSION,
        probe_runs=1,
        signal_risk=signal_risk,
        confidence=confidence,
        custom_anti_patterns=custom_anti_patterns,
        custom_dispositions=custom_dispositions,
    )
