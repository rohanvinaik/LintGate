"""Backward-compatibility shim — moved to lintgate.controlplane.model.probe_features."""

from lintgate.controlplane.model.probe_features import *  # noqa: F401,F403
from lintgate.controlplane.model.probe_features import (  # noqa: F401 — explicit private re-exports
    _check_misleading_error_follow,
    _check_root_cause_identification,
    _extract_read_before_edit,
    _extract_reading_order_features,
    _extract_reference_features,
    _extract_retry_features,
    _extract_root_cause_features,
    _extract_verification_features,
    _first_indicator_pos,
)
