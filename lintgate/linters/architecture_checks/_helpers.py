"""Shared helpers for architecture checks: import extraction, module resolution."""

from __future__ import annotations

import ast
import os
from typing import Any


def extract_imports(filepath: str, file_module: str | None = None) -> list[tuple[str, int]]:
    """Extract all import module names and line numbers from a file.

    Args:
        filepath: Path to the Python file.
        file_module: Dotted module name of the file (e.g. ``"pkg.sub.mod"``).
            When provided, relative imports (``from .sibling import ...``) are
            resolved to absolute module names.  Without this, relative imports
            are silently skipped (backward-compatible).

    Returns list of (module_name, lineno) tuples.
    """
    try:
        with open(filepath) as f:
            source = f.read()
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    imports: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imports.append((node.module, node.lineno))
            elif node.level > 0 and file_module:
                resolved = _resolve_relative_import(file_module, node.level, node.module)
                if resolved:
                    imports.append((resolved, node.lineno))

    return imports


def _resolve_relative_import(file_module: str, level: int, module: str | None) -> str | None:
    """Resolve a relative import to an absolute module name.

    ``from .sibling import X`` in ``pkg.sub.mod`` → ``pkg.sub.sibling``
    ``from ..other import Y`` in ``pkg.sub.mod`` → ``pkg.other``
    ``from . import Z``       in ``pkg.sub.mod`` → ``pkg.sub``
    """
    parts = file_module.split(".")
    # level=1 means "current package" — drop the module name to get the package
    # level=2 means "parent package" — drop one more, etc.
    if level > len(parts):
        return None  # invalid relative import (too many dots)
    package_parts = parts[:-level]
    if module:
        return ".".join(package_parts) + "." + module if package_parts else module
    # ``from . import name`` — the import target is the package itself
    return ".".join(package_parts) if package_parts else None


def filepath_to_module(filepath: str, project_root: str) -> str | None:
    """Convert a file path to a dotted module name.

    /project/src/app/views.py → app.views
    /project/app/models/__init__.py → app.models
    """
    try:
        relpath = os.path.relpath(filepath, project_root)
    except ValueError:
        return None

    relpath = relpath.replace(os.sep, "/")

    for prefix in ("src/", "lib/"):
        if relpath.startswith(prefix):
            relpath = relpath[len(prefix) :]
            break

    if relpath.endswith("/__init__.py"):
        module = relpath[: -len("/__init__.py")]
    elif relpath.endswith(".py"):
        module = relpath[:-3]
    else:
        return None

    return module.replace("/", ".")


def is_project_local(module_name: str, project_root: str) -> bool:
    """Check if a module name corresponds to a local project file."""
    top_level = module_name.split(".")[0]
    candidates = [
        os.path.join(project_root, top_level + ".py"),
        os.path.join(project_root, top_level, "__init__.py"),
        os.path.join(project_root, "src", top_level + ".py"),
        os.path.join(project_root, "src", top_level, "__init__.py"),
    ]
    return any(os.path.exists(c) for c in candidates)


def module_matches_prefix(module: str, prefix: str) -> bool:
    """Check if a module matches a prefix (exact or dotted child)."""
    return module == prefix or module.startswith(prefix + ".")


def build_layer_map(
    layers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build lookup from module prefix → layer config."""
    layer_map: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for module in layer.get("modules", []):
            layer_map[module] = layer
    return layer_map


def find_layer(
    module_name: str,
    layer_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Find which layer a module belongs to (longest prefix match)."""
    best_match: dict[str, Any] | None = None
    best_length = 0

    for prefix, layer in layer_map.items():
        if (module_name == prefix or module_name.startswith(prefix + ".")) and len(
            prefix
        ) > best_length:
            best_match = layer
            best_length = len(prefix)

    return best_match
