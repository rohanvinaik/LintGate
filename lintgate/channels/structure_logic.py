"""Structure channel — cycle detection, size distribution, and cohesion checks.

Core STRUCT checks: STRUCT001 (cycles), STRUCT002 (size skew), STRUCT004 (cohesion).
Orphan detection (STRUCT003) and file discovery live in structure_orphans.py
and structure_discovery.py respectively.
"""

from __future__ import annotations

import os
import statistics
from collections import defaultdict
from typing import Any

from lintgate.types import LintIssue

# Re-export discovery symbols from the extracted module
from .structure_discovery import (  # noqa: F401
    _NESTED_SUBPROJECT_MARKERS,
    _build_import_graph,
    _count_loc,
    _detect_nested_subproject_roots,
    _discover_python_files,
)

# Re-export orphan/re-export symbols from the extracted module
from .structure_orphans import (  # noqa: F401
    _build_reexport_map,
    _check_orphans,
    _detect_reexports,
    _is_orphan_excluded,
    _parse_node_reexports,
)

# ── Constants ────────────────────────────────────────────────────────────

# Minimum number of Python files for analysis to be meaningful
_MIN_FILES_FOR_SIZE_ANALYSIS = 5
_MIN_FILES_FOR_COHESION = 3

# Module-size skew thresholds (quantile-based)
_P90_P50_WARNING_RATIO = 5.0  # p90/p50 >= 5 → informational finding
_ABSOLUTE_LOC_FLOOR = 50  # Ignore files smaller than this for outlier analysis

# Package cohesion
_MIN_IMPORTS_FOR_COHESION = 3  # Skip packages with fewer total imports

# Config/build files that indicate structural changes when edited
_STRUCTURAL_CONFIG_FILES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "mypy.ini",
        ".mypy.ini",
        ".ruff.toml",
        "ruff.toml",
        "tox.ini",
        ".flake8",
        ".pylintrc",
        "MANIFEST.in",
    }
)


# ── STRUCT001: Import Cycles ─────────────────────────────────────────────


def _check_import_cycles(
    import_graph: dict[str, set[str]],
    file_map: dict[str, str],
    project_root: str,
    deferred_edges: set[tuple[str, str]] | None = None,
) -> list[LintIssue]:
    """Detect import cycles using DFS and classify as hard or soft.

    Hard cycles: all edges are module-level imports — will crash at import time.
    Soft cycles: at least one edge is a deferred import (inside a function body)
    — the code runs fine but the cycle indicates structural coupling.

    Soft cycles are downgraded to informational with lower confidence.
    """
    deferred_edges = deferred_edges or set()
    cycles = _find_cycles(import_graph)
    findings: list[LintIssue] = []
    seen_cycles: set[frozenset[str]] = set()

    for cycle in cycles:
        cycle_key = frozenset(cycle)
        if cycle_key in seen_cycles:
            continue
        seen_cycles.add(cycle_key)

        relevant_files = [m for m in cycle if m in file_map]
        if not relevant_files:
            continue

        cycle_str = " → ".join(cycle + [cycle[0]])
        filepath = file_map.get(relevant_files[0], "")

        classification, deferred_info = _classify_cycle(cycle, deferred_edges)

        if classification == "soft":
            message = f"Soft import cycle (deferred import): {cycle_str}"
            severity = "informational"
            confidence = 0.5
            evidence: dict[str, object] = {
                "cycle": cycle,
                "length": len(cycle),
                "code": "STRUCT001",
                "classification": "soft",
                "deferred_edges": deferred_info,
            }
            suggestions = [
                "Soft cycle — code runs fine but indicates structural coupling",
                "Consider extracting shared types to break the dependency",
            ]
        else:
            message = f"Import cycle detected: {cycle_str}"
            severity = "warning" if len(cycle) > 2 else "informational"
            confidence = 0.9
            evidence = {
                "cycle": cycle,
                "length": len(cycle),
                "code": "STRUCT001",
                "classification": "hard",
            }
            suggestions = [
                "Break the cycle by extracting shared types to a common module",
                "Use lazy imports (import inside function) if unavoidable",
                "Consider dependency injection to decouple modules",
            ]

        findings.append(
            LintIssue(
                linter="structure_channel",
                kind="STRUCT001",
                message=message,
                file=filepath,
                severity=severity,
                confidence=confidence,
                evidence=evidence,
                suggestions=suggestions,
            )
        )

    return findings


def _classify_cycle(
    cycle: list[str], deferred_edges: set[tuple[str, str]]
) -> tuple[str, list[str]]:
    """Classify an import cycle as 'hard' or 'soft'.

    Returns (classification, deferred_edge_descriptions).
    A cycle is 'soft' if at least one edge is a deferred import.
    """
    deferred_info: list[str] = []
    for i, module in enumerate(cycle):
        next_module = cycle[(i + 1) % len(cycle)]
        if (module, next_module) in deferred_edges:
            deferred_info.append(f"{module} → {next_module}")
    classification = "soft" if deferred_info else "hard"
    return classification, deferred_info


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Find all cycles in a directed graph using DFS (max depth 5)."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()

    def dfs(node: str) -> None:
        if len(path) > 5:
            return
        if node in path_set:
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:])
            return
        if node in visited:
            return

        path.append(node)
        path_set.add(node)

        for neighbor in graph.get(node, set()):
            dfs(neighbor)

        path.pop()
        path_set.discard(node)
        visited.add(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return cycles


# ── STRUCT002: Module-Size Distribution ──────────────────────────────────


def _check_module_size_distribution(
    file_loc: dict[str, int],
    project_root: str,
) -> list[LintIssue]:
    """Detect module-size skew using quantile-based thresholds.

    Uses p90/p50 ratio to identify concentration of complexity.
    Reports individual outliers that exceed p90 threshold.
    """
    findings: list[LintIssue] = []

    # Filter to files with meaningful content
    meaningful_locs = {fp: loc for fp, loc in file_loc.items() if loc >= _ABSOLUTE_LOC_FLOOR}

    if len(meaningful_locs) < _MIN_FILES_FOR_SIZE_ANALYSIS:
        return findings

    loc_values = sorted(meaningful_locs.values())
    p50 = statistics.median(loc_values)
    p90 = _percentile(loc_values, 0.90)

    if p50 == 0:
        return findings

    ratio = p90 / p50

    if ratio >= _P90_P50_WARNING_RATIO:
        # Find the specific outliers (files above p90)
        outliers = [(fp, loc) for fp, loc in meaningful_locs.items() if loc >= p90]
        outliers.sort(key=lambda x: -x[1])

        # Report the distribution finding
        findings.append(
            LintIssue(
                linter="structure_channel",
                kind="STRUCT002",
                message=(
                    f"Module-size concentration: p90/p50 ratio is {ratio:.1f}x "
                    f"(p50={int(p50)} LOC, p90={int(p90)} LOC). "
                    f"{len(outliers)} module(s) above p90 threshold."
                ),
                severity="informational",
                confidence=0.85,
                evidence={
                    "code": "STRUCT002",
                    "p50_loc": int(p50),
                    "p90_loc": int(p90),
                    "ratio": round(ratio, 2),
                    "sample_size": len(meaningful_locs),
                    "outlier_count": len(outliers),
                    "outliers": [
                        {
                            "file": os.path.relpath(fp, project_root),
                            "loc": loc,
                            "ratio_to_median": round(loc / p50, 1),
                        }
                        for fp, loc in outliers[:5]  # Top 5
                    ],
                },
                suggestions=[
                    "Consider splitting large modules into focused sub-modules",
                    "Check if the largest modules are accumulating unrelated responsibilities",
                ],
            )
        )

    return findings


def _percentile(sorted_data: list[int], pct: float) -> float:
    """Compute percentile from sorted data using linear interpolation."""
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    if n == 1:
        return float(sorted_data[0])

    k = (n - 1) * pct
    f = int(k)
    c = f + 1
    if c >= n:
        return float(sorted_data[-1])

    d0 = sorted_data[f]
    d1 = sorted_data[c]
    return d0 + (d1 - d0) * (k - f)


# ── STRUCT004: Package Cohesion ──────────────────────────────────────────


def _check_package_cohesion(
    import_graph: dict[str, set[str]],
    file_map: dict[str, str],
    project_root: str,
) -> list[LintIssue]:
    """Measure package cohesion: ratio of intra-package to total imports.

    A well-structured package has high intra-package imports relative
    to inter-package imports. Low cohesion suggests the package boundary
    isn't doing useful work.

    Skips packages with fewer than _MIN_IMPORTS_FOR_COHESION total imports
    to avoid noisy findings on tiny packages.
    """
    findings: list[LintIssue] = []

    # Group modules by their top-level package
    packages: dict[str, list[str]] = defaultdict(list)
    for module in file_map:
        parts = module.split(".")
        if len(parts) >= 2:
            # Use the top-level package name
            pkg = parts[0]
            packages[pkg].append(module)
        # Skip top-level modules (not in a package)

    if len(packages) < 2:
        # Need at least 2 packages to measure cohesion meaningfully
        return findings

    for pkg_name, pkg_modules in packages.items():
        pkg_module_set = set(pkg_modules)
        # Also include the package prefix for matching
        pkg_prefix = pkg_name + "."

        intra_count = 0
        inter_count = 0

        for module in pkg_modules:
            imports = import_graph.get(module, set())
            for imp in imports:
                if imp in pkg_module_set or imp.startswith(pkg_prefix) or imp == pkg_name:
                    intra_count += 1
                else:
                    inter_count += 1

        total = intra_count + inter_count
        if total < _MIN_IMPORTS_FOR_COHESION:
            continue

        cohesion_ratio = intra_count / total if total > 0 else 0.0

        # Low cohesion: more inter-package imports than intra-package
        if cohesion_ratio < 0.3 and total >= _MIN_IMPORTS_FOR_COHESION:
            findings.append(
                LintIssue(
                    linter="structure_channel",
                    kind="STRUCT004",
                    message=(
                        f"Low package cohesion in '{pkg_name}': "
                        f"{cohesion_ratio:.0%} intra-package imports "
                        f"({intra_count} intra / {inter_count} inter / {total} total). "
                        f"The package boundary may not be grouping related code."
                    ),
                    severity="informational",
                    confidence=0.7,
                    evidence={
                        "code": "STRUCT004",
                        "package": pkg_name,
                        "intra_imports": intra_count,
                        "inter_imports": inter_count,
                        "total_imports": total,
                        "cohesion_ratio": round(cohesion_ratio, 3),
                        "module_count": len(pkg_modules),
                    },
                    suggestions=[
                        f"Review whether '{pkg_name}' modules belong together",
                        "High inter-package coupling may indicate misplaced modules",
                        "Consider merging small low-cohesion packages with their dependents",
                    ],
                )
            )

    return findings


# ── Structure Snapshot (compact orientation) ─────────────────────────────


def _build_structure_snapshot(
    py_files: list[str],
    import_graph: dict[str, set[str]],
    file_map: dict[str, str],
    file_loc: dict[str, int],
    cycle_findings: list[LintIssue],
    size_findings: list[LintIssue],
    orphan_findings: list[LintIssue],
    cohesion_findings: list[LintIssue],
    project_root: str,
    *,
    module_fan_in: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a compact structure snapshot for controlplane_run output.

    This is the token-saving orientation data: layers, cycles, largest
    modules, orphan count — the mental model a senior engineer carries.
    """
    # Largest modules by LOC
    sorted_by_loc = sorted(file_loc.items(), key=lambda x: -x[1])
    largest = [
        {
            "file": os.path.relpath(fp, project_root),
            "loc": loc,
        }
        for fp, loc in sorted_by_loc[:3]
    ]

    # Package distribution
    packages: dict[str, int] = defaultdict(int)
    for module in file_map:
        parts = module.split(".")
        if len(parts) >= 2:
            packages[parts[0]] += 1
        else:
            packages["<top-level>"] += 1

    # LOC statistics
    all_locs = [loc for loc in file_loc.values() if loc > 0]
    median_loc = int(statistics.median(all_locs)) if all_locs else 0
    total_loc = sum(all_locs)

    snapshot: dict[str, Any] = {
        "file_count": len(py_files),
        "total_loc": total_loc,
        "median_module_loc": median_loc,
        "largest_modules": largest,
        "package_count": len(packages),
        "packages": dict(packages),
        "import_cycle_count": len(cycle_findings),
        "orphan_count": len(orphan_findings),
        "low_cohesion_packages": len(cohesion_findings),
        "checks_run": 4,
    }

    # Fan-in enrichment (Gap 1 — import fan-in surfaced)
    if module_fan_in:
        snapshot["zero_fan_in_count"] = sum(1 for v in module_fan_in.values() if v == 0)
        snapshot["high_fan_in_modules"] = [
            {"module": m, "fan_in": fi}
            for m, fi in sorted(module_fan_in.items(), key=lambda x: -x[1])[:3]
            if fi >= 2
        ]

    return snapshot
