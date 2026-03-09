"""Backward-compatibility shim — moved to lintgate.controlplane.model.probe."""

from lintgate.controlplane.model.probe import *  # noqa: F401,F403
from lintgate.controlplane.model.probe import (  # noqa: F401 — explicit private re-exports
    _compute_trace_quality,
    _derive_custom_anti_patterns,
    _derive_custom_dispositions,
    _extract_features_for_task,
)
