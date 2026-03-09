"""Backward-compatibility shim — canonical location: lintgate/channels/structure/orphans.py.

All symbols re-exported so existing imports continue to work.
New code should import from ``lintgate.channels.structure.orphans`` instead.
"""

from __future__ import annotations

from .structure.orphans import (  # noqa: F401
    _build_reexport_map,
    _check_orphans,
    _classify_orphan,
    _detect_reexports,
    _has_entrypoint_marker,
    _is_in_excluded_dir,
    _is_orphan_excluded,
    _parse_node_reexports,
)
