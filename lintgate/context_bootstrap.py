"""Best-practice context file bootstrap for CLAUDE.md and AGENTS.md.

Builds concise, theory-grounded context files by combining:
- context guidance/rules already present in the project
- context health audit diagnostics
- theory extraction summaries and enforceable-rule proposals

This is meant for MCP usage where an LLM calls a deterministic tool to
generate high-quality starting context files, then reviews/edits them.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bootstrap_defaults import ZERO_STATE_ANTI_PATTERNS, ZERO_STATE_FACET_FALLBACKS
from .context_auditor import (
    audit_context_health,
    classify_directive_enforceability,
)
from .context_guidance import build_context_guidance
from .theory_extractor import build_theory_pack, extract_theory

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    import tomli as tomllib

_NEGATIVE_CUE_RE = re.compile(
    r"\b(?:do not|don't|never|avoid|anti-pattern|pitfall|risk|wrong|"
    r"fail|ruin|break|undermine|bypass|must not)\b",
    re.IGNORECASE,
)
_NO_THEORY = "(no theory content found)"
_PERF_ANTI_PATTERN_CUE = "O(n²)"


@dataclass
class ReviewItem:
    """A structured uncertainty marker for the calling agent to resolve.

    Bootstrap surfaces these when its heuristic is uncertain about a
    classification, a dead-path reference has nearby candidates, or a
    facet fell back to generic defaults.  The calling agent reads these,
    resolves each one trivially (it has project context the deterministic
    tool lacks), and edits the draft accordingly.

    Attributes:
        review_type: Category of uncertainty: ``directive_classification``,
            ``dead_path_candidate``, or ``facet_fallback``.
        context: The specific content in question (directive text, path
            reference, or facet key).
        question: A short, answerable question for the agent.
        options: Concrete resolution choices (e.g., ["enforceable",
            "architectural", "remove"]).
        detail: Extra context such as confidence score or candidate paths.
    """

    review_type: str  # directive_classification | dead_path_candidate | facet_fallback
    context: str
    question: str
    options: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.review_type,
            "context": self.context,
            "question": self.question,
            "options": self.options,
            "detail": self.detail,
        }


def bootstrap_context_files(
    project_root: str,
    *,
    write: bool = False,
    overwrite: bool = False,
    include_theory_rules_doc: bool = True,
    max_machine_rules: int = 12,
    model_id: str | None = None,
    use_model_profile: bool = True,
) -> dict[str, Any]:
    """Generate or write context-file drafts grounded in project theory.

    Args:
        project_root: Repository root.
        write: When True, write files to disk.
        overwrite: When writing, overwrite existing files.
        include_theory_rules_doc: Include `.claude/rules/theory.md` draft.
        max_machine_rules: Max LINTGATE_* rule lines to include.
        model_id: Optional model identifier for model-aware calibration.
        use_model_profile: When True (default), apply model profile if available.

    Returns:
        A structured payload with draft contents, write outcomes, and source
        diagnostics used during synthesis.
    """
    root = Path(project_root).resolve()
    if max_machine_rules <= 0:
        raise ValueError("max_machine_rules must be > 0")

    guidance = build_context_guidance(str(root))
    audit = audit_context_health(str(root))
    theory_pack = build_theory_pack(str(root), include_full_profile=False)
    theory_full = extract_theory(str(root))

    # Resolve model profile for calibration
    model_profile_dict: dict[str, Any] | None = None
    model_key_resolved: str | None = None
    model_profile_confidence: float = 0.0
    if model_id and use_model_profile:
        try:
            from .controlplane.model_profiles import get_profile, resolve_model_key

            model_key_resolved = resolve_model_key(model_id)
            if model_key_resolved:
                profile = get_profile(model_id)
                if profile and profile.is_usable():
                    model_profile_dict = profile.to_dict()
                    model_profile_confidence = profile.confidence
        except Exception:
            pass  # Non-fatal — fall through to generic defaults

    metadata = _project_metadata(root)
    facet_summaries = theory_pack.get("facet_summaries", {})

    # Model-specific anti-patterns take precedence when available
    if model_profile_dict and model_profile_dict.get("custom_anti_patterns"):
        anti_patterns = model_profile_dict["custom_anti_patterns"][:5]
    else:
        anti_patterns = _select_actionable_anti_patterns(
            theory_pack.get("anti_patterns", [])
        )

    rule_lines = _collect_machine_rule_lines(
        guidance=guidance,
        theory=theory_full,
        max_machine_rules=max_machine_rules,
    )

    claude_text = _render_claude_md(
        metadata=metadata,
        facet_summaries=facet_summaries,
        anti_patterns=anti_patterns,
        rule_lines=rule_lines,
        project_root=str(root),
        model_profile=model_profile_dict,
    )
    agents_text = _render_agents_md(
        metadata=metadata,
        facet_summaries=facet_summaries,
        commands=_recommended_commands(root),
    )

    drafts: dict[str, str] = {
        ".claude/CLAUDE.md": claude_text,
        "AGENTS.md": agents_text,
        ".claude/rules/inquiry.md": _render_inquiry_md(),
    }
    if include_theory_rules_doc:
        drafts[".claude/rules/theory.md"] = _render_theory_rules_md(
            metadata=metadata,
            theory_pack=theory_pack,
            theory_full=theory_full,
            anti_patterns=anti_patterns,
            rule_lines=rule_lines,
        )

    # AGENTS.md is hand-maintained (source of truth for all agents).
    # Never overwrite it — even with overwrite=True. Only generate if missing.
    never_overwrite = {"AGENTS.md"}

    file_reports: list[dict[str, Any]] = []
    written: list[str] = []
    skipped_existing: list[str] = []
    for rel_path, content in drafts.items():
        target = root / rel_path
        exists = target.exists()
        status = "planned"

        if write:
            if exists and (not overwrite or rel_path in never_overwrite):
                status = "skipped_exists"
                skipped_existing.append(str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content.rstrip() + "\n")
                status = "written"
                written.append(str(target))

        file_reports.append(
            {
                "relative_path": rel_path,
                "absolute_path": str(target),
                "status": status,
                "line_count": len(content.splitlines()),
                "content": content,
            }
        )

    # Build quick-win suggestions based on project state
    quick_wins = _build_quick_wins(root, guidance, theory_full)

    # Collect structured uncertainty markers for the calling agent
    review_items = _collect_review_items(
        guidance=guidance,
        facet_summaries=facet_summaries,
        audit=audit,
        project_root=str(root),
    )

    # Build ordered agent instructions, referencing files by relative_path search
    claude_md_path = next(
        (r["relative_path"] for r in file_reports if "CLAUDE.md" in r["relative_path"]),
        "CLAUDE.md",
    )
    agent_steps: list[str] = [
        f"Review the generated {claude_md_path} draft — check that principles match the project.",
    ]
    if review_items:
        agent_steps.append(
            f"Resolve {len(review_items)} needs_review item(s) — "
            "each is a quick classification or short summary."
        )
    if quick_wins:
        agent_steps.append("Act on quick_wins: " + "; ".join(quick_wins[:3]))
    agent_steps.append("If satisfied, re-run with write=true to save files to disk.")
    agent_steps.append("Run controlplane_run(path) for a full project health check.")

    return {
        "project": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_signals": {
            "docs_scanned": theory_full.get("docs_scanned", 0),
            "context_files_found": len(guidance.get("context_files", [])),
            "proposed_rule_count": len(
                theory_full.get("enforceable_rules", {}).get("proposed_rules", [])
            ),
            "audit_summary": _summarize_audit(audit),
            "model_profile_applied": model_profile_dict is not None,
            "model_key": model_key_resolved,
            "model_profile_confidence": model_profile_confidence,
        },
        "files": file_reports,
        "written_files": written,
        "skipped_existing_files": skipped_existing,
        "quick_wins": quick_wins,
        "needs_review": [item.to_dict() for item in review_items],
        "agent_instructions": agent_steps,
        "llm_usage_hint": (
            "Review generated drafts. If 'needs_review' contains items, resolve "
            "each one (they are cheap — just classify or provide a short summary) "
            "then edit the draft accordingly. Finally run audit_context_health and "
            "context_guidance to validate."
        ),
    }


def _collect_machine_rule_lines(
    guidance: dict[str, Any],
    theory: dict[str, Any],
    max_machine_rules: int,
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    for rule in guidance.get("rules", []):
        line = _rule_to_line(rule)
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)

    proposed = theory.get("enforceable_rules", {}).get("proposed_rules", [])
    for item in proposed:
        line = str(item.get("add_line", "")).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)

    return lines[:max_machine_rules]


def _rule_to_line(rule: dict[str, Any]) -> str:
    kind = str(rule.get("kind", "")).strip()
    pattern = str(rule.get("pattern", "")).strip()
    if not pattern:
        return ""
    if kind == "forbid_regex":
        return f"LINTGATE_FORBID_REGEX: {pattern}"
    if kind == "require_regex":
        return f"LINTGATE_REQUIRE_REGEX: {pattern}"
    return ""


def _project_metadata(root: Path) -> dict[str, str]:
    name = root.name
    description = ""

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            parsed = tomllib.loads(pyproject.read_text())
            project = parsed.get("project", {})
            if isinstance(project, dict):
                maybe_name = project.get("name")
                maybe_desc = project.get("description")
                if isinstance(maybe_name, str) and maybe_name.strip():
                    name = maybe_name.strip()
                if isinstance(maybe_desc, str) and maybe_desc.strip():
                    description = maybe_desc.strip()
        except Exception:
            pass

    if not description:
        description = _read_readme_description(root)

    return {
        "name": name,
        "description": description,
    }


def _read_readme_description(root: Path) -> str:
    for candidate in ("README.md", "README.MD", "readme.md"):
        path = root / candidate
        if not path.exists():
            continue
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return ""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("[") and "http" in stripped:
                continue
            return _normalize_sentence(stripped)
    return ""


def _select_actionable_anti_patterns(claims: list[str], max_items: int = 5) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    for claim in claims:
        text = _normalize_sentence(str(claim))
        key = text.lower()
        if not text or key in seen:
            continue
        if not _NEGATIVE_CUE_RE.search(text):
            continue
        if len(text) > 260:
            text = text[:257].rstrip() + "..."
        seen.add(key)
        selected.append(text)
        if len(selected) >= max_items:
            break

    if selected:
        return selected

    if max_items <= 0:
        return []

    defaults = ZERO_STATE_ANTI_PATTERNS[:max_items]
    if max_items >= 4:
        perf_item = next(
            (item for item in ZERO_STATE_ANTI_PATTERNS if _PERF_ANTI_PATTERN_CUE in item),
            None,
        )
        # Keep at least one performance anti-pattern in the first 4 visible DO NOT entries.
        if perf_item and perf_item not in defaults[:4]:
            if perf_item in defaults:
                defaults.remove(perf_item)
            defaults.insert(min(3, len(defaults)), perf_item)
            defaults = defaults[:max_items]

    return defaults


def _recommended_commands(root: Path) -> list[str]:
    commands: list[str] = []

    has_python = (root / "pyproject.toml").exists() or any(root.glob("*.py"))
    has_node = (root / "package.json").exists()
    has_rust = (root / "Cargo.toml").exists()

    if has_python:
        prefix = "uv run " if (root / "uv.lock").exists() else ""
        commands.append(f"{prefix}ruff check .")
        commands.append(f"{prefix}pytest -q")
        commands.append(f"{prefix}mypy .")

    if has_node:
        commands.append("npm run lint")
        commands.append("npm test")

    if has_rust:
        commands.append("cargo fmt --check")
        commands.append("cargo clippy --all-targets -- -D warnings")
        commands.append("cargo test")

    if not commands:
        commands.append("Run the project's lint and test commands before concluding work.")

    deduped: list[str] = []
    seen: set[str] = set()
    for cmd in commands:
        if cmd in seen:
            continue
        seen.add(cmd)
        deduped.append(cmd)
    return deduped


_GUARDRAIL_MAP: dict[str, str] = {
    "approach_cycling": (
        "- MUST run `behavior_precheck` before attempting a 3rd approach"
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
        "- MUST use `behavior_precheck` proactively at session start"
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


def _model_biased_guardrails(
    profile: dict[str, Any] | None,
    threshold: float = 0.3,
    max_guardrails: int = 4,
) -> list[str]:
    """Generate model-specific guardrails from a profile's signal_risk vector.

    Returns guardrail lines for signals with risk >= threshold, capped at max_guardrails.
    """
    if not profile:
        return []

    signal_risk = profile.get("signal_risk", {})
    if not signal_risk:
        return []

    # Rank signals by risk, descending
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


def _render_claude_md(
    *,
    metadata: dict[str, str],
    facet_summaries: dict[str, str],
    anti_patterns: list[str],
    rule_lines: list[str],
    project_root: str = "",
    model_profile: dict[str, Any] | None = None,
) -> str:
    """Render a full CLAUDE.md with cognitive context, not just a skeleton.

    This produces a functional cognitive operating system: epistemic state
    framework, dispositions, guardrails, managed sections, and deep references.
    The output should be usable without hand-editing.
    """
    core = _facet_or_fallback(
        facet_summaries,
        "core_theory",
        ZERO_STATE_FACET_FALLBACKS["core_theory"],
    )
    approach = _facet_or_fallback(
        facet_summaries,
        "problem_solving",
        ZERO_STATE_FACET_FALLBACKS["problem_solving"],
    )
    alignment = _facet_or_fallback(
        facet_summaries,
        "alignment",
        ZERO_STATE_FACET_FALLBACKS["alignment"],
    )
    architecture = _facet_or_fallback(
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
        "- **What is my prediction accuracy?** If `behavior_precheck` shows accuracy"
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
        " `behavior_precheck` with a structured prediction (`prediction_type`:"
        " exit_code/error_signature/stdout_contains, `prediction_value`: what you"
        " expect). The system checks your prediction on the next tool event. This"
        " is not bureaucracy — it builds the accuracy signal that modulates"
        " behavioral finding confidence.",
        "",
        "**When stuck** — DO NOT try variant #4. If you have cycled through 3"
        " approaches, your problem is not execution — it is understanding. Run"
        " `behavior_precheck` to see your constraint coverage. Read the theory"
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
        " `grep -Rho \"@mcp.tool()\" mcp_server.py mcp_tools | wc -l`."
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
        "- DO NOT try a 4th approach without running `behavior_precheck` first.",
        "- DO NOT ignore theory codas on behavioral findings — they exist to"
        " connect observations to project values.",
        "- MUST keep hook and MCP outputs machine-readable and stable for"
        " downstream consumers.",
        "- MUST preserve backward-compatible MCP tool contracts unless versioned"
        " intentionally.",
        "- MUST update AGENTS.md, README.md, and docs/design.md when adding,"
        " removing, or changing MCP tools. Verify with"
        ' `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools | wc -l`.',
        "- MUST update docs/design.md YAML examples when adding config options.",
    ]

    # Inject model-biased guardrails when a usable profile exists
    model_guardrails = _model_biased_guardrails(model_profile) if model_profile else []
    if model_guardrails:
        lines.append("")
        lines.append("<!-- Model-profile calibrated guardrails -->")
        lines.extend(model_guardrails)

    lines.extend([
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
    ])

    # Add extracted do/don't items
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
            context_map_lines.append(
                "- `lintgate.yaml` - lint and ControlPlane configuration."
            )
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


def _render_agents_md(
    *,
    metadata: dict[str, str],
    facet_summaries: dict[str, str],
    commands: list[str],
) -> str:
    alignment = _facet_or_fallback(
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


def _render_theory_rules_md(
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
        summary = _facet_or_fallback(
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
        lines.append(f"- Warning: {_normalize_sentence(str(warn))}")

    return "\n".join(lines).strip()


def _render_inquiry_md() -> str:
    """Render the Architecture of Inquiry protocol reference.

    This is static content — the inquiry protocol is the same for any project
    using LintGate. It documents the 5 opt-in features that close the loop
    between behavioral detection and theory extraction.
    """
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
`behavior_precheck` with structured expected outcomes.

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

- **Implementation**: `lintgate/context_bootstrap.py` — `ContextPatch`, \
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

- **Implementation**: `lintgate/context_auditor.py` — `SessionReadiness`, \
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


def _facet_or_fallback(
    facet_summaries: dict[str, str],
    key: str,
    fallback: str,
) -> str:
    value = _normalize_sentence(str(facet_summaries.get(key, "")).strip())
    if not value or value == _NO_THEORY:
        return fallback
    return value


def _normalize_sentence(text: str) -> str:
    cleaned = re.sub(r"`([^`]+)`", r"\1", text)
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _summarize_audit(audit: dict[str, Any]) -> dict[str, int]:
    audit_items = audit.get("audit", [])
    out = {
        "files": len(audit_items),
        "errors": 0,
        "warnings": 0,
        "passes": 0,
    }
    for item in audit_items:
        status = item.get("status")
        if status == "error":
            out["errors"] += 1
        elif status == "warn":
            out["warnings"] += 1
        elif status == "pass":
            out["passes"] += 1
    return out


def _build_quick_wins(
    root: Path,
    guidance: dict[str, Any],
    theory: dict[str, Any],
) -> list[str]:
    """Generate actionable quick-win suggestions for the project.

    These are concrete next steps that improve LintGate's value immediately.
    """
    wins: list[str] = []

    # 1. ControlPlane not configured
    has_config = (
        (root / "lintgate.yaml").exists()
        or (root / ".claude" / "lintgate.yaml").exists()
    )
    if not has_config:
        wins.append(
            "Create `.claude/lintgate.yaml` with `controlplane: {enabled: true}` "
            "to activate the full supervision mesh (lint + tests + deps + git + behavior + structure)."
        )

    # 2. No enforceable rules despite existing directives
    rules = guidance.get("rules", [])
    do_not_count = len(guidance.get("directives", {}).get("do_not", []))
    if do_not_count > 0 and not rules:
        wins.append(
            f"Found {do_not_count} DO NOT directive(s) but no LINTGATE_FORBID_REGEX rules. "
            "Run `extract_theory_constraints` to generate enforceable rules."
        )

    # 3. Proposed rules not yet adopted
    proposed = theory.get("enforceable_rules", {}).get("proposed_rules", [])
    if proposed:
        wins.append(
            f"{len(proposed)} rule(s) proposed from theory extraction — review and "
            "add to CLAUDE.md to make them enforceable."
        )

    # 4. No lockfile
    if (root / "pyproject.toml").exists():
        has_lock = (root / "uv.lock").exists() or (root / "poetry.lock").exists()
        if not has_lock:
            wins.append(
                "No lockfile found. Run `uv lock` or `poetry lock` for reproducible installs."
            )

    return wins


# ── needs_review Collection ──────────────────────────────────────────


def _collect_review_items(
    *,
    guidance: dict[str, Any],
    facet_summaries: dict[str, str],
    audit: dict[str, Any],
    project_root: str,
) -> list[ReviewItem]:
    """Collect structured uncertainty markers for the calling agent.

    Three sources of review items:

    1. **Directive classification** — DO NOT directives where the
       heuristic is uncertain whether they are regex-enforceable or
       architectural.
    2. **Dead path candidates** — backtick-quoted paths flagged as dead
       by the auditor that the agent might be able to resolve (e.g., the
       path is slightly wrong, or the file was moved).
    3. **Facet fallback** — theory facets that fell back to generic
       zero-state defaults because extraction found no project-specific
       claims.  The agent can often provide a better summary.
    """
    items: list[ReviewItem] = []
    _collect_directive_review_items(items, guidance)
    _collect_dead_path_review_items(items, audit)
    _collect_facet_fallback_items(items, facet_summaries)
    return items


def _collect_directive_review_items(
    items: list[ReviewItem],
    guidance: dict[str, Any],
) -> None:
    """Surface DO NOT directives where enforceability is uncertain."""
    do_not_directives = guidance.get("directives", {}).get("do_not", [])
    for directive in do_not_directives:
        result = classify_directive_enforceability(directive)
        if result.classification == "uncertain":
            items.append(
                ReviewItem(
                    review_type="directive_classification",
                    context=directive,
                    question=(
                        "Is this directive regex-enforceable (references a specific "
                        "API, identifier, or pattern) or architectural (describes a "
                        "process/approach that requires human judgment)?"
                    ),
                    options=["enforceable", "architectural"],
                    detail={
                        "confidence": result.confidence,
                        "reason": result.reason,
                    },
                )
            )


def _collect_dead_path_review_items(
    items: list[ReviewItem],
    audit: dict[str, Any],
) -> None:
    """Surface dead-path warnings so the agent can confirm/fix them."""
    for file_result in audit.get("audit", []):
        for check in file_result.get("health_checks", []):
            if check.get("check") != "path_references":
                continue
            if check.get("status") != "warn":
                continue
            # Extract dead paths from the detail string
            detail_text = check.get("detail", "")
            if "don't exist" not in detail_text:
                continue
            # The detail format is:
            #   "3 referenced path(s) don't exist: foo, bar, baz (+N more)"
            colon_idx = detail_text.find(": ")
            if colon_idx < 0:
                continue
            paths_part = detail_text[colon_idx + 2 :]
            # Strip "(+N more)" suffix
            paren_idx = paths_part.rfind(" (+")
            if paren_idx >= 0:
                paths_part = paths_part[:paren_idx]
            dead_paths = [p.strip() for p in paths_part.split(",") if p.strip()]
            for dp in dead_paths:
                items.append(
                    ReviewItem(
                        review_type="dead_path_candidate",
                        context=dp,
                        question=(
                            f"The path `{dp}` referenced in "
                            f"`{file_result.get('name', '?')}` does not exist. "
                            "Should it be updated, removed, or is it correct "
                            "(e.g., it's created at runtime)?"
                        ),
                        options=["update_path", "remove_reference", "keep_as_is"],
                        detail={"source_file": file_result.get("file", "")},
                    )
                )


def _collect_facet_fallback_items(
    items: list[ReviewItem],
    facet_summaries: dict[str, str],
) -> None:
    """Surface facets that fell back to zero-state defaults."""
    for key, fallback in ZERO_STATE_FACET_FALLBACKS.items():
        actual = (facet_summaries.get(key) or "").strip()
        if not actual or actual in (_NO_THEORY, fallback):
            items.append(
                ReviewItem(
                    review_type="facet_fallback",
                    context=key,
                    question=(
                        f"The '{key}' theory facet has no project-specific content "
                        "and fell back to a generic default. Can you provide a "
                        "1-2 sentence summary of this project's approach to "
                        f"'{key}'?"
                    ),
                    options=["provide_summary", "keep_default"],
                    detail={"default_used": fallback},
                )
            )


# ── Managed Section Parsing & Patch Protocol ─────────────────────────


_MANAGED_BEGIN_RE = re.compile(
    r"<!--\s*LINTGATE:BEGIN\s+(\w+)\s+v(\d+)\s*-->",
)
_MANAGED_END_RE = re.compile(
    r"<!--\s*LINTGATE:END\s+(\w+)\s*-->",
)

MANAGED_SECTION_IDS = ("machine_rules", "do_dont", "theory_alignment", "context_map")


@dataclass
class ManagedSection:
    """A parsed managed section from a CLAUDE.md file."""

    section_id: str
    version: int
    content: str
    start_pos: int  # char offset of BEGIN marker
    end_pos: int  # char offset after END marker


@dataclass
class ContextPatch:
    """A proposed patch to a managed section in CLAUDE.md."""

    patch_id: str = ""
    section_id: str = ""
    trigger: str = ""  # "constraint_accepted" | "prediction_confirmed" | "recurring_behavioral_signal" | "theory_coherence_update"
    old_content: str = ""
    new_content: str = ""
    rationale: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    coherence_check: dict[str, Any] | None = None
    status: str = "pending"  # "pending" | "applied" | "rejected"
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "section_id": self.section_id,
            "trigger": self.trigger,
            "old_content": self.old_content,
            "new_content": self.new_content,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "coherence_check": self.coherence_check,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPatch:
        return cls(
            patch_id=data.get("patch_id", ""),
            section_id=data.get("section_id", ""),
            trigger=data.get("trigger", ""),
            old_content=data.get("old_content", ""),
            new_content=data.get("new_content", ""),
            rationale=data.get("rationale", ""),
            evidence=data.get("evidence", {}),
            coherence_check=data.get("coherence_check"),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", 0.0),
        )


def _parse_managed_sections(text: str) -> dict[str, ManagedSection]:
    """Parse LINTGATE:BEGIN/END markers from CLAUDE.md text.

    Returns dict of section_id → ManagedSection.
    """
    sections: dict[str, ManagedSection] = {}

    for begin_match in _MANAGED_BEGIN_RE.finditer(text):
        section_id = begin_match.group(1)
        version = int(begin_match.group(2))
        begin_end = begin_match.end()

        end_pattern = re.compile(
            rf"<!--\s*LINTGATE:END\s+{re.escape(section_id)}\s*-->",
        )
        end_match = end_pattern.search(text, begin_end)
        if end_match is None:
            continue  # Unclosed marker — skip

        content = text[begin_end : end_match.start()]
        sections[section_id] = ManagedSection(
            section_id=section_id,
            version=version,
            content=content,
            start_pos=begin_match.start(),
            end_pos=end_match.end(),
        )

    return sections


def _migrate_to_managed_sections(text: str) -> tuple[str, list[str]]:
    """Add managed section markers to a pre-upgrade CLAUDE.md.

    Uses heading heuristics to identify where to insert markers.
    Returns (migrated_text, list_of_migrated_section_ids).

    If markers already exist, returns text unchanged.
    """
    if "LINTGATE:BEGIN" in text:
        return text, []

    migrated_ids: list[str] = []
    lines = text.split("\n")
    result_lines: list[str] = []
    i = 0

    # Heading → section_id mapping for migration
    heading_map: dict[str, str] = {
        "theory-aligned development": "theory_alignment",
        "do / do not": "do_dont",
        "do/do not": "do_dont",
        "machine-enforceable rules": "machine_rules",
        "machine rules": "machine_rules",
        "context map": "context_map",
    }

    open_section: str | None = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip().lower()

        # Check if this line is a heading that matches a managed section
        if stripped.startswith("#"):
            heading_text = re.sub(r"^#+\s*", "", stripped).strip()
            # Remove trailing markers like "(lintgate)" if present
            heading_text = re.sub(r"\s*\(.*\)\s*$", "", heading_text).strip()

            matched_id = None
            for pattern, sid in heading_map.items():
                if pattern in heading_text:
                    matched_id = sid
                    break

            if matched_id:
                # Close any currently open section
                if open_section:
                    result_lines.append(f"<!-- LINTGATE:END {open_section} -->")

                # Open new section
                result_lines.append(f"<!-- LINTGATE:BEGIN {matched_id} v1 -->")
                open_section = matched_id
                migrated_ids.append(matched_id)
                result_lines.append(line)
                i += 1
                continue

            # Different heading — close any open section
            if open_section:
                result_lines.append(f"<!-- LINTGATE:END {open_section} -->")
                open_section = None

        result_lines.append(line)
        i += 1

    # Close any trailing open section
    if open_section:
        result_lines.append(f"<!-- LINTGATE:END {open_section} -->")

    return "\n".join(result_lines), migrated_ids


def generate_context_patch(
    project_root: str,
    trigger: str,
    evidence: dict[str, Any],
) -> ContextPatch | None:
    """Generate a patch for a managed section in CLAUDE.md.

    Args:
        project_root: Repository root.
        trigger: One of "constraint_accepted", "prediction_confirmed",
                 "recurring_behavioral_signal", "theory_coherence_update".
        evidence: Supporting data for the patch (e.g., rule text, signal name).

    Returns:
        ContextPatch or None if no update needed / already present.
    """
    claude_path = Path(project_root) / "CLAUDE.md"
    if not claude_path.exists():
        return None

    text = claude_path.read_text()

    # Migrate if needed
    if "LINTGATE:BEGIN" not in text:
        text, _ = _migrate_to_managed_sections(text)

    sections = _parse_managed_sections(text)

    # Determine target section and new content based on trigger
    if trigger == "constraint_accepted":
        section_id = "machine_rules"
        rule_text = evidence.get("rule", "")
        if not rule_text:
            return None
        section = sections.get(section_id)
        if section is None:
            return None
        # Idempotency: check if rule already exists
        if rule_text in section.content:
            return None
        new_content = section.content.rstrip() + f"\n{rule_text}\n"

    elif trigger in ("prediction_confirmed", "recurring_behavioral_signal"):
        section_id = "do_dont"
        entry = evidence.get("entry", "")
        if not entry:
            return None
        section = sections.get(section_id)
        if section is None:
            return None
        # Idempotency
        if entry in section.content:
            return None
        new_content = section.content.rstrip() + f"\n- DO NOT: {entry}\n"

    elif trigger == "theory_coherence_update":
        section_id = "theory_alignment"
        update_text = evidence.get("update", "")
        if not update_text:
            return None
        section = sections.get(section_id)
        if section is None:
            return None
        if update_text in section.content:
            return None
        new_content = section.content.rstrip() + f"\n- {update_text}\n"

    else:
        return None

    return ContextPatch(
        patch_id=uuid.uuid4().hex[:8],
        section_id=section_id,
        trigger=trigger,
        old_content=section.content,
        new_content=new_content,
        rationale=evidence.get("rationale", f"Auto-generated from {trigger}"),
        evidence=evidence,
        status="pending",
        created_at=time.time(),
    )


def apply_context_patch(
    project_root: str,
    patch: ContextPatch,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply a context patch to CLAUDE.md.

    Args:
        project_root: Repository root.
        patch: The ContextPatch to apply.
        dry_run: If True (default), return diff preview without writing.

    Returns:
        Dict with "applied", "diff_preview", and optionally "validation".
    """
    claude_path = Path(project_root) / "CLAUDE.md"
    if not claude_path.exists():
        return {"applied": False, "error": "CLAUDE.md not found"}

    text = claude_path.read_text()

    # Migrate if needed
    migrated = False
    if "LINTGATE:BEGIN" not in text:
        text, migrated_ids = _migrate_to_managed_sections(text)
        migrated = bool(migrated_ids)

    sections = _parse_managed_sections(text)
    section = sections.get(patch.section_id)
    if section is None:
        return {"applied": False, "error": f"Section '{patch.section_id}' not found"}

    # Build new text: replace section content and increment version
    new_version = section.version + 1
    new_begin = f"<!-- LINTGATE:BEGIN {patch.section_id} v{new_version} -->"

    # Replace the entire managed block
    before = text[: section.start_pos]
    end_marker = f"<!-- LINTGATE:END {patch.section_id} -->"
    after = text[section.end_pos :]

    new_text = before + new_begin + patch.new_content + end_marker + after

    # Build diff preview (simple before/after)
    diff_preview = {
        "section_id": patch.section_id,
        "old_version": section.version,
        "new_version": new_version,
        "old_content": patch.old_content.strip(),
        "new_content": patch.new_content.strip(),
        "migrated": migrated,
    }

    if dry_run:
        return {"applied": False, "dry_run": True, "diff_preview": diff_preview}

    # Write the file
    claude_path.write_text(new_text)
    patch.status = "applied"

    # Validate with audit
    validation = None
    try:
        audit_result = audit_context_health(project_root)
        validation = _summarize_audit(audit_result)
    except Exception:
        pass

    return {
        "applied": True,
        "diff_preview": diff_preview,
        "validation": validation,
    }
