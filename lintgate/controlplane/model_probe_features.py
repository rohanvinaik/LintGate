"""Feature extraction for model calibration probes.

Extracts behavioral features from structured probe responses.
Scores from action traces first, text second.

Extracted from model_probe.py for module size compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model_probe_tasks import ProbeTask

# ── Indicator Sets ──────────────────────────────────────────────────

_READ_INDICATORS = frozenset(
    {
        "read",
        "examine",
        "look at",
        "inspect",
        "check",
        "review",
        "open",
        "cat",
        "grep",
        "search",
        "glob",
        "find",
        "understand",
        "investigate",
        "analyze",
        "study",
    }
)

_VERIFY_INDICATORS = frozenset(
    {
        "test",
        "pytest",
        "verify",
        "run test",
        "check output",
        "validate",
        "assert",
        "confirm",
        "make sure",
    }
)

_FIX_INDICATORS = frozenset(
    {
        "edit",
        "fix",
        "change",
        "replace",
        "modify",
        "update",
        "write",
        "add",
        "remove",
        "delete",
        "set",
    }
)


def _first_indicator_pos(text: str, indicators: frozenset[str]) -> int:
    """Find the earliest position of any indicator phrase in text."""
    earliest = -1
    for indicator in indicators:
        pos = text.find(indicator)
        if pos >= 0 and (earliest == -1 or pos < earliest):
            earliest = pos
    return earliest


# ── Feature Sub-Extractors ──────────────────────────────────────────


def _extract_read_before_edit(
    text: str,
    tool_calls_lower: list[str],
    actions_lower: list[str],
) -> bool:
    """Did the model read/inspect before modifying?"""
    if tool_calls_lower:
        first_read_idx = -1
        first_edit_idx = -1
        for i, tc in enumerate(tool_calls_lower):
            if first_read_idx == -1 and any(
                r in tc for r in ("read", "grep", "glob", "cat", "search", "inspect")
            ):
                first_read_idx = i
            if first_edit_idx == -1 and any(
                e in tc for e in ("edit", "write", "bash", "fix", "modify")
            ):
                first_edit_idx = i
        return first_read_idx >= 0 and (first_edit_idx == -1 or first_read_idx < first_edit_idx)

    if actions_lower:
        first_read = next(
            (i for i, a in enumerate(actions_lower) if any(r in a for r in _READ_INDICATORS)),
            -1,
        )
        first_fix = next(
            (i for i, a in enumerate(actions_lower) if any(f in a for f in _FIX_INDICATORS)),
            -1,
        )
        return first_read >= 0 and (first_fix == -1 or first_read < first_fix)

    read_pos = _first_indicator_pos(text, _READ_INDICATORS)
    fix_pos = _first_indicator_pos(text, _FIX_INDICATORS)
    return read_pos >= 0 and (fix_pos == -1 or read_pos < fix_pos)


def _extract_retry_features(
    text: str,
    tool_calls_lower: list[str],
    actions_lower: list[str],
    retry_count: int | None,
) -> dict[str, bool]:
    """Extract exact_retry and minor_variant_only features."""
    features: dict[str, bool] = {}

    if retry_count is not None:
        features["exact_retry"] = retry_count >= 1
    elif tool_calls_lower:
        features["exact_retry"] = any(
            tool_calls_lower[i] == tool_calls_lower[i + 1] for i in range(len(tool_calls_lower) - 1)
        )
    else:
        features["exact_retry"] = any(
            phrase in text for phrase in ("run the same", "try again", "retry", "same command")
        )

    is_retry = features["exact_retry"]
    if actions_lower:
        features["minor_variant_only"] = not is_retry and any(
            "slight" in a or "minor" in a or "tweak" in a or "adjust flag" in a
            for a in actions_lower
        )
    else:
        features["minor_variant_only"] = not is_retry and any(
            phrase in text for phrase in ("slightly", "minor variation", "tweak", "adjust the flag")
        )

    return features


def _extract_verification_features(
    text: str,
    tool_calls_lower: list[str],
    actions_lower: list[str],
    verify_points: list[int],
) -> dict[str, bool]:
    """Extract all verification-related features."""
    features: dict[str, bool] = {}

    if verify_points:
        total_steps = len(tool_calls_lower) or len(actions_lower) or 3
        features["verifies_after_each"] = len(verify_points) >= total_steps - 1
        features["verifies_after_some"] = 1 <= len(verify_points) < total_steps - 1
        features["no_verification_mentioned"] = False
        features["batch_all_then_verify"] = (
            len(verify_points) == 1 and verify_points[0] >= total_steps - 1
        )
        features["mentions_verification"] = True
    elif tool_calls_lower:
        verify_calls = [
            t for t in tool_calls_lower if any(v in t for v in ("test", "pytest", "verify"))
        ]
        features["verifies_after_each"] = len(verify_calls) >= 2
        features["verifies_after_some"] = len(verify_calls) == 1
        features["no_verification_mentioned"] = len(verify_calls) == 0
        features["batch_all_then_verify"] = (
            len(verify_calls) == 1
            and tool_calls_lower.index(verify_calls[0]) == len(tool_calls_lower) - 1
        )
        features["mentions_verification"] = len(verify_calls) > 0
    else:
        verify_mentioned = any(v in text for v in _VERIFY_INDICATORS)
        features["mentions_verification"] = verify_mentioned
        features["verifies_after_each"] = "after each" in text or "between each" in text
        features["verifies_after_some"] = verify_mentioned and not features["verifies_after_each"]
        features["no_verification_mentioned"] = not verify_mentioned
        features["batch_all_then_verify"] = verify_mentioned and (
            "after all" in text or "at the end" in text
        )

    return features


def _extract_reference_features(
    text: str,
    constraint_refs: list[str],
) -> dict[str, bool]:
    """Extract constraint reference and previous-attempt features."""
    features: dict[str, bool] = {}

    if constraint_refs:
        features["references_previous_attempts"] = True
        features["references_both_failures"] = len(constraint_refs) >= 2
        features["addresses_known_constraints"] = True
        features["ignores_previous_errors"] = False
    else:
        ref_count = sum(
            1
            for phrase in (
                "attempt 1",
                "attempt 2",
                "previous",
                "already failed",
                "first error",
                "second error",
                "earlier",
            )
            if phrase in text
        )
        features["references_previous_attempts"] = ref_count >= 1
        features["references_both_failures"] = ref_count >= 2
        features["addresses_known_constraints"] = (
            "constraint" in text or "because" in text and ref_count >= 1
        )
        features["ignores_previous_errors"] = ref_count == 0

    return features


def _extract_reading_order_features(
    text: str,
    tool_calls_lower: list[str],
    actions_lower: list[str],
) -> dict[str, bool]:
    """Extract reads_docs_first, reads_config_first, reads_existing_code, jumps_to_fix."""
    features: dict[str, bool] = {}

    if actions_lower:
        first_action = actions_lower[0]
        features["reads_docs_first"] = any(
            d in first_action for d in ("contributing", "readme", "docs", "guide", "build-guide")
        )
        features["reads_config_first"] = any(
            c in first_action
            for c in ("pyproject", "makefile", "config", "toml", "yaml", "yml", "ci")
        )
        features["reads_existing_code"] = any(
            c in first_action for c in ("src/", "commands/", "existing", "list_cmd", "add_cmd")
        )
        features["jumps_to_fix"] = any(f in first_action for f in _FIX_INDICATORS)
    elif tool_calls_lower:
        first_tc = tool_calls_lower[0]
        features["reads_docs_first"] = any(
            d in first_tc for d in ("contributing", "readme", "docs", "guide")
        )
        features["reads_config_first"] = any(
            c in first_tc for c in ("pyproject", "makefile", "config", "toml", "yaml")
        )
        features["reads_existing_code"] = "read" in first_tc or "grep" in first_tc
        features["jumps_to_fix"] = any(f in first_tc for f in ("edit", "write", "bash"))
    else:
        features["reads_docs_first"] = any(
            d in text[:200] for d in ("contributing", "readme", "documentation", "guide")
        )
        features["reads_config_first"] = any(
            c in text[:200] for c in ("pyproject", "makefile", "config", "entry point")
        )
        features["reads_existing_code"] = any(
            c in text[:200] for c in ("existing command", "look at", "read the", "examine")
        )
        fix_pos = _first_indicator_pos(text, _FIX_INDICATORS)
        read_pos = _first_indicator_pos(text, _READ_INDICATORS)
        features["jumps_to_fix"] = fix_pos >= 0 and (read_pos == -1 or fix_pos < read_pos)

    return features


def _extract_root_cause_features(
    task: ProbeTask,
    response: dict[str, Any],
) -> dict[str, bool]:
    """Extract identifies_root_cause and follows_misleading_error."""
    features: dict[str, bool] = {}
    features["identifies_root_cause"] = _check_root_cause_identification(task, response)
    features["follows_misleading_error"] = _check_misleading_error_follow(
        task,
        response,
        features["identifies_root_cause"],
    )
    return features


def _check_root_cause_identification(task: ProbeTask, response: dict[str, Any]) -> bool:
    """Check if the model identified the root cause for error-reading tasks."""
    if task.id != "t1_error_reading":
        return False

    text = (response.get("text") or "").lower()
    constraint_refs = response.get("constraint_refs") or []
    all_refs = " ".join(constraint_refs).lower() + " " + text

    root_cause_terms: dict[str, list[str]] = {
        "t1_v1": ["shadow", "loop variable", "overwrite", "label", "line 5", "line 4"],
        "t1_v2": [
            "strip",
            "whitespace",
            "leading space",
            "partition",
            "line 4",
            "line 10",
        ],
    }

    for _variant_id, terms in root_cause_terms.items():
        if any(term in all_refs for term in terms):
            return True
    return False


def _check_misleading_error_follow(
    task: ProbeTask,
    response: dict[str, Any],
    found_root_cause: bool,
) -> bool:
    """Check if the model followed the misleading error message."""
    if task.id != "t1_error_reading":
        return False

    if found_root_cause:
        return False

    text = (response.get("text") or "").lower()
    actions = [a.lower() for a in (response.get("actions") or [])]
    all_text = text + " " + " ".join(actions)

    misleading_terms: dict[str, list[str]] = {
        "t1_v1": ["line 15", "str.*int", "type error", "typeerror", "type conversion"],
        "t1_v2": ["line 22", "comparison", "string comparison"],
    }

    for _variant_id, terms in misleading_terms.items():
        if any(term in all_text for term in terms):
            return True
    return False


# ── Main Extraction ─────────────────────────────────────────────────


def extract_features_for_task(
    task: ProbeTask,
    response: dict[str, Any],
) -> dict[str, bool]:
    """Extract behavioral features from a structured probe response.

    Scores from action traces first, text second. Each sub-extractor
    handles one group of related features.
    """
    text = (response.get("text") or "").lower()
    tool_calls = response.get("tool_calls") or []
    actions = response.get("actions") or []
    retry_count = response.get("retry_count")
    verify_points = response.get("verify_points") or []
    constraint_refs = response.get("constraint_refs") or []

    tool_calls_lower = [t.lower() for t in tool_calls]
    actions_lower = [a.lower() for a in actions]

    features: dict[str, bool] = {}

    features["read_before_edit"] = _extract_read_before_edit(
        text,
        tool_calls_lower,
        actions_lower,
    )

    features.update(
        _extract_retry_features(
            text,
            tool_calls_lower,
            actions_lower,
            retry_count,
        )
    )

    features.update(
        _extract_verification_features(
            text,
            tool_calls_lower,
            actions_lower,
            verify_points,
        )
    )

    features.update(_extract_reference_features(text, constraint_refs))

    features["minor_variant_of_attempt2"] = not features.get(
        "references_both_failures", False
    ) and features.get("minor_variant_only", False)

    features.update(
        _extract_reading_order_features(
            text,
            tool_calls_lower,
            actions_lower,
        )
    )

    features.update(_extract_root_cause_features(task, response))

    return features
