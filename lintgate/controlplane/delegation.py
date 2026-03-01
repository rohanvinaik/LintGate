"""Sub-agent delegation suitability scoring (#195).

Annotates ControlPlane findings with delegation safety scores so agents
know which findings can be safely dispatched to sub-agents and which
require the lead agent's cumulative project context.

Scoring is deterministic — no LLM calls, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DelegationSuitability:
    """Per-finding delegation safety annotation."""

    score: float  # 0.0 (must be lead) to 1.0 (trivially delegatable)
    category: str  # "high" | "medium" | "low"
    reason: str
    requires_context: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "category": self.category,
            "reason": self.reason,
            "requires_context": self.requires_context,
        }


# Finding kinds that are purely mechanical single-file fixes
MECHANICAL_FIXES: set[str] = {
    # Ruff / lint fixes
    "E401",  # multiple imports on one line
    "E711",  # comparison to None
    "E712",  # comparison to True/False
    "F401",  # unused import
    "F841",  # unused variable
    "I001",  # import order
    "UP006",  # use type instead of Type
    "UP007",  # use X | Y instead of Union
    "UP035",  # deprecated import
    "C4",  # unnecessary comprehension (prefix match)
    "SIM118",  # use `key in dict` instead of `key in dict.keys()`
    # Performance checks
    "PERF001",  # quadratic membership
    "PERF002",  # redundant materialization
    "PERF003",  # unnecessary sort
    "PERF009",  # redundant list copy
    "PERF010",  # dict.items() unused value
    "PERF011",  # unnecessary list wrap
}

# Finding kinds that depend on project-wide conventions
CONVENTION_DEPENDENT: set[str] = {
    "N801",  # class name naming
    "N802",  # function name naming
    "N803",  # argument name naming
    "N806",  # variable name naming
    "STRUCT006",  # cross-file patterns
}

# Finding kinds that involve structural decomposition
STRUCTURAL_DECOMPOSITION: set[str] = {
    "C901",  # cognitive complexity
    "PLR0912",  # too many branches
    "PLR0915",  # too many statements
    "STRUCT002",  # module size
}


def compute_delegation_suitability(
    finding: dict[str, Any],
    import_graph: dict[str, list[str] | set[str]] | None = None,
    file_map: dict[str, str] | None = None,
) -> DelegationSuitability:
    """Compute delegation suitability for a single finding.

    Args:
        finding: Finding dict with at least ``kind``, ``severity``, ``file``.
        import_graph: Module → imported modules (forward graph).
        file_map: Module name → file path mapping.

    Returns:
        DelegationSuitability with score, category, reason, and required context.
    """
    kind = str(finding.get("kind", ""))
    severity = str(finding.get("severity", "informational"))
    fpath = str(finding.get("file", ""))
    channel = str(finding.get("channel", ""))

    score = 0.5
    factors: list[str] = []
    required_context: list[str] = []

    # Mechanical fixes: high delegation safety
    if _is_mechanical(kind):
        score += 0.4
        factors.append("mechanical fix")

    # Convention-dependent: low delegation safety
    if kind in CONVENTION_DEPENDENT:
        score -= 0.3
        factors.append("convention-dependent")
        required_context.append("project naming conventions")

    # Structural decomposition: needs understanding
    if kind in STRUCTURAL_DECOMPOSITION:
        score -= 0.2
        factors.append("structural decomposition")
        required_context.append("understanding of function purpose")

    # Cross-file impact from import graph
    if import_graph and file_map:
        dependents = _count_downstream_dependents(fpath, import_graph, file_map)
        if dependents > 0:
            score -= min(0.3, dependents * 0.1)
            factors.append(f"{dependents} downstream dependents")
            required_context.append("import patterns from prior fixes")

    # Blocking severity: harder to delegate safely
    if severity == "blocking":
        score -= 0.1
        factors.append("blocking severity")

    # Behavior channel findings: never delegatable
    if channel == "behavior":
        score = 0.0
        factors = ["behavioral signal (lead-agent only)"]
        required_context = ["full session context"]

    score = max(0.0, min(1.0, score))
    category = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
    reason = ". ".join(factors) if factors else "default score"

    return DelegationSuitability(
        score=score,
        category=category,
        reason=reason,
        requires_context=required_context,
    )


def annotate_findings_with_suitability(
    findings: list[dict[str, Any]],
    details: dict[str, Any],
) -> None:
    """Annotate a list of finding dicts in-place with delegation_suitability.

    Extracts import graph from the structure channel in the run details
    for cross-file impact scoring.
    """
    import_graph, file_map = _extract_graph_from_details(details)

    for finding in findings:
        suitability = compute_delegation_suitability(finding, import_graph, file_map)
        finding["delegation_suitability"] = suitability.to_dict()


def _extract_graph_from_details(
    details: dict[str, Any],
) -> tuple[dict[str, list[str] | set[str]], dict[str, str]]:
    """Extract import graph from saved run details."""
    channels = details.get("channels", {})
    structure = channels.get("structure", {})
    metrics = structure.get("metrics", {})
    ig = metrics.get("_import_graph", {})
    fm = metrics.get("_file_map", {})
    if isinstance(ig, dict) and isinstance(fm, dict):
        return ig, fm
    return {}, {}


def _is_mechanical(kind: str) -> bool:
    """Check if a finding kind is a mechanical fix (exact or prefix match)."""
    if kind in MECHANICAL_FIXES:
        return True
    # Prefix matching for families (e.g., C4xx)
    return any(len(prefix) <= 3 and kind.startswith(prefix) for prefix in MECHANICAL_FIXES)


def _count_downstream_dependents(
    filepath: str,
    import_graph: dict[str, list[str] | set[str]],
    file_map: dict[str, str],
) -> int:
    """Count how many project modules import the module at filepath."""
    # Find module name for this filepath
    path_to_module: dict[str, str] = {}
    for mod, path in file_map.items():
        path_to_module[path] = mod

    module = path_to_module.get(filepath, "")
    if not module:
        return 0

    # Count modules that import this one
    count = 0
    for _mod, imports in import_graph.items():
        if module in (imports if isinstance(imports, set) else set(imports)):
            count += 1
    return count
