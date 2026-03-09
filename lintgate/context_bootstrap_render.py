"""Backward-compatible shim — delegates to lintgate.context.bootstrap_render."""

from .context.bootstrap_render import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.bootstrap_render import (  # noqa: F401,E402
    _GUARDRAIL_MAP,
    _NO_THEORY,
)
