"""Shared CLI utilities for LintGate scripts.

Every script imports from here instead of the MCP helpers dict.
This replaces: _validate_project_root, _json_dumps, _save_analysis,
_tool_response, _collect_python_files, _resolve_files.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def validate_project_root(path: str, *, require_python: bool = False) -> str:
    """Resolve and validate a project root path.

    Walks up from the given path looking for markers (.git, pyproject.toml,
    setup.py, .claude). Returns the first directory that has one.
    """
    p = Path(path).resolve()
    if p.is_file():
        p = p.parent

    if not p.is_dir():
        print(json.dumps({"error": f"Not a directory: {path}"}))
        sys.exit(1)

    # Walk up looking for project markers
    markers = {".git", "pyproject.toml", "setup.py", "setup.cfg", ".claude", "lintgate.yaml"}
    check = p
    while check != check.parent:
        if any((check / m).exists() for m in markers):
            return str(check)
        check = check.parent

    # No marker found — use the path as-is
    return str(p)


def collect_python_files(project_root: str, *, exclude_tests: bool = False) -> list[str]:
    """Collect all .py files under a project root."""
    root = Path(project_root)
    files = []
    for py in sorted(root.rglob("*.py")):
        rel = str(py.relative_to(root))
        if "__pycache__" in rel:
            continue
        if exclude_tests and (rel.startswith("tests/") or rel.startswith("test_")):
            continue
        files.append(str(py))
    return files


def resolve_files(files: list[str], project_root: str) -> tuple[list[str], list[str]]:
    """Resolve file paths relative to project root. Returns (existing, missing)."""
    existing, missing = [], []
    for f in files:
        full = f if os.path.isabs(f) else os.path.join(project_root, f)
        if os.path.isfile(full):
            existing.append(full)
        else:
            missing.append(f)
    return existing, missing


def save_analysis(data: Any, tool_name: str, project_root: str, *, run_id: str = "") -> str:
    """Write full analysis to .lintgate/analysis/<tool>/<id>.json. Returns filepath."""
    if not project_root:
        project_root = os.getcwd()
    analysis_dir = os.path.join(project_root, ".lintgate", "analysis", tool_name)
    os.makedirs(analysis_dir, exist_ok=True)
    serialized = json.dumps(data, separators=(",", ":"), default=str)
    content_hash = hashlib.sha256(serialized.encode()).hexdigest()[:10]
    filename = f"{run_id}.json" if run_id else f"{content_hash}.json"
    filepath = os.path.join(analysis_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(serialized)
    return filepath


def emit(
    data: Any,
    tool_name: str,
    project_root: str,
    summary: str,
    *,
    run_id: str = "",
    next_actions: list | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Save analysis to disk, print slim JSON summary to stdout, then exit 0.

    This is the standard script exit point. Every script ends with emit().
    """
    filepath = save_analysis(data, tool_name, project_root, run_id=run_id)
    analysis_id = run_id or os.path.basename(filepath).removesuffix(".json")
    response: dict[str, Any] = {
        "analysis_id": analysis_id,
        "summary": summary,
        "file": filepath,
    }
    if extra:
        response.update(extra)
    if next_actions:
        response["next_actions"] = next_actions
    print(json.dumps(response, separators=(",", ":"), default=str))


def emit_error(message: str, *, exit_code: int = 1) -> None:
    """Print an error JSON to stdout and exit."""
    print(json.dumps({"error": message}))
    sys.exit(exit_code)


def add_project_to_path(project_root: str) -> None:
    """Add the project root to sys.path so lintgate imports work."""
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
