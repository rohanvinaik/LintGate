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
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .context_guidance import build_context_guidance

# ─── Constants ───────────────────────────────────────────────────────────

# Max files to scan (prevent runaway on huge repos)
_MAX_MD_FILES = 100

# Directories to skip when scanning for .md files.
# Note: .claude is skipped EXCEPT for .claude/rules/ which is scanned
# explicitly in _discover_md_files(). This avoids picking up session
# transcripts and temp files while still finding theory in rules docs.
_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    "dist",
    "build",
    ".eggs",
    ".mypy_cache",
    ".pytest_cache",
    "downloaded",
    ".claude",
    "retrospectives",
}

# Heading patterns that signal theory-relevant sections
_THEORY_HEADING_SIGNALS: dict[str, list[str]] = {
    "core_theory": [
        r"theor",
        r"abstract",
        r"introduction",
        r"foundation",
        r"what (?:this|it) is",
        r"overview",
        r"definition",
        r"core (?:insight|concept|idea|principle)",
        r"key insight",
        r"fundamental",
        r"hypothesis",
        r"research question",
        r"motivation",
        r"framing",
        r"scope and stance",
    ],
    "problem_solving": [
        r"approach",
        r"method",
        r"strateg",
        r"heuristic",
        r"how (?:it|we|this) (?:work|solv|think|approach)",
        r"algorithm",
        r"pipeline",
        r"workflow",
        r"process",
        r"walkthrough",
        r"technique",
        r"lesson",
        r"recommendation",
        r"what worked",
        r"practical guidance",
    ],
    "alignment": [
        r"alignment",
        r"proper.*(?:vs|versus).*improper",
        r"(?:wrong|correct|right|good|bad)\s+(?:vs|versus|and|example)",
        r"example.*(?:wrong|correct|proper|improper)",
        r"(?:what|how).*(?:good|right|correct).*look",
        r"(?:why|how).*matters?",
        r"non[- ]?goal",
        r"scope",
        r"what could be better",
        r"what .* gets right",
    ],
    "architecture": [
        r"architect",
        r"design",
        r"system",
        r"structure",
        r"why (?:this|we|not)",
        r"rationale",
        r"trade-?off",
        r"comparison",
        r"alternative",
        r"integration",
        r"specification",
        r"decomposition",
        r"module",
        r"performance",
        r"optimi[sz]",
        r"profil",
        r"benchmark",
        r"scaling",
    ],
    "anti_patterns": [
        r"anti[- ]?pattern",
        r"pitfall",
        r"(?:do )?not",
        r"avoid",
        r"warning",
        r"danger",
        r"mistake",
        r"(?:what|how).*(?:wrong|fail|break|ruin)",
        r"common (?:error|mistake|problem)",
        r"what didn.t work",
        r"caused problem",
        r"risk",
        r"mitigation",
    ],
    "abstractions": [
        r"concept",
        r"vocabular",
        r"terminolog",
        r"glossar",
        r"key (?:term|concept|abstraction|definition)",
        r"primitive",
        r"building block",
        r"data (?:model|structure|type)",
        r"component",
        r"metric",
        r"evaluation",
    ],
}

# Paragraph-level signals for theory content
_THEORY_PARAGRAPH_SIGNALS = {
    "core_theory": [
        re.compile(r"the (?:key|core|fundamental|central) (?:insight|idea|principle|claim)", re.I),
        re.compile(
            r"this (?:system|project|architecture|approach) (?:is|uses|implements|demonstrates)",
            re.I,
        ),
        re.compile(r"(?:we|the system) (?:define|articulate|propose|claim|argue)", re.I),
        re.compile(r"the theory (?:of|behind|underlying)", re.I),
        re.compile(r"we (?:hypothesize|propose|conjecture) that", re.I),
        re.compile(r"this work (?:addresses|tests|investigates|explores)", re.I),
        re.compile(r"the (?:hypothesis|conjecture|thesis) is", re.I),
    ],
    "problem_solving": [
        re.compile(r"(?:it is|we find that) .*(?:easier|better|faster|more efficient) to", re.I),
        re.compile(r"rather than .*, (?:we|the system|this)", re.I),
        re.compile(r"(?:by|through) (?:encoding|exploiting|leveraging|using)", re.I),
        re.compile(r"transform.* (?:intractable|exponential|brute.?force).* into", re.I),
        re.compile(r"(?:instead of|rather than|not by|not through)", re.I),
        re.compile(r"\*\*lesson[:\*]", re.I),
        re.compile(r"\*\*recommendation", re.I),
        re.compile(r"(?:what worked|what didn.t work)", re.I),
        re.compile(r"the (?:fix|solution|workaround|approach) was", re.I),
    ],
    "alignment": [
        re.compile(r"if you .*, you will (?:ruin|break|destroy|undermine|bypass)", re.I),
        re.compile(r"(?:wrong|incorrect|improper|bad|misaligned).*(?:because|since|as)", re.I),
        re.compile(r"(?:correct|proper|right|good|aligned).*(?:because|since|as)", re.I),
        re.compile(r"the (?:goal|purpose|point) is (?:not )?(?:just )?to", re.I),
        re.compile(r"this (?:approach|method|way|solution) (?:supports?|enables?|allows?)", re.I),
        re.compile(r"\*\*non[- ]?goal\*\*", re.I),
        re.compile(r"(?:primary|secondary) objective", re.I),
    ],
    "anti_patterns": [
        re.compile(r"(?:task|problem)[- ]specific (?:function|solution|hack|workaround)", re.I),
        re.compile(r"(?:black[- ]?box|monolith|hard[- ]?cod|ad[- ]?hoc)", re.I),
        re.compile(r"bypass.* (?:learning|composition|architecture|system)", re.I),
        re.compile(r"(?:will|would|can) (?:ruin|break|destroy|undermine)", re.I),
        re.compile(r"(?:trying harder|brute force|premature|overfitting)", re.I),
    ],
    "architecture": [
        re.compile(r"\b(?:O\(n[²2]\)|quadratic|exponential|linear time)\b", re.I),
        re.compile(r"\b(?:vectori[sz]|batch|parallel)\b.*\b(?:instead|rather|prefer)\b", re.I),
        re.compile(r"\b(?:performance|latency|throughput|bottleneck)\b.*\b(?:because|since|critical)\b", re.I),
        re.compile(r"\b(?:JIT|numba|numpy|vectori[sz]ed)\b.*\b(?:hot|loop|path|critical)\b", re.I),
    ],
}

# Contrastive markers that signal alignment criteria
_CONTRASTIVE_MARKERS = [
    re.compile(r"^#+\s*(?:WRONG|INCORRECT|BAD|IMPROPER|ANTI-PATTERN)", re.I | re.M),
    re.compile(r"^#+\s*(?:CORRECT|RIGHT|GOOD|PROPER|ALIGNED)", re.I | re.M),
    re.compile(r"(?:wrong|incorrect).*(?:vs|versus|→).*(?:correct|right)", re.I),
    re.compile(r"(?:instead of|rather than|not like|don't do)", re.I),
]

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


def extract_theory(project_root: str) -> dict[str, Any]:
    """Extract the conceptual theory profile of a project.

    Scans all markdown documents in the codebase and produces a structured
    theory profile covering 7 facets: core theory, problem-solving approach,
    alignment criteria, architecture philosophy, anti-patterns, key
    abstractions, and enforceable rules.

    Args:
        project_root: Absolute path to the project root.

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

    return {
        "theory_profile": profile,
        "docs_scanned": len(included_md_files),
        "doc_paths": [os.path.relpath(p, project_root) for p in included_md_files],
        "enforceable_rules": enforceable,
        "summary": summary,
        "validity": validity,
    }


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


# ─── Document discovery ──────────────────────────────────────────────────


def _discover_md_files(project_root: str) -> list[str]:
    """Find all markdown files in the project, respecting skip dirs.

    Also explicitly scans .claude/rules/ which is a first-class location
    for project theory and constraint documentation.
    """
    root = Path(project_root)
    found: list[str] = []

    # Scan .claude/rules first — this is high-value theory content and
    # should survive the global file cap.
    rules_dir = root / ".claude" / "rules"
    if rules_dir.is_dir():
        for fname in sorted(os.listdir(rules_dir)):
            if fname.lower().endswith(".md"):
                found.append(str(rules_dir / fname))
                if len(found) >= _MAX_MD_FILES:
                    return found

    # Main walk — skips hidden dirs and known noise dirs
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = sorted([d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")])

        for fname in sorted(filenames):
            if fname.lower().endswith(".md"):
                full_path = os.path.join(dirpath, fname)
                if full_path in found:
                    continue
                found.append(full_path)
                if len(found) >= _MAX_MD_FILES:
                    return found

    return found


# ─── Document parsing ────────────────────────────────────────────────────


class _Section:
    """A headed section from a markdown document."""

    __slots__ = ("heading", "heading_level", "body", "source_file", "rel_path", "line_no")

    def __init__(
        self,
        heading: str,
        heading_level: int,
        body: str,
        source_file: str,
        rel_path: str,
        line_no: int,
    ):
        self.heading = heading
        self.heading_level = heading_level
        self.body = body
        self.source_file = source_file
        self.rel_path = rel_path
        self.line_no = line_no


def _has_frontmatter_opt_out(md_path: str) -> bool:
    """Return True when markdown frontmatter declares `theory_scope: false`."""
    try:
        with open(md_path, errors="replace") as f:
            head_lines: list[str] = []
            for _ in range(10):
                line = f.readline()
                if line == "":
                    break
                head_lines.append(line.rstrip("\n"))
    except OSError:
        return False

    if not head_lines:
        return False
    if head_lines[0].lstrip("\ufeff").strip() != "---":
        return False

    fm_end = None
    for i, line in enumerate(head_lines[1:], 1):
        if line.strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return False

    frontmatter = "\n".join(head_lines[1:fm_end])
    return bool(
        re.search(
            r"^\s*theory_scope\s*:\s*false\s*(?:#.*)?$",
            frontmatter,
            re.IGNORECASE | re.MULTILINE,
        )
    )


def _parse_document(md_path: str, project_root: str) -> list[_Section]:
    """Parse a markdown file into headed sections."""
    if _has_frontmatter_opt_out(md_path):
        return []

    try:
        text = Path(md_path).read_text(errors="replace")
    except OSError:
        return []

    rel_path = os.path.relpath(md_path, project_root)
    lines = text.splitlines()
    sections: list[_Section] = []
    current_heading = os.path.basename(md_path).replace(".md", "")
    current_level = 0
    current_body_lines: list[str] = []
    current_line_no = 1

    for i, line in enumerate(lines, 1):
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            # Flush previous section
            if current_body_lines:
                body = "\n".join(current_body_lines).strip()
                if body:
                    sections.append(
                        _Section(
                            heading=current_heading,
                            heading_level=current_level,
                            body=body,
                            source_file=md_path,
                            rel_path=rel_path,
                            line_no=current_line_no,
                        )
                    )
            current_heading = heading_match.group(2).strip()
            current_level = len(heading_match.group(1))
            current_body_lines = []
            current_line_no = i
        else:
            current_body_lines.append(line)

    # Flush last section
    if current_body_lines:
        body = "\n".join(current_body_lines).strip()
        if body:
            sections.append(
                _Section(
                    heading=current_heading,
                    heading_level=current_level,
                    body=body,
                    source_file=md_path,
                    rel_path=rel_path,
                    line_no=current_line_no,
                )
            )

    return sections


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


def _classify_section(section: _Section) -> list[str]:
    """Determine which theory facets a section belongs to."""
    facets: list[str] = []
    heading_lower = section.heading.lower()

    # Check heading signals
    for facet, patterns in _THEORY_HEADING_SIGNALS.items():
        for pattern in patterns:
            if re.search(pattern, heading_lower):
                facets.append(facet)
                break

    # Check paragraph-level signals in body
    for facet, compiled_patterns in _THEORY_PARAGRAPH_SIGNALS.items():
        if facet in facets:
            continue  # Already classified by heading
        for pat in compiled_patterns:
            if pat.search(section.body):
                facets.append(facet)
                break

    # Check contrastive markers for alignment
    if "alignment" not in facets:
        for marker in _CONTRASTIVE_MARKERS:
            if marker.search(section.body):
                facets.append("alignment")
                break

    return facets


def _extract_claims(section: _Section, facet: str) -> list[str]:
    """Extract the substantive claims from a section for a given facet.

    A "claim" is a sentence or short passage that expresses a theoretical
    position, design rationale, heuristic, or alignment criterion.
    We extract these by looking for assertion patterns in the text.
    """
    claims: list[str] = []
    # Strip code blocks to avoid extracting code as theory
    text = re.sub(r"```[\s\S]*?```", "", section.body)
    # Strip inline code
    text = re.sub(r"`[^`]+`", "CODE", text)
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
            # Skip sentences that are mostly code references
            if cleaned.count("CODE") > 2:
                continue
            # Skip sentences that START with CODE (usually just referencing docs)
            if cleaned.startswith("CODE") or cleaned.startswith("See CODE"):
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


def _score_claim(sentence: str, facet: str) -> int:
    """Score how likely a sentence is to be a meaningful theory claim.

    Returns 0 for non-claims, 1+ for likely claims. Higher = stronger signal.
    """
    score = 0
    s = sentence

    # Universal theory claim signals
    if re.search(r"\b(?:because|since|therefore|thus|hence|so that)\b", s, re.I):
        score += 1  # Causal reasoning
    if re.search(r"\b(?:rather than|instead of|not by|unlike)\b", s, re.I):
        score += 1  # Contrastive reasoning
    if re.search(r"\b(?:key|core|fundamental|central|critical|essential)\b", s, re.I):
        score += 1  # Importance markers
    if re.search(r"\b(?:emerges?|enables?|ensures?|provides?|demonstrates?)\b", s, re.I):
        score += 1  # Mechanistic language

    # Facet-specific scoring
    if facet == "core_theory":
        if re.search(r"\b(?:is defined as|we define|the definition)\b", s, re.I):
            score += 2
        if re.search(r"\b(?:theory|principle|axiom|invariant|postulate)\b", s, re.I):
            score += 1
        if re.search(r"\b(?:we hypothesize|hypothesis|conjecture|research question)\b", s, re.I):
            score += 2
        if re.search(r"this (?:work|project|research) (?:address|test|investigat|explor)", s, re.I):
            score += 1
    elif facet == "problem_solving":
        if re.search(
            r"\b(?:easier|better|tractable|efficient|guided)\b.*\b(?:than|over|compared)\b", s, re.I
        ):
            score += 2
        if re.search(r"\btransform.*(?:into|to)\b", s, re.I):
            score += 1
        if re.search(
            r"\b(?:scan|parse|split|classif|extract|deduplicat|score|report)\w*\b", s, re.I
        ):
            score += 1
        if re.search(r"\b(?:step|phase|pipeline|workflow|process)\b", s, re.I):
            score += 1
        if re.search(r"\b(?:first|then|next|finally)\b", s, re.I):
            score += 1
        if re.search(r"\*\*Lesson", s):
            score += 2  # "**Lesson:**" pattern from journals
        if re.search(r"the (?:fix|solution|workaround) was", s, re.I):
            score += 1
    elif facet == "alignment":
        if re.search(r"\b(?:ruin|break|destroy|undermine|bypass)\b", s, re.I):
            score += 2
        if re.search(r"\b(?:proper|correct|aligned|right way)\b", s, re.I):
            score += 1
        if re.search(r"\b(?:goal|purpose|point) is\b", s, re.I):
            score += 1
        if re.search(r"\b(?:non[- ]?goal|not a goal|out of scope)\b", s, re.I):
            score += 2
        if re.search(r"\b(?:primary|secondary) objective\b", s, re.I):
            score += 1
    elif facet == "architecture":
        if re.search(r"\bwhy\b.*\b(?:not|over|instead|rather)\b", s, re.I):
            score += 2
        if re.search(r"\b(?:design|chose|decided|tradeoff|trade-off)\b", s, re.I):
            score += 1
        if re.search(r"\*\*Rationale\*\*", s):
            score += 2  # "**Rationale:**" pattern from research docs
        if re.search(r"\b(?:decompos|modular|separation of concerns|drop[- ]?in)\b", s, re.I):
            score += 1
        if re.search(r"\b(?:O\(n|quadratic|exponential|vectori[sz]|batch|performance|latency|throughput)\b", s, re.I):
            score += 1  # Performance-related architectural claim
    elif facet == "anti_patterns":
        if re.search(r"\b(?:will|would|can|could)\s+(?:ruin|break|destroy|fail)\b", s, re.I):
            score += 2
        if re.search(r"\b(?:black.?box|monolith|hard.?cod|ad.?hoc|hack|workaround)\b", s, re.I):
            score += 1
        if re.search(r"\b(?:trying harder|premature|overfitting|scope creep)\b", s, re.I):
            score += 1
    elif facet == "abstractions":
        if re.search(r"\b(?:we (?:call|define|term)|is called|known as|refers to)\b", s, re.I):
            score += 2
        if re.search(r"\*\*\w+(?:\s+\w+){0,3}\*\*", s):
            score += 1  # Bold-defined terms

    return score


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, handling common markdown artifacts."""
    # Replace newlines that aren't paragraph breaks with spaces
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # Split on paragraph breaks
    paragraphs = re.split(r"\n\s*\n", text)

    sentences: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("|") or para.startswith("- ["):
            continue
        # Split paragraph into sentences
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", para)
        sentences.extend(parts)

    return sentences


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


def _pick_best_summary_claim(
    claims: list[str],
    exclude: set[str] | None = None,
) -> str:
    """Pick the most informative claim to use as a facet summary.

    Prefers claims that:
    - Have causal/contrastive language (because, rather than, enables)
    - Are a reasonable length (40-200 chars) — not too terse, not too verbose
    - Don't contain CODE placeholders or path fragments
    - Haven't already been used as a summary for another facet
    """
    exclude = exclude or set()

    def quality_score(claim: str) -> float:
        s = 0.0
        # Penalize very short or very long claims
        length = len(claim)
        if length < 40:
            s -= 2.0  # Too terse to be informative
        elif length < 80:
            s += 0.5
        elif length <= 200:
            s += 1.0  # Sweet spot
        else:
            s -= 0.5  # Getting verbose

        # Penalize noise markers
        if "CODE" in claim:
            s -= 3.0
        if claim.count("/") > 2:
            s -= 2.0
        # Penalize purely descriptive content (no theory markers)
        if not re.search(
            r"\b(?:because|since|therefore|rather|instead|enables?|designed|approach|key|core|fundamental|must|should|critical|hypothesis)\b",
            claim,
            re.I,
        ):
            s -= 1.0

        # Reward theory-quality language
        if re.search(r"\b(?:because|since|therefore|thus)\b", claim, re.I):
            s += 2.0
        if re.search(r"\b(?:rather than|instead of|not by|unlike)\b", claim, re.I):
            s += 2.0
        if re.search(
            r"\b(?:enables?|ensures?|provides?|designed|architecture|approach)\b", claim, re.I
        ):
            s += 1.0
        if re.search(r"\b(?:key|core|fundamental|central|critical)\b", claim, re.I):
            s += 1.0
        if re.search(r"\b(?:hypothesis|hypothesize|conjecture|propose|we argue)\b", claim, re.I):
            s += 1.5

        return s

    # Filter out already-used summaries
    available = [c for c in claims if c not in exclude]
    if not available:
        available = claims  # Fall back to all if all were excluded

    scored = [(quality_score(c), c) for c in available]
    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored else "(no theory content found)"


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
    if not parts:
        return re.escape(words)
    return r"[_\s-]*".join(re.escape(p) for p in parts)
