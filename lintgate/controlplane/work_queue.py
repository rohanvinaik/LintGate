"""Guided work queue — dependency-ordered finding execution (#192).

Orders findings leaf-first through the import graph so agents work on
foundation modules before their dependents.  Identifies parallelizable
groups (same-tier, single-file findings with no cross-file impact).

Pure computation — no I/O, no LLM calls, no subprocess calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueuedFinding:
    """A single work-queue entry grouping findings for one file."""

    file: str
    finding_ids: list[str] = field(default_factory=list)
    tier: int = 0
    severity: str = "informational"
    locality: str = "single_file"
    depends_on: list[str] = field(default_factory=list)
    delegation_safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "finding_ids": self.finding_ids,
            "tier": self.tier,
            "severity": self.severity,
            "locality": self.locality,
            "depends_on": self.depends_on,
            "delegation_safe": self.delegation_safe,
        }


@dataclass
class WorkQueue:
    """Ordered queue of findings with parallelization metadata."""

    items: list[QueuedFinding] = field(default_factory=list)
    total_files: int = 0
    parallelizable_groups: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_files": self.total_files,
            "parallelizable_groups": self.parallelizable_groups,
        }


_SEVERITY_ORDER = {"blocking": 0, "warning": 1, "informational": 2}


def build_work_queue(
    findings: list[Any],
    import_graph: dict[str, list[str] | set[str]] | None = None,
    file_map: dict[str, str] | None = None,
) -> WorkQueue:
    """Build a dependency-ordered work queue from findings.

    Args:
        findings: List of finding dicts with at least ``file``, ``kind``,
            ``severity`` keys (from ``build_finding_index`` values or
            LintIssue-like objects).
        import_graph: Module → imported modules (forward graph).
            If None, all files get tier 0.
        file_map: Module name → file path mapping.

    Returns:
        WorkQueue with items sorted by tier (asc), severity (blocking first),
        locality (single_file first).
    """
    file_map = file_map or {}
    import_graph = import_graph or {}

    # Invert file_map: filepath → module name
    path_to_module: dict[str, str] = {}
    for mod, path in file_map.items():
        path_to_module[path] = mod

    # Compute tiers
    project_modules = set(file_map.keys())
    tiers = _compute_dependency_tiers(import_graph, project_modules)

    # Group findings by file
    file_findings: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        fpath = _extract_file(f)
        if not fpath:
            continue
        file_findings.setdefault(fpath, []).append(f)

    # Collect all finding files for cross-file impact detection
    all_finding_files = set(file_findings.keys())

    # Build queued findings
    items: list[QueuedFinding] = []
    for fpath, f_list in file_findings.items():
        module = path_to_module.get(fpath, "")
        tier = tiers.get(module, 0) if module else 0

        # Highest severity wins
        severity = _highest_severity(f_list)

        # Locality: does this file's module import other finding-files?
        locality = _compute_locality(
            module, import_graph, path_to_module, all_finding_files
        )

        # depends_on: other finding-files that this module imports
        depends_on = _compute_depends_on(
            module, import_graph, file_map, all_finding_files
        )

        # Delegation safety: tier-0, single_file, non-blocking
        delegation_safe = (
            tier == 0 and locality == "single_file" and severity != "blocking"
        )

        finding_ids = [_extract_kind(f) for f in f_list]

        items.append(
            QueuedFinding(
                file=fpath,
                finding_ids=finding_ids,
                tier=tier,
                severity=severity,
                locality=locality,
                depends_on=depends_on,
                delegation_safe=delegation_safe,
            )
        )

    # Sort: tier asc → severity asc (blocking=0 first) → locality (single first)
    items.sort(
        key=lambda q: (
            q.tier,
            _SEVERITY_ORDER.get(q.severity, 9),
            0 if q.locality == "single_file" else 1,
            q.file,
        )
    )

    # Build parallelizable groups: same-tier items that are all delegation_safe
    parallel_groups = _find_parallelizable_groups(items)

    return WorkQueue(
        items=items,
        total_files=len(items),
        parallelizable_groups=parallel_groups,
    )


def _compute_dependency_tiers(
    import_graph: dict[str, list[str] | set[str]],
    project_modules: set[str],
) -> dict[str, int]:
    """Compute dependency tiers from the import graph.

    Tier 0: modules that don't import any other project module (leaves).
    Tier N: modules whose deepest project-local import is tier N-1.
    Modules in cycles get max_tier + 1.
    """
    tiers: dict[str, int] = {}

    def _tier(module: str, visiting: frozenset[str]) -> int:
        if module in tiers:
            return tiers[module]
        if module in visiting:
            return -1  # Cycle detected

        imports = set(import_graph.get(module, [])) & project_modules
        if not imports:
            tiers[module] = 0
            return 0

        max_dep = 0
        cycle_found = False
        for dep in imports:
            t = _tier(dep, visiting | {module})
            if t < 0:
                cycle_found = True
            else:
                max_dep = max(max_dep, t)

        if cycle_found and module not in tiers:
            return -1  # Propagate cycle signal
        tiers[module] = max_dep + 1
        return tiers[module]

    for mod in project_modules:
        _tier(mod, frozenset())

    # Assign cycle modules to max_tier + 1
    max_tier = max(tiers.values(), default=0)
    for mod in project_modules:
        if mod not in tiers:
            tiers[mod] = max_tier + 1

    return tiers


def _compute_locality(
    module: str,
    import_graph: dict[str, list[str] | set[str]],
    path_to_module: dict[str, str],
    all_finding_files: set[str],
) -> str:
    """Determine if fixing this module affects other finding-files."""
    if not module:
        return "single_file"
    imports = set(import_graph.get(module, []))
    module_to_path = {v: k for k, v in path_to_module.items()}
    for imp in imports:
        imp_path = module_to_path.get(imp, "")
        if imp_path in all_finding_files:
            return "cross_file"
    return "single_file"


def _compute_depends_on(
    module: str,
    import_graph: dict[str, list[str] | set[str]],
    file_map: dict[str, str],
    all_finding_files: set[str],
) -> list[str]:
    """Return file paths of finding-files that this module imports."""
    if not module:
        return []
    imports = set(import_graph.get(module, []))
    deps = []
    for imp in imports:
        imp_path = file_map.get(imp, "")
        if imp_path and imp_path in all_finding_files:
            deps.append(imp_path)
    return sorted(deps)


def _find_parallelizable_groups(items: list[QueuedFinding]) -> list[list[str]]:
    """Find groups of delegation-safe items at the same tier."""
    tier_groups: dict[int, list[str]] = {}
    for item in items:
        if item.delegation_safe:
            tier_groups.setdefault(item.tier, []).append(item.file)

    return [files for files in tier_groups.values() if len(files) >= 2]


def _extract_file(finding: Any) -> str:
    """Extract file path from a finding (dict or object)."""
    if isinstance(finding, dict):
        return str(finding.get("file", ""))
    return str(getattr(finding, "file", ""))


def _extract_kind(finding: Any) -> str:
    """Extract kind/rule ID from a finding."""
    if isinstance(finding, dict):
        return str(finding.get("kind", ""))
    return str(getattr(finding, "kind", ""))


def _highest_severity(findings: list[Any]) -> str:
    """Return the highest severity among findings."""
    best = "informational"
    for f in findings:
        sev = (
            f.get("severity", "informational")
            if isinstance(f, dict)
            else getattr(f, "severity", "informational")
        )
        if _SEVERITY_ORDER.get(sev, 9) < _SEVERITY_ORDER.get(best, 9):
            best = sev
    return best
