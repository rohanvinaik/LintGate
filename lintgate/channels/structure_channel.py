"""Backward-compatibility shim — canonical location: lintgate/channels/structure/channel.py.

All symbols re-exported so existing ``from lintgate.channels.structure_channel import X``
continues to work.  New code should import from ``lintgate.channels.structure`` instead.
"""

from __future__ import annotations

from .structure.channel import (  # noqa: F401
    StructureChannel,
    _select_cohesion_candidates,
)
from .structure.discovery import (  # noqa: F401
    _NESTED_SUBPROJECT_MARKERS,
    _build_import_graph,
    _count_loc,
    _detect_nested_subproject_roots,
    _discover_python_files,
)
from .structure.logic import (  # noqa: F401
    _MIN_FILES_FOR_SIZE_ANALYSIS,
    _STRUCTURAL_CONFIG_FILES,
    StructureSnapshotInputs,
    _build_structure_snapshot,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_orphans,
    _check_package_cohesion,
    _find_cycles,
    _percentile,
)
from .structure.orphans import (  # noqa: F401
    _build_reexport_map,
    _detect_reexports,
    _is_orphan_excluded,
    _parse_node_reexports,
)

__all__ = [
    "StructureChannel",
    "_MIN_FILES_FOR_SIZE_ANALYSIS",
    "_STRUCTURAL_CONFIG_FILES",
    "_select_cohesion_candidates",
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
    "_parse_node_reexports",
    "_percentile",
    "StructureSnapshotInputs",
    "_build_structure_snapshot",
]
