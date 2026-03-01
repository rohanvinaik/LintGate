"""Onboarding tools — getting_started entry point for LintGate MCP."""

from __future__ import annotations

import glob as glob_mod
import json
import os
import shlex
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from mcp_tools.quality.discovery import (
    _parse_pyproject_metadata as _quality_parse_pyproject_metadata,
)
from mcp_tools.quality.discovery import (
    _scan_project_dirs as _quality_scan_project_dirs,
)
from mcp_tools.quality.rules_gen import (
    _normalize_qlty_exclude_pattern as _quality_normalize_qlty_exclude_pattern,
)
from mcp_tools.quality_helpers import (
    _BADGE_BLOCK_END,
    _BADGE_BLOCK_START,
    _README_NAMES,
    _REQUIRED_BADGE_FINGERPRINTS,
    _detect_github_remote,
)
from mcp_tools.quality_helpers import (
    _compute_gitignore_additions as _quality_compute_gitignore_additions,
)
from mcp_tools.quality_helpers import (
    _detect_sonar_scanner as _quality_detect_sonar_scanner,
)
from mcp_tools.quality_helpers import (
    _detect_subprocess_usage as _quality_detect_subprocess_usage,
)
from mcp_tools.quality_helpers import (
    _generate_qlty_toml as _quality_generate_qlty_toml,
)
from mcp_tools.quality_helpers import (
    _inject_badges_into_readme as _quality_inject_badges_into_readme,
)
from mcp_tools.quality_helpers import (
    _run_sonar_scanner as _quality_run_sonar_scanner,
)
from mcp_tools.quality_helpers import (
    _write_pre_push_hook as _quality_write_pre_push_hook,
)

_OPTIONAL_STARTUP_PACKAGES = {
    "pip-audit": "pip-audit",
    "ty": "ty",
}


# Backward-compat wrappers for helpers moved to mcp_tools.quality_helpers.
def _write_pre_push_hook(project_root: str, write: bool) -> dict[str, Any]:
    return _quality_write_pre_push_hook(project_root, write)


def _compute_gitignore_additions(project_root: str) -> dict[str, Any]:
    return _quality_compute_gitignore_additions(project_root)


def _inject_badges_into_readme(
    project_root: str,
    badge_markdown: str,
    write: bool,
) -> dict[str, Any]:
    return _quality_inject_badges_into_readme(
        project_root,
        badge_markdown,
        write=write,
    )


def _generate_qlty_toml(layout: dict[str, Any], *, is_tool_runner: bool = False) -> str:
    return _quality_generate_qlty_toml(layout, is_tool_runner=is_tool_runner)


def _normalize_qlty_exclude_pattern(pattern: str) -> str:
    return _quality_normalize_qlty_exclude_pattern(pattern)


def _detect_subprocess_usage(project_root: str) -> bool:
    return _quality_detect_subprocess_usage(project_root)


def _detect_sonar_scanner() -> str | None:
    return _quality_detect_sonar_scanner()


def _run_sonar_scanner(
    project_root: str,
    sonar_token: str,
    scanner_path: str,
) -> dict[str, Any]:
    return _quality_run_sonar_scanner(
        project_root,
        sonar_token,
        scanner_path,
    )


def _parse_pyproject_metadata(root: Path) -> tuple[str, str | None, list[str], bool]:
    return _quality_parse_pyproject_metadata(root)


def _scan_project_dirs(
    root: Path, test_dirs: list[str]
) -> tuple[list[str], list[str], list[str], str | None]:
    return _quality_scan_project_dirs(root, test_dirs)


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


def _linter_available(linter: Any, project_root: str) -> bool:
    """Check linter availability with backward-compatible signatures."""
    try:
        return bool(linter.available(project_root=project_root))
    except TypeError:
        return bool(linter.available())


def _venv_create_command() -> tuple[list[str], str]:
    """Build preferred venv creation command and manager label."""
    uv_path = shutil.which("uv")
    if uv_path:
        return [uv_path, "venv", ".venv"], "uv"
    return [sys.executable, "-m", "venv", ".venv"], "python_venv"


def _ensure_project_venv(project_root: str) -> dict[str, Any]:
    """Ensure a project-local virtualenv exists and has pip available."""
    existing = _project_venv_python(project_root)
    if existing:
        return {"status": "present", "venv_python": existing}

    create_cmd, manager = _venv_create_command()
    try:
        create_result = subprocess.run(
            create_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
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

    venv_python = _project_venv_python(project_root)
    if not venv_python:
        return {
            "status": "error",
            "manager": manager,
            "command": _format_cmd(create_cmd),
            "reason": "venv_created_but_python_missing",
        }

    pip_check_cmd = [venv_python, "-m", "pip", "--version"]
    try:
        pip_check = subprocess.run(
            pip_check_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
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
        ensure_result = subprocess.run(
            ensurepip_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
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
    venv_python = _project_venv_python(project_root)
    if not venv_python:
        return []

    commands: list[list[str]] = []
    uv_path = shutil.which("uv")
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
        entry["available"] = entry["available"] and _linter_available(
            linter, project_root
        )

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
                "auto_installable": install_cmd is not None
                and tool in _OPTIONAL_STARTUP_PACKAGES,
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

        cmds = _install_commands_for_package(project_root, package)
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
                result = subprocess.run(
                    cmd,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except subprocess.TimeoutExpired:
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


def _scaffold_config_yaml(project_root: str, helpers: dict) -> str:
    """Analyze project and generate a tailored lintgate.yaml."""
    lines: list[str] = []
    lines.append("# LintGate configuration — generated by scaffold_config")
    lines.append("# Review and adjust to match your project's needs.")
    lines.append("")

    # Detect Python source files for critical path analysis
    py_files = sorted(
        glob_mod.glob(os.path.join(project_root, "**", "*.py"), recursive=True)
    )
    # Exclude venv, __pycache__, .git
    py_files = [
        f
        for f in py_files
        if not any(
            seg in f for seg in ("/.venv/", "/__pycache__/", "/.git/", "/node_modules/")
        )
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
        lines.append(
            "  B603: informational  # subprocess calls — expected for tool orchestration"
        )
        lines.append(
            "  B107: informational  # hardcoded passwords — review if unexpected"
        )
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


# ---------------------------------------------------------------------------
# GitHub quality infrastructure helpers
# ---------------------------------------------------------------------------


# ── qlty (Code Climate CLI) triage patterns ─────────────────────────────

# Bandit rules that are domain-expected in test code

# Bandit rules that are expected in tool-runner projects (subprocess usage)

# Radarlint rules to set to monitor mode (intentional patterns)


def _readme_has_quality_badges(project_root: str) -> bool:
    """Return True if README contains the minimum badge fingerprints."""
    root = Path(project_root)
    readme_path: Path | None = None
    for name in _README_NAMES:
        candidate = root / name
        if candidate.exists():
            readme_path = candidate
            break

    if readme_path is None:
        return False

    try:
        content = readme_path.read_text(errors="ignore")
    except OSError:
        return False

    if _BADGE_BLOCK_START in content and _BADGE_BLOCK_END in content:
        start = content.find(_BADGE_BLOCK_START)
        end = content.find(_BADGE_BLOCK_END, start)
        if end == -1:
            return False
        managed_block = content[start : end + len(_BADGE_BLOCK_END)]
        return all(fp in managed_block for fp in _REQUIRED_BADGE_FINGERPRINTS)

    return all(fp in content for fp in _REQUIRED_BADGE_FINGERPRINTS)


def _reset_project_state(project_root: str) -> list[dict[str, str]]:
    """Reset project state directories while preserving config.

    Clears:
    - .claude/lintgate/state/     (session state, compass, habit)
    - .claude/lintgate/runs/      (lint run history)
    - .claude/lintgate/sessions/  (session memory)

    Does NOT touch:
    - .claude/lintgate.yaml       (config preserved)
    - .claude/lintgate/issue_memory.json (accumulated issue data)

    Returns list of {"action": ..., "path": ...} for audit.
    """
    actions: list[dict[str, str]] = []
    lintgate_dir = os.path.join(project_root, ".claude", "lintgate")

    state_dirs = ["state", "runs", "sessions"]
    for subdir in state_dirs:
        target = os.path.join(lintgate_dir, subdir)
        if os.path.isdir(target):
            shutil.rmtree(target)
            actions.append({"action": "reset_dir", "path": target})

    # Also clear project-hash-keyed habit state files under LINTGATE_HOME
    lintgate_home = os.environ.get("LINTGATE_HOME")
    habit_base = (
        Path(lintgate_home) / "habit_state"
        if lintgate_home
        else Path.home() / ".lintgate" / "habit_state"
    )
    if habit_base.is_dir():
        # Compute project hash to find matching habit state files
        import hashlib

        project_hash = hashlib.sha256(
            os.path.abspath(project_root).encode()
        ).hexdigest()[:12]
        for item in habit_base.iterdir():
            if item.is_file() and project_hash in item.name:
                item.unlink()
                actions.append({"action": "reset_file", "path": str(item)})

    return actions


def _handle_config_and_venv(
    project_root: str, auto_setup: bool, startup_actions: list, helpers: Any
) -> dict[str, Any]:
    config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
    if auto_setup and not os.path.exists(config_path):
        yaml_content = _scaffold_config_yaml(project_root, helpers)
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(yaml_content)
        startup_actions.append({"action": "config_scaffolded", "path": config_path})

    venv_setup: dict[str, Any] = {"status": "not_requested"}
    if auto_setup:
        venv_setup = _ensure_project_venv(project_root)
        if venv_setup.get("status") == "created":
            startup_actions.append(
                {
                    "action": "venv_provisioned",
                    "manager": venv_setup.get("manager"),
                    "venv_python": venv_setup.get("venv_python"),
                    "pip_ready": venv_setup.get("pip_ready"),
                }
            )
        elif venv_setup.get("status") in {"error", "timeout"}:
            startup_actions.append(
                {
                    "action": "venv_provision_failed",
                    "manager": venv_setup.get("manager"),
                    "reason": venv_setup.get("reason", "unknown"),
                }
            )
    return venv_setup


def _handle_tool_installs(
    project_root: str, auto_install: bool, startup_actions: list
) -> list[dict[str, Any]]:
    # Use the toolchain manifest system (gate_contract.yaml) as primary source.
    # Falls back to legacy _collect_external_tool_gaps if manifest unavailable.
    try:
        from lintgate.tool_manifest import (
            check_tool_health,
            install_missing_tools,
            load_toolchain_manifest,
            reconcile_with_registry,
        )

        manifest = load_toolchain_manifest(project_root)
        statuses = check_tool_health(project_root, manifest)

        # Report drift warnings (self-managing loop)
        drift = reconcile_with_registry(project_root)
        if drift:
            startup_actions.append(
                {"action": "toolchain_drift_detected", "warnings": drift}
            )

        if auto_install:
            results = install_missing_tools(project_root, statuses, auto_only=True)
            if results:
                startup_actions.append(
                    {"action": "toolchain_install_attempted", "count": len(results)}
                )
            return results
        return []

    except ImportError:
        # Fallback: legacy path (no tool_manifest module available)
        tool_gaps = _collect_external_tool_gaps(project_root)
        if auto_install and tool_gaps["missing_tools"]:
            attempts = _auto_install_optional_tools(
                project_root, tool_gaps["missing_tools"]
            )
            if attempts:
                startup_actions.append(
                    {
                        "action": "optional_tool_install_attempted",
                        "count": len(attempts),
                    }
                )
            return attempts
        return []


def _handle_quality_bootstrap(
    project_root: str, auto_setup: bool, startup_actions: list
) -> dict[str, Any]:
    from lintgate.quality_infra import audit_quality_infrastructure

    from .setup_github_quality import setup_github_quality

    gh = _detect_github_remote(project_root)
    qi_audit = audit_quality_infrastructure(project_root)
    has_configs = qi_audit.complete and (
        qi_audit.has_github_remote or not gh.get("detected")
    )

    result: dict[str, Any] = {"status": "not_requested"}
    if auto_setup and gh.get("detected") and not has_configs:
        with suppress(Exception):
            result = json.loads(setup_github_quality(path=project_root, write=True))
            startup_actions.append(
                {
                    "action": "github_quality_bootstrapped",
                    "status": result.get("status", "unknown"),
                }
            )
    return result


def register(mcp, helpers):
    """Register onboarding tools on the shared MCP instance."""

    @mcp.tool()
    def getting_started(
        path: str,
        auto_setup: bool = True,
        auto_install_optional_linters: bool = True,
        reset: bool = False,
        intent: str | None = None,
    ) -> str:
        """Start here. Get oriented with LintGate on any project."""
        project_root = helpers["_validate_project_root"](path)
        config_status_before = helpers["_build_onboarding_status"](project_root)
        tool_gaps_before = _collect_external_tool_gaps(project_root)
        startup_actions: list[dict[str, Any]] = []

        if reset:
            startup_actions.extend(_reset_project_state(project_root))

        venv_setup = _handle_config_and_venv(
            project_root, auto_setup, startup_actions, helpers
        )
        install_attempts = _handle_tool_installs(
            project_root, auto_install_optional_linters, startup_actions
        )
        quality_bootstrap = _handle_quality_bootstrap(
            project_root, auto_setup, startup_actions
        )

        config_status = helpers["_build_onboarding_status"](project_root)
        tool_gaps_after = _collect_external_tool_gaps(project_root)
        venv_python_after = _project_venv_python(project_root)

        def _build_next_actions() -> list[dict[str, str]]:
            actions = []
            reason_suffix = (
                " (works without config)"
                if config_status["config_state"] != "config_enabled"
                else ""
            )
            actions.append(
                {
                    "tool": "controlplane_run",
                    "reason": f"Run a comprehensive health check{reason_suffix}",
                    "example": f'controlplane_run(path="{project_root}")',
                }
            )
            if config_status["config_state"] != "config_enabled":
                actions.append(
                    {
                        "tool": "scaffold_config",
                        "reason": "Generate/repair project-specific lintgate.yaml for persistent config",
                        "example": f'scaffold_config(path="{project_root}", write=True)',
                    }
                )
            if not venv_python_after:
                create_cmd, _ = _venv_create_command()
                actions.append(
                    {
                        "tool": "Bash",
                        "reason": "Create project virtual environment for tool installs",
                        "example": _format_cmd(create_cmd),
                    }
                )
            for gap in tool_gaps_after["missing_tools"]:
                actions.append(
                    {
                        "tool": "Bash",
                        "reason": f"Install missing tool '{gap['tool']}'",
                        "example": gap["install_command"],
                    }
                )
            if not os.path.exists(os.path.join(project_root, ".claude", "CLAUDE.md")):
                actions.append(
                    {
                        "tool": "bootstrap_context_files",
                        "reason": "Generate project-specific CLAUDE.md",
                        "example": f'bootstrap_context_files(path="{project_root}", write=True)',
                    }
                )
            actions.append(
                {
                    "tool": "lint_project",
                    "reason": "Full project lint scan",
                    "example": f'lint_project(path="{project_root}")',
                }
            )
            return actions

        from lintgate.orchestration.workflows import get_workflow_for_intent

        custom_workflow = get_workflow_for_intent(intent)

        output: dict[str, Any] = {
            "project": project_root,
            "config_status": config_status,
            "essential_tools": {
                "lint_files": "Check specific files after edits",
                "lint_project": "Full project scan",
                "lint_fix": "Auto-fix safe issues",
                "controlplane_run": "6-channel health check",
                "controlplane_get_details": "Drill into health check findings",
                "bootstrap_context_files": "Generate project CLAUDE.md",
            },
            "first_session_workflow": custom_workflow
            if custom_workflow
            else [
                "1. getting_started(path) applies startup setup automatically",
                "2. Run controlplane_run(path) for a full project health check",
                "3. Run controlplane_get_details(run_id) to review specific findings",
                "4. Run lint_fix(path, dry_run=False) to auto-fix safe issues",
                "5. Run bootstrap_context_files(path, write=true) to generate context",
            ],
            "intent": intent,
            "all_tools_count": 49,
            "startup_setup": {
                "auto_setup_requested": auto_setup,
                "config_status_before": config_status_before,
                "config_status_after": config_status,
                "missing_tools_before": tool_gaps_before["missing_tools"],
                "missing_tools_after": tool_gaps_after["missing_tools"],
                "venv_setup": venv_setup,
                "venv_python": venv_python_after,
                "install_attempts": install_attempts,
                "github_quality": quality_bootstrap,
                "actions_applied": startup_actions,
            },
            "next_actions": _build_next_actions(),
        }

        # Determine system mutation guard status
        pre_hook_enabled = False
        try:
            with open(os.path.expanduser("~/.claude/settings.json")) as f:
                settings = json.load(f)
                hooks = settings.get("hooks", {})
                pre = hooks.get("PreToolUse", [])
                for entry in pre:
                    for h in entry.get("hooks", []):
                        if "lintgate-pre" in str(h.get("command", "")):
                            pre_hook_enabled = True
                            break
        except Exception:
            pass

        output["system_mutation_guard"] = "active" if pre_hook_enabled else "inactive"
        if pre_hook_enabled:
            output["security_guidance"] = (
                "The System Mutation Guard (lintgate-pre) intercepts global state changes. "
                "Use # lintgate-override to bypass if required."
            )

        json_dumps = helpers.get("_json_dumps")
        if json_dumps:
            return json_dumps(output, output_mode="compact")
        return json.dumps(output, indent=2)

    @mcp.tool()
    def tool_applicability_guide() -> str:
        """Returns the definitive guide on when and how to use each LintGate MCP tool.

        This covers cadence (how often to run), triggers (what events should prompt a run),
        and anti-patterns (when NOT to use the tool).
        """
        guide = {
            "controlplane_run": {
                "purpose": "Comprehensive cross-dimensional project health check.",
                "cadence": "Every 3-5 tool uses, or when starting a new session/feature.",
                "triggers": [
                    "Session start",
                    "Major refactor complete",
                    "Before pushing code",
                    "Ship gate parity mismatch",
                ],
                "anti_patterns": [
                    "Running in the middle of a tight file edit cycle",
                    "Running multiple times without changing code",
                ],
            },
            "lint_files": {
                "purpose": "Targeted static analysis on specific files.",
                "cadence": "After every edit or small batch of edits.",
                "triggers": ["File modifications", "Pre-commit check on changed files"],
                "anti_patterns": ["Using lint_project when only a few files changed"],
            },
            "lint_project": {
                "purpose": "Full repository static analysis.",
                "cadence": "Rarely, mostly via controlplane_run.",
                "triggers": ["CI/CD pipelines", "Global configuration changes"],
                "anti_patterns": ["Iterative debugging (use lint_files instead)"],
            },
            "lint_fix": {
                "purpose": "Auto-apply safe linting and formatting fixes.",
                "cadence": "When tools report auto-fixable errors.",
                "triggers": [
                    "Ruff or Black complain about formatting",
                    "Imports need sorting",
                ],
                "anti_patterns": [
                    "Running blindly without checking git status if working outside of a safe environment"
                ],
            },
            "scaffold_config": {
                "purpose": "Generate or repair lintgate.yaml for the project.",
                "cadence": "Once per project setup.",
                "triggers": ["No config exists", "Need to override specific behaviors"],
                "anti_patterns": [
                    "Running continuously",
                    "Overwriting manually tuned configs without caution",
                ],
            },
            "getting_started": {
                "purpose": "Onboarding entry point for LintGate.",
                "cadence": "Onboarding only.",
                "triggers": ["First time using LintGate in a repository"],
                "anti_patterns": ["Running during regular development workflows"],
            },
        }

        json_dumps = helpers.get("_json_dumps")
        if json_dumps:
            return json_dumps(guide)
        return json.dumps(guide, indent=2)

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

        status = (
            "written"
            if write
            else ("preview_existing" if existing_config else "preview")
        )
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
            output["message"] = (
                "Config already exists. Returning scaffold preview only."
            )
        return json.dumps(output, indent=2)

    @mcp.tool()
    def setup_github_quality(
        path: str,
        write: bool = False,
        sonar_token: str | None = None,
    ) -> str:
        """Set up GitHub code quality badges and infrastructure for a project.

        WHEN TO USE: After getting_started when you want to add code quality
        badges and CI configuration to a project. Detects GitHub remote,
        project layout, and generates tailored configs for Code Climate,
        SonarCloud, qlty CLI, .gitignore augmentation, and README badge injection.

        Generates fifteen artifacts:
        - .codeclimate.yml — Code Climate / qlty Cloud config
        - sonar-project.properties — SonarCloud scanner config
        - .coveragerc — shared coverage scope for CI/Sonar workflows
        - .gitleaks.toml — gitleaks baseline config (extends defaults)
        - .github/workflows/sonarcloud.yml — SonarCloud analysis on push/PR
        - .github/workflows/qlty.yml — qlty analysis on push/PR
        - .github/workflows/security-lite.yml — secrets + SAST + supply-chain checks
        - .github/workflows/scorecard.yml — OpenSSF Scorecard analysis
        - .github/workflows/quality-infra-gate.yml — hard gate for infra completeness
        - .github/dependabot.yml — automated dependency updates
        - SECURITY.md — responsible disclosure policy
        - .qlty/qlty.toml — qlty analysis config with smart triage (commit to repo)
        - .githooks/pre-push — local git pre-push quality gate (with infra enforcement)
        - .gitignore augmentation — standard Python patterns
        - README badge injection — quality badges after title (8 badges + license)

        Default mode is non-destructive (write=false) — returns previews
        of all generated files. Set write=true to create/modify files.

        When sonar_token is provided with write=true, runs sonar-scanner
        to push initial analysis to SonarCloud (activates the badge).
        The token is passed via environment variable — never written to
        any file that could be committed.

        Example: setup_github_quality(path="/my/project", write=True)
        Example: setup_github_quality(path="/my/project", write=True,
                 sonar_token="your_token_here")
        """
        from mcp_tools.setup_github_quality import (
            setup_github_quality as _setup_github_quality_impl,
        )

        return _setup_github_quality_impl(
            path,
            write=write,
            sonar_token=sonar_token,
            _helpers=helpers,
        )

    return {
        "getting_started": getting_started,
        "scaffold_config": scaffold_config,
        "setup_github_quality": setup_github_quality,
        "tool_applicability_guide": tool_applicability_guide,
    }
