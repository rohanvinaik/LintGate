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

from .structure_logic import (
    _MIN_FILES_FOR_SIZE_ANALYSIS,
    _NESTED_SUBPROJECT_MARKERS,  # re-export shim — canonical: structure_logic
    _STRUCTURAL_CONFIG_FILES,
    _build_import_graph,
    _build_reexport_map,  # re-export shim — canonical: structure_logic
    _build_structure_snapshot,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_orphans,
    _check_package_cohesion,
    _count_loc,  # re-export shim — canonical: structure_logic
    _detect_nested_subproject_roots,  # re-export shim — canonical: structure_logic
    _detect_reexports,  # re-export shim — canonical: structure_logic
    _discover_python_files,
    _find_cycles,  # re-export shim — canonical: structure_logic
    _is_orphan_excluded,  # re-export shim — canonical: structure_logic
    _percentile,  # re-export shim — canonical: structure_logic
)

if TYPE_CHECKING:
    from lintgate.types import LintIssue

# ---------------------------------------------------------------------------
# Backward-compatibility re-exports (PR-C / #73)
# Symbols were moved to structure_logic.py. These names are preserved here so
# that existing tests and any external importers continue to work.  Do not add
# new imports from this module — import from structure_logic directly instead.
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
        import_graph, file_map, file_loc = _build_import_graph(py_files, project_root)

        cycle_findings = _check_import_cycles(import_graph, file_map, project_root)
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
            severity = (
                "warning" if any(f.severity == "warning" for f in findings) else "informational"
            )

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            metrics=snapshot,
            duration_ms=elapsed_ms,
        )
