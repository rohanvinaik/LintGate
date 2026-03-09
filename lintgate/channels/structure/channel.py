"""Structure channel — codebase structural analysis for ControlPlane.

Professional instinct modeled: A senior engineer maintains a mental model of
the codebase's shape — where the import graph tangles, which modules accumulate
too much responsibility, which files nobody references anymore. This channel
provides that awareness cheaply via AST-based symbolic analysis.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)

from .logic import (
    _MIN_FILES_FOR_SIZE_ANALYSIS,
    _NESTED_SUBPROJECT_MARKERS,  # re-export shim — canonical: logic
    _STRUCTURAL_CONFIG_FILES,
    StructureSnapshotInputs,
    _build_import_graph,
    _build_reexport_map,  # re-export shim — canonical: logic
    _build_structure_snapshot,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_orphans,
    _check_package_cohesion,
    _count_loc,  # re-export shim — canonical: logic
    _detect_nested_subproject_roots,  # re-export shim — canonical: logic
    _detect_reexports,  # re-export shim — canonical: logic
    _discover_python_files,
    _find_cycles,  # re-export shim — canonical: logic
    _is_orphan_excluded,  # re-export shim — canonical: logic
    _percentile,  # re-export shim — canonical: logic
)

if TYPE_CHECKING:
    from lintgate.types import LintIssue

# ---------------------------------------------------------------------------
# Backward-compatibility re-exports (PR-C / #73)
# Symbols were moved to logic.py. These names are preserved here so
# that existing tests and any external importers continue to work.  Do not add
# new imports from this module — import from logic directly instead.
# ---------------------------------------------------------------------------
__all__ = [
    "StructureChannel",
    # re-exported helpers
    "_build_import_graph",
    "_build_reexport_map",
    "_check_import_cycles",
    "_check_module_size_distribution",
    "_check_orphans",
    "_check_package_cohesion",
    "_count_loc",
    "_detect_nested_subproject_roots",
    "_detect_reexports",
    "_discover_python_files",
    "_find_cycles",
    "_is_orphan_excluded",
    "_NESTED_SUBPROJECT_MARKERS",
    "_percentile",
]


def _select_cohesion_candidates(
    file_loc: dict[str, int], project_root: str, py_files: list[str], max_candidates: int = 5
) -> list[str]:
    """Select top N files by LOC above p90 as cohesion analysis candidates (bounded cost)."""
    if not file_loc:
        return []
    sorted_locs = sorted(file_loc.values())
    p90 = _percentile(sorted_locs, 0.90)
    above_p90 = [(fp, loc) for fp, loc in file_loc.items() if loc > p90]
    above_p90.sort(key=lambda x: x[1], reverse=True)
    return [fp for fp, _ in above_p90[:max_candidates]]


class StructureChannel:
    """Supervision channel for codebase structural analysis.

    Advisory only — structure findings are informational unless
    corroborated by other channels.
    """

    name = "structure"
    timeout_ms = 5000
    blocking_capable = False  # Advisory

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run on MCP invocations; on hooks, only for structurally relevant changes."""
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
            if basename == "__init__.py" or basename in _STRUCTURAL_CONFIG_FILES:
                return True

        # Import-only or class structure changes
        return classification.import_only or classification.class_structure_changed

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute structural analysis checks."""
        start = time.perf_counter()
        findings: list[LintIssue] = []
        project_root = event.project_root

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

        # Build import graph and run checks
        import_graph, file_map, file_loc, deferred_edges = _build_import_graph(
            py_files, project_root
        )

        # Build reverse import graph and fan-in metrics (Gap 1)
        from .graph import build_reverse_import_graph, compute_module_fan_in

        reverse_graph = build_reverse_import_graph(import_graph)
        module_fan_in = compute_module_fan_in(reverse_graph, file_map)

        cycle_findings = _check_import_cycles(import_graph, file_map, project_root, deferred_edges)
        findings.extend(cycle_findings)

        size_findings = _check_module_size_distribution(file_loc, project_root)
        findings.extend(size_findings)

        _ch_config = config.channels.get("structure")
        _structure_settings = _ch_config.settings if _ch_config else {}
        _extra_orphan_dirs = _structure_settings.get("orphan_exclude_dirs", [])
        _extra_orphan_frozen = frozenset(_extra_orphan_dirs) if _extra_orphan_dirs else None

        orphan_findings = _check_orphans(
            py_files, import_graph, file_map, project_root, _extra_orphan_frozen
        )
        findings.extend(orphan_findings)

        cohesion_findings = _check_package_cohesion(import_graph, file_map, project_root)
        findings.extend(cohesion_findings)

        # STRUCT005/006: Pattern-based structure checks (Gap 3, 4)
        from .patterns import (
            check_cross_file_patterns,
            check_package_candidates,
        )

        _pkg_min = _structure_settings.get("package_detection_min_files", 3)
        _pat_max_loc = _structure_settings.get("pattern_detection_max_file_loc", 1000)
        _pat_max_files = _structure_settings.get("pattern_detection_max_files", 100)

        package_findings = check_package_candidates(
            py_files, import_graph, file_map, project_root, min_files=_pkg_min
        )
        findings.extend(package_findings)

        pattern_findings = check_cross_file_patterns(
            py_files,
            project_root,
            max_file_loc=_pat_max_loc,
            max_files=_pat_max_files,
        )
        findings.extend(pattern_findings)

        # File-level cohesion analysis for convergence (#215 A3)
        file_cohesion: dict[str, dict] = {}
        cohesion_candidates = _select_cohesion_candidates(file_loc, project_root, py_files)
        if cohesion_candidates:
            import ast as _ast

            from lintgate.linters.structure_checks.cohesion_analysis import (
                analyze_file_cohesion,
            )

            for candidate_path in cohesion_candidates:
                try:
                    with open(candidate_path, encoding="utf-8", errors="replace") as fh:
                        tree = _ast.parse(fh.read(), filename=candidate_path)
                    result = analyze_file_cohesion(tree, candidate_path)
                    file_cohesion[candidate_path] = {
                        "score": result.score,
                        "component_count": result.component_count,
                        "split_proposals": [
                            {
                                "kind": p.kind,
                                "target": p.target,
                                "action": p.action,
                                "confidence": p.confidence,
                            }
                            for p in result.split_proposals
                        ],
                    }
                except Exception:
                    continue

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Build structure snapshot for compact output
        snapshot = _build_structure_snapshot(
            StructureSnapshotInputs(
                py_files=py_files,
                file_map=file_map,
                file_loc=file_loc,
                project_root=project_root,
                cycle_count=len(cycle_findings),
                orphan_count=len(orphan_findings),
                cohesion_count=len(cohesion_findings),
                module_fan_in=module_fan_in,
            )
        )

        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            severity = (
                "warning" if any(f.severity == "warning" for f in findings) else "informational"
            )

        # Expose import graph for work queue builder (#192)
        snapshot["_import_graph"] = {mod: sorted(deps) for mod, deps in import_graph.items()}
        snapshot["_file_map"] = dict(file_map)
        snapshot["_module_fan_in"] = dict(module_fan_in) if module_fan_in else {}
        if file_cohesion:
            snapshot["_file_cohesion"] = file_cohesion

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            metrics=snapshot,
            duration_ms=elapsed_ms,
        )
