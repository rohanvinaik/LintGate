"""Best-practice context file bootstrap for CLAUDE.md and AGENTS.md.

Builds concise, theory-grounded context files by combining:
- context guidance/rules already present in the project
- context health audit diagnostics
- theory extraction summaries and enforceable-rule proposals

This is meant for MCP usage where an LLM calls a deterministic tool to
generate high-quality starting context files, then reviews/edits them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Re-export from helper sub-modules for backward compatibility.
from ._context_bootstrap_helpers import (  # noqa: F401
    _NEGATIVE_CUE_RE,
    _NO_THEORY,
    _PERF_ANTI_PATTERN_CUE,
    _build_quick_wins,
    _collect_machine_rule_lines,
    _project_metadata,
    _read_readme_description,
    _recommended_commands,
    _rule_to_line,
    _select_actionable_anti_patterns,
)
from ._context_bootstrap_review import (  # noqa: F401
    ReviewItem,
    _collect_dead_path_review_items,
    _collect_directive_review_items,
    _collect_facet_fallback_items,
    _collect_review_items,
    _extract_dead_paths,
)
from .context_auditor import audit_context_health

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


def _resolve_model_profile(
    model_id: str | None, use_model_profile: bool
) -> tuple[dict[str, Any] | None, str | None, float]:
    """Resolve model profile for calibration. Returns (dict, key, confidence)."""
    if not model_id or not use_model_profile:
        return None, None, 0.0
    try:
        from .controlplane.model_profiles import get_profile, resolve_model_key

        model_key_resolved = resolve_model_key(model_id)
        if not model_key_resolved:
            return None, None, 0.0
        profile = get_profile(model_id)
        if profile and profile.is_usable():
            return profile.to_dict(), model_key_resolved, profile.confidence
    except Exception:
        pass
    return None, None, 0.0


_NEVER_OVERWRITE = frozenset({"AGENTS.md"})


def _write_drafts(
    root: Path,
    drafts: dict[str, str],
    write: bool,
    overwrite: bool,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Write or plan draft files. Returns (file_reports, written, skipped)."""
    file_reports: list[dict[str, Any]] = []
    written: list[str] = []
    skipped_existing: list[str] = []
    for rel_path, content in drafts.items():
        target = root / rel_path
        exists = target.exists()
        status = "planned"

        if write:
            if exists and (not overwrite or rel_path in _NEVER_OVERWRITE):
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
    return file_reports, written, skipped_existing


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

    model_profile_dict, model_key_resolved, model_profile_confidence = _resolve_model_profile(
        model_id, use_model_profile
    )

    metadata = _project_metadata(root)
    facet_summaries = theory_pack.get("facet_summaries", {})

    if model_profile_dict and model_profile_dict.get("custom_anti_patterns"):
        anti_patterns = model_profile_dict["custom_anti_patterns"][:5]
    else:
        anti_patterns = _select_actionable_anti_patterns(theory_pack.get("anti_patterns", []))

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

    file_reports, written, skipped_existing = _write_drafts(root, drafts, write, overwrite)

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
