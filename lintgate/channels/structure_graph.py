"""Backward-compatibility shim — canonical location: lintgate/channels/structure/graph.py.

All symbols re-exported so existing imports continue to work.
New code should import from ``lintgate.channels.structure.graph`` instead.
"""

from __future__ import annotations

from .structure.graph import (  # noqa: F401
    annotate_proposals_with_fan_in,
    build_directed_call_graph,
    build_reverse_import_graph,
    compute_function_fan_metrics,
    compute_module_fan_in,
    compute_removal_impact,
)
