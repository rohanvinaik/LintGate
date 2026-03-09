"""Backward-compatibility shim — canonical location: lintgate/channels/structure/patterns.py.

All symbols re-exported so existing imports continue to work.
New code should import from ``lintgate.channels.structure.patterns`` instead.
"""

from __future__ import annotations

from .structure.patterns import (  # noqa: F401
    _collect_file_patterns,
    _collect_structural_features,
    _emit_pattern_finding,
    _extract_prefix_groups,
    _fingerprint_function,
    _get_call_name_simple,
    check_cross_file_patterns,
    check_package_candidates,
)
