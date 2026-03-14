"""Rendering functions for context bootstrap files.

Generates CLAUDE.md, AGENTS.md, theory.md, and inquiry.md content
from project metadata, theory facets, and anti-patterns.

Extracted from context_bootstrap.py for module size compliance.
"""

from __future__ import annotations

import os
import re
from typing import Any

from ..bootstrap_defaults import ZERO_STATE_FACET_FALLBACKS

_NO_THEORY = "(no theory content found)"

# ── Helpers ────────────────────────────────────────────────────────────


def facet_or_fallback(
    facet_summaries: dict[str, str],
    key: str,
    fallback: str,
) -> str:
    value = normalize_sentence(str(facet_summaries.get(key, "")).strip())
    if not value or value == _NO_THEORY:
        return fallback
    return value


def normalize_sentence(text: str) -> str:
    cleaned = re.sub(r"`([^`]+)`", r"\1", text)
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ── Model-Biased Guardrails ───────────────────────────────────────────

_GUARDRAIL_MAP: dict[str, str] = {
    "approach_cycling": (
        "- MUST run `constraint_check` before attempting a 3rd approach"
        " (model profile indicates high approach-cycling risk)."
    ),
    "verification_debt": (
        "- MUST verify after every 3 edits, not just at the end"
        " (model profile indicates high verification-debt risk)."
    ),
    "premature_action": (
        "- MUST read relevant code before any Bash command"
        " (model profile indicates premature-action risk)."
    ),
    "serial_discovery": (
        "- MUST use `constraint_check` proactively at session start"
        " (model profile indicates reactive constraint discovery)."
    ),
    "failure_amnesia": (
        "- MUST review error signatures from prior attempts before retrying"
        " (model profile indicates failure-amnesia risk)."
    ),
    "stale_model": (
        "- MUST update hypothesis model after each failed approach"
        " (model profile indicates stale-model risk)."
    ),
    "tool_repetition": (
        "- MUST vary approach after 2 repetitions of the same command"
        " (model profile indicates tool-repetition risk)."
    ),
}


def model_biased_guardrails(
    profile: dict[str, Any] | None,
    threshold: float = 0.3,
    max_guardrails: int = 4,
) -> list[str]:
    """Generate model-specific guardrails from a profile's signal_risk vector."""
    if not profile:
        return []

    signal_risk = profile.get("signal_risk", {})
    if not signal_risk:
        return []

    ranked = sorted(signal_risk.items(), key=lambda x: -x[1])
    guardrails: list[str] = []
    for signal, risk in ranked:
        if risk < threshold:
            continue
        if signal in _GUARDRAIL_MAP:
            guardrails.append(_GUARDRAIL_MAP[signal])
        if len(guardrails) >= max_guardrails:
            break
    return guardrails


# ── CLAUDE.md Renderer ────────────────────────────────────────────────


def render_claude_md(
    *,
    metadata: dict[str, str],
    facet_summaries: dict[str, str],
    anti_patterns: list[str],
    rule_lines: list[str],
    project_root: str = "",
    model_profile: dict[str, Any] | None = None,
) -> str:
    """Render a full CLAUDE.md with cognitive context, not just a skeleton."""
    core = facet_or_fallback(
        facet_summaries,
        "core_theory",
        ZERO_STATE_FACET_FALLBACKS["core_theory"],
    )
    approach = facet_or_fallback(
        facet_summaries,
        "problem_solving",
        ZERO_STATE_FACET_FALLBACKS["problem_solving"],
    )
    alignment = facet_or_fallback(
        facet_summaries,
        "alignment",
        ZERO_STATE_FACET_FALLBACKS["alignment"],
    )
    architecture = facet_or_fallback(
        facet_summaries,
        "architecture",
        ZERO_STATE_FACET_FALLBACKS["architecture"],
    )

    description = metadata.get("description", "").strip()
    name = metadata.get("name", "project")
    mission = description or core

    lines: list[str] = [
        f"# {name} Context",
        "",
        "## What You Are Doing",
        "",
        f"{mission}. Your job is to write correct code; LintGate's job is to catch"
        " the discipline failures that waste your intelligence budget."
        if not mission.endswith(".")
        else f"{mission} Your job is to write correct code; LintGate's job is to"
        " catch the discipline failures that waste your intelligence budget.",
        "",
        "## Know Your Epistemic State",
        "",
        "Before acting on a file you haven't read, before trying a third approach,"
        " before ignoring a behavioral finding — ask yourself:",
        "",
        "- **Do I have theory context?** If not → `build_theory_pack`. The theory"
        " profile tells you what this project values, how it solves problems, and"
        " what it considers anti-patterns. Without it you are guessing at alignment.",
        "- **Am I session-ready?** The session gate checks three things: theory"
        " profile has core_theory + problem_solving + alignment facets with claims,"
        " at least one enforceable rule exists, and no missing required facets."
        " If the gate fires an advisory, run `bootstrap_context_files` before continuing.",
        "- **What is my prediction accuracy?** If `constraint_check` shows accuracy"
        " below 50% after 5+ predictions, your mental model of the project's"
        " constraints is wrong. Stop and re-orient.",
        "- **What is the coherence state?** If `controlplane_run` shows cross-channel"
        " disagreement (lint + tests + git pointing at the same files), that"
        " convergence IS the diagnosis. Read it.",
        "",
        "## Dispositions",
        "",
        "**When starting a session** — orient first. Call `build_theory_pack` or"
        " `controlplane_status`. Read theory claims before writing code. The 200"
        " tokens you spend orienting prevent the 2,000 tokens you spend on a"
        " doomed approach.",
        "",
        "**When acting** — register predictions. Before any Bash command, call"
        " `constraint_check` with a structured prediction (`prediction_type`:"
        " exit_code/error_signature/stdout_contains, `prediction_value`: what you"
        " expect). The system checks your prediction on the next tool event. This"
        " is not bureaucracy — it builds the accuracy signal that modulates"
        " behavioral finding confidence.",
        "",
        "**When stuck** — DO NOT try variant #4. If you have cycled through 3"
        " approaches, your problem is not execution — it is understanding. Run"
        " `constraint_check` to see your constraint coverage. Read the theory"
        " claims attached to behavioral findings. The answer is almost always in"
        " the constraints you have not yet verified, not in the approaches you"
        " have not yet tried.",
        "",
        "**When the system speaks** — findings are weather reports, not commands."
        ' "3 approaches in 20min, all failed" is an observation. The theory coda'
        " attached to it connects the observation to the project's values. You"
        " decide what to do. But if you ignore a hard signal and try the same"
        " pattern again, the system will escalate — and it will be right.",
        "",
        "**When context evolves** — review and apply patches explicitly. When the"
        " living_context system generates a context patch (from accepted"
        " constraints, confirmed predictions, or recurring behavioral signals),"
        " use `context_patch_review` to see the diff. Use `context_patch_apply`"
        " to write it. Patches are never auto-applied. The cumulative rebasing"
        " ensures multiple patches to the same section compose correctly — each"
        " apply re-reads the current on-disk state.",
        "",
        "**When you change the system** — update the docs immediately, in the same"
        " action. If you add an MCP tool, add it to AGENTS.md and README.md tool"
        " tables and increment the count. Source of truth for tool count:"
        ' `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools | wc -l`.'
        " Documentation precision has"
        " compounding returns — one stale count becomes a chain of wrong"
        " assumptions across every session that reads it.",
        "",
        "## Mission",
        "",
        "- Keep feedback loops tight between generated code and validated code quality.",
        "- Prefer deterministic checks and explicit diagnostics over ambiguous heuristics.",
        "- Preserve graceful degradation when optional tooling is unavailable.",
        "- Offload discipline to the deterministic layer so the agent spends its"
        " intelligence budget on novel reasoning.",
        "",
        "## Guardrails",
        "",
        "- DO NOT disable lint channels globally to hide regressions.",
        "- DO NOT auto-apply generated repairs without explicit acceptance.",
        "- DO NOT try a 4th approach without running `constraint_check` first.",
        "- DO NOT ignore theory codas on behavioral findings — they exist to"
        " connect observations to project values.",
        "- MUST keep hook and MCP outputs machine-readable and stable for downstream consumers.",
        "- MUST preserve backward-compatible MCP tool contracts unless versioned intentionally.",
        "- MUST update AGENTS.md, README.md, and docs/design.md when adding,"
        " removing, or changing MCP tools. Verify with"
        ' `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools | wc -l`.',
        "- MUST update docs/design.md YAML examples when adding config options.",
    ]

    # Inject model-biased guardrails when a usable profile exists
    model_guardrails = model_biased_guardrails(model_profile) if model_profile else []
    if model_guardrails:
        lines.append("")
        lines.append("<!-- Model-profile calibrated guardrails -->")
        lines.extend(model_guardrails)

    lines.extend(
        [
            "",
            "<!-- LINTGATE:BEGIN theory_alignment v1 -->",
            "## Theory-Aligned Development",
            f"- Core theory: {core}",
            f"- Preferred approach: {approach}",
            f"- Alignment criteria: {alignment}",
            f"- Architecture intent: {architecture}",
            "<!-- LINTGATE:END theory_alignment -->",
            "",
            "<!-- LINTGATE:BEGIN do_dont v1 -->",
        ]
    )

    lines.append(f"- DO: {approach}")
    lines.append(f"- DO: {alignment}")
    for item in anti_patterns[:4]:
        lines.append(f"- DO NOT: {item}")
    lines.append("<!-- LINTGATE:END do_dont -->")

    lines.extend(
        [
            "",
            "<!-- LINTGATE:BEGIN machine_rules v1 -->",
        ]
    )
    if rule_lines:
        lines.extend(rule_lines)
    else:
        lines.extend(
            [
                "# Add project-specific constraints as they become stable:",
                "# LINTGATE_FORBID_REGEX: <regex>",
                "# LINTGATE_REQUIRE_REGEX: <regex>",
            ]
        )
    lines.append("<!-- LINTGATE:END machine_rules -->")

    context_map_lines = [
        "",
        "<!-- LINTGATE:BEGIN context_map v1 -->",
        "## Context Map",
        "- `.claude/rules/theory.md` - extracted theory summaries and anti-patterns.",
    ]
    has_lintgate_yaml = False
    if project_root:
        if os.path.exists(os.path.join(project_root, "lintgate.yaml")):
            context_map_lines.append("- `lintgate.yaml` - lint and ControlPlane configuration.")
            has_lintgate_yaml = True
        elif os.path.exists(os.path.join(project_root, ".claude", "lintgate.yaml")):
            context_map_lines.append(
                "- `.claude/lintgate.yaml` - lint and ControlPlane configuration."
            )
            has_lintgate_yaml = True
    if not has_lintgate_yaml:
        context_map_lines.append(
            "- `.claude/lintgate.yaml` - lint and ControlPlane configuration"
            " (**not yet created** — create with `controlplane: {enabled: true}`"
            " to activate the full supervision mesh)."
        )
    context_map_lines.append("<!-- LINTGATE:END context_map -->")
    lines.extend(context_map_lines)

    # Prescriptive spec section (only when specs exist)
    if project_root:
        pspec_lines = _render_prescriptive_rules(project_root)
        if pspec_lines:
            lines.extend(pspec_lines)

    lines.extend(
        [
            "",
            "## Debt Tracking Policy",
            "",
            "- Known structural debt should be tracked in .claude/lintgate.yaml"
            " exemptions with ticket references.",
            "- Exemptions should target specific files and codes instead of global"
            " severity downgrades.",
            "- New exemptions require a concrete rationale and a remediation ticket.",
            "",
            "## Deep Reference",
            "",
            "- Architecture of Inquiry protocol: `.claude/rules/inquiry.md`",
            "- Tool reference by cognitive mode: `AGENTS.md`",
            "- Design deep dive: `docs/design.md`",
        ]
    )

    return "\n".join(lines).strip()


def _render_prescriptive_rules(project_root: str) -> list[str]:
    """Render prescriptive_rules managed section from saved specs."""
    try:
        from lintgate.specification.prescriptive_spec import load_all_specs

        specs = load_all_specs(project_root)
        if not specs:
            return []
    except Exception:
        return []

    lines = [
        "",
        "<!-- LINTGATE:BEGIN prescriptive_rules v1 -->",
        "## Prescriptive Specifications",
        "",
    ]
    for spec in list(specs.values())[:10]:
        invariant_strs = [inv.description[:60] for inv in spec.invariants[:2]]
        summary = "; ".join(invariant_strs) if invariant_strs else "no invariants"
        lines.append(f"- `{spec.target_key}` ({spec.problem_class}): {summary}")
    lines.append("<!-- LINTGATE:END prescriptive_rules -->")
    return lines


# ── AGENTS.md Renderer ────────────────────────────────────────────────


def render_agents_md(
    *,
    metadata: dict[str, str],
    facet_summaries: dict[str, str],
    commands: list[str],
) -> str:
    alignment = facet_or_fallback(
        facet_summaries,
        "alignment",
        "Deliver changes that are correct, maintainable, and aligned to project constraints.",
    )

    lines: list[str] = [
        "# AGENTS.md",
        "",
        "## Scope",
        f"- Applies to the entire `{metadata.get('name', 'project')}` repository.",
        "- Follow user instructions first, then this file, then local nested guidance.",
        "",
        "## Execution Contract",
        "- Read relevant files before editing.",
        "- Prefer minimal diffs over broad rewrites.",
        "- Avoid behavior changes unless requested or required to fix defects.",
        "- Surface assumptions and risks when information is incomplete.",
        "",
        "## Required Validation",
        "- Run the smallest check set that proves the change is correct.",
    ]

    for cmd in commands:
        lines.append(f"- `{cmd}`")

    lines.extend(
        [
            "",
            "## Theory and Context",
            "- Read `CLAUDE.md` and `.claude/rules/theory.md` before deep refactors.",
            f"- Keep implementation aligned with: {alignment}",
            "- If work conflicts with explicit rules, stop and request clarification.",
            "",
            "## Handoff Expectations",
            "- Summarize what changed and why.",
            "- Report what was tested and what remains unverified.",
        ]
    )

    return "\n".join(lines).strip()


# ── Theory Rules Renderer ─────────────────────────────────────────────


def render_theory_rules_md(
    *,
    metadata: dict[str, str],
    theory_pack: dict[str, Any],
    theory_full: dict[str, Any],
    anti_patterns: list[str],
    rule_lines: list[str],
) -> str:
    facet_labels = {
        "core_theory": "Core Theory",
        "problem_solving": "Problem-Solving",
        "alignment": "Alignment",
        "architecture": "Architecture",
        "anti_patterns": "Anti-Patterns",
        "abstractions": "Key Abstractions",
    }
    summaries = theory_pack.get("facet_summaries", {})
    validity = theory_full.get("validity", {})

    lines: list[str] = [
        "---",
        "paths:",
        '  - "**/*.py"',
        "---",
        "",
        "# Theory Rules",
        "",
        f"This file stores extracted theory signals for `{metadata.get('name', 'project')}`.",
        "",
        "## Facet Summaries",
    ]

    for key, label in facet_labels.items():
        summary = facet_or_fallback(
            summaries,
            key,
            "No strong signal extracted for this facet yet.",
        )
        lines.append(f"- {label}: {summary}")

    lines.extend(
        [
            "",
            "## High-Signal Anti-Patterns",
        ]
    )
    for item in anti_patterns:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Enforceable Rules",
        ]
    )
    if rule_lines:
        lines.extend([f"- `{line}`" for line in rule_lines])
    else:
        lines.append("- No enforceable rules extracted yet.")

    missing = validity.get("missing_required_facets", [])
    warnings = validity.get("warnings", [])
    lines.extend(
        [
            "",
            "## Extraction Quality",
            f"- Validity status: {validity.get('status', 'unknown')}",
            f"- Docs scanned: {theory_full.get('docs_scanned', 0)}",
            f"- Total claims: {validity.get('total_claims', 0)}",
            f"- Missing required facets: {', '.join(missing) if missing else 'none'}",
        ]
    )
    for warn in warnings[:3]:
        lines.append(f"- Warning: {normalize_sentence(str(warn))}")

    return "\n".join(lines).strip()


# ── Inquiry Protocol Renderer ─────────────────────────────────────────


def render_inquiry_md() -> str:
    """Render the Architecture of Inquiry protocol reference."""
    return """\
# Architecture of Inquiry — Protocol Reference

Five opt-in features that close the loop between behavioral detection and \
theory extraction. All disabled by default. Enable in `.claude/lintgate.yaml` \
under `controlplane.inquiry.*`.

## Features

### theory_grounded_signals

When a behavioral signal fires, relevant theory claims are pulled from the \
project's theory profile and appended as a coda to the finding message.

- **Implementation**: `lintgate/channels/behavior_channel.py` — \
`SIGNAL_THEORY_MAP`, `_ground_finding_in_theory()`
- **Coda cap**: 150 characters max, 1-2 claims per finding
- **Dedup**: Consecutive identical codas for the same signal are suppressed
- **Requires**: Theory profile cache (extracted once per ControlPlane run)

### prediction_tracking

Before Bash commands, the agent registers a falsifiable prediction via \
`constraint_check` with structured expected outcomes.

- **Implementation**: `lintgate/controlplane/behavior_compass.py` — \
`Prediction`, `PredictionExpectation`, `_check_predictions()`
- **Prediction types**: `exit_code` (int), `error_signature` (substring), \
`stdout_contains` (substring)
- **Matching**: Exact full command-signature match. Empty/unknown sigs rejected.
- **Accuracy modulation**: After 5+ checked predictions. >70% softens by -0.15. \
<30% amplifies by +0.15.
- **Expiry**: Unchecked predictions expire after 20 events.

### theory_coherence_check

When the constraint proposer generates a new rule, it checks the rule against \
the project's theory profile.

- **Implementation**: `lintgate/controlplane/constraint_proposer.py` — \
`TheoryCoherenceResult`, `check_theory_coherence()`
- **Output**: `aligned`, `supporting_claims`, `contradicting_claims`, \
`coherence_score` (-1.0 to +1.0)
- **Metadata-only**: Confidence is NOT auto-adjusted (conservative by design).
- **Config gated**: Only runs when `inquiry.theory_coherence_check` is True.

### living_context

CLAUDE.md becomes a living document. Behavioral discoveries flow back as \
managed-section patches.

- **Implementation**: `lintgate/context/bootstrap.py` — `ContextPatch`, \
`generate_context_patch()`, `apply_context_patch()`
- **Managed sections**: `<!-- LINTGATE:BEGIN section_id vN -->` / \
`<!-- LINTGATE:END section_id -->`. Section IDs: `machine_rules`, `do_dont`, \
`theory_alignment`, `context_map`
- **Cumulative rebasing**: `context_patch_apply` re-reads on-disk state. \
Multiple patches to the same section compose correctly.
- **Apply is always explicit**: Use `context_patch_review` to inspect, \
`context_patch_apply` to write.

### session_gate

Advisory warning on file modification when the context bootstrap hasn't passed \
minimum validity.

- **Implementation**: `lintgate/context/auditor.py` — `SessionReadiness`, \
`check_session_readiness()`
- **On failure**: Advisory warning + short-circuits expensive channels.
- **On pass**: Marks session ready; subsequent events skip the check.

## Enabling

```yaml
controlplane:
  enabled: true
  inquiry:
    theory_grounded_signals: true
    prediction_tracking: true
    theory_coherence_check: true
    living_context: true
    session_gate: true
```

All five features require `controlplane.enabled: true`. Each degrades gracefully \
when its dependencies are unavailable."""
