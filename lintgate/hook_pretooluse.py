"""Backward-compatibility shim — moved to lintgate.hooks.pretooluse."""

from lintgate.hooks.pretooluse import (  # noqa: F401
    _BLOCKED_PATTERNS,
    _is_mutation,
    main,
)
