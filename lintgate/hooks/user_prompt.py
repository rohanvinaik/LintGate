"""UserPromptSubmit hook — pre-generation primer engine.

Fires when the user submits a prompt. Injects targeted session context
as a system message BEFORE the model starts reasoning. This is the
highest-attention pre-generation surface — the model reads this
immediately before generating its response.

Enhanced version loads RuntimeState for rich context. Falls back to
legacy mode-indicator behavior when RuntimeState is unavailable.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Keywords that suggest the user wants theory exploration
_THEORY_KEYWORDS = frozenset(
    {
        "theory",
        "compass",
        "explore",
        "understand",
        "why",
        "philosophy",
        "principle",
        "architecture",
    }
)

# Maximum primer output (~120 tokens / ~480 chars)
_PRIMER_MAX_CHARS = 480


def _load_mode(project_root: str) -> str:
    """Load current cognitive mode from session memory."""
    try:
        from lintgate.controlplane.session_memory import get_or_create_session

        session = get_or_create_session(project_root)
        mode = session.behavior_compass.get("mode_state", {}).get("current", "normal")
        return str(mode or "normal")
    except Exception:
        return "normal"


def _build_primer(project_root: str) -> str | None:
    """Build a targeted primer from RuntimeState.

    Returns the primer string, or None if RuntimeState is unavailable
    (caller falls back to legacy behavior).
    """
    try:
        from lintgate.runtime_state import load_runtime_state

        runtime = load_runtime_state(project_root)
        if runtime is None:
            return None
    except Exception:
        return None

    parts: list[str] = []

    # ── Tier 1: Actionable state (highest attention) ──────────────

    # 1. Blocking issues (must-address signal)
    if runtime.blocking_issues > 0:
        parts.append(f"BLOCKING: {runtime.blocking_issues} issues")

    # 2. Coherence alert (cross-channel diagnosis)
    if runtime.coherence_state in ("coupled", "systemic"):
        parts.append(f"Coherence: {runtime.coherence_state}")

    # 3. Behavioral warning (approaching stuck state)
    if runtime.approach_failures >= 2:
        parts.append(
            f"WARNING: {runtime.approach_failures} failed approaches \u2014 run constraint_check"
        )
    elif runtime.prediction_accuracy >= 0 and runtime.prediction_accuracy < 0.5:
        parts.append(f"Prediction accuracy: {runtime.prediction_accuracy:.0%}")

    # ── Tier 2: Context (orientation signals) ─────────────────────

    # 4. Mode line
    mode_str = f"Mode: {runtime.mode}"
    if runtime.mode == "habit" and runtime.habit_score > 0:
        mode_str += f" ({runtime.habit_score:.0%})"
    parts.append(mode_str)

    # 5. Focus context
    if runtime.active_files:
        basenames = [f.rsplit("/", 1)[-1] for f in runtime.active_files[:3]]
        parts.append(f"Focus: [{', '.join(basenames)}]")

    # 6. PrescriptiveSpec coverage
    if getattr(runtime, "prescriptive_spec_count", 0) > 0:
        pspec_count = runtime.prescriptive_spec_count
        pspec_ratio = getattr(runtime, "prescriptive_coverage_ratio", 0.0)
        parts.append(f"PSpec: {pspec_count} specs ({pspec_ratio:.0%} covered)")

    primer = " | ".join(parts)

    # Hard-cap at budget
    if len(primer) > _PRIMER_MAX_CHARS:
        primer = primer[: _PRIMER_MAX_CHARS - 3] + "..."

    return primer


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process UserPromptSubmit event."""
    project_root = data.get("cwd", ".")
    user_message = data.get("userMessage", data.get("message", ""))
    if isinstance(user_message, dict):
        user_message = user_message.get("content", "")

    # Try enhanced primer first
    primer = _build_primer(project_root)
    if primer is not None:
        # Check for theory keywords (still useful for mode hint)
        msg_lower = str(user_message).lower()
        if any(kw in msg_lower for kw in _THEORY_KEYWORDS):
            primer += " (theory-relevant prompt)"

        return {
            "continue": True,
            "systemMessage": f"[LG] {primer}" if primer else "",
        }

    # Fallback: legacy mode-indicator behavior
    mode = _load_mode(project_root)
    msg_lower = str(user_message).lower()
    mode_hint = ""
    if any(kw in msg_lower for kw in _THEORY_KEYWORDS):
        mode_hint = " (theory-relevant prompt detected)"

    system_message = ""
    if mode_hint:
        system_message = f"[Compass] Mode: {mode}{mode_hint}"
    elif mode != "normal":
        system_message = f"[Compass] Mode: {mode}"

    return {
        "continue": True,
        "systemMessage": system_message,
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
