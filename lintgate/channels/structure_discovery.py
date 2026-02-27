"""File discovery, import graph construction, and LOC counting for the structure channel.

Extracted from structure_logic.py for module size compliance.
"""

from __future__ import annotations

import os
from collections import defaultdict

from lintgate.path_filters import is_backup_like_directory

# Markers that identify a directory as a separate nested subproject root.
_NESTED_SUBPROJECT_MARKERS = frozenset(
    {
        ".git",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
    }
)


def _detect_nested_subproject_roots(
    project_root: str,
    allowlist: frozenset[str] | None = None,
) -> frozenset[str]:
    """Find immediate subdirectories of project_root that contain their own subproject.

    Args:
        project_root: Root of the project being analyzed.
        allowlist: Optional set of directory names (relative path components) that
            should NOT be treated as nested subprojects even if they contain markers.

    Returns:
        Set of absolute directory paths that should be excluded from analysis.
    """
    excluded: set[str] = set()
    allowlist = allowlist or frozenset()

    try:
        entries = os.scandir(project_root)
    except OSError:
        return frozenset()

    with entries:
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in allowlist:
                continue
            for marker in _NESTED_SUBPROJECT_MARKERS:
                marker_path = os.path.join(entry.path, marker)
                if os.path.exists(marker_path):
                    excluded.add(entry.path)
                    break

    return frozenset(excluded)


_EXCLUDE_DIRS = frozenset(
    {
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".eggs",
        "dist",
        "build",
        ".nox",
        ".pytest_cache",
        "mutants",
        ".mutmut",
        ".mutmut-cache",
    }
)


def _discover_python_files(
    project_root: str,
    nested_subproject_allowlist: frozenset[str] | None = None,
) -> list[str]:
    """Discover Python files, excluding venvs, caches, hidden directories,
    and nested subproject roots (PR-H / #69).

    Args:
        project_root: Root of the project to analyze.
        nested_subproject_allowlist: Directory names to include even if they look
            like nested subprojects (e.g. a vendored copy you *do* want analyzed).
    """
    py_files: list[str] = []

    nested_excluded = _detect_nested_subproject_roots(
        project_root, allowlist=nested_subproject_allowlist
    )

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _EXCLUDE_DIRS
            and not d.startswith(".")
            and not is_backup_like_directory(d)
            and os.path.join(dirpath, d) not in nested_excluded
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                py_files.append(os.path.join(dirpath, fn))

    return py_files


# ── Import Graph Construction ────────────────────────────────────────────


def _build_import_graph(
    py_files: list[str], project_root: str
) -> tuple[dict[str, set[str]], dict[str, str], dict[str, int]]:
    """Build the project import graph and collect LOC per file.

    Returns:
        import_graph: module_name -> set of imported module_names
        file_map: module_name -> file_path
        file_loc: file_path -> line_count
    """
    from lintgate.linters.architecture_checks._helpers import (
        extract_imports,
        filepath_to_module,
        is_project_local,
    )

    import_graph: dict[str, set[str]] = defaultdict(set)
    file_map: dict[str, str] = {}
    file_loc: dict[str, int] = {}

    for filepath in py_files:
        module = filepath_to_module(filepath, project_root)
        if not module:
            continue

        file_map[module] = filepath
        loc = _count_loc(filepath)
        file_loc[filepath] = loc

        imports = extract_imports(filepath)
        for imp_module, _ in imports:
            if is_project_local(imp_module, project_root):
                import_graph[module].add(imp_module)

    return dict(import_graph), file_map, file_loc


def _count_loc(filepath: str) -> int:
    """Count non-blank, non-comment lines of code."""
    try:
        with open(filepath) as f:
            lines = f.readlines()
    except OSError:
        return 0

    loc = 0
    in_docstring = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if not stripped or stripped.startswith("#"):
            continue
        loc += 1
    return loc
