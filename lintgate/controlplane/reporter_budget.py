"""Dynamic token budget computation for ControlPlane reporter.

Extracted from reporter.py -- computes proportional token budgets
based on finding volume. Worse code produces more findings which
need more tokens for signal fidelity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import ControlPlaneConfig, MeshResult

# ── Dynamic token budget ────────────────────────────────────────────────

# Per-section token costs (empirical from typical output)
_BUDGET_BASE = 300  # header + coherence + channel summary + close tag
_BUDGET_PER_BLOCKING = 75  # each blocking finding with evidence
_BUDGET_PER_WARNING = 50  # each warning finding
_BUDGET_PER_INFO = 20  # each informational finding (channel summary line)
_BUDGET_PER_REPAIR = 40  # each repair suggestion
_BUDGET_HARD_CAP = 12000  # common-sense upper bound for dynamic growth


def _compute_dynamic_budget(
    all_findings: list,
    mesh_result: MeshResult,
    config: ControlPlaneConfig,
) -> int:
    """Compute token budget proportional to finding volume.

    The budget scales with the actual content that needs reporting.
    Worse code produces more findings, which need more tokens -- that's
    signal fidelity. The static hook_max_tokens serves as the floor
    for clean codebases.
    """
    blocking = sum(1 for f in all_findings if f.severity == "blocking")
    warnings = sum(1 for f in all_findings if f.severity == "warning")
    informational = sum(1 for f in all_findings if f.severity == "informational")
    repairs = sum(len(cr.repairs) for cr in mesh_result.channel_results)

    dynamic = (
        _BUDGET_BASE
        + blocking * _BUDGET_PER_BLOCKING
        + warnings * _BUDGET_PER_WARNING
        + informational * _BUDGET_PER_INFO
        + repairs * _BUDGET_PER_REPAIR
    )

    # Dynamic budget can grow with issue volume, but never unbounded.
    floor = max(1, int(config.token_policy.hook_max_tokens))
    effective_floor = min(floor, _BUDGET_HARD_CAP)
    dynamic_capped = min(dynamic, _BUDGET_HARD_CAP)
    return max(dynamic_capped, effective_floor)
