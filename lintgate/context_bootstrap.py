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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context_auditor import audit_context_health
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


def bootstrap_context_files(
    project_root: str,
    *,
    write: bool = False,
    overwrite: bool = False,
    include_theory_rules_doc: bool = True,
    max_machine_rules: int = 12,
) -> dict[str, Any]:
    """Generate or write context-file drafts grounded in project theory.

    Args:
        project_root: Repository root.
        write: When True, write files to disk.
        overwrite: When writing, overwrite existing files.
        include_theory_rules_doc: Include `.claude/rules/theory.md` draft.
        max_machine_rules: Max LINTGATE_* rule lines to include.

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

    metadata = _project_metadata(root)
    facet_summaries = theory_pack.get("facet_summaries", {})
    anti_patterns = _select_actionable_anti_patterns(theory_pack.get("anti_patterns", []))
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
    )
    agents_text = _render_agents_md(
        metadata=metadata,
        facet_summaries=facet_summaries,
        commands=_recommended_commands(root),
    )

    drafts: dict[str, str] = {
        "CLAUDE.md": claude_text,
        "AGENTS.md": agents_text,
    }
    if include_theory_rules_doc:
        drafts[".claude/rules/theory.md"] = _render_theory_rules_md(
            metadata=metadata,
            theory_pack=theory_pack,
            theory_full=theory_full,
            anti_patterns=anti_patterns,
            rule_lines=rule_lines,
        )

    file_reports: list[dict[str, Any]] = []
    written: list[str] = []
    skipped_existing: list[str] = []
    for rel_path, content in drafts.items():
        target = root / rel_path
        exists = target.exists()
        status = "planned"

        if write:
            if exists and not overwrite:
                status = "skipped_exists"
                skipped_existing.append(str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content.rstrip() + "\n")
                status = "written"
                written.append(str(target))

        file_reports.append({
            "relative_path": rel_path,
            "absolute_path": str(target),
            "status": status,
            "line_count": len(content.splitlines()),
            "content": content,
        })

    return {
        "project": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_signals": {
            "docs_scanned": theory_full.get("docs_scanned", 0),
            "context_files_found": len(guidance.get("context_files", [])),
            "proposed_rule_count": len(theory_full.get("enforceable_rules", {}).get("proposed_rules", [])),
            "audit_summary": _summarize_audit(audit),
        },
        "files": file_reports,
        "written_files": written,
        "skipped_existing_files": skipped_existing,
        "llm_usage_hint": (
            "Review generated drafts, tailor directives to your repo conventions, "
            "then run audit_context_health and context_guidance to validate."
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

    return [
        "Do not add task-specific one-off code that bypasses shared abstractions.",
        "Do not ignore failing lint, test, or dependency checks before handoff.",
    ]


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


def _render_claude_md(
    *,
    metadata: dict[str, str],
    facet_summaries: dict[str, str],
    anti_patterns: list[str],
    rule_lines: list[str],
    project_root: str = "",
) -> str:
    core = _facet_or_fallback(
        facet_summaries, "core_theory",
        "Preserve the project's core abstractions and conceptual framing.",
    )
    approach = _facet_or_fallback(
        facet_summaries, "problem_solving",
        "Prefer compositional and testable implementations over ad-hoc shortcuts.",
    )
    alignment = _facet_or_fallback(
        facet_summaries, "alignment",
        "Optimize for correctness, clarity, and maintainability.",
    )
    architecture = _facet_or_fallback(
        facet_summaries, "architecture",
        "Maintain explicit module boundaries and stable interfaces.",
    )

    description = metadata.get("description", "").strip()
    mission = description or core

    lines: list[str] = [
        "# CLAUDE.md",
        "",
        "## Project",
        f"- Name: {metadata.get('name', 'project')}",
        f"- Mission: {mission}",
        "",
        "## Working Mode",
        "- Start with a short plan for non-trivial edits.",
        "- Keep changes small, local, and easy to review.",
        "- Run focused validation before handoff.",
        "- Keep this file concise and move deep details into `.claude/rules/*.md`.",
        "",
        "## Theory-Aligned Development",
        f"- Core theory: {core}",
        f"- Preferred approach: {approach}",
        f"- Alignment criteria: {alignment}",
        f"- Architecture intent: {architecture}",
        "",
        "## Do / Do Not",
        f"- DO: {approach}",
        f"- DO: {alignment}",
    ]

    for item in anti_patterns[:4]:
        lines.append(f"- DO NOT: {item}")

    lines.extend([
        "",
        "## Machine-Enforceable Rules (LintGate)",
    ])
    if rule_lines:
        lines.extend(rule_lines)
    else:
        lines.extend([
            "# Add project-specific constraints as they become stable:",
            "# LINTGATE_FORBID_REGEX: <regex>",
            "# LINTGATE_REQUIRE_REGEX: <regex>",
        ])

    context_map_lines = [
        "",
        "## Context Map",
        "- `.claude/rules/theory.md` - extracted theory summaries and anti-patterns.",
    ]
    # Dynamically detect which config path exists (or omit the line)
    if project_root:
        if os.path.exists(os.path.join(project_root, "lintgate.yaml")):
            context_map_lines.append("- `lintgate.yaml` - lint and ControlPlane configuration.")
        elif os.path.exists(os.path.join(project_root, ".claude", "lintgate.yaml")):
            context_map_lines.append("- `.claude/lintgate.yaml` - lint and ControlPlane configuration.")
        # else: omit the config line entirely — user hasn't created one yet
    else:
        # Fallback when project_root not available
        context_map_lines.append("- `.claude/lintgate.yaml` - lint and ControlPlane configuration.")

    lines.extend(context_map_lines)
    lines.extend([
        "",
        "## Maintenance",
        "- Keep under ~300 lines; split detailed guidance into `.claude/rules/*.md` files.",
        "- Re-run `audit_context_health` after major context updates.",
    ])

    return "\n".join(lines).strip()


def _render_agents_md(
    *,
    metadata: dict[str, str],
    facet_summaries: dict[str, str],
    commands: list[str],
) -> str:
    alignment = _facet_or_fallback(
        facet_summaries, "alignment",
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

    lines.extend([
        "",
        "## Theory and Context",
        "- Read `CLAUDE.md` and `.claude/rules/theory.md` before deep refactors.",
        f"- Keep implementation aligned with: {alignment}",
        "- If work conflicts with explicit rules, stop and request clarification.",
        "",
        "## Handoff Expectations",
        "- Summarize what changed and why.",
        "- Report what was tested and what remains unverified.",
    ])

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

    lines.extend([
        "",
        "## High-Signal Anti-Patterns",
    ])
    for item in anti_patterns:
        lines.append(f"- {item}")

    lines.extend([
        "",
        "## Enforceable Rules",
    ])
    if rule_lines:
        lines.extend([f"- `{line}`" for line in rule_lines])
    else:
        lines.append("- No enforceable rules extracted yet.")

    missing = validity.get("missing_required_facets", [])
    warnings = validity.get("warnings", [])
    lines.extend([
        "",
        "## Extraction Quality",
        f"- Validity status: {validity.get('status', 'unknown')}",
        f"- Docs scanned: {theory_full.get('docs_scanned', 0)}",
        f"- Total claims: {validity.get('total_claims', 0)}",
        f"- Missing required facets: {', '.join(missing) if missing else 'none'}",
    ])
    for warn in warnings[:3]:
        lines.append(f"- Warning: {_normalize_sentence(str(warn))}")

    return "\n".join(lines).strip()


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
