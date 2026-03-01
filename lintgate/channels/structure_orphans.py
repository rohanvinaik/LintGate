"""Orphan detection and re-export analysis for the structure channel.

Extracted from structure_logic.py for module size compliance.
Contains STRUCT003 orphan detection logic, re-export parsing from __init__.py,
and the exclusion rules for entrypoints, tests, plugins, etc.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from lintgate.types import LintIssue

# ── Constants (orphan detection) ─────────────────────────────────────────

_ORPHAN_EXCLUDE_NAMES = frozenset(
    {
        "__init__",
        "__main__",
        "setup",
        "conftest",
        "manage",
        "wsgi",
        "asgi",
        "app",
        "main",
        "cli",
        "server",
        "hook",
    }
)

_ORPHAN_EXCLUDE_DIR_PARTS = frozenset(
    {
        "migrations",
        "alembic",
        "scripts",
        "bin",
        "plugins",
        "fixtures",
        "stubs",
        "tests",
        "test",
        "testing",
        "benchmarks",
    }
)

_PLUGIN_DIR_PATTERNS = frozenset(
    {
        "linters",
        "renderers",
        "mcp_tools",
        "plugins",
        "extensions",
        "handlers",
        "backends",
        "drivers",
        "adapters",
    }
)


# ── Re-export Detection (for STRUCT003 orphan analysis) ─────────────────


def _parse_import_from_reexport(
    node: ast.ImportFrom, reexports: dict[str, str]
) -> None:
    """Handle `from .sub import ...` re-export patterns."""
    if not node.module or node.level <= 0:
        return
    stem = node.module.split(".")[0]
    if node.names and len(node.names) == 1 and node.names[0].name == "*":
        if stem not in reexports or reexports[stem] != "definite":
            reexports[stem] = "unknown"
    else:
        reexports[stem] = "definite"


def _parse_all_assignment(node: ast.Assign, reexports: dict[str, str]) -> None:
    """Handle `__all__ = [...]` assignments."""
    for target in node.targets:
        if not (isinstance(target, ast.Name) and target.id == "__all__"):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                reexports[elt.value] = "definite"


def _is_dynamic_import_call(node: ast.Call) -> bool:
    """Check if a Call node is a dynamic import (importlib or __import__)."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "import_module":
        return True
    return bool(isinstance(func, ast.Name) and func.id == "__import__")


def _parse_node_reexports(node: ast.AST, reexports: dict[str, str]) -> bool:
    """Parse a single AST node for re-export patterns. Returns True if dynamic import found."""
    if isinstance(node, ast.ImportFrom):
        _parse_import_from_reexport(node, reexports)
    elif isinstance(node, ast.Assign):
        _parse_all_assignment(node, reexports)
    elif isinstance(node, ast.Call):
        return _is_dynamic_import_call(node)
    return False


def _detect_reexports(init_file: str, project_root: str) -> dict[str, str]:
    """Detect re-exported modules from an __init__.py file."""
    try:
        with open(init_file) as f:
            source = f.read()
        tree = ast.parse(source, filename=init_file)
    except (OSError, SyntaxError):
        return {}

    reexports: dict[str, str] = {}
    has_dynamic_import = False

    for node in ast.walk(tree):
        if _parse_node_reexports(node, reexports):
            has_dynamic_import = True

    if has_dynamic_import:
        reexports.setdefault("*", "unknown")

    return reexports


def _build_reexport_map(
    py_files: list[str], project_root: str
) -> dict[str, dict[str, str]]:
    """Build a map of parent_package -> {module_stem: certainty} from all __init__.py.

    Returns:
        {package_dir_relpath: {module_stem: "definite"|"unknown"}}
    """
    reexport_map: dict[str, dict[str, str]] = {}

    for filepath in py_files:
        if os.path.basename(filepath) != "__init__.py":
            continue

        parent_dir = os.path.dirname(filepath)
        reexports = _detect_reexports(filepath, project_root)
        if reexports:
            reexport_map[parent_dir] = reexports

    return reexport_map


# ── STRUCT003: Orphan Detection ──────────────────────────────────────────


def _is_orphan_excluded(
    filepath: str,
    module: str,
    project_root: str,
    extra_exclude_dirs: frozenset[str] | None = None,
) -> bool:
    """Check whether a file should be excluded from orphan analysis."""
    basename = os.path.basename(filepath)
    stem = basename.replace(".py", "")

    if basename == "__init__.py":
        return True
    if stem in _ORPHAN_EXCLUDE_NAMES:
        return True
    if "." not in module:
        return True

    relpath = os.path.relpath(filepath, project_root)
    dir_parts = Path(relpath).parts[:-1]

    if _is_in_excluded_dir(dir_parts, extra_exclude_dirs):
        return True

    if stem.startswith("test_") or stem.endswith("_test"):
        return True

    return _has_entrypoint_marker(filepath)


def _is_in_excluded_dir(
    dir_parts: tuple[str, ...],
    extra_exclude_dirs: frozenset[str] | None,
) -> bool:
    """Check if any parent directory matches exclusion patterns."""
    all_exclude_dirs = _PLUGIN_DIR_PATTERNS | (extra_exclude_dirs or frozenset())
    combined = _ORPHAN_EXCLUDE_DIR_PARTS | all_exclude_dirs
    return any(part in combined for part in dir_parts)


def _has_entrypoint_marker(filepath: str) -> bool:
    """Check for shebang lines or __main__ guards that indicate an entrypoint."""
    try:
        with open(filepath) as f:
            source = f.read()
    except OSError:
        return False
    if source.startswith("#!"):
        return True
    return "__name__" in source and "__main__" in source


def _check_orphans(
    py_files: list[str],
    import_graph: dict[str, set[str]],
    file_map: dict[str, str],
    project_root: str,
    extra_exclude_dirs: frozenset[str] | None = None,
) -> list[LintIssue]:
    """Detect orphaned files -- modules not imported by any other module.

    Excludes:
    - Entrypoints/scripts (__main__, manage, cli, app, server, main, etc.)
    - Migrations and alembic directories
    - Test files and test directories
    - __init__.py files
    - Plugin/discovery patterns
    - conftest.py
    - Files outside packages (top-level scripts)

    Re-export awareness:
    - Modules explicitly re-exported from __init__.py (named imports, __all__)
      are treated as referenced ("definite") and skipped.
    - Modules ambiguously re-exported (wildcard/dynamic imports) are still
      reported but at lower confidence (0.3) with reexport_status evidence.
    """
    findings: list[LintIssue] = []

    all_imported: set[str] = set()
    for targets in import_graph.values():
        all_imported.update(targets)

    # Count parent packages as "referenced"
    parent_refs: set[str] = set()
    for mod in all_imported:
        parts = mod.split(".")
        for i in range(1, len(parts)):
            parent_refs.add(".".join(parts[:i]))
    all_imported.update(parent_refs)

    reexport_map = _build_reexport_map(py_files, project_root)

    for module, filepath in file_map.items():
        if module in all_imported:
            continue
        if _is_orphan_excluded(filepath, module, project_root, extra_exclude_dirs):
            continue

        finding = _classify_orphan(module, filepath, project_root, reexport_map)
        if finding:
            findings.append(finding)

    return findings


def _classify_orphan(
    module: str,
    filepath: str,
    project_root: str,
    reexport_map: dict[str, dict[str, str]],
) -> LintIssue | None:
    """Classify an orphan module by its re-export status and create the finding."""
    parent_dir = os.path.dirname(filepath)
    stem = os.path.basename(filepath).replace(".py", "")
    parent_reexports = reexport_map.get(parent_dir, {})

    module_short = module.rsplit(".", 1)[-1] if "." in module else module
    reexport_certainty = parent_reexports.get(stem, parent_reexports.get(module_short))

    if reexport_certainty is None and "*" in parent_reexports:
        reexport_certainty = "unknown"

    if reexport_certainty == "definite":
        return None

    relpath = os.path.relpath(filepath, project_root)

    if reexport_certainty == "unknown":
        return LintIssue(
            linter="structure_channel",
            kind="STRUCT003",
            message=(
                f"Possibly orphaned module: {relpath} is not directly "
                f"imported but may be re-exported via wildcard or "
                f"dynamic import."
            ),
            file=filepath,
            severity="informational",
            confidence=0.3,
            evidence={
                "code": "STRUCT003",
                "module": module,
                "file": relpath,
                "reexport_status": "unknown",
                "note": (
                    "Module may be re-exported via wildcard or dynamic import in parent __init__.py"
                ),
            },
            suggestions=[
                "Check parent __init__.py for wildcard or dynamic imports",
                "If intentionally re-exported, use explicit named imports "
                "in __init__.py for clarity",
            ],
        )

    return LintIssue(
        linter="structure_channel",
        kind="STRUCT003",
        message=(
            f"Orphaned module: {relpath} is not imported by any other module in the project."
        ),
        file=filepath,
        severity="informational",
        confidence=0.6,
        evidence={
            "code": "STRUCT003",
            "module": module,
            "file": relpath,
        },
        suggestions=[
            "Verify this file is still needed — it may be dead code",
            "If it's an entrypoint or plugin, this finding can be ignored",
            "If dynamically imported, consider adding a comment for clarity",
        ],
    )
