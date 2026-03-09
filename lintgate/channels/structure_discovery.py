"""Backward-compatibility shim — canonical location: lintgate/channels/structure/discovery.py.

All symbols re-exported so existing imports continue to work.
New code should import from ``lintgate.channels.structure.discovery`` instead.
"""

from __future__ import annotations

from .structure.discovery import (  # noqa: F401
    _NESTED_SUBPROJECT_MARKERS,
    _build_import_graph,
    _count_loc,
    _detect_nested_subproject_roots,
    _discover_python_files,
    _find_deferred_import_lines,
    _walk_for_deferred,
)
