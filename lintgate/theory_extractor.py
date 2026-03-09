"""Theory extractor — identifies the conceptual space of a project.

Scans ALL markdown documents in a codebase (not just CLAUDE.md) to extract
the project's theoretical framework, problem-solving approach, alignment
criteria, and architectural philosophy. Produces a structured theory profile
that can be used for drift detection and alignment checking.

The 7 theory facets extracted (inspired by analysis of theory-rich projects):

1. **Core Theory**: The foundational conceptual framework — what the project
   IS and how it thinks about its problem domain.
2. **Problem-Solving Approach**: The heuristics and strategies the project
   uses to solve problems (elimination vs selection, composition vs monolith, etc.)
3. **Alignment Criteria**: What "good" looks like — contrastive examples of
   aligned vs misaligned solutions, with explanations of WHY.
4. **Architectural Philosophy**: Why the system is designed the way it is —
   design rationale, alternatives rejected, and invariants.
5. **Anti-Patterns (Conceptual)**: Not code style, but conceptual violations —
   approaches that undermine the project's theory.
6. **Key Abstractions**: The vocabulary of domain concepts, named mechanisms,
   and theoretical constructs the project defines.
7. **Enforceable Rules**: Concrete DO NOT / MUST directives that can be
   turned into lint rules (the old extractor's scope, kept as a subset).

Design decisions:
- Scans all .md files in the project, not just CLAUDE.md/AGENTS.md
- Rule-based extraction using structural heuristics (heading context,
  paragraph clustering, contrastive markers)
- No LLM token cost — deterministic, fast
- Deduplicates enforceable rules against existing LINTGATE_* rules
- Theory profile is hierarchical: facets → claims → evidence

This module is a facade — the implementation is split across:
- theory_scoring.py: claim scoring, sentence splitting, section classification
- theory_discovery.py: file enumeration, document parsing, docstring extraction
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .context_guidance import build_context_guidance

# ─── Re-exports from theory subpackage ───────────────────────────────────
# All symbols that were previously defined here are re-exported so that
# existing `from lintgate.theory_extractor import X` statements continue
# to work without changes.
from .theory.discovery import (  # noqa: F401
    _EXTRA_MD_SKIP_DIRS,
    _MAX_MD_FILES,
    _discover_md_files,
    _has_frontmatter_opt_out,
    _parse_document,
    _Section,
    extract_docstring_claims,
)
from .theory.scoring import (  # noqa: F401
    _CONTRASTIVE_MARKERS,
    _THEORY_HEADING_SIGNALS,
    _THEORY_PARAGRAPH_SIGNALS,
    _classify_section,
    _pick_best_summary_claim,
    _score_claim,
    _split_sentences,
)

# ─── Enforceable rule templates (kept from v1) ──────────────────────────

_RULE_TEMPLATES: list[tuple[str, str, Any, str]] = [
    (
        r"DO NOT create (\w[\w\s-]*\w) functions?",
        "forbid_regex",
        lambda m: rf"def\s+{_words_to_pattern(m.group(1))}",
        "medium",
    ),
    (
        r"DO NOT create (\w+(?:[\s-]\w+){0,2}) (?:that|which|for|to)\b",
        "forbid_regex",
        lambda m: rf"def\s+{_words_to_pattern(m.group(1))}",
        "medium",
    ),
    (
        r"DO NOT (?:ever )?use (\w+(?:\.\w+)*)",
        "forbid_regex",
        lambda m: rf"\b{re.escape(m.group(1))}\b",
        "high",
    ),
    (
        r"DO NOT import (\w+(?:\.\w+)*)",
        "forbid_regex",
        lambda m: rf"(?:from\s+{re.escape(m.group(1))}|import\s+{re.escape(m.group(1))})",
        "high",
    ),
    (
        r"DO NOT call (\w+(?:\.\w+)*\(\))",
        "forbid_regex",
        lambda m: rf"\b{re.escape(m.group(1).rstrip('()'))}\s*\(",
        "high",
    ),
    (
        r"MUST (?:always )?use (\w+(?:\.\w+)*)",
        "require_regex",
        lambda m: rf"\b{re.escape(m.group(1))}\b",
        "medium",
    ),
    (
        r"MUST (?:import|include) (\w+(?:\.\w+)*)",
        "require_regex",
        lambda m: rf"(?:from\s+\S*{re.escape(m.group(1))}|import\s+\S*{re.escape(m.group(1))})",
        "medium",
    ),
]

_REQUIRED_THEORY_FACETS = (
    "core_theory",
    "problem_solving",
    "alignment",
)


# ─── Public API ──────────────────────────────────────────────────────────


def extract_theory(
    project_root: str,
    working_tree_files: list[str] | None = None,
) -> dict[str, Any]:
    """Extract the conceptual theory profile of a project.

    Scans all markdown documents in the codebase and produces a structured
    theory profile covering 7 facets: core theory, problem-solving approach,
    alignment criteria, architecture philosophy, anti-patterns, key
    abstractions, and enforceable rules.

    When working_tree_files is provided (#182), also scans module-level
    docstrings in those Python files for design intent claims.

    Args:
        project_root: Absolute path to the project root.
        working_tree_files: Optional list of uncommitted Python file paths
            (relative to project_root) to scan for docstring theory.

    Returns:
        Dict with theory_profile (7 facets), docs_scanned count,
        enforceable_rules (with deduplication), and summary.
    """
    md_files = _discover_md_files(project_root)

    # Parse each document into sections
    all_sections: list[_Section] = []
    included_md_files: list[str] = []
    for md_path in md_files:
        if _has_frontmatter_opt_out(md_path):
            continue
        included_md_files.append(md_path)
        all_sections.extend(_parse_document(md_path, project_root))

    # Working-tree docstring extraction (#182)
    docstring_source_count = 0
    if working_tree_files:
        docstring_sections = extract_docstring_claims(project_root, working_tree_files)
        all_sections.extend(docstring_sections)
        docstring_source_count = len(docstring_sections)

    # Classify sections into theory facets
    profile = _build_theory_profile(all_sections)

    # Extract enforceable rules (the old extractor's scope, as a subset)
    guidance = build_context_guidance(project_root)
    existing_rules = guidance.get("rules", [])
    existing_patterns = {r.get("pattern", "") for r in existing_rules if r.get("pattern")}
    enforceable = _extract_enforceable_rules(guidance, existing_patterns, existing_rules)

    # Build summary
    summary = _build_summary(profile)
    validity = _build_validity_report(
        profile,
        docs_scanned=len(included_md_files),
        sections_scanned=len(all_sections),
        enforceable=enforceable,
    )

    result = {
        "theory_profile": profile,
        "docs_scanned": len(included_md_files),
        "doc_paths": [os.path.relpath(p, project_root) for p in included_md_files],
        "enforceable_rules": enforceable,
        "summary": summary,
        "validity": validity,
    }
    if docstring_source_count:
        result["docstring_sources"] = docstring_source_count

    return result


# Keep backward compat — old MCP tool calls extract_constraints
def extract_constraints(project_root: str) -> dict[str, Any]:
    """Backward-compatible wrapper that returns the full theory extraction."""
    return extract_theory(project_root)


def build_theory_pack(
    project_root: str,
    include_full_profile: bool = False,
) -> dict[str, Any]:
    """Build a compact runtime payload optimized for agent context injection.

    The theory pack is designed for two-tier consumption:

    **Tier 1 (always included, cacheable)**: A compact digest that fits
    in ~500-1500 tokens. Contains enforceable rules, 1-sentence facet
    summaries, and the anti-pattern checklist. Goes in the system prompt
    prefix where it benefits from prompt caching (90% cost reduction on
    Anthropic) and lands in the high-attention zone (start of context).

    **Tier 2 (on-demand)**: Full claim text retrievable via
    ``get_theory_context(facet, keywords)`` when the agent needs deeper
    reasoning about a specific violation.

    Layout follows "lost in the middle" mitigation:
    - Top: enforceable rules (highest value, most actionable)
    - Middle: facet summaries (compressed context)
    - End: anti-pattern list (high attention zone)

    Returns:
        Dict with:
        - digest_text: ready-to-inject text block (~500-1500 tokens)
        - digest_token_estimate: rough token count
        - enforceable_rules: list of rule dicts
        - facet_summaries: 1-sentence per facet
        - anti_patterns: extracted anti-pattern claims
        - full_profile: complete theory for Tier 2 retrieval
          (only when include_full_profile=True)
    """
    full = extract_theory(project_root)
    profile = full["theory_profile"]
    enforceable = full["enforceable_rules"]

    # Build facet summaries (1 sentence each, deduplicated across facets)
    facet_summaries: dict[str, str] = {}
    used_summaries: set[str] = set()
    for facet, entries in profile.items():
        if not entries:
            facet_summaries[facet] = "(no theory content found)"
            continue
        # Pick the highest-quality claim as the summary, avoiding reuse
        all_claims = [c for e in entries for c in e["claims"]]
        if all_claims:
            best = _pick_best_summary_claim(all_claims, exclude=used_summaries)
            facet_summaries[facet] = best
            used_summaries.add(best)
        else:
            facet_summaries[facet] = "(no theory content found)"

    # Extract anti-pattern list (capped)
    anti_patterns: list[str] = []
    for entry in profile.get("anti_patterns", []):
        for claim in entry["claims"]:
            if len(anti_patterns) < 10:
                anti_patterns.append(claim)

    # Build the digest text block
    lines: list[str] = []
    lines.append("## Project Theory (Enforceable Rules)")
    rules = enforceable.get("proposed_rules", [])
    existing_count = enforceable.get("existing_rule_count", 0)
    if existing_count > 0:
        lines.append(f"({existing_count} active rules enforced by linter)")
    if rules:
        for r in rules[:10]:
            lines.append(f"- {r['add_line']}")
    elif existing_count == 0:
        lines.append("(no enforceable rules extracted)")
    lines.append("")

    lines.append("## Project Theory (Facet Summaries)")
    facet_labels = {
        "core_theory": "Core Theory",
        "problem_solving": "Problem-Solving Approach",
        "alignment": "Alignment Criteria",
        "architecture": "Architecture Philosophy",
        "anti_patterns": "Anti-Patterns",
        "abstractions": "Key Abstractions",
    }
    for facet, label in facet_labels.items():
        summary = facet_summaries.get(facet, "")
        if summary and summary != "(no theory content found)":
            lines.append(f"- **{label}**: {summary}")
    lines.append("")

    if anti_patterns:
        lines.append("## Anti-Patterns (Conceptual Violations)")
        for ap in anti_patterns[:7]:
            # Truncate long claims
            display = ap[:150] + "..." if len(ap) > 150 else ap
            lines.append(f"- {display}")

    digest_text = "\n".join(lines)
    # Rough token estimate: ~1.3 tokens per word for English prose/code mix
    word_count = len(digest_text.split())
    token_estimate = int(word_count * 1.3)

    pack = {
        "digest_text": digest_text,
        "digest_token_estimate": token_estimate,
        "enforceable_rules": enforceable,
        "facet_summaries": facet_summaries,
        "anti_patterns": anti_patterns,
        "summary": full.get("summary", {}),
        "validity": full.get("validity", {}),
    }

    if include_full_profile:
        pack["full_profile"] = profile

    return pack


def get_theory_context(
    project_root: str,
    facet: str | None = None,
    keywords: list[str] | None = None,
    max_claims: int = 5,
) -> dict[str, Any]:
    """On-demand Tier 2 retrieval: get full claim text for a topic.

    Called by the agent when it needs deeper reasoning about a specific
    violation or design decision. Returns the most relevant claims
    matched by facet and/or keyword overlap.

    Args:
        project_root: Absolute path to the project.
        facet: Optional facet to filter by (core_theory, problem_solving,
            alignment, architecture, anti_patterns, abstractions).
        keywords: Optional keywords to match against claim text.
        max_claims: Maximum claims to return (default 5).

    Returns:
        Dict with matched claims, their sources, and the facet they belong to.
    """
    if max_claims <= 0:
        raise ValueError("max_claims must be > 0")

    full = extract_theory(project_root)
    profile = full["theory_profile"]

    results: list[dict[str, Any]] = []

    facets_to_search = [facet] if facet and facet in profile else list(profile.keys())

    for f in facets_to_search:
        for entry in profile.get(f, []):
            for claim in entry["claims"]:
                score = 0
                if keywords:
                    claim_lower = claim.lower()
                    for kw in keywords:
                        if kw.lower() in claim_lower:
                            score += 1
                else:
                    score = 1  # No keywords = return everything in facet

                if score > 0:
                    results.append(
                        {
                            "facet": f,
                            "claim": claim,
                            "source": entry["source"],
                            "heading": entry["heading"],
                            "relevance_score": score,
                        }
                    )

    # Sort by relevance, then truncate
    results.sort(key=lambda r: -r["relevance_score"])
    total_matched = len(results)
    results = results[:max_claims]

    return {
        "claims": results,
        "total_matched": total_matched,
        "returned_count": len(results),
        "truncated": total_matched > len(results),
        "query": {"facet": facet, "keywords": keywords},
    }


def get_theory_context_from_profile(
    profile: dict[str, Any],
    facet: str | None = None,
    keywords: list[str] | None = None,
    max_claims: int = 5,
) -> dict[str, Any]:
    """Tier 2 retrieval from a pre-extracted theory profile (no I/O).

    Same logic as get_theory_context() but takes a pre-extracted profile dict
    instead of calling extract_theory(). This avoids re-scanning all markdown
    files on every signal — the caller caches the profile once per mesh run.

    Args:
        profile: The "theory_profile" dict from extract_theory() output.
        facet: Optional facet to filter by.
        keywords: Optional keywords to match against claim text.
        max_claims: Maximum claims to return.

    Returns:
        Dict with matched claims, their sources, and the facet they belong to.
        Returns empty results if profile is empty or None.
    """
    if not profile or max_claims <= 0:
        return {
            "claims": [],
            "total_matched": 0,
            "returned_count": 0,
            "truncated": False,
            "query": {"facet": facet, "keywords": keywords},
        }

    results: list[dict[str, Any]] = []

    facets_to_search = [facet] if facet and facet in profile else list(profile.keys())

    for f in facets_to_search:
        for entry in profile.get(f, []):
            for claim in entry.get("claims", []):
                score = 0
                if keywords:
                    claim_lower = claim.lower()
                    for kw in keywords:
                        if kw.lower() in claim_lower:
                            score += 1
                else:
                    score = 1  # No keywords = return everything in facet

                if score > 0:
                    results.append(
                        {
                            "facet": f,
                            "claim": claim,
                            "source": entry.get("source", ""),
                            "heading": entry.get("heading", ""),
                            "relevance_score": score,
                        }
                    )

    # Sort by relevance, then truncate
    results.sort(key=lambda r: -r["relevance_score"])
    total_matched = len(results)
    results = results[:max_claims]

    return {
        "claims": results,
        "total_matched": total_matched,
        "returned_count": len(results),
        "truncated": total_matched > len(results),
        "query": {"facet": facet, "keywords": keywords},
    }


# ─── Theory profile construction ─────────────────────────────────────────


def _build_theory_profile(sections: list[_Section]) -> dict[str, Any]:
    """Classify sections into theory facets and extract claims."""
    profile: dict[str, list[dict[str, Any]]] = {
        "core_theory": [],
        "problem_solving": [],
        "alignment": [],
        "architecture": [],
        "anti_patterns": [],
        "abstractions": [],
    }

    for section in sections:
        facets = _classify_section(section)
        for facet in facets:
            claims = _extract_claims(section, facet)
            if claims:
                profile[facet].append(
                    {
                        "heading": section.heading,
                        "source": f"{section.rel_path}:{section.line_no}",
                        "claims": claims,
                    }
                )

    # Deduplicate claims within each facet
    for facet in profile:
        profile[facet] = _dedupe_facet_entries(profile[facet])

    return profile


def _extract_claims(section: _Section, facet: str) -> list[str]:
    """Extract the substantive claims from a section for a given facet.

    A "claim" is a sentence or short passage that expresses a theoretical
    position, design rationale, heuristic, or alignment criterion.
    We extract these by looking for assertion patterns in the text.
    """
    claims: list[str] = []
    # Strip code blocks to avoid extracting code as theory
    text = re.sub(r"```[\s\S]*?```", "", section.body)
    # Strip inline code markers but preserve the content inside them
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Strip markdown tables
    text = re.sub(r"^\|.*\|$", "", text, flags=re.M)

    sentences = _split_sentences(text)

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 20 or len(sentence) > 500:
            continue

        score = _score_claim(sentence, facet)
        if score > 0:
            # Clean up for presentation
            cleaned = re.sub(r"\s+", " ", sentence).strip()
            # Skip sentences that are mostly code references (CamelCase/path-heavy)
            if len(re.findall(r"\b[A-Z][a-z]+[A-Z]\w+\b", cleaned)) > 3:
                continue
            # Skip list-of-files or path-heavy sentences
            if cleaned.count("/") > 3:
                continue
            # Skip code-like fragments (MACRO definitions, command examples)
            if re.match(r"^(?:MACRO|SHORTCUT|ENDSHORTCUT|python|bash|pip)\b", cleaned):
                continue
            claims.append(cleaned)

    # Cap claims per section to avoid noise
    return claims[:8]


def _dedupe_facet_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate within a facet by claim text similarity."""
    seen_claims: set[str] = set()
    deduped: list[dict[str, Any]] = []

    for entry in entries:
        unique_claims = []
        for claim in entry["claims"]:
            # Normalize for comparison
            key = re.sub(r"\s+", " ", claim.lower().strip())
            if key not in seen_claims:
                seen_claims.add(key)
                unique_claims.append(claim)

        if unique_claims:
            entry_copy = dict(entry)
            entry_copy["claims"] = unique_claims
            deduped.append(entry_copy)

    return deduped


# ─── Enforceable rules extraction (from v1, kept as subset) ─────────────


def _extract_enforceable_rules(
    guidance: dict[str, Any],
    existing_patterns: set[str],
    existing_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract concrete enforceable lint rules from directives."""
    directives = guidance.get("directives", {})
    all_directives = [
        *directives.get("do_not", []),
        *directives.get("must", []),
        *directives.get("critical", []),
    ]

    proposed_rules: list[dict[str, Any]] = []
    already_covered_count = 0

    for directive in all_directives:
        cleaned = _strip_markdown(directive)
        for regex, kind, pattern_builder, confidence in _RULE_TEMPLATES:
            match = re.search(regex, cleaned, re.IGNORECASE)
            if not match:
                continue

            pattern = pattern_builder(match)
            if pattern is None:
                continue

            if _is_covered_by_existing(pattern, existing_patterns, existing_rules):
                already_covered_count += 1
                continue

            prefix = "LINTGATE_FORBID_REGEX" if kind == "forbid_regex" else "LINTGATE_REQUIRE_REGEX"
            proposed_rules.append(
                {
                    "source_directive": directive,
                    "proposed_rule": {
                        "kind": kind,
                        "pattern": pattern,
                        "severity": "blocking" if kind == "forbid_regex" else "warning",
                        "message": f"Violates: {directive}",
                    },
                    "confidence": confidence,
                    "add_line": f"{prefix}: {pattern}",
                }
            )
            break

    # Dedupe by pattern
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for rule in proposed_rules:
        p = rule["proposed_rule"]["pattern"]
        if p not in seen:
            seen.add(p)
            deduped.append(rule)

    return {
        "proposed_rules": deduped,
        "existing_rule_count": len(existing_rules),
        "directives_analyzed": len(all_directives),
        "already_covered": already_covered_count,
    }


# ─── Summary generation ─────────────────────────────────────────────────


def _build_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a human-readable summary of the theory profile."""
    summary: dict[str, Any] = {}

    for facet, entries in profile.items():
        total_claims = sum(len(e["claims"]) for e in entries)
        sources = [e["source"] for e in entries]
        # Pick the top 3 most important claims (from entries with most claims)
        top_claims: list[str] = []
        for entry in sorted(entries, key=lambda e: len(e["claims"]), reverse=True):
            for claim in entry["claims"]:
                if len(top_claims) < 3:
                    top_claims.append(claim)

        summary[facet] = {
            "claim_count": total_claims,
            "source_count": len(sources),
            "top_claims": top_claims,
        }

    return summary


def _build_validity_report(
    profile: dict[str, Any],
    docs_scanned: int,
    sections_scanned: int,
    enforceable: dict[str, Any],
) -> dict[str, Any]:
    """Build deterministic quality diagnostics for theory extraction validity."""
    claims_by_facet = {
        facet: sum(len(entry["claims"]) for entry in entries) for facet, entries in profile.items()
    }
    total_claims = sum(claims_by_facet.values())
    facets_with_claims = [facet for facet, count in claims_by_facet.items() if count > 0]
    missing_required = [
        facet for facet in _REQUIRED_THEORY_FACETS if claims_by_facet.get(facet, 0) == 0
    ]

    total_entries = sum(len(entries) for entries in profile.values())
    traceable_entries = sum(
        1
        for entries in profile.values()
        for entry in entries
        if str(entry.get("source", "")).strip()
    )
    traceability_pct = round((traceable_entries / max(total_entries, 1)) * 100, 1)

    claims_per_doc = round(total_claims / max(docs_scanned, 1), 2)
    proposed_rules = len(enforceable.get("proposed_rules", []))
    existing_rules = int(enforceable.get("existing_rule_count", 0))

    warnings: list[str] = []
    recommendations: list[str] = []

    if missing_required:
        missing_labels = ", ".join(missing_required)
        warnings.append(f"Missing required facets: {missing_labels}")
        recommendations.append(
            "Add explicit sections for missing facets (core theory, approach, alignment) in primary docs."
        )

    if claims_per_doc < 1.0 and docs_scanned > 0:
        warnings.append(
            f"Low claim density ({claims_per_doc} claims/doc). "
            "Extraction may be too sparse for robust theory alignment."
        )
        recommendations.append(
            "Add rationale-rich prose with causal/contrastive language to improve extractable theory signal."
        )

    if existing_rules == 0 and proposed_rules == 0:
        warnings.append("No enforceable rules found (existing or proposed).")
        recommendations.append(
            "Add LINTGATE_FORBID_REGEX / LINTGATE_REQUIRE_REGEX lines for critical constraints."
        )

    status = "strong"
    if missing_required or total_claims < 6:
        status = "weak"
    elif warnings:
        status = "partial"

    return {
        "status": status,
        "docs_scanned": docs_scanned,
        "sections_scanned": sections_scanned,
        "total_claims": total_claims,
        "claims_per_doc": claims_per_doc,
        "facets_with_claims": facets_with_claims,
        "missing_required_facets": missing_required,
        "traceability_pct": traceability_pct,
        "existing_rules": existing_rules,
        "proposed_rules": proposed_rules,
        "warnings": warnings,
        "recommendations": recommendations,
    }


# ─── Utility functions ───────────────────────────────────────────────────


def _is_covered_by_existing(
    pattern: str,
    existing_patterns: set[str],
    existing_rules: list[dict[str, Any]],
) -> bool:
    """Check if a proposed pattern is already covered by existing rules."""
    if pattern in existing_patterns:
        return True

    for rule in existing_rules:
        existing_pattern = rule.get("pattern", "")
        if not existing_pattern:
            continue
        try:
            if re.search(existing_pattern, pattern):
                return True
        except re.error:
            continue

    return False


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting to expose raw directive text."""
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"^\d+\.\s*", "", text)
    text = re.sub(r"`[^`]+`", lambda m: m.group(0).strip("`"), text)
    return text.strip()


def _words_to_pattern(words: str) -> str:
    """Convert a multi-word phrase to a regex-friendly pattern."""
    parts = re.split(r"[\s-]+", words.strip())
    return r"[_\s-]*".join(re.escape(p) for p in parts) if parts else re.escape(words)


# ─── Theory staleness checking ───────────────────────────────────────────


def check_theory_staleness(
    project_root: str,
    theory_profile: dict[str, Any] | None,
    git_context: dict[str, Any],
) -> dict[str, Any]:
    """Check if the theory profile covers uncommitted working-tree files.

    Returns a staleness report with:
    - stale: True if uncommitted files lack theory coverage
    - uncovered_files: list of uncommitted Python files with docstrings
      not covered by existing theory claims
    - total_uncommitted_py: count of uncommitted .py files
    - recommendation: actionable suggestion

    Args:
        project_root: Absolute path to the project root.
        theory_profile: The theory_profile dict from extract_theory() output.
            None means no theory profile exists.
        git_context: Git working tree context from collect_working_tree_context().
    """
    modified = git_context.get("modified_files", [])
    untracked = git_context.get("untracked_files", [])
    all_uncommitted = modified + untracked

    # Filter to Python source files (not tests, not __pycache__)
    py_files = [
        f
        for f in all_uncommitted
        if f.endswith(".py")
        and not f.startswith("tests/")
        and not f.startswith("test_")
        and "__pycache__" not in f
    ]

    result: dict[str, Any] = {
        "stale": False,
        "uncovered_files": [],
        "total_uncommitted_py": len(py_files),
        "recommendation": "",
    }

    if not py_files:
        return result

    if theory_profile is None:
        result["stale"] = True
        result["uncovered_files"] = py_files[:20]
        result["recommendation"] = (
            f"No theory profile exists. {len(py_files)} uncommitted Python files "
            "have no theory grounding. Run `build_theory_pack` to extract design intent."
        )
        return result

    # Collect all source files mentioned in theory profile claims
    covered_sources: set[str] = set()
    for facet_entries in theory_profile.values():
        if not isinstance(facet_entries, list):
            continue
        for entry in facet_entries:
            source = entry.get("source", "")
            if ":" in source:
                covered_sources.add(source.split(":")[0])
            else:
                covered_sources.add(source)

    # Check which uncommitted Python files have module-level docstrings
    # but are not covered by existing theory claims
    uncovered: list[str] = []
    for fpath in py_files:
        # Check if any theory source path covers this file's directory/module
        if fpath in covered_sources:
            continue

        # Check if the file has a substantive module-level docstring
        abs_path = os.path.join(project_root, fpath)
        if not os.path.isfile(abs_path):
            continue
        try:
            import ast

            source = Path(abs_path).read_text(errors="replace")
            tree = ast.parse(source)
            docstring = ast.get_docstring(tree)
            if docstring and len(docstring.strip()) >= 30:
                uncovered.append(fpath)
        except (SyntaxError, OSError):
            continue

    if uncovered:
        result["stale"] = True
        result["uncovered_files"] = uncovered[:20]
        result["recommendation"] = (
            f"Theory profile doesn't cover {len(uncovered)} uncommitted file(s) "
            "with design docstrings. Run `build_theory_pack` to extract design intent "
            f"from working tree. Files: {', '.join(uncovered[:5])}"
        )

    return result
