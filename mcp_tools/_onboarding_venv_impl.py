"""Onboarding helpers — venv management, tool-gap detection, package installation.

Extracted from onboarding_tools.py to keep the MCP registration file lean.

NOTE: Functions access ``shutil``, ``subprocess``, ``sys`` and sibling helpers
through ``mcp_tools.onboarding_tools`` at call time so that test patches
targeting ``mcp_tools.onboarding_tools.<name>`` remain effective.
"""

from __future__ import annotations

import shlex
from typing import Any

_OPTIONAL_STARTUP_PACKAGES = {
    "pip-audit": "pip-audit",
    "ty": "ty",
}


# ---------------------------------------------------------------------------
# Lazy accessor — avoids circular import at load time.
# ---------------------------------------------------------------------------


def _ot():  # noqa: ANN202
    """Return the onboarding_tools module."""
    import mcp_tools.onboarding_tools as _mod

    return _mod


# ---------------------------------------------------------------------------
# Small utilities (no patchable stdlib dependency)
# ---------------------------------------------------------------------------


def _tool_package_name(tool: str) -> str:
    """Map executable names to pip package names."""
    if tool in _OPTIONAL_STARTUP_PACKAGES:
        return _OPTIONAL_STARTUP_PACKAGES[tool]
    return tool


def _project_venv_python(project_root: str) -> str | None:
    """Return project venv python path, if present."""
    from pathlib import Path

    for venv_name in (".venv", "venv", "env"):
        py = Path(project_root) / venv_name / "bin" / "python"
        if py.exists() and py.is_file():
            return str(py)
    return None


def _format_cmd(cmd: list[str]) -> str:
    """Render shell-safe command text for output payloads."""
    return " ".join(shlex.quote(part) for part in cmd)


def _linter_available(linter: Any, project_root: str) -> bool:
    """Check linter availability with backward-compatible signatures."""
    try:
        return bool(linter.available(project_root=project_root))
    except TypeError:
        return bool(linter.available())


# ---------------------------------------------------------------------------
# Functions that use patchable stdlib modules via _ot()
# ---------------------------------------------------------------------------


def _venv_create_command() -> tuple[list[str], str]:
    """Build preferred venv creation command and manager label."""
    ot = _ot()
    uv_path = ot.shutil.which("uv")
    if uv_path:
        return [uv_path, "venv", ".venv"], "uv"
    return [ot.sys.executable, "-m", "venv", ".venv"], "python_venv"


def _ensure_project_venv(project_root: str) -> dict[str, Any]:
    """Ensure a project-local virtualenv exists and has pip available."""
    ot = _ot()
    existing = ot._project_venv_python(project_root)
    if existing:
        return {"status": "present", "venv_python": existing}

    create_cmd, manager = ot._venv_create_command()
    try:
        create_result = ot.subprocess.run(
            create_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except ot.subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "reason": "venv_create_timed_out_after_120s",
        }

    if create_result.returncode != 0:
        return {
            "status": "error",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "returncode": create_result.returncode,
            "stderr_tail": (create_result.stderr or "")[-240:],
            "reason": "venv_create_failed",
        }

    venv_python = ot._project_venv_python(project_root)
    if not venv_python:
        return {
            "status": "error",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "reason": "venv_created_but_python_missing",
        }

    pip_check_cmd = [venv_python, "-m", "pip", "--version"]
    try:
        pip_check = ot.subprocess.run(
            pip_check_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except ot.subprocess.TimeoutExpired:
        return {
            "status": "created",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "venv_python": venv_python,
            "pip_ready": False,
            "pip_check": "timeout",
        }

    if pip_check.returncode == 0:
        return {
            "status": "created",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "venv_python": venv_python,
            "pip_ready": True,
        }

    ensurepip_cmd = [venv_python, "-m", "ensurepip", "--upgrade"]
    try:
        ensure_result = ot.subprocess.run(
            ensurepip_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except ot.subprocess.TimeoutExpired:
        return {
            "status": "created",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "venv_python": venv_python,
            "pip_ready": False,
            "pip_bootstrap": "timeout",
        }

    return {
        "status": "created",
        "manager": manager,
        "command": _format_cmd(create_cmd),
        "venv_python": venv_python,
        "pip_ready": ensure_result.returncode == 0,
        "pip_bootstrap_command": _format_cmd(ensurepip_cmd),
        "pip_bootstrap_returncode": ensure_result.returncode,
        "pip_bootstrap_stderr_tail": (ensure_result.stderr or "")[-240:],
    }


def _install_commands_for_package(project_root: str, package: str) -> list[list[str]]:
    """Build preferred installer commands for a package in the project venv."""
    ot = _ot()
    venv_python = ot._project_venv_python(project_root)
    if not venv_python:
        return []

    commands: list[list[str]] = []
    uv_path = ot.shutil.which("uv")
    if uv_path:
        commands.append([uv_path, "pip", "install", "--python", venv_python, package])
    commands.append([venv_python, "-m", "pip", "install", package])
    return commands


def _install_command_for_package(project_root: str, package: str) -> list[str] | None:
    """Build an installer command targeting the project venv."""
    commands = _install_commands_for_package(project_root, package)
    return commands[0] if commands else None


def _collect_external_tool_gaps(project_root: str) -> dict[str, Any]:
    """Collect missing external tool information from the active registry."""
    from lintgate.config import load_config
    from lintgate.registry import build_registry

    config = load_config(project_root)
    registry = build_registry(config)

    tool_matrix: dict[str, dict[str, Any]] = {}
    for linter_name, linter in sorted(registry.items()):
        tool = linter.required_tool
        if not tool:
            continue
        entry = tool_matrix.setdefault(
            tool,
            {
                "tool": tool,
                "package": _tool_package_name(tool),
                "available": True,
                "required_by": [],
            },
        )
        entry["required_by"].append(linter_name)
        entry["available"] = entry["available"] and _linter_available(linter, project_root)

    missing_tools: list[dict[str, Any]] = []
    for tool in sorted(tool_matrix):
        entry = tool_matrix[tool]
        if entry["available"]:
            continue
        package = entry["package"]
        install_cmd = _install_command_for_package(project_root, package)
        missing_tools.append(
            {
                "tool": tool,
                "package": package,
                "required_by": entry["required_by"],
                "reason": "executable_not_found",
                "install_command": _format_cmd(install_cmd)
                if install_cmd
                else f"pip install {package}",
                "auto_installable": install_cmd is not None and tool in _OPTIONAL_STARTUP_PACKAGES,
            }
        )

    return {
        "tool_status": [tool_matrix[k] for k in sorted(tool_matrix)],
        "missing_tools": missing_tools,
    }


def _auto_install_optional_tools(
    project_root: str,
    missing_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attempt to install optional missing tools into the project venv."""
    ot = _ot()
    attempts: list[dict[str, Any]] = []
    for item in missing_tools:
        tool = str(item.get("tool", ""))
        package = str(item.get("package", tool))
        if tool not in _OPTIONAL_STARTUP_PACKAGES:
            continue

        cmds = ot._install_commands_for_package(project_root, package)
        if not cmds:
            attempts.append(
                {
                    "tool": tool,
                    "package": package,
                    "status": "skipped",
                    "reason": "no_project_venv_detected",
                }
            )
            continue

        command_results: list[dict[str, Any]] = []
        installed = False
        for cmd in cmds:
            try:
                result = ot.subprocess.run(
                    cmd,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except ot.subprocess.TimeoutExpired:
                command_results.append(
                    {
                        "status": "timeout",
                        "command": _format_cmd(cmd),
                        "reason": "install_timed_out_after_180s",
                    }
                )
                continue

            command_result = {
                "status": "installed" if result.returncode == 0 else "error",
                "command": _format_cmd(cmd),
                "returncode": result.returncode,
                "stderr_tail": (result.stderr or "")[-240:],
            }
            command_results.append(command_result)
            if result.returncode == 0:
                attempts.append(
                    {
                        "tool": tool,
                        "package": package,
                        "status": "installed",
                        "command": command_result["command"],
                        "returncode": 0,
                        "attempted_commands": command_results,
                    }
                )
                installed = True
                break

        if installed:
            continue

        last_result = command_results[-1] if command_results else {}
        attempts.append(
            {
                "tool": tool,
                "package": package,
                "status": "error",
                "reason": "all_install_commands_failed",
                "command": last_result.get("command"),
                "returncode": last_result.get("returncode"),
                "stderr_tail": last_result.get("stderr_tail"),
                "attempted_commands": command_results,
            }
        )

    return attempts
