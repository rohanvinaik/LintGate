"""Backward-compatibility shim — moved to lintgate.controlplane.model.probe_tasks."""

from lintgate.controlplane.model.probe_tasks import *  # noqa: F401,F403
from lintgate.controlplane.model.probe_tasks import (  # noqa: F401 — explicit private re-exports
    _SIGNAL_ANTI_PATTERN_MAP,
    _SIGNAL_DISPOSITION_MAP,
)
