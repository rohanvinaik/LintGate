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
- performance_tools: inspect_algebra, generate_property_tests
- test_effectiveness_tools: analyze_test_strength, inspect_test_assertions
- behavior_tools: hygiene_check, constraint_check, prediction_register,
                  behavior_precheck (deprecated), global_memory_status, global_memory_reset
- model_tools: model_profile_status, model_profile_probe_start, model_profile_probe_submit
- bootstrap_tools: bootstrap_tests, bootstrap_status
- telemetry_tools: telemetry_summary
- habit_tools: declare_mode, habit_status, habit_compact, habit_configure
- compass_tools: compass_status, compass_check, compass_update, compass_interview,
                  compass_reset, theory_mode_enter, theory_mode_freeze, setup_hooks
- refactor_tools: refactor_checkpoint, refactor_resume, refactor_thesis
- convergence_tools: convergence_analyze, extraction_plan, optimization_landscape
- gh_tools: project_organize_audit, project_organize_apply, project_wiki_sync, project_wiki_read
- wiki_tools: wiki_materialize, wiki_status, wiki_publish_pages, wiki_check_links
- specification_tools: spec_analyze, spec_prescribe, spec_composition, spec_gate_check
- mutation_tools: mutation_run_sampling, mutation_run_full, mutation_get_state,
                  mutation_prescribe, mutation_decompose, mutation_refactor_loop,
                  mutation_prescribe_tests, mutation_validate_tests, mutation_clear_state
- contract_tools: contract_audit
- test_hygiene_tools: test_hygiene_scan
- cold_start_tools: test_triage, test_infer_inputs, test_characterize, test_characterize_mark
- redundancy_tools: test_redundancy_project
"""

from . import (
    behavior_tools,
    bootstrap_tools,
    cold_start_tools,
    compass_tools,
    context_tools,
    contract_tools,
    controlplane_tools,
    convergence_tools,
    dep_tools,
    gh_tools,
    habit_tools,
    lint_tools,
    model_tools,
    mutation_tools,
    nsil_tools,
    onboarding_tools,
    performance_tools,
    redundancy_tools,
    refactor_tools,
    specification_tools,
    telemetry_tools,
    test_effectiveness_tools,
    test_hygiene_tools,
    wiki_tools,
)

ALL_MODULES = [
    onboarding_tools,
    lint_tools,
    context_tools,
    dep_tools,
    controlplane_tools,
    performance_tools,
    test_effectiveness_tools,
    behavior_tools,
    model_tools,
    telemetry_tools,
    habit_tools,
    compass_tools,
    bootstrap_tools,
    nsil_tools,
    refactor_tools,
    convergence_tools,
    gh_tools,
    wiki_tools,
    specification_tools,
    mutation_tools,
    contract_tools,
    test_hygiene_tools,
    cold_start_tools,
    redundancy_tools,
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
