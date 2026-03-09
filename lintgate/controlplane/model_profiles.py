"""Backward-compatibility shim — moved to lintgate.controlplane.model.profiles."""

from lintgate.controlplane.model.profiles import *  # noqa: F401,F403
from lintgate.controlplane.model.profiles import (  # noqa: F401 — explicit private re-exports
    _EMA_ALPHA,
    _PROVIDER_PREFIXES,
    _lintgate_home,
)
