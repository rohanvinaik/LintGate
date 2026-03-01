"""Gap detection and interview templates for the compass system.

Detects coverage gaps across compass axes and generates prioritized
interview questions to fill them. Code inference runs first (Step 3);
gap detection evaluates the result; interview is the fallback.
"""

from __future__ import annotations

from lintgate.compass import (
    AXIS_NAMES,
    REQUIRED_AXES,
    CompassAxis,
    CompassClaim,
    CompassState,
    GapReport,
    compute_axis_depth,
    compute_gap_report,
)

# ── Interview Templates ──────────────────────────────────────────────

INTERVIEW_QUESTIONS: dict[str, list[str]] = {
    "problem": [
        "What specific problem does this solve?",
        "What are the non-goals?",
        "What does success look like?",
    ],
    "solution": [
        "Why this approach over alternatives?",
        "What tradeoffs were made?",
        "What prior work inspired this?",
    ],
    "implementation": [
        "What naming conventions exist?",
        "What tools/techniques are standard?",
        "What does a well-written module look like?",
    ],
    "world": [
        "Language/runtime constraints?",
        "Infrastructure assumptions?",
        "Important dependency constraints?",
    ],
}


# ── Gap Detection ────────────────────────────────────────────────────


def detect_gaps(state: CompassState) -> GapReport:
    """Recompute gap report from current axis depths.

    Refreshes depth scores for all axes, then delegates to
    ``compute_gap_report`` which computes spikiness on REQUIRED_AXES
    only and recommends an interview when spikiness > 0.3 or any
    required axis is empty.
    """
    # Ensure all axes exist in state
    for name in AXIS_NAMES:
        if name not in state.axes:
            state.axes[name] = CompassAxis(name=name)

    # Recompute depths from current claims
    for name in AXIS_NAMES:
        axis = state.axes[name]
        axis.depth = compute_axis_depth(axis.claims)

    gap_report = compute_gap_report(state)
    state.gap_report = gap_report
    return gap_report


# ── Interview Builder ────────────────────────────────────────────────


def build_interview(gap_report: GapReport, max_questions: int = 6) -> list[dict]:
    """Build a prioritized interview from a gap report.

    Returns a list of ``{axis, question, priority}`` dicts sorted by
    gap severity, capped at *max_questions*.

    Priority levels:
      1 = required axis, empty (depth 0)
      2 = required axis, sparse (depth 1)
      3 = optional axis, empty (depth 0)
      4 = optional axis, sparse (depth 1)
    """
    entries: list[dict] = []

    for axis_name in AXIS_NAMES:
        depth = gap_report.axis_depths.get(axis_name, 0)
        if depth > 1:
            continue  # axis is already structural or deep

        is_required = axis_name in REQUIRED_AXES
        priority = (
            (1 if is_required else 3) if depth == 0 else (2 if is_required else 4)
        )

        questions = INTERVIEW_QUESTIONS.get(axis_name, [])
        for question in questions:
            entries.append(
                {
                    "axis": axis_name,
                    "question": question,
                    "priority": priority,
                }
            )

    # Sort by priority (lower = more urgent), then by axis order
    axis_order = {name: idx for idx, name in enumerate(AXIS_NAMES)}
    entries.sort(key=lambda e: (e["priority"], axis_order.get(e["axis"], 99)))

    return entries[:max_questions]


# ── Answer Application ───────────────────────────────────────────────


def apply_answer(
    state: CompassState, axis: str, question_idx: int, answer: str
) -> CompassClaim:
    """Create a claim from an interview answer and add it to the axis.

    The claim is created with ``provenance="interviewed"`` and
    ``confidence=0.9``. After adding, axis depth and the gap report
    are recomputed.

    Returns the created ``CompassClaim``.
    """
    questions = INTERVIEW_QUESTIONS.get(axis, [])
    if 0 <= question_idx < len(questions):
        source_question = questions[question_idx]
    else:
        source_question = f"question#{question_idx}"

    claim = CompassClaim(
        text=answer,
        source=f"interview:{axis}",
        heading=source_question,
        confidence=0.9,
        provenance="interviewed",
    )

    # Ensure axis exists
    if axis not in state.axes:
        state.axes[axis] = CompassAxis(name=axis)

    state.axes[axis].claims.append(claim)
    state.axes[axis].depth = compute_axis_depth(state.axes[axis].claims)

    # Recompute gap report
    state.gap_report = compute_gap_report(state)

    return claim


# ── Skip Interview ───────────────────────────────────────────────────


def skip_interview(state: CompassState) -> None:
    """Mark the interview as not recommended, suppressing the advisory."""
    state.gap_report.interview_recommended = False
