"""Agent-initiated signal tuning: structured suppression of persistent advisory findings.

Prevents trust erosion from findings the agent has reviewed and determined
non-actionable for the current project. Tunings are stored in
``.lintgate/signal_tunings.yaml``, expire after 10 sessions without refresh,
and are limited by eligibility rules (severity, confidence, recurrence).

Design: no new MCP tools — tuning flows through ``controlplane_agent_feedback``
(existing tool, extended with ``tuned_findings`` parameter) and is surfaced
via the PostToolUse hook when findings recur.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import LintIssue

# ── Tunable Allowlist ────────────────────────────────────────────────────

_TUNABLE_CODES: frozenset[str] = frozenset(
    {
        # Style/formatting
        "ruff_format/format",
        "E501",
        "W291",
        "W292",
        "W293",
        # Docstring presence
        "D100",
        "D101",
        "D102",
        "D103",
        # Complexity thresholds (advisory)
        "C901",
        "STRUCT003",
        "STRUCT004",
        # Pattern detection
        "STRUCT005",
        "STRUCT006",
        # Performance hints (low confidence)
        "PERF001",
        "PERF002",
        "PERF004",
        # Arg count
        "PLR0913",
        # File structure (advisory)
        "too-many-functions",
        "file-too-long",
    }
)

# Never tunable regardless of severity
_BLOCKED_CODES: frozenset[str] = frozenset(
    {
        "F821",
        "E999",
        "B603",
        "B608",
        "F811",
        "E402",
    }
)

# ── Tuning Actions ───────────────────────────────────────────────────────

VALID_ACTIONS = frozenset({"suppress", "downgrade", "reset"})

# ── Storage ──────────────────────────────────────────────────────────────


def _tunings_path(project_root: str) -> str:
    return os.path.join(project_root, ".lintgate", "signal_tunings.yaml")


def load_tunings(project_root: str) -> list[dict[str, Any]]:
    """Load signal tunings from disk. Returns empty list if not found."""
    path = _tunings_path(project_root)
    if not os.path.isfile(path):
        return []
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return []
        return data.get("tunings", [])
    except Exception:
        return []


def save_tunings(project_root: str, tunings: list[dict[str, Any]]) -> str:
    """Save signal tunings to disk. Returns the file path written."""
    path = _tunings_path(project_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        import yaml

        data = {"version": 1, "tunings": tunings}
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    except Exception:
        pass
    return path


# ── Eligibility ──────────────────────────────────────────────────────────


def is_tunable(
    finding_code: str,
    severity: str,
    confidence: float,
    recurrence_count: int,
    *,
    policy: dict[str, Any] | None = None,
) -> bool:
    """Check if a finding is eligible for agent tuning.

    Eligibility requires ALL of:
    - Severity is ``informational`` (never blocking or warning)
    - Confidence < 0.8
    - Finding code is in the tunable allowlist
    - Finding code is not in the blocked list
    - Recurrence count >= 3 (prevents premature dismissal)
    - Agent tuning is enabled in policy (default: True)
    """
    policy = policy or {}
    if not policy.get("allow_agent_tuning", True):
        return False

    blocked = set(_BLOCKED_CODES)
    blocked.update(policy.get("blocked_codes", []))
    if finding_code in blocked:
        return False

    if severity != "informational":
        return False
    if confidence >= 0.8:
        return False

    allowed = set(_TUNABLE_CODES)
    allowed.update(policy.get("tunable_codes_override", []))
    if finding_code not in allowed:
        return False

    min_recurrence = policy.get("min_recurrence", 3)
    return recurrence_count >= min_recurrence


def build_finding_signature(issue: LintIssue) -> str:
    """Build a stable signature for matching tunings to findings.

    Format: ``channel|code|relative_file``
    """
    file_part = os.path.basename(issue.file) if issue.file else "unknown"
    return f"{issue.linter}|{issue.kind}|{file_part}"


# ── Tuning Application ──────────────────────────────────────────────────


def match_tuning(
    issue: LintIssue, tunings: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Find a matching active tuning for a finding."""
    sig = build_finding_signature(issue)
    for tuning in tunings:
        if tuning.get("signature") == sig and tuning.get("action") != "reset":
            return tuning
    return None


def apply_tuning(
    project_root: str,
    signature: str,
    action: str,
    rationale: str,
    recurrence_count: int = 0,
) -> dict[str, Any]:
    """Apply a tuning decision. Returns the applied tuning record."""
    if action not in VALID_ACTIONS:
        return {
            "error": f"Invalid action: {action}. Must be one of {sorted(VALID_ACTIONS)}"
        }

    tunings = load_tunings(project_root)
    now = datetime.now(timezone.utc).isoformat()

    if action == "reset":
        tunings = [t for t in tunings if t.get("signature") != signature]
        save_tunings(project_root, tunings)
        return {"signature": signature, "action": "reset", "removed": True}

    # Check project-level tuning cap
    active_count = sum(1 for t in tunings if t.get("action") != "reset")
    max_tunings = 20  # Default cap
    if active_count >= max_tunings:
        return {
            "error": f"Tuning cap reached ({max_tunings}). "
            "Use lintgate.yaml exemptions for bulk suppression.",
        }

    # Upsert: update existing or create new
    existing = next((t for t in tunings if t.get("signature") == signature), None)
    if existing:
        existing["action"] = action
        existing["rationale"] = rationale
        existing["last_refresh"] = now
        existing["sessions_since_tune"] = 0
    else:
        tunings.append(
            {
                "signature": signature,
                "action": action,
                "rationale": rationale,
                "created": now,
                "last_refresh": now,
                "recurrence_at_tune": recurrence_count,
                "sessions_since_tune": 0,
            }
        )

    save_tunings(project_root, tunings)
    return {"signature": signature, "action": action, "applied": True}


def filter_tuned_issues(
    issues: list[LintIssue], project_root: str
) -> tuple[list[LintIssue], int]:
    """Apply tunings to a list of issues. Returns (filtered_issues, tuned_count).

    - ``suppress``: issue is removed from the list
    - ``downgrade``: issue severity set to ``"tuned"`` (excluded from coherence)
    """
    tunings = load_tunings(project_root)
    if not tunings:
        return issues, 0

    result: list[LintIssue] = []
    tuned_count = 0

    for issue in issues:
        tuning = match_tuning(issue, tunings)
        if tuning is None:
            result.append(issue)
            continue

        action = tuning.get("action")
        if action == "suppress":
            tuned_count += 1
            continue  # Remove from output
        elif action == "downgrade":
            issue.severity = "informational"
            issue.evidence = dict(issue.evidence) if issue.evidence else {}
            issue.evidence["tuned"] = True
            issue.evidence["tuning_rationale"] = tuning.get("rationale", "")
            tuned_count += 1

        result.append(issue)

    return result, tuned_count
