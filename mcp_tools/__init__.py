"""MCP tool domain modules for LintGate.

Each module registers its tools on a shared FastMCP instance via
register(mcp, helpers) functions. The main mcp_server.py
calls each registration function to assemble the full tool surface.

Domain modules:
- onboarding_tools: getting_started, scaffold_config, setup_github_quality
- lint_tools: lint_files, lint_project, lint_get_details, lint_status, audit_tool_versions, lint_fix
- context_tools: context_guidance, audit_context_health, bootstrap_context_files,
                  context_patch_review, context_patch_apply, extract_theory_constraints,
                  extract_project_theory, build_theory_pack, get_theory_context
- dep_tools: dep_health_check, dep_sync
- controlplane_tools: controlplane_run, controlplane_get_details, controlplane_status,
                      controlplane_test_skeleton, controlplane_report_repair,
                      controlplane_agent_feedback, controlplane_apply_repairs
- behavior_tools: hygiene_check, constraint_check, prediction_register,
                  behavior_precheck (deprecated), global_memory_status, global_memory_reset
- model_tools: model_profile_status, model_profile_probe_start, model_profile_probe_submit
- telemetry_tools: telemetry_summary
"""

from . import (
    behavior_tools,
    context_tools,
    controlplane_tools,
    dep_tools,
    lint_tools,
    model_tools,
    onboarding_tools,
    telemetry_tools,
)

ALL_MODULES = [
    onboarding_tools,
    lint_tools,
    context_tools,
    dep_tools,
    controlplane_tools,
    behavior_tools,
    model_tools,
    telemetry_tools,
]


def register_all(mcp, helpers):
    """Register all domain tool modules on the shared MCP instance.

    Returns a dict mapping tool function names to their callable objects,
    so the caller can expose them as module-level attributes for backward
    compatibility (e.g. ``from mcp_server import behavior_precheck``).
    """
    all_tools = {}
    for module in ALL_MODULES:
        tool_funcs = module.register(mcp, helpers)
        if tool_funcs:
            all_tools.update(tool_funcs)
    return all_tools
