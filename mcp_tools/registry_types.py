"""Tool registry contract for explicit MCP exports.

This module defines the callable signatures for all domain tools exported
via mcp_server.py. It replaces dynamic symbol lookup with a static,
type-checked contract.
"""

from collections.abc import Callable
from typing import Protocol


class ToolRegistry(Protocol):
    """The explicit export contract for LintGate MCP tools."""

    # -- onboarding_tools --
    getting_started: Callable[..., str]
    scaffold_config: Callable[..., str]

    # -- lint_tools --
    lint_files: Callable[..., str]
    lint_project: Callable[..., str]
    lint_get_details: Callable[..., str]
    lint_status: Callable[..., str]
    audit_tool_versions: Callable[..., str]
    lint_fix: Callable[..., str]

    # -- controlplane_tools --
    controlplane_run: Callable[..., str]
    controlplane_get_details: Callable[..., str]
    controlplane_status: Callable[..., str]
    controlplane_test_skeleton: Callable[..., str]
    controlplane_report_repair: Callable[..., str]
    controlplane_agent_feedback: Callable[..., str]
    controlplane_apply_repairs: Callable[..., str]

    # -- context_tools --
    context_guidance: Callable[..., str]
    audit_context_health: Callable[..., str]
    bootstrap_context_files: Callable[..., str]
    context_patch_review: Callable[..., str]
    context_patch_apply: Callable[..., str]
    extract_theory_constraints: Callable[..., str]
    extract_project_theory: Callable[..., str]
    build_theory_pack: Callable[..., str]
    get_theory_context: Callable[..., str]

    # -- compass_tools --
    compass_status: Callable[..., str]
    compass_check: Callable[..., str]
    compass_update: Callable[..., str]
    compass_interview: Callable[..., str]
    compass_reset: Callable[..., str]
    theory_mode_enter: Callable[..., str]
    theory_mode_freeze: Callable[..., str]
    setup_hooks: Callable[..., str]

    # -- behavior_tools --
    hygiene_check: Callable[..., str]
    constraint_check: Callable[..., str]
    prediction_register: Callable[..., str]
    behavior_precheck: Callable[..., str]
    global_memory_status: Callable[..., str]
    global_memory_reset: Callable[..., str]

    # -- and other modules (dynamic/backward compat layer can sit here, but core should be typed) --
    # For now, we will add explicit signatures to the main ones and allow Any for the rest.
