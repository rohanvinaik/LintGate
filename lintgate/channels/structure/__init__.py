"""Structure subpackage — codebase structural analysis for ControlPlane.

Re-exports all public names so that existing imports like
``from lintgate.channels.structure.channel import StructureChannel``
and ``from lintgate.channels.structure import StructureChannel`` both work.
"""

from __future__ import annotations

from .channel import StructureChannel, _select_cohesion_candidates
from .discovery import (
    _NESTED_SUBPROJECT_MARKERS,
    _build_import_graph,
    _count_loc,
    _detect_nested_subproject_roots,
    _discover_python_files,
)
from .graph import (
    annotate_proposals_with_fan_in,
    build_directed_call_graph,
    build_reverse_import_graph,
    compute_function_fan_metrics,
    compute_module_fan_in,
    compute_removal_impact,
)
from .logic import (
    StructureSnapshotInputs,
    _build_structure_snapshot,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_orphans,
    _check_package_cohesion,
    _find_cycles,
    _percentile,
)
from .orphans import (
    _build_reexport_map,
    _detect_reexports,
    _is_orphan_excluded,
    _parse_node_reexports,
)
from .patterns import (
    _fingerprint_function,
    _get_call_name_simple,
    check_cross_file_patterns,
    check_package_candidates,
)

__all__ = [
    "StructureChannel",
    "_select_cohesion_candidates",
    # discovery
    "_NESTED_SUBPROJECT_MARKERS",
    "_build_import_graph",
    "_count_loc",
    "_detect_nested_subproject_roots",
    "_discover_python_files",
    # graph
    "annotate_proposals_with_fan_in",
    "build_directed_call_graph",
    "build_reverse_import_graph",
    "compute_function_fan_metrics",
    "compute_module_fan_in",
    "compute_removal_impact",
    # logic
    "StructureSnapshotInputs",
    "_build_structure_snapshot",
    "_check_import_cycles",
    "_check_module_size_distribution",
    "_check_orphans",
    "_check_package_cohesion",
    "_find_cycles",
    "_percentile",
    # orphans
    "_build_reexport_map",
    "_detect_reexports",
    "_is_orphan_excluded",
    "_parse_node_reexports",
    # patterns
    "_fingerprint_function",
    "_get_call_name_simple",
    "check_cross_file_patterns",
    "check_package_candidates",
]
