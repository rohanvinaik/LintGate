"""Behavioral compass — live hypothesis model of agent behavior.

Tracks constraints discovered through tool-use patterns, maintains
confidence-scored hypotheses, and computes coverage metrics for the
constraint_check tool (formerly behavior_precheck).

Design principles (from Grail hidden compass):
- Hypotheses are live experiments, not static facts
- Confidence rises/falls based on outcome evidence
- Wrong hypotheses are productive (they improve uncertainty targeting)
- Co-construction: agent declares its model, tool computes gaps

No LLM calls, no subprocess calls, no file I/O in this module.
All persistence flows through session_memory.
"""

from __future__ import annotations

import re
import time
from typing import Any

from lintgate.controlplane.command_normalization import (  # noqa: F401
    DEFAULT_INTENT_MAP,
    DEFAULT_INTENT_SIG_MAP,
    INTENT_CATEGORIES,
    error_memory_key,
    extract_error_sig,
    normalize_command_sig,
    resolve_intent,
)

# Re-export hypothesis management functions
from .compass_hypothesis import (  # noqa: F401
    _find_conflicting_hypotheses,
    _find_low_confidence_hypotheses,
    _find_uncovered_approaches,
    _hypothesis_matches_sig,
    _strengthen_hypothesis,
    _test_hypotheses,
    _weaken_hypothesis,
    add_declared_hypothesis,
    compute_coverage,
    compute_uncertainty_zones,
    decay_stale,
    evict_overflow,
    find_relevant_hypotheses,
    update_hypothesis,
)

# Re-export prediction functions
from .compass_predictions import (  # noqa: F401
    _apply_prediction_to_hypothesis,
    _check_predictions,
    _evaluate_prediction_match,
    compute_prediction_accuracy,
)

# ── Re-exports for backward compatibility ────────────────────────────────
# All types, constants, and command normalization functions are re-exported
# so existing imports from this module continue to work.
from .types import (  # noqa: F401
    DEFAULT_HYPOTHESIS_CONFIG,
    DEFAULT_THRESHOLDS,
    MAX_ACTION_HISTORY,
    MAX_APPROACHES,
    MAX_ERROR_MEMORY,
    MAX_EVIDENCE_ITEMS,
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    NudgeState,
    Prediction,
    PredictionExpectation,
    PredictionStateContainer,
    SignalState,
    make_hypothesis_id,
    new_compass,
)

# Private alias for internal use
_make_hypothesis_id = make_hypothesis_id


# ── Core update functions ────────────────────────────────────────────────


def _parse_bash_event(
    tool_input: dict[str, Any] | str,
    tool_output: str | dict[str, Any],
) -> tuple[str, int | None, str, str]:
    """Parse command signature, exit code, and error from a Bash event.

    Returns (command_sig, exit_code, error_sig, output_str).
    """
    cmd = ""
    if isinstance(tool_input, dict):
        cmd = tool_input.get("command", "")
    elif isinstance(tool_input, str):
        cmd = tool_input

    command_sig = normalize_command_sig(cmd)

    output_str = tool_output if isinstance(tool_output, str) else str(tool_output)
    exit_match = re.search(
        r"(?:exit[_ ]code|exit[_ ]status|exitstatus)[: =]+(\d+)",
        output_str,
        re.IGNORECASE,
    )

    if exit_match:
        exit_code: int | None = int(exit_match.group(1))
    elif "error" in output_str.lower() or "failed" in output_str.lower():
        exit_code = 1
    else:
        exit_code = 0

    error_sig = extract_error_sig(output_str) if exit_code != 0 else ""

    return command_sig, exit_code, error_sig, output_str


def record_tool_event(
    compass: BehaviorCompass,
    tool_name: str,
    tool_input: dict[str, Any] | str,
    tool_output: str | dict[str, Any],
    *,
    now: float | None = None,
    hyp_config: dict[str, Any] | None = None,
) -> list[str]:
    """Record a tool-use event and update compass state.

    This is the core update function called on every PostToolUse event.
    Auto-generates low-confidence hypotheses from Bash failures.
    Tests existing hypotheses against outcome.
    Applies decay to stale hypotheses.

    Returns list of alert names triggered (for detection rules to check).
    """
    if now is None:
        now = time.time()
    cfg = {**DEFAULT_HYPOTHESIS_CONFIG, **(hyp_config or {})}

    compass.event_counter += 1

    # Extract structured data from the event
    command_sig = ""
    exit_code: int | None = None
    error_sig = ""
    output_str = ""

    if tool_name == "Bash":
        command_sig, exit_code, error_sig, output_str = _parse_bash_event(
            tool_input,
            tool_output,
        )
        if exit_code != 0:
            _update_error_memory(compass, error_sig, now)
    elif tool_name in ("Agent", "Task") and isinstance(tool_input, dict):
        # Capture description for delegation detection
        command_sig = str(tool_input.get("description", ""))[:80]

    # Resolve and track intent
    intent = resolve_intent(tool_name, command_sig)

    # Append to action history (rolling window)
    compass.action_history.append(
        {
            "tool": tool_name,
            "ts": now,
            "sig": command_sig,
            "exit": exit_code,
            "err": error_sig,
            "intent": intent,
        }
    )
    compass.intent_history.append(intent)
    if len(compass.intent_history) > MAX_ACTION_HISTORY:
        compass.intent_history = compass.intent_history[-MAX_ACTION_HISTORY:]
    if len(compass.action_history) > MAX_ACTION_HISTORY:
        compass.action_history = compass.action_history[-MAX_ACTION_HISTORY:]

    # Update approaches (Bash commands only)
    if tool_name == "Bash" and command_sig and exit_code is not None:
        _update_approach(compass, command_sig, exit_code, error_sig, now)

    # Test existing hypotheses (before auto-generating, to avoid double-strengthening)
    if tool_name == "Bash" and command_sig:
        _test_hypotheses(compass, command_sig, exit_code, error_sig, now, cfg)

    # Check pending predictions against actual outcomes (Bash events only)
    _check_predictions(compass, tool_name, command_sig, exit_code, error_sig, output_str, cfg)

    # Auto-generate hypothesis from Bash failure
    if tool_name == "Bash" and exit_code is not None and exit_code != 0 and error_sig:
        _auto_generate_hypothesis(compass, command_sig, error_sig, now, cfg)

    # Integration verification tracking (Phase 3)
    _update_integration_counters(compass, tool_name, tool_input, command_sig, now)

    # Decay stale hypotheses
    decay_stale(compass, now, cfg)

    # Recompute coverage
    compass.coverage = compute_coverage(compass, cfg)
    compass.uncertainty_zones = compute_uncertainty_zones(compass)

    return []  # Alerts computed by channel, not here


def _update_integration_counters(
    compass: BehaviorCompass,
    tool_name: str,
    tool_input: dict[str, Any] | str,
    command_sig: str,
    now: float,
) -> None:
    """Update integration verification tracking counters.

    Increments on Edit/Write to integration paths.
    Resets on verification tools or integration test bash commands.
    """
    from lintgate.channels.behavior_detection import (
        INTEGRATION_PATHS,
        INTEGRATION_VERIFY_BASH_PATTERNS,
        INTEGRATION_VERIFY_TOOLS,
    )

    # Check for verification events (reset counter)
    if tool_name in INTEGRATION_VERIFY_TOOLS:
        compass.integration_edits_since_verify = 0
        compass.last_integration_verify_ts = now
        return

    if tool_name == "Bash" and command_sig:
        for pattern in INTEGRATION_VERIFY_BASH_PATTERNS:
            if re.search(pattern, command_sig):
                compass.integration_edits_since_verify = 0
                compass.last_integration_verify_ts = now
                return

    # Check for edits to integration paths (increment counter)
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        file_path = ""
        if isinstance(tool_input, dict):
            file_path = tool_input.get("file_path", "")
        elif isinstance(tool_input, str):
            file_path = tool_input
        # Normalize to forward slashes for matching
        file_path = file_path.replace("\\", "/")
        for integration_path in INTEGRATION_PATHS:
            if integration_path in file_path:
                compass.integration_edits_since_verify += 1
                return


def _update_error_memory(
    compass: BehaviorCompass,
    error_sig: str,
    now: float,
) -> None:
    """Update rolling cross-window error memory for amnesia detection."""
    key = error_memory_key(error_sig)
    if not key:
        return

    current = compass.error_memory.get(key)
    if current is None:
        compass.error_memory[key] = {
            "count": 1,
            "first_seen": now,
            "last_seen": now,
            "last_sig": error_sig[:120],
        }
    else:
        current["count"] = int(current.get("count", 0)) + 1
        current["last_seen"] = now
        current.setdefault("first_seen", now)
        current["last_sig"] = error_sig[:120]

    if len(compass.error_memory) > MAX_ERROR_MEMORY:
        ordered = sorted(
            compass.error_memory.items(),
            key=lambda kv: float(kv[1].get("last_seen", 0.0)),
        )
        overflow = len(compass.error_memory) - MAX_ERROR_MEMORY
        for key_to_remove, _ in ordered[:overflow]:
            compass.error_memory.pop(key_to_remove, None)


def _update_approach(
    compass: BehaviorCompass,
    sig: str,
    exit_code: int,
    error_sig: str,
    now: float,
) -> None:
    """Update or create an approach attempt for this command signature."""
    existing = None
    for approach in compass.approaches:
        if approach.approach_sig == sig:
            existing = approach
            break

    if existing is not None:
        existing.exit_codes.append(exit_code)
        existing.error_sigs.append(error_sig)
        existing.last_event = now
        existing.event_count += 1
        if exit_code == 0:
            existing.outcome = "success"
        elif existing.outcome != "success":
            existing.outcome = "failed"
        if len(existing.exit_codes) > MAX_EVIDENCE_ITEMS:
            existing.exit_codes = existing.exit_codes[-MAX_EVIDENCE_ITEMS:]
        if len(existing.error_sigs) > MAX_EVIDENCE_ITEMS:
            existing.error_sigs = existing.error_sigs[-MAX_EVIDENCE_ITEMS:]
    else:
        new_approach = ApproachAttempt(
            approach_sig=sig,
            exit_codes=[exit_code],
            error_sigs=[error_sig] if error_sig else [],
            started_at=now,
            last_event=now,
            outcome="success" if exit_code == 0 else "failed",
            event_count=1,
            hyp_version_at_start=compass.hypothesis_version,
        )
        compass.approaches.append(new_approach)
        if len(compass.approaches) > MAX_APPROACHES:
            compass.approaches = compass.approaches[-MAX_APPROACHES:]


def _auto_generate_hypothesis(
    compass: BehaviorCompass,
    command_sig: str,
    error_sig: str,
    now: float,
    cfg: dict[str, Any],
) -> None:
    """Auto-generate a low-confidence hypothesis from a Bash failure."""
    # Check for existing hypothesis with same error sig
    for hyp in compass.hypotheses:
        if hyp.status not in ("active", "confirmed"):
            continue
        for ev in hyp.evidence_for:
            if error_sig in ev:
                update_hypothesis(
                    compass,
                    hyp.id,
                    "strengthen",
                    f"Re-observed: {error_sig}",
                    now=now,
                    cfg=cfg,
                )
                return

    hyp_id = _make_hypothesis_id(error_sig, command_sig)
    if any(h.id == hyp_id for h in compass.hypotheses):
        return

    binary = command_sig.split(":")[0] if ":" in command_sig else command_sig
    hypothesis = BehaviorHypothesis(
        id=hyp_id,
        claim=f"{binary} failed: {error_sig[:100]}",
        confidence=cfg["auto_generate_confidence"],
        evidence_for=["exit!=0 (outcome attribution)"],
        created_at=now,
        last_tested=now,
        last_decay=now,
        source="command_failure",
        applies_to_sigs=[f"{binary}:*"],
        applies_to_tools=["Bash"],
        trust_score=0.3,  # Command failures are purely reactive (outcome-only)
    )
    compass.hypotheses.append(hypothesis)
    compass.hypothesis_version += 1
    evict_overflow(compass, cfg)
