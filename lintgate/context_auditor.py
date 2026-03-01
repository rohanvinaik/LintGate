"""Context health auditor — CLAUDE.md/AGENTS.md quality assessment.

Audits LLM context files against established best practices for
LLM coding agent context/memory files. Configurable thresholds via
lintgate.yaml → linters.context_auditor.

Best practices synthesized from:
- Claude Code docs (CLAUDE.md <300 lines, hierarchical discovery,
  .claude/rules/ with paths frontmatter, progressive disclosure)
- AGENTS.md cross-tool standard (25+ platforms, MIT licensed)
- Chroma research on context rot (all models degrade with input length)

Reuses:
- context_guidance.discover_context_files() for file discovery
- context_guidance.build_context_guidance() for directive parsing
- context_guidance._extract_path_hints() for path reference extraction
- config.load_config() for threshold overrides
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import load_config

# Re-export from extracted module for backward compatibility.
# Tests and external callers import these private names from context_auditor.
from .context_auditor_checks import (  # noqa: F401
    DirectiveClassification,
    _is_regex_enforceable,
    check_contradictions,
    check_length,
    check_path_references,
    check_rule_coverage,
    check_staleness,
    check_structure,
    classify_directive_enforceability,
    extract_path_refs,
    find_dead_paths,
)
from .context_guidance import (
    build_context_guidance,
    discover_context_files,
)

# Backward-compatible underscore aliases for check functions.
_check_length = check_length
_check_structure = check_structure
_check_staleness = check_staleness
_check_contradictions = check_contradictions
_check_rule_coverage = check_rule_coverage
_check_path_references = check_path_references
_extract_path_refs = extract_path_refs
_find_dead_paths = find_dead_paths

# ─── Default thresholds (overridable via lintgate.yaml) ──────────────────

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "max_lines_warn": 300,
    "max_lines_error": 500,
    "staleness_days": 30,
    "min_rule_coverage_pct": 50,
    "max_path_references": 50,
}


def audit_context_health(
    project_root: str,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit CLAUDE.md/AGENTS.md quality against LLM best practices.

    Args:
        project_root: Absolute path to the project.
        thresholds: Optional threshold overrides (merged on top of
            config → defaults).

    Returns:
        Dict with per-file audit results, suggestions, and thresholds used.
    """
    config = load_config(project_root)
    config_thresholds = config.linter_configs.get("context_auditor", {})
    effective: dict[str, Any] = {
        **DEFAULT_THRESHOLDS,
        **{k: v for k, v in config_thresholds.items() if k in DEFAULT_THRESHOLDS},
        **(thresholds or {}),
    }

    exempt_files = config_thresholds.get("exempt_files", [])

    context_files = discover_context_files(project_root)
    guidance = build_context_guidance(project_root)
    existing_rules = guidance.get("rules", [])

    results: list[dict[str, Any]] = []

    for file_path in context_files:
        basename = os.path.basename(file_path)

        # Skip exempt files
        if basename in exempt_files:
            results.append(
                {
                    "file": file_path,
                    "name": basename,
                    "status": "exempt",
                    "health_checks": [],
                    "suggestions": [],
                }
            )
            continue

        try:
            text = Path(file_path).read_text()
        except OSError:
            results.append(
                {
                    "file": file_path,
                    "name": basename,
                    "status": "unreadable",
                    "health_checks": [],
                    "suggestions": [],
                }
            )
            continue

        lines = text.splitlines()
        line_count = len(lines)
        checks: list[dict[str, Any]] = []
        suggestions: list[str] = []

        check_length(checks, suggestions, line_count, effective)
        check_structure(checks, suggestions, text, lines)
        check_staleness(checks, suggestions, file_path, effective)
        check_contradictions(checks, suggestions, guidance)
        check_rule_coverage(checks, suggestions, guidance, existing_rules, effective)
        check_path_references(checks, suggestions, text, project_root, effective)

        status = "pass"
        if any(c["status"] == "error" for c in checks):
            status = "error"
        elif any(c["status"] == "warn" for c in checks):
            status = "warn"

        results.append(
            {
                "file": file_path,
                "name": basename,
                "line_count": line_count,
                "status": status,
                "health_checks": checks,
                "suggestions": suggestions,
            }
        )

    return {
        "audit": results,
        "thresholds_used": effective,
        "context_file_count": len(context_files),
    }


# ── Session Readiness Advisory ───────────────────────────────────────

_REQUIRED_FACETS = ("core_theory", "problem_solving", "alignment")


@dataclass
class SessionReadiness:
    """Result of checking whether theory context is ready for deep supervision."""

    ready: bool = False
    missing: list[str] = field(default_factory=list)
    recommendation: str = ""


def check_session_readiness(
    project_root: str,
    theory_profile: dict[str, Any] | None = None,
    git_context: dict[str, Any] | None = None,
) -> SessionReadiness:
    """Check if the session has sufficient theory context for deep supervision.

    Checks:
    - Theory profile has required facets (core_theory, problem_solving, alignment)
      with at least one claim each.
    - At least one enforceable rule exists.
    - Theory profile covers uncommitted files (#182).

    Args:
        project_root: Repository root.
        theory_profile: Pre-extracted theory profile (avoids re-extraction).
        git_context: Optional git working tree context for staleness checking.

    Returns:
        SessionReadiness with ready flag, missing items, and recommendation.
    """
    missing: list[str] = []

    # Check theory profile facets
    if theory_profile is None:
        missing.append("no_theory_profile")
    else:
        for facet in _REQUIRED_FACETS:
            entries = theory_profile.get(facet, [])
            has_claims = False
            for entry in entries:
                if isinstance(entry, dict) and entry.get("claims"):
                    has_claims = True
                    break
            if not has_claims:
                missing.append(f"missing_facet:{facet}")

    # Check for enforceable rules (look for CLAUDE.md or context guidance)
    has_rules = False
    claude_path = Path(project_root) / "CLAUDE.md"
    if claude_path.exists():
        text = claude_path.read_text()
        if "LINTGATE_FORBID_REGEX" in text or "LINTGATE_REQUIRE_REGEX" in text:
            has_rules = True

    if not has_rules:
        missing.append("no_enforceable_rules")

    # Check theory coverage of uncommitted files (#182)
    if git_context and theory_profile is not None:
        try:
            from .theory_extractor import check_theory_staleness

            staleness = check_theory_staleness(
                project_root, theory_profile, git_context
            )
            if staleness.get("stale"):
                uncovered = staleness.get("uncovered_files", [])
                missing.append(f"theory_stale:{len(uncovered)}_uncommitted_files")
        except Exception:
            pass  # Graceful degradation

    if missing:
        parts = []
        if "no_theory_profile" in missing:
            parts.append("extract project theory")
        facet_missing = [
            m.split(":")[1] for m in missing if m.startswith("missing_facet:")
        ]
        if facet_missing:
            parts.append(f"add claims for facets: {', '.join(facet_missing)}")
        if "no_enforceable_rules" in missing:
            parts.append("add enforceable rules to CLAUDE.md")
        stale_items = [m for m in missing if m.startswith("theory_stale:")]
        if stale_items:
            parts.append(
                "run build_theory_pack to cover uncommitted files with design docstrings"
            )
        recommendation = f"Run bootstrap_context_files to {'; '.join(parts)}."
    else:
        recommendation = ""

    return SessionReadiness(
        ready=not missing,
        missing=missing,
        recommendation=recommendation,
    )
