"""Canonical function key construction and parsing.

Single source of truth for the "relpath.py::qualname" key format used by
PropertyManifest, TestEffectivenessManifest, SpecificationLedger, and
cross-module call graph.

All manifest producers and consumers MUST use these functions instead of
constructing keys with raw f-string interpolation.
"""

from __future__ import annotations

import os

SCHEMA_VERSION = "3"


def canonical_function_key(relpath: str, qualname: str) -> str:
    """Produce the canonical function key: 'relpath.py::qualname'.

    Normalizes path separators to forward slashes for cross-platform
    consistency (Windows os.path.relpath returns backslashes).
    Raises ValueError if relpath doesn't end with .py.
    """
    # Normalize to forward slashes before checking/embedding
    relpath = relpath.replace("\\", "/")
    if not relpath.endswith(".py"):
        raise ValueError(
            f"relpath must end with .py, got {relpath!r}. "
            "Use canonical_relpath() to compute the correct relative path."
        )
    return f"{relpath}::{qualname}"


def parse_function_key(key: str) -> tuple[str, str]:
    """Parse 'relpath.py::qualname' -> (relpath, qualname).

    Raises ValueError if key doesn't contain '::'.
    """
    if "::" not in key:
        raise ValueError(f"Key must contain '::', got {key!r}")
    relpath, qualname = key.split("::", 1)
    return relpath, qualname


def try_parse_function_key(key: str) -> tuple[str, str] | None:
    """Lenient parser: returns None if key doesn't match canonical format.

    Used by consumers that must handle legacy bare-name keys.
    """
    if "::" not in key:
        return None
    relpath, qualname = key.split("::", 1)
    return relpath, qualname


def canonical_relpath(filepath: str, project_root: str) -> str:
    """Compute relative path from project root, always preserving .py extension.

    Uses os.path.relpath for consistent behavior across platforms.
    """
    rel = os.path.relpath(filepath, project_root)
    # Normalize path separators to forward slashes for consistency
    rel = rel.replace(os.sep, "/") if os.sep != "/" else rel
    return rel
