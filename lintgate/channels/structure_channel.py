"""Structure channel — codebase structural analysis for ControlPlane.

Professional instinct modeled: A senior engineer maintains a mental model of
the codebase's shape — where the import graph tangles, which modules accumulate
too much responsibility, which files nobody references anymore. This channel
provides that awareness cheaply via AST-based symbolic analysis.

Four checks (Iteration 1):
  STRUCT001 — Import cycle detection (reuses architecture_checks DFS)
  STRUCT002 — Module-size distribution skew (quantile-based, p90/p50 ratio)
  STRUCT003 — Orphan detection (unreferenced files, excludes entrypoints/migrations)
  STRUCT004 — Package cohesion ratio (intra- vs. inter-package imports)

Design principles:
  - Symbolic only: AST-based, no ML, no external services, fully deterministic
  - Evidence-first: every finding includes machine-verifiable evidence payloads
  - Mostly informational: severity escalates only when corroborated by other channels
  - Minimum sample-size guards: skip analysis when file count is too small
  - Explicit false-positive exclusions: entrypoints, migrations, plugins, __init__.py
"""

from __future__ import annotations

import os
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.path_filters import is_backup_like_directory
from lintgate.types import LintIssue

# ── Constants ────────────────────────────────────────────────────────────

# Minimum number of Python files for analysis to be meaningful
_MIN_FILES_FOR_SIZE_ANALYSIS = 5
_MIN_FILES_FOR_COHESION = 3

# Module-size skew thresholds (quantile-based)
_P90_P50_WARNING_RATIO = 5.0  # p90/p50 >= 5 → informational finding
_ABSOLUTE_LOC_FLOOR = 50  # Ignore files smaller than this for outlier analysis

# Package cohesion
_MIN_IMPORTS_FOR_COHESION = 3  # Skip packages with fewer total imports

# Orphan detection: files/patterns to exclude
_ORPHAN_EXCLUDE_NAMES = frozenset(
    {
        "__init__",
        "__main__",
        "setup",
        "conftest",
        "manage",
        "wsgi",
        "asgi",
        "app",
        "main",
        "cli",
        "server",
        "hook",
    }
)

_ORPHAN_EXCLUDE_DIR_PARTS = frozenset(
    {
        "migrations",
        "alembic",
        "scripts",
        "bin",
        "plugins",
        "fixtures",
        "stubs",
        "tests",
        "test",
        "testing",
        "benchmarks",
    }
)

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


class StructureChannel:
    """Supervision channel for codebase structural analysis.

    Advisory only — structure findings are informational unless
    corroborated by other channels (convergent evidence escalation
    happens in the coherence engine, not here).
    """

    name = "structure"
    timeout_ms = 5000
    blocking_capable = False  # Advisory

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run on MCP invocations; on hooks, only for structurally relevant changes.

        Hook gating: full project structural scans are expensive and noisy for
        routine edits. Only run when the change is likely to affect structure:
        - risk_level is "structural" or "architectural"
        - Package boundary files (__init__.py) changed
        - Config/build files changed (pyproject.toml, mypy.ini, etc.)
        - Import-only changes (may affect import graph)
        - Module moves/renames (class_structure_changed)
        """
        if event.surface == "mcp":
            return True
        classification = event.change_classification
        if classification is None:
            return False
        if classification.risk_level in ("none", "cosmetic"):
            return False

        # Always run for structural/architectural risk levels
        if classification.risk_level in ("structural", "architectural"):
            return True

        # Check for structurally relevant files in the changeset
        for filepath in event.files_changed:
            basename = os.path.basename(filepath)
            # Package boundary files
            if basename == "__init__.py":
                return True
            # Config/build files
            if basename in _STRUCTURAL_CONFIG_FILES:
                return True

        # Import-only changes affect the import graph
        if classification.import_only:
            return True

        # Class structure changes may indicate module reorganization
        return classification.class_structure_changed

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute structural analysis checks."""
        start = time.perf_counter()
        findings: list[LintIssue] = []

        project_root = event.project_root

        # Discover Python files
        py_files = _discover_python_files(project_root)

        if len(py_files) < _MIN_FILES_FOR_SIZE_ANALYSIS:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "too_few_files", "file_count": len(py_files)},
                duration_ms=elapsed_ms,
            )

        # Build import graph (shared across checks)
        import_graph, file_map, file_loc = _build_import_graph(py_files, project_root)

        # STRUCT001: Import cycle detection
        cycle_findings = _check_import_cycles(import_graph, file_map, project_root)
        findings.extend(cycle_findings)

        # STRUCT002: Module-size distribution skew
        size_findings = _check_module_size_distribution(file_loc, project_root)
        findings.extend(size_findings)

        # STRUCT003: Orphan detection
        orphan_findings = _check_orphans(py_files, import_graph, file_map, project_root)
        findings.extend(orphan_findings)

        # STRUCT004: Package cohesion
        cohesion_findings = _check_package_cohesion(import_graph, file_map, project_root)
        findings.extend(cohesion_findings)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Build structure snapshot for compact output
        snapshot = _build_structure_snapshot(
            py_files,
            import_graph,
            file_map,
            file_loc,
            cycle_findings,
            size_findings,
            orphan_findings,
            cohesion_findings,
            project_root,
        )

        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            severity = "informational"
            if any(f.severity == "warning" for f in findings):
                severity = "warning"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            metrics=snapshot,
            duration_ms=elapsed_ms,
        )


# ── File Discovery ───────────────────────────────────────────────────────


def _discover_python_files(project_root: str) -> list[str]:
    """Discover Python files, excluding venvs, caches, and hidden directories."""
    py_files: list[str] = []
    exclude_dirs = {
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".eggs",
        "dist",
        "build",
        ".nox",
        ".pytest_cache",
    }

    for dirpath, dirnames, filenames in os.walk(project_root):
        # Prune excluded directories in-place
        dirnames[:] = [
            d
            for d in dirnames
            if d not in exclude_dirs and not d.startswith(".") and not is_backup_like_directory(d)
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                py_files.append(os.path.join(dirpath, fn))

    return py_files


# ── Import Graph Construction ────────────────────────────────────────────


def _build_import_graph(
    py_files: list[str], project_root: str
) -> tuple[dict[str, set[str]], dict[str, str], dict[str, int]]:
    """Build the project import graph and collect LOC per file.

    Returns:
        import_graph: module_name → set of imported module_names
        file_map: module_name → file_path
        file_loc: file_path → line_count
    """
    from lintgate.linters.architecture_checks._helpers import (
        extract_imports,
        filepath_to_module,
        is_project_local,
    )

    import_graph: dict[str, set[str]] = defaultdict(set)
    file_map: dict[str, str] = {}
    file_loc: dict[str, int] = {}

    for filepath in py_files:
        module = filepath_to_module(filepath, project_root)
        if not module:
            continue

        file_map[module] = filepath

        # Count LOC (non-blank, non-comment)
        loc = _count_loc(filepath)
        file_loc[filepath] = loc

        # Extract imports
        imports = extract_imports(filepath)
        for imp_module, _ in imports:
            if is_project_local(imp_module, project_root):
                import_graph[module].add(imp_module)

    return dict(import_graph), file_map, file_loc


def _count_loc(filepath: str) -> int:
    """Count non-blank, non-comment lines of code."""
    try:
        with open(filepath) as f:
            lines = f.readlines()
    except OSError:
        return 0

    loc = 0
    in_docstring = False
    for line in lines:
        stripped = line.strip()

        # Track triple-quoted docstrings
        if '"""' in stripped or "'''" in stripped:
            count = stripped.count('"""') + stripped.count("'''")
            if count == 1:
                in_docstring = not in_docstring
                continue
            # Opening and closing on same line
            continue

        if in_docstring:
            continue

        if stripped and not stripped.startswith("#"):
            loc += 1

    return loc


# ── STRUCT001: Import Cycles ─────────────────────────────────────────────


def _check_import_cycles(
    import_graph: dict[str, set[str]],
    file_map: dict[str, str],
    project_root: str,
) -> list[LintIssue]:
    """Detect import cycles using DFS.

    Reuses the cycle-detection algorithm from architecture_checks but
    emits structure-channel finding codes (STRUCT001).
    """
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

        findings.append(
            LintIssue(
                linter="structure_channel",
                kind="STRUCT001",
                message=f"Import cycle detected: {cycle_str}",
                file=filepath,
                severity="informational",
                confidence=0.9,
                evidence={
                    "cycle": cycle,
                    "length": len(cycle),
                    "code": "STRUCT001",
                },
                suggestions=[
                    "Break the cycle by extracting shared types to a common module",
                    "Use lazy imports (import inside function) if unavoidable",
                    "Consider dependency injection to decouple modules",
                ],
            )
        )

    return findings


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


# ── STRUCT003: Orphan Detection ──────────────────────────────────────────


def _check_orphans(
    py_files: list[str],
    import_graph: dict[str, set[str]],
    file_map: dict[str, str],
    project_root: str,
) -> list[LintIssue]:
    """Detect orphaned files — modules not imported by any other module.

    Excludes:
    - Entrypoints/scripts (__main__, manage, cli, app, server, main, etc.)
    - Migrations and alembic directories
    - Test files and test directories
    - __init__.py files
    - Plugin/discovery patterns
    - conftest.py
    - Files outside packages (top-level scripts)
    """
    findings: list[LintIssue] = []

    # Build the set of all imported modules
    all_imported: set[str] = set()
    for targets in import_graph.values():
        all_imported.update(targets)

    # Also count parent packages as "referenced" (e.g., if `foo.bar` is
    # imported, then the `foo` package is referenced)
    parent_refs: set[str] = set()
    for mod in all_imported:
        parts = mod.split(".")
        for i in range(1, len(parts)):
            parent_refs.add(".".join(parts[:i]))
    all_imported.update(parent_refs)

    for module, filepath in file_map.items():
        # Skip if this module is imported by something
        if module in all_imported:
            continue

        # Check exclusion rules
        if _is_orphan_excluded(filepath, module, project_root):
            continue

        # This module is not imported by anything in the project
        relpath = os.path.relpath(filepath, project_root)
        findings.append(
            LintIssue(
                linter="structure_channel",
                kind="STRUCT003",
                message=(
                    f"Orphaned module: {relpath} is not imported by any "
                    f"other module in the project."
                ),
                file=filepath,
                severity="informational",
                confidence=0.6,  # Lower confidence — many valid reasons for orphans
                evidence={
                    "code": "STRUCT003",
                    "module": module,
                    "file": relpath,
                },
                suggestions=[
                    "Verify this file is still needed — it may be dead code",
                    "If it's an entrypoint or plugin, this finding can be ignored",
                    "If dynamically imported, consider adding a comment for clarity",
                ],
            )
        )

    return findings


def _is_orphan_excluded(filepath: str, module: str, project_root: str) -> bool:
    """Check whether a file should be excluded from orphan analysis."""
    basename = os.path.basename(filepath)
    stem = basename.replace(".py", "")

    # Exclude __init__.py
    if basename == "__init__.py":
        return True

    # Exclude known entrypoint/script names
    if stem in _ORPHAN_EXCLUDE_NAMES:
        return True

    # Exclude top-level modules outside packages (typically standalone scripts)
    if "." not in module:
        return True

    # Exclude files in excluded directory patterns
    relpath = os.path.relpath(filepath, project_root)
    parts = Path(relpath).parts
    for part in parts[:-1]:  # Don't check the filename itself
        if part in _ORPHAN_EXCLUDE_DIR_PARTS:
            return True

    # Exclude files that start with test_ or end with _test
    if stem.startswith("test_") or stem.endswith("_test"):
        return True

    # Exclude files with a shebang (entrypoint scripts)
    try:
        with open(filepath) as f:
            first_line = f.readline()
            if first_line.startswith("#!"):
                return True
    except OSError:
        pass

    # Exclude modules with explicit __main__ entrypoint guards
    try:
        with open(filepath) as f:
            source = f.read()
            if "__name__" in source and "__main__" in source:
                return True
    except OSError:
        pass

    return False


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

    return snapshot
