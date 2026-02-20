"""Onboarding tools — getting_started entry point for LintGate MCP."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

_OPTIONAL_STARTUP_PACKAGES = {
    "pip-audit": "pip-audit",
    "ty": "ty",
}


def _tool_package_name(tool: str) -> str:
    """Map executable names to pip package names."""
    if tool in _OPTIONAL_STARTUP_PACKAGES:
        return _OPTIONAL_STARTUP_PACKAGES[tool]
    return tool


def _project_venv_python(project_root: str) -> str | None:
    """Return project venv python path, if present."""
    for venv_name in (".venv", "venv", "env"):
        py = Path(project_root) / venv_name / "bin" / "python"
        if py.exists() and py.is_file():
            return str(py)
    return None


def _format_cmd(cmd: list[str]) -> str:
    """Render shell-safe command text for output payloads."""
    return " ".join(shlex.quote(part) for part in cmd)


def _install_command_for_package(project_root: str, package: str) -> list[str] | None:
    """Build an installer command targeting the project venv."""
    venv_python = _project_venv_python(project_root)
    if not venv_python:
        return None
    return [venv_python, "-m", "pip", "install", package]


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
        entry["available"] = entry["available"] and linter.available(project_root=project_root)

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
                "install_command": _format_cmd(install_cmd) if install_cmd else f"pip install {package}",
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
    attempts: list[dict[str, Any]] = []
    for item in missing_tools:
        tool = str(item.get("tool", ""))
        package = str(item.get("package", tool))
        if tool not in _OPTIONAL_STARTUP_PACKAGES:
            continue

        cmd = _install_command_for_package(project_root, package)
        if not cmd:
            attempts.append(
                {
                    "tool": tool,
                    "package": package,
                    "status": "skipped",
                    "reason": "no_project_venv_detected",
                }
            )
            continue

        try:
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            attempts.append(
                {
                    "tool": tool,
                    "package": package,
                    "status": "timeout",
                    "command": _format_cmd(cmd),
                    "reason": "install_timed_out_after_180s",
                }
            )
            continue

        attempts.append(
            {
                "tool": tool,
                "package": package,
                "status": "installed" if result.returncode == 0 else "error",
                "command": _format_cmd(cmd),
                "returncode": result.returncode,
                "stderr_tail": (result.stderr or "")[-240:],
            }
        )

    return attempts


def _scaffold_config_yaml(project_root: str, helpers: dict) -> str:
    """Analyze project and generate a tailored lintgate.yaml."""
    import glob as glob_mod

    lines: list[str] = []
    lines.append("# LintGate configuration — generated by scaffold_config")
    lines.append("# Review and adjust to match your project's needs.")
    lines.append("")

    # Detect Python source files for critical path analysis
    py_files = sorted(glob_mod.glob(os.path.join(project_root, "**", "*.py"), recursive=True))
    # Exclude venv, __pycache__, .git
    py_files = [
        f for f in py_files
        if not any(seg in f for seg in ("/.venv/", "/__pycache__/", "/.git/", "/node_modules/"))
    ]

    # Find large files (potential critical paths)
    critical_paths: list[str] = []
    for fpath in py_files:
        try:
            with open(fpath) as f:
                line_count = sum(1 for _ in f)
            if line_count > 300:
                rel = os.path.relpath(fpath, project_root)
                critical_paths.append(rel)
        except OSError:
            continue

    if critical_paths:
        lines.append("pipeline_critical_paths:")
        for cp in sorted(critical_paths)[:10]:
            lines.append(f'  - "{cp}"')
        lines.append("")

    # Check if project uses subprocess-heavy patterns (tool-orchestration)
    has_subprocess = False
    for fpath in py_files[:50]:  # Sample first 50 files
        try:
            with open(fpath) as f:
                content = f.read(8192)
            if "subprocess" in content:
                has_subprocess = True
                break
        except OSError:
            continue

    if has_subprocess:
        lines.append("severity_overrides:")
        lines.append("  B603: informational  # subprocess calls — expected for tool orchestration")
        lines.append("  B107: informational  # hardcoded passwords — review if unexpected")
        lines.append("")

    # ControlPlane config
    lines.append("controlplane:")
    lines.append("  enabled: true")
    lines.append("  severity_weighted_coherence: true")
    lines.append("  channels:")
    lines.append("    behavior:")
    lines.append("      enabled: true")
    lines.append("      thresholds:")
    lines.append("        approach_cycling_count: 3")
    lines.append("        failure_amnesia_lookback: 30")
    lines.append("  inquiry:")
    lines.append("    theory_grounded_signals: true")
    lines.append("    prediction_tracking: true")
    lines.append("    theory_coherence_check: true")
    lines.append("    living_context: true")
    lines.append("    session_gate: true")
    lines.append("")

    return "\n".join(lines) + "\n"


def register(mcp, helpers):
    """Register onboarding tools on the shared MCP instance."""

    @mcp.tool()
    def getting_started(
        path: str,
        auto_setup: bool = True,
        auto_install_optional_linters: bool = True,
    ) -> str:
        """Start here. Get oriented with LintGate on any project.

        WHEN TO USE: First time using LintGate on a project, or when unsure
        what to do next. Returns project status, recommended next steps, and
        the essential tool workflow.

        Startup automation (default ON):
        - Auto-generates .claude/lintgate.yaml when missing
        - Detects missing linter executables with install commands
        - Attempts auto-install of optional tools (ty, pip-audit) in project venv

        Example: getting_started(path="/my/project")
        """
        project_root = helpers["_validate_project_root"](path)
        config_path = os.path.join(project_root, ".claude", "lintgate.yaml")

        config_status_before = helpers["_build_onboarding_status"](project_root)
        startup_actions: list[dict[str, Any]] = []

        if auto_setup and not os.path.exists(config_path):
            yaml_content = _scaffold_config_yaml(project_root, helpers)
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w") as f:
                f.write(yaml_content)
            startup_actions.append(
                {
                    "action": "config_scaffolded",
                    "path": config_path,
                }
            )

        tool_gaps_before = _collect_external_tool_gaps(project_root)
        install_attempts: list[dict[str, Any]] = []
        if auto_install_optional_linters and tool_gaps_before["missing_tools"]:
            install_attempts = _auto_install_optional_tools(
                project_root,
                tool_gaps_before["missing_tools"],
            )
            if install_attempts:
                startup_actions.append(
                    {
                        "action": "optional_tool_install_attempted",
                        "count": len(install_attempts),
                    }
                )

        config_status = helpers["_build_onboarding_status"](project_root)
        tool_gaps_after = _collect_external_tool_gaps(project_root)

        # Build dynamic next_actions based on project state
        next_actions: list[dict[str, str]] = []
        if config_status["config_state"] != "config_enabled":
            next_actions.append(
                {
                    "tool": "controlplane_run",
                    "reason": "Run a comprehensive health check (works without config)",
                    "example": f'controlplane_run(path="{project_root}")',
                }
            )
        else:
            next_actions.append(
                {
                    "tool": "controlplane_run",
                    "reason": "Run a comprehensive health check",
                    "example": f'controlplane_run(path="{project_root}")',
                }
            )

        # Suggest scaffold_config when startup automation is disabled or config still not enabled
        if config_status["config_state"] != "config_enabled":
            next_actions.append(
                {
                    "tool": "scaffold_config",
                    "reason": "Generate/repair project-specific lintgate.yaml for persistent config",
                    "example": f'scaffold_config(path="{project_root}", write=True)',
                }
            )

        # Surface explicit install actions if tools remain missing after auto-install.
        for gap in tool_gaps_after["missing_tools"]:
            next_actions.append(
                {
                    "tool": "Bash",
                    "reason": (
                        f"Install missing tool '{gap['tool']}' required by "
                        f"{', '.join(gap['required_by'])}"
                    ),
                    "example": gap["install_command"],
                }
            )

        # Check if bootstrap files exist
        claude_md = os.path.join(project_root, ".claude", "CLAUDE.md")
        if not os.path.exists(claude_md):
            next_actions.append(
                {
                    "tool": "bootstrap_context_files",
                    "reason": "Generate project-specific CLAUDE.md with documented principles",
                    "example": f'bootstrap_context_files(path="{project_root}", write=True)',
                }
            )

        next_actions.append(
            {
                "tool": "lint_project",
                "reason": "Full project lint scan",
                "example": f'lint_project(path="{project_root}")',
            }
        )

        output: dict[str, Any] = {
            "project": project_root,
            "config_status": config_status,
            "essential_tools": {
                "lint_files": "Check specific files after edits — "
                'lint_files(files=["/path/to/file.py"])',
                "lint_project": 'Full project scan — lint_project(path="/my/project")',
                "lint_fix": 'Auto-fix safe issues — lint_fix(path="/my/project", dry_run=False)',
                "controlplane_run": "6-channel health check (lint + tests + deps + git + behavior + structure) — "
                'controlplane_run(path="/my/project")',
                "controlplane_get_details": "Drill into health check findings — "
                'controlplane_get_details(run_id="...")',
                "bootstrap_context_files": "Generate project CLAUDE.md — "
                'bootstrap_context_files(path="/my/project", write=True)',
            },
            "first_session_workflow": [
                "1. getting_started(path) applies startup setup automatically",
                "2. Run controlplane_run(path) for a full project health check",
                "3. Run controlplane_get_details(run_id) to review specific findings",
                "4. Run lint_fix(path, dry_run=False) to auto-fix safe issues",
                "5. Run bootstrap_context_files(path, write=true) to generate persistent context files",
            ],
            "all_tools_count": 36,
            "startup_setup": {
                "auto_setup_requested": auto_setup,
                "auto_install_optional_linters_requested": auto_install_optional_linters,
                "config_status_before": config_status_before,
                "config_status_after": config_status,
                "missing_tools_before": tool_gaps_before["missing_tools"],
                "install_attempts": install_attempts,
                "missing_tools_after": tool_gaps_after["missing_tools"],
                "actions_applied": startup_actions,
                "startup_ready": (
                    config_status["config_state"] == "config_enabled"
                    and len(tool_gaps_after["missing_tools"]) == 0
                ),
            },
            "next_actions": next_actions,
        }

        return json.dumps(output, indent=2)

    @mcp.tool()
    def scaffold_config(path: str, write: bool = False) -> str:
        """Generate a project-specific lintgate.yaml from observed signals.

        WHEN TO USE: After running controlplane_run and reviewing findings.
        Analyzes the project to produce a tailored config with:
        - ControlPlane enabled with sensible channel defaults
        - Severity overrides for domain-expected bandit findings
        - Pipeline critical paths from file-too-long / CC hotspots
        - Inquiry features enabled

        Default mode is non-destructive (write=false) — returns the YAML
        for review. Set write=true to create .claude/lintgate.yaml.

        Example: scaffold_config(path="/my/project", write=True)
        """
        project_root = helpers["_validate_project_root"](path)
        config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
        existing_config = os.path.exists(config_path)
        yaml_content = _scaffold_config_yaml(project_root, helpers)

        if write:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w") as f:
                f.write(yaml_content)

        status = "written" if write else ("preview_existing" if existing_config else "preview")
        output = {
            "status": status,
            "path": config_path,
            "yaml": yaml_content,
            "next_actions": [
                {
                    "tool": "controlplane_run",
                    "reason": "Verify the new config works correctly",
                    "example": f'controlplane_run(path="{project_root}")',
                },
            ],
        }
        if existing_config and not write:
            output["message"] = "Config already exists. Returning scaffold preview only."
        return json.dumps(output, indent=2)

    return {"getting_started": getting_started, "scaffold_config": scaffold_config}
