"""Shared lint helpers — used by scripts/lint_run.py and re-exported from mcp_tools/lint_tools.py."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any


def _tool_package_name(tool: str) -> str:
    """Map external tool executable names to install package names."""
    return "pip-audit" if tool == "pip-audit" else tool


def _project_venv_python(project_root: str) -> str | None:
    """Return project venv python path if present."""
    for venv_name in (".venv", "venv", "env"):
        py = Path(project_root) / venv_name / "bin" / "python"
        if py.exists() and py.is_file():
            return str(py)
    return None


def _format_cmd(cmd: list[str]) -> str:
    """Render command as shell-safe string."""
    return " ".join(shlex.quote(part) for part in cmd)


def _linter_available(linter: Any, project_root: str) -> bool:
    """Check linter availability with backward-compatible call signatures."""
    try:
        return bool(linter.available(project_root=project_root))
    except TypeError:
        return bool(linter.available())


def _missing_tool_hints(
    project_root: str,
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build actionable install hints for unavailable external tools."""
    missing: dict[str, dict[str, Any]] = {}
    for linter_name, linter in sorted(registry.items()):
        tool = getattr(linter, "required_tool", None)
        if not tool:
            continue
        if _linter_available(linter, project_root):
            continue

        entry = missing.setdefault(
            tool,
            {
                "tool": tool,
                "package": _tool_package_name(tool),
                "required_by": [],
                "reason": "executable_not_found",
            },
        )
        entry["required_by"].append(linter_name)

    venv_python = _project_venv_python(project_root)
    hints: list[dict[str, Any]] = []
    for tool in sorted(missing):
        item = missing[tool]
        package = item["package"]
        if venv_python:
            cmd = [venv_python, "-m", "pip", "install", package]
            install_command = _format_cmd(cmd)
            auto_installable = tool in {"ty", "pip-audit"}
        else:
            install_command = f"pip install {package}"
            auto_installable = False

        hints.append(
            {
                **item,
                "install_command": install_command,
                "auto_installable": auto_installable,
            }
        )
    return hints
