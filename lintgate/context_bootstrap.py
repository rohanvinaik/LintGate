"""Best-practice context file bootstrap for CLAUDE.md and AGENTS.md.

Builds concise, theory-grounded context files by combining:
- context guidance/rules already present in the project
- context health audit diagnostics
- theory extraction summaries and enforceable-rule proposals

This is meant for MCP usage where an LLM calls a deterministic tool to
generate high-quality starting context files, then reviews/edits them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bootstrap_defaults import ZERO_STATE_ANTI_PATTERNS, ZERO_STATE_FACET_FALLBACKS
from .context_auditor import (
    audit_context_health,
    classify_directive_enforceability,
)

# Re-export from extracted modules for backward compatibility.
from .context_bootstrap_patches import (  # noqa: F401
    MANAGED_SECTION_IDS,
    ContextPatch,
    ManagedSection,
    apply_context_patch,
    generate_context_patch,
    migrate_to_managed_sections,
    parse_managed_sections,
    summarize_audit,
)
from .context_bootstrap_render import (
    facet_or_fallback,
    model_biased_guardrails,
    normalize_sentence,
    render_agents_md,
    render_claude_md,
    render_inquiry_md,
    render_theory_rules_md,
)
from .context_guidance import build_context_guidance
from .theory_extractor import build_theory_pack, extract_theory

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    import tomli as tomllib  # type: ignore[import-not-found]

# Backward-compatible underscore aliases.
_render_claude_md = render_claude_md
_render_agents_md = render_agents_md
_render_theory_rules_md = render_theory_rules_md
_render_inquiry_md = render_inquiry_md
_model_biased_guardrails = model_biased_guardrails
_normalize_sentence = normalize_sentence
_facet_or_fallback = facet_or_fallback
_summarize_audit = summarize_audit
_parse_managed_sections = parse_managed_sections
_migrate_to_managed_sections = migrate_to_managed_sections

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

    Attributes:
        review_type: Category of uncertainty: ``directive_classification``,
            ``dead_path_candidate``, or ``facet_fallback``.
        context: The specific content in question.
        question: A short, answerable question for the agent.
        options: Concrete resolution choices.
        detail: Extra context such as confidence score or candidate paths.
    """

    review_type: str
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
    """Generate or write context-file drafts grounded in project theory."""
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
            pass

    metadata = _project_metadata(root)
    facet_summaries = theory_pack.get("facet_summaries", {})

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

    claude_text = render_claude_md(
        metadata=metadata,
        facet_summaries=facet_summaries,
        anti_patterns=anti_patterns,
        rule_lines=rule_lines,
        project_root=str(root),
        model_profile=model_profile_dict,
    )
    agents_text = render_agents_md(
        metadata=metadata,
        facet_summaries=facet_summaries,
        commands=_recommended_commands(root),
    )

    drafts: dict[str, str] = {
        ".claude/CLAUDE.md": claude_text,
        "AGENTS.md": agents_text,
        ".claude/rules/inquiry.md": render_inquiry_md(),
    }
    if include_theory_rules_doc:
        drafts[".claude/rules/theory.md"] = render_theory_rules_md(
            metadata=metadata,
            theory_pack=theory_pack,
            theory_full=theory_full,
            anti_patterns=anti_patterns,
            rule_lines=rule_lines,
        )

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

    quick_wins = _build_quick_wins(root, guidance, theory_full)

    review_items = _collect_review_items(
        guidance=guidance,
        facet_summaries=facet_summaries,
        audit=audit,
        project_root=str(root),
    )

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
            "audit_summary": summarize_audit(audit),
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


# ── Helpers ────────────────────────────────────────────────────────────


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
            return normalize_sentence(stripped)
    return ""


def _select_actionable_anti_patterns(
    claims: list[str], max_items: int = 5
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    for claim in claims:
        text = normalize_sentence(str(claim))
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
            (
                item
                for item in ZERO_STATE_ANTI_PATTERNS
                if _PERF_ANTI_PATTERN_CUE in item
            ),
            None,
        )
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
        commands.append(
            "Run the project's lint and test commands before concluding work."
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for cmd in commands:
        if cmd in seen:
            continue
        seen.add(cmd)
        deduped.append(cmd)
    return deduped


def _build_quick_wins(
    root: Path,
    guidance: dict[str, Any],
    theory: dict[str, Any],
) -> list[str]:
    """Generate actionable quick-win suggestions for the project."""
    wins: list[str] = []

    has_config = (root / "lintgate.yaml").exists() or (
        root / ".claude" / "lintgate.yaml"
    ).exists()
    if not has_config:
        wins.append(
            "Create `.claude/lintgate.yaml` with `controlplane: {enabled: true}` "
            "to activate the full supervision mesh (lint + tests + deps + git + behavior + structure)."
        )

    rules = guidance.get("rules", [])
    do_not_count = len(guidance.get("directives", {}).get("do_not", []))
    if do_not_count > 0 and not rules:
        wins.append(
            f"Found {do_not_count} DO NOT directive(s) but no LINTGATE_FORBID_REGEX rules. "
            "Run `extract_theory_constraints` to generate enforceable rules."
        )

    proposed = theory.get("enforceable_rules", {}).get("proposed_rules", [])
    if proposed:
        wins.append(
            f"{len(proposed)} rule(s) proposed from theory extraction — review and "
            "add to CLAUDE.md to make them enforceable."
        )

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
    """Collect structured uncertainty markers for the calling agent."""
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
            detail_text = check.get("detail", "")
            if "don't exist" not in detail_text:
                continue
            colon_idx = detail_text.find(": ")
            if colon_idx < 0:
                continue
            paths_part = detail_text[colon_idx + 2 :]
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
