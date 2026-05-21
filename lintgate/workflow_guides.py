"""Workflow mode guide templates — data-driven orientation for each session mode.

Each ModeSpec defines:
- The edit loop for that mode
- Which tools to ignore (reduce cognitive load)
- Escalation triggers that suggest switching modes
- A rendered guide template that reads current project state

All tool names referenced in ModeSpecs must exist in @mcp.tool() registry.
CI check: verify_mode_specs() validates this.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeSpec:
    """Specification for a workflow mode's edit loop and signal policy."""

    name: str
    description: str
    loop_steps: list[str]  # Tool names in order
    tools_to_ignore: list[str]  # Tools irrelevant in this mode
    escalation_triggers: list[str]  # When to suggest switching modes
    hook_policy: str  # Description of PostToolUse behavior


# ── Mode Specifications ─────────────────────────────────────────────

SURGICAL = ModeSpec(
    name="surgical",
    description="Narrow edits on an existing, trusted codebase. Silent-on-clean.",
    loop_steps=[
        "Read(file)",
        "Edit(file, old, new)",
        "lint_files([file], scope='surgical')",
    ],
    tools_to_ignore=[
        "controlplane_run", "compass_update", "compass_interview",
        "platonic_project", "platonic_converge", "platonic_sweep",
        "mutation_run_sampling", "mutation_run_full", "mutation_decompose",
        "prescriptive_spec_compose", "prescriptive_spec_compile",
        "test_rebuild_plan", "test_rebuild_generate",
        "refactor_extract_method", "refactor_move",
        "spec_file_analyze", "spec_project_rollup",
    ],
    escalation_triggers=[
        "A single edit produces >5 findings — consider refactor mode",
        "lint_files reports coherence break across files — investigate",
        "You are about to create a new file — consider greenfield mode",
    ],
    hook_policy=(
        "PostToolUse is silent when your edit is clean. "
        "Findings are surfaced only when attributable to your edit. "
        "Heartbeat every 5 clean edits confirms the channel is live."
    ),
)

REFACTOR = ModeSpec(
    name="refactor",
    description="Structural changes across modules. Full channel output.",
    loop_steps=[
        "compass_update(path, write=True)",
        "controlplane_run(path)",
        "mutation_decompose(path, file)",
        "refactor_extract_method(path, file, function)",
        "lint_files([changed_files])",
        "mutation_validate_tests(path, file)",
    ],
    tools_to_ignore=[
        "prescriptive_spec_compose", "prescriptive_spec_compile",
    ],
    escalation_triggers=[
        "All files show clean lint — consider surgical for remaining touches",
        "New module needed — switch to greenfield for that module",
    ],
    hook_policy="Full controlplane output. All channels active.",
)

GREENFIELD = ModeSpec(
    name="greenfield",
    description="Writing new code from scratch. Prescriptive spec pipeline.",
    loop_steps=[
        "prescriptive_spec_compose(path, target, description, claims)",
        "prescriptive_spec_compile(path, target)",
        "Write code guided by generation_prompt",
        "prescriptive_spec_verify(path, target)",
        "platonic_converge(path, file)",
    ],
    tools_to_ignore=[
        "refactor_extract_method", "refactor_move",
        "mutation_decompose",
    ],
    escalation_triggers=[
        "Editing existing code more than writing new — switch to surgical",
        "Cross-module dependencies emerging — consider refactor mode",
    ],
    hook_policy="Full controlplane output. Prescriptive spec advisories active.",
)

EXPLORE = ModeSpec(
    name="explore",
    description="Read-heavy orientation. Relaxed linting.",
    loop_steps=[
        "controlplane_run(path)",
        "controlplane_get_details(run_id)",
        "bootstrap_context_files(path, write=True)",
    ],
    tools_to_ignore=[
        "mutation_run_sampling", "mutation_run_full",
        "prescriptive_spec_compose", "prescriptive_spec_compile",
        "refactor_extract_method", "refactor_move",
    ],
    escalation_triggers=[
        "Ready to make changes — switch to surgical or refactor",
    ],
    hook_policy="Relaxed. Warnings downgraded. Focus on orientation.",
)

DEBUG_SPIRAL = ModeSpec(
    name="debug_spiral",
    description="Recovery from repeated failures. Constraint-first.",
    loop_steps=[
        "controlplane_run(path)",
        "constraint_check(path, prediction)",
        "mutation_decompose(path, file)",
        "mutation_prescribe(path, file)",
    ],
    tools_to_ignore=[
        "prescriptive_spec_compose", "platonic_project",
    ],
    escalation_triggers=[
        "Constraint accuracy >70% after 5+ predictions — safe to resume normal mode",
        "Root cause identified — switch to surgical for the fix",
    ],
    hook_policy="Full output. Behavioral channel amplified. Constraint codas attached.",
)

# ── Registry ────────────────────────────────────────────────────────

MODE_SPECS: dict[str, ModeSpec] = {
    "surgical": SURGICAL,
    "refactor": REFACTOR,
    "greenfield": GREENFIELD,
    "explore": EXPLORE,
    "debug_spiral": DEBUG_SPIRAL,
}


def render_guide(spec: ModeSpec, project_root: str = "") -> str:
    """Render a mode-specific guide for the agent.

    Reads current project state to ground the guide in reality.
    """
    lines: list[str] = []
    lines.append(f"MODE: {spec.name}")
    lines.append(f"  {spec.description}")
    lines.append("")

    lines.append("LOOP:")
    for i, step in enumerate(spec.loop_steps, 1):
        lines.append(f"  {i}. {step}")
    lines.append("")

    lines.append("HOOK POLICY:")
    lines.append(f"  {spec.hook_policy}")
    lines.append("")

    if spec.tools_to_ignore:
        lines.append("NOT NEEDED IN THIS MODE:")
        # Group into lines of ~3 tools each for readability
        ignore = spec.tools_to_ignore
        for i in range(0, len(ignore), 3):
            chunk = ", ".join(ignore[i : i + 3])
            lines.append(f"  {chunk}")
        lines.append("")

    lines.append("ESCALATE IF:")
    for trigger in spec.escalation_triggers:
        lines.append(f"  - {trigger}")

    # Ground with project state if available
    if project_root:
        try:
            from lintgate.runtime_state import load_runtime_state

            runtime = load_runtime_state(project_root)
            if runtime is not None:
                lines.append("")
                lines.append("CURRENT STATE:")
                lines.append(f"  Coherence: {runtime.coherence_state}")
                lines.append(f"  Blocking: {runtime.blocking_issues}, Warnings: {runtime.warning_issues}")
                if runtime.active_files:
                    basenames = [f.rsplit("/", 1)[-1] for f in runtime.active_files[:5]]
                    lines.append(f"  Active files: {', '.join(basenames)}")
        except Exception:
            pass

    return "\n".join(lines)


def render_all_guides_summary() -> str:
    """Render the task-shape index — 5 rows mapping situation to loop."""
    lines: list[str] = []
    lines.append("WORKFLOW MODES — pick the one that matches your situation:")
    lines.append("")

    task_shapes = [
        ("FIXING A BUG / NARROW EDIT", "surgical",
         "lint_files(scope='surgical')"),
        ("REFACTORING / RESTRUCTURING", "refactor",
         "compass_update → mutation_decompose → refactor_extract_method"),
        ("WRITING NEW CODE", "greenfield",
         "prescriptive_spec_compose → compile → write → verify"),
        ("AUDITING / EXPLORING A REPO", "explore",
         "controlplane_run → controlplane_get_details → bootstrap_context_files"),
        ("RECOVERING FROM DEBUG SPIRAL", "debug_spiral",
         "controlplane_run → constraint_check → mutation_prescribe"),
    ]

    for situation, mode, loop in task_shapes:
        spec = MODE_SPECS[mode]
        lines.append(f"  {situation}")
        lines.append(f"    → declare_workflow(path, '{mode}')")
        lines.append(f"    → {loop}")
        lines.append(f"    {spec.description}")
        lines.append("")

    lines.append("Set with: declare_workflow(path, '<mode>')")
    lines.append("Clear with: declare_workflow(path)")
    return "\n".join(lines)


def verify_mode_specs(tool_names: set[str]) -> list[str]:
    """CI check: verify all tool names in ModeSpecs exist in the registry.

    Returns list of errors (empty = pass).
    """
    errors: list[str] = []
    # Only check tools_to_ignore — loop_steps may contain Read/Edit/Write
    # which are Claude Code builtins, not MCP tools
    for name, spec in MODE_SPECS.items():
        for tool in spec.tools_to_ignore:
            if tool not in tool_names:
                errors.append(f"ModeSpec '{name}' references unknown tool '{tool}' in tools_to_ignore")
    return errors
