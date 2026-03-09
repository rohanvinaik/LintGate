"""Backward-compatible shim — delegates to lintgate.context.bootstrap_patches."""

from .context.bootstrap_patches import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.bootstrap_patches import (  # noqa: F401,E402
    _MANAGED_BEGIN_RE,
    _MANAGED_END_RE,
    _TRIGGER_HANDLERS,
    _patch_constraint_accepted,
    _patch_do_dont,
    _patch_theory_coherence,
)
