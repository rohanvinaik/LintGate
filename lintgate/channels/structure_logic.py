"""Backward-compatibility shim — canonical location: lintgate/channels/structure/logic.py.

All symbols re-exported so existing imports continue to work.
New code should import from ``lintgate.channels.structure.logic`` instead.
"""

from __future__ import annotations

# Re-exports that logic.py itself re-exports from discovery/orphans
from .structure.discovery import (  # noqa: F401
    _NESTED_SUBPROJECT_MARKERS,
    _build_import_graph,
    _count_loc,
    _detect_nested_subproject_roots,
    _discover_python_files,
)
from .structure.logic import (  # noqa: F401
    _STRUCTURAL_CONFIG_FILES,
    StructureSnapshotInputs,
    _build_structure_snapshot,
    _check_import_cycles,
    _check_module_size_distribution,
    _check_orphans,
    _check_package_cohesion,
    _classify_cycle,
    _find_cycles,
    _percentile,
)
from .structure.orphans import (  # noqa: F401
    _build_reexport_map,
    _detect_reexports,
    _is_orphan_excluded,
    _parse_node_reexports,
)
