"""Onboarding helpers — getting_started, tool applicability, scaffold, setup_github_quality.

Extracted from onboarding_tools.py to keep the MCP registration file lean.

NOTE: Functions that tests patch at ``mcp_tools.onboarding_tools.*`` are
accessed through that module at call time (lazy import) so that
``unittest.mock.patch`` targets remain effective.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from typing import Any

from mcp_tools._disk_helpers import _safe_json

# ---------------------------------------------------------------------------
# Lazy accessor — resolves names through the parent module so that
# ``mock.patch("mcp_tools.onboarding_tools._foo")`` works correctly.
# ---------------------------------------------------------------------------


def _ot():  # noqa: ANN202
    """Return the onboarding_tools module (lazy to avoid circular imports)."""
    import mcp_tools.onboarding_tools as _mod

    return _mod


# ---------------------------------------------------------------------------
# Orchestration helpers called by _impl_getting_started
# ---------------------------------------------------------------------------


def _handle_config_and_venv(
    project_root: str, auto_setup: bool, startup_actions: list, helpers: Any
) -> dict[str, Any]:
    ot = _ot()
    config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
    if auto_setup and not os.path.exists(config_path):
        yaml_content = ot._scaffold_config_yaml(project_root, helpers)
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(yaml_content)
        startup_actions.append({"action": "config_scaffolded", "path": config_path})

    venv_setup: dict[str, Any] = {"status": "not_requested"}
    if auto_setup:
        venv_setup = ot._ensure_project_venv(project_root)
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
    ot = _ot()
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
            startup_actions.append({"action": "toolchain_drift_detected", "warnings": drift})

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
        tool_gaps = ot._collect_external_tool_gaps(project_root)
        if auto_install and tool_gaps["missing_tools"]:
            attempts = ot._auto_install_optional_tools(project_root, tool_gaps["missing_tools"])
            if attempts:
                startup_actions.append(
                    {
                        "action": "optional_tool_install_attempted",
                        "count": len(attempts),
                    }
                )
            return attempts  # type: ignore[no-any-return]
        return []


def _handle_quality_bootstrap(
    project_root: str, auto_setup: bool, startup_actions: list
) -> dict[str, Any]:
    ot = _ot()
    from lintgate.quality_infra import audit_quality_infrastructure
    from mcp_tools.setup_github_quality import setup_github_quality

    gh = ot._detect_github_remote(project_root)
    qi_audit = audit_quality_infrastructure(project_root)
    has_configs = qi_audit.complete and (qi_audit.has_github_remote or not gh.get("detected"))

    result: dict[str, Any] = {"status": "not_requested"}
    if auto_setup and gh.get("detected") and not has_configs:
        with suppress(Exception):
            raw = json.loads(setup_github_quality(path=project_root, write=True))
            # tool_response returns slim envelope — load full data from disk
            if "file" in raw and os.path.isfile(raw.get("file", "")):
                with open(raw["file"]) as _f:
                    result = json.loads(_f.read())
            else:
                result = raw
            startup_actions.append(
                {
                    "action": "github_quality_bootstrapped",
                    "status": result.get("status", "unknown"),
                }
            )
    return result


# ---------------------------------------------------------------------------
# Module-level constants and _impl functions
# ---------------------------------------------------------------------------

_DEFAULT_WORKFLOW = [
    "1. getting_started(path) applies startup setup automatically",
    "2. Run controlplane_run(path) for a full project health check",
    "3. Run controlplane_get_details(run_id) to review specific findings",
    "4. Run lint_fix(path, dry_run=False) to auto-fix safe issues",
    "5. Run bootstrap_context_files(path, write=true) to generate context",
]

_ESSENTIAL_TOOLS = {
    "lint_files": "Check specific files after edits",
    "lint_project": "Full project scan",
    "lint_fix": "Auto-fix safe issues",
    "controlplane_run": "6-channel health check",
    "controlplane_get_details": "Drill into health check findings",
    "bootstrap_context_files": "Generate project CLAUDE.md",
}


def _build_next_actions(
    project_root: str,
    config_status: dict[str, Any],
    venv_python_after: str | None,
    tool_gaps_after: dict[str, Any],
    workflow_mode: str | None = None,
) -> list[dict[str, str]]:
    """Build the next_actions list for getting_started output."""
    # Surgical mode: minimal next_actions — just the edit loop
    if workflow_mode == "surgical":
        return [
            {
                "tool": "declare_workflow",
                "reason": "Activate surgical mode for silent-on-clean editing",
                "example": f'declare_workflow(path="{project_root}", workflow="surgical")',
            },
            {
                "tool": "lint_files",
                "reason": "Your whole loop: Read → Edit → lint_files. That's it.",
                "example": 'lint_files(files=["<your-file>"], scope="surgical")',
            },
        ]

    ot = _ot()
    actions: list[dict[str, str]] = []

    # Workflow activation if intent mapped to a mode
    if workflow_mode:
        actions.append(
            {
                "tool": "declare_workflow",
                "reason": f"Activate {workflow_mode} mode",
                "example": f'declare_workflow(path="{project_root}", workflow="{workflow_mode}")',
            }
        )

    config_enabled = config_status.get("config_state") == "config_enabled"
    reason_suffix = "" if config_enabled else " (works without config)"
    actions.append(
        {
            "tool": "controlplane_run",
            "reason": f"Run a comprehensive health check{reason_suffix}",
            "example": f'controlplane_run(path="{project_root}")',
        }
    )
    if not config_enabled:
        actions.append(
            {
                "tool": "scaffold_config",
                "reason": "Generate/repair project-specific lintgate.yaml for persistent config",
                "example": f'scaffold_config(path="{project_root}", write=True)',
            }
        )
    if not venv_python_after:
        create_cmd, _ = ot._venv_create_command()
        actions.append(
            {
                "tool": "Bash",
                "reason": "Create project virtual environment for tool installs",
                "example": ot._format_cmd(create_cmd),
            }
        )
    for gap in tool_gaps_after.get("missing_tools", []):
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


def _detect_mutation_guard() -> bool:
    """Return True if the lintgate-pre system mutation guard hook is active."""
    try:
        with open(os.path.expanduser("~/.claude/settings.json")) as f:
            settings = json.load(f)
        for entry in settings.get("hooks", {}).get("PreToolUse", []):
            for h in entry.get("hooks", []):
                if "lintgate-pre" in str(h.get("command", "")):
                    return True
    except Exception:
        pass
    return False


def _impl_getting_started(
    helpers: dict,
    path: str,
    auto_setup: bool = True,
    auto_install_optional_linters: bool = True,
    reset: bool = False,
    intent: str | None = None,
) -> dict:
    """Core logic for the getting_started MCP tool."""
    ot = _ot()
    project_root = helpers["_validate_project_root"](path)
    config_status_before = helpers["_build_onboarding_status"](project_root)
    tool_gaps_before = ot._collect_external_tool_gaps(project_root)
    startup_actions: list[dict[str, Any]] = []

    if reset:
        startup_actions.extend(ot._reset_project_state(project_root))

    venv_setup = _handle_config_and_venv(project_root, auto_setup, startup_actions, helpers)
    install_attempts = _handle_tool_installs(
        project_root, auto_install_optional_linters, startup_actions
    )
    quality_bootstrap = _handle_quality_bootstrap(project_root, auto_setup, startup_actions)

    config_status = helpers["_build_onboarding_status"](project_root)
    tool_gaps_after = ot._collect_external_tool_gaps(project_root)
    venv_python_after = ot._project_venv_python(project_root)

    from lintgate.orchestration.workflows import get_workflow_for_intent, intent_to_workflow_mode

    custom_workflow = get_workflow_for_intent(intent)

    # Route intent to workflow mode + render guide
    workflow_mode = intent_to_workflow_mode(intent)
    workflow_guide = None
    if workflow_mode:
        try:
            from lintgate.workflow_guides import MODE_SPECS, render_guide

            spec = MODE_SPECS.get(workflow_mode)
            if spec:
                workflow_guide = render_guide(spec, project_root)
        except Exception:
            pass

    output: dict[str, Any] = {
        "project": project_root,
        "config_status": config_status,
        "essential_tools": _ESSENTIAL_TOOLS,
        "first_session_workflow": custom_workflow if custom_workflow else _DEFAULT_WORKFLOW,
        "intent": intent,
        "workflow_mode": workflow_mode,
        "workflow_guide": workflow_guide,
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
        "next_actions": _build_next_actions(
            project_root, config_status, venv_python_after, tool_gaps_after,
            workflow_mode=workflow_mode,
        ),
    }

    pre_hook_enabled = _detect_mutation_guard()
    output["system_mutation_guard"] = "active" if pre_hook_enabled else "inactive"
    if pre_hook_enabled:
        output["security_guidance"] = (
            "The System Mutation Guard (lintgate-pre) intercepts global state changes. "
            "Use # lintgate-override to bypass if required."
        )

    return output


def _impl_tool_applicability_guide(helpers: dict) -> str:
    """Core logic: render task-shape index from workflow guide specs."""
    try:
        from lintgate.workflow_guides import render_all_guides_summary

        guide_text = render_all_guides_summary()
    except Exception:
        guide_text = "Workflow guides unavailable."

    return _safe_json({"guide": guide_text, "format": "task_shape_index"})


def _impl_scaffold_config(helpers: dict, path: str, write: bool = False) -> str:
    """Core logic for the scaffold_config MCP tool."""
    ot = _ot()
    project_root = helpers["_validate_project_root"](path)
    config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
    existing_config = os.path.exists(config_path)
    yaml_content = ot._scaffold_config_yaml(project_root, helpers)

    if write:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(yaml_content)

    status = "written" if write else ("preview_existing" if existing_config else "preview")
    output: dict[str, Any] = {
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
    return _safe_json(output)


def _impl_setup_github_quality(
    helpers: dict,
    path: str,
    write: bool = False,
    sonar_token: str | None = None,
) -> str:
    """Core logic for the setup_github_quality MCP tool."""
    from mcp_tools.setup_github_quality import (
        setup_github_quality as _setup_github_quality_impl,
    )

    return _setup_github_quality_impl(
        path,
        write=write,
        sonar_token=sonar_token,
        _helpers=helpers,
    )
