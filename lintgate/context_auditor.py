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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import load_config
from .context_guidance import (
    build_context_guidance,
    discover_context_files,
)

# ─── Default thresholds (overridable via lintgate.yaml) ──────────────────

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "max_lines_warn": 300,
    "max_lines_error": 500,
    "staleness_days": 30,
    "min_rule_coverage_pct": 50,
    "max_path_references": 50,
}

_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")


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

        # ── Check 1: Length ──────────────────────────────────────────
        _check_length(checks, suggestions, line_count, effective)

        # ── Check 2: Structure ───────────────────────────────────────
        _check_structure(checks, suggestions, text, lines)

        # ── Check 3: Staleness ───────────────────────────────────────
        _check_staleness(checks, suggestions, file_path, effective)

        # ── Check 4: Contradiction detection ─────────────────────────
        _check_contradictions(checks, suggestions, guidance)

        # ── Check 5: Machine-rule coverage ───────────────────────────
        _check_rule_coverage(checks, suggestions, guidance, existing_rules, effective)

        # ── Check 6: Path reference validation ───────────────────────
        _check_path_references(checks, suggestions, text, project_root, effective)

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


# ─── Individual check implementations ───────────────────────────────────


def _check_length(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    line_count: int,
    thresholds: dict[str, Any],
) -> None:
    warn = thresholds["max_lines_warn"]
    error = thresholds["max_lines_error"]

    if line_count > error:
        checks.append(
            {
                "check": "length",
                "status": "error",
                "detail": f"{line_count} lines (max recommended: {warn}, hard limit: {error})",
            }
        )
        suggestions.append(
            f"Context file is {line_count} lines — well beyond the {warn}-line recommendation. "
            f"Move detailed docs to .claude/rules/*.md with paths frontmatter, "
            f"keep the main file as a concise index."
        )
    elif line_count > warn:
        checks.append(
            {
                "check": "length",
                "status": "warn",
                "detail": f"{line_count} lines (recommend <{warn})",
            }
        )
        suggestions.append(
            f"Consider splitting into .claude/rules/ files — "
            f"current length ({line_count}) exceeds the {warn}-line guideline."
        )
    else:
        checks.append(
            {
                "check": "length",
                "status": "pass",
                "detail": f"{line_count} lines (within {warn}-line guideline)",
            }
        )


def _check_structure(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    text: str,
    lines: list[str],
) -> None:
    headers = _HEADER_RE.findall(text)
    has_sections = len(headers) >= 2

    # Check for progressive disclosure patterns
    has_code_blocks = "```" in text
    has_tables = "|" in text and "---" in text

    if not has_sections:
        checks.append(
            {
                "check": "structure",
                "status": "warn",
                "detail": f"Only {len(headers)} section header(s) found — file lacks structure",
            }
        )
        suggestions.append(
            "Add markdown headers (## Section) to organize content. "
            "LLM agents navigate context files by scanning headers first."
        )
    else:
        detail = f"{len(headers)} sections"
        if has_code_blocks:
            detail += ", has code examples"
        if has_tables:
            detail += ", has tables"
        checks.append(
            {
                "check": "structure",
                "status": "pass",
                "detail": detail,
            }
        )


def _check_staleness(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    file_path: str,
    thresholds: dict[str, Any],
) -> None:
    staleness_days = thresholds["staleness_days"]

    try:
        mtime = Path(file_path).stat().st_mtime
        age_days = (time.time() - mtime) / 86400
    except OSError:
        checks.append(
            {
                "check": "staleness",
                "status": "warn",
                "detail": "Could not determine file modification time",
            }
        )
        return

    if age_days > staleness_days:
        checks.append(
            {
                "check": "staleness",
                "status": "warn",
                "detail": f"Last modified {int(age_days)} days ago (threshold: {staleness_days} days)",
            }
        )
        suggestions.append(
            f"Context file hasn't been updated in {int(age_days)} days. "
            f"Review and update to ensure it reflects current project state."
        )
    else:
        checks.append(
            {
                "check": "staleness",
                "status": "pass",
                "detail": f"Last modified {int(age_days)} days ago",
            }
        )


def _check_contradictions(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    guidance: dict[str, Any],
) -> None:
    directives = guidance.get("directives", {})
    do_directives = {d.lower() for d in directives.get("do", [])}
    do_not_directives = {d.lower() for d in directives.get("do_not", [])}

    # Extract key nouns/verbs from each directive for overlap detection
    do_keywords = _extract_keywords(do_directives)
    do_not_keywords = _extract_keywords(do_not_directives)

    overlap = do_keywords & do_not_keywords
    # Filter out very common words that are likely false positives
    noise_words = {
        "use",
        "create",
        "make",
        "add",
        "set",
        "get",
        "run",
        "check",
        "test",
        "write",
        "read",
        "file",
        "code",
        "the",
        "and",
        "for",
    }
    meaningful_overlap = overlap - noise_words

    if meaningful_overlap:
        overlap_str = ", ".join(sorted(meaningful_overlap)[:5])
        checks.append(
            {
                "check": "contradictions",
                "status": "warn",
                "detail": f"DO and DO NOT directives reference overlapping concepts: {overlap_str}",
            }
        )
        suggestions.append(
            f"Review potentially contradictory directives involving: {overlap_str}. "
            f"Ensure DO and DO NOT sections don't give conflicting guidance."
        )
    else:
        checks.append(
            {
                "check": "contradictions",
                "status": "pass",
                "detail": "No obvious contradictions detected between DO and DO NOT directives",
            }
        )


def _check_rule_coverage(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    guidance: dict[str, Any],
    existing_rules: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> None:
    do_not_directives = guidance.get("directives", {}).get("do_not", [])
    total_do_not = len(do_not_directives)
    min_coverage_pct = thresholds["min_rule_coverage_pct"]

    if total_do_not == 0:
        checks.append(
            {
                "check": "machine_rules",
                "status": "pass",
                "detail": "No DO NOT directives found (nothing to enforce)",
            }
        )
        return

    # Categorize directives: only regex-enforceable ones count toward
    # the coverage percentage.  Architectural/process directives are
    # reported separately — they can't be meaningfully enforced via regex.
    enforceable: list[str] = []
    architectural: list[str] = []
    for directive in do_not_directives:
        if _is_regex_enforceable(directive):
            enforceable.append(directive)
        else:
            architectural.append(directive)

    # Count how many enforceable DO NOT directives have corresponding rules.
    # Match by: (a) directive text appears in rule message/source, OR
    # (b) key nouns from the directive appear in the rule pattern, OR
    # (c) the rule's message references the directive.
    forbid_rules = [r for r in existing_rules if r.get("kind") == "forbid_regex"]
    require_rules = [r for r in existing_rules if r.get("kind") == "require_regex"]
    all_matching_rules = forbid_rules + require_rules

    covered = 0
    uncovered_directives: list[str] = []

    for directive in enforceable:
        directive_lower = directive.lower()
        # Extract significant words from the directive for fuzzy matching
        directive_words = _coverage_tokens(directive_lower)
        directive_words -= _COVERAGE_STOPWORDS

        has_rule = False
        for r in all_matching_rules:
            rule_text = " ".join(
                [
                    str(r.get("message", "")),
                    str(r.get("source", "")),
                    str(r.get("pattern", "")),
                ]
            ).lower()

            # Direct text match
            if directive_lower in rule_text:
                has_rule = True
                break

            # Key-word overlap: 2+ significant directive words must appear
            # in the rule's text.  For very short directives (≤3 tokens
            # after stopword removal), 1 overlapping token suffices — these
            # are typically single-term bans like "DO NOT use checkra1n".
            if directive_words:
                rule_words = _coverage_tokens(rule_text)
                overlap = directive_words & rule_words
                min_overlap = 1 if len(directive_words) <= 3 else 2
                if len(overlap) >= min_overlap:
                    has_rule = True
                    break

        if has_rule:
            covered += 1
        else:
            uncovered_directives.append(directive)

    total_enforceable = len(enforceable)

    # When all directives are architectural, report pass — there's nothing
    # regex can enforce, so 0/0 is not a failure.
    if total_enforceable == 0:
        arch_note = f" ({len(architectural)} architectural directive(s) noted)" if architectural else ""
        checks.append(
            {
                "check": "machine_rules",
                "status": "pass",
                "detail": f"No regex-enforceable DO NOT directives found{arch_note}",
            }
        )
        return

    coverage_pct = (covered / total_enforceable) * 100
    arch_note = f" ({len(architectural)} architectural)" if architectural else ""

    if coverage_pct < min_coverage_pct:
        checks.append(
            {
                "check": "machine_rules",
                "status": "warn",
                "detail": (
                    f"{covered}/{total_enforceable} enforceable DO NOT directives have rules "
                    f"({coverage_pct:.0f}%, threshold: {min_coverage_pct}%){arch_note}"
                ),
            }
        )
        for directive in uncovered_directives[:3]:
            truncated = directive[:100] + "..." if len(directive) > 100 else directive
            suggestions.append(f"Add LINTGATE_FORBID_REGEX for: '{truncated}'")
    else:
        checks.append(
            {
                "check": "machine_rules",
                "status": "pass",
                "detail": (
                    f"{covered}/{total_enforceable} enforceable DO NOT directives have rules "
                    f"({coverage_pct:.0f}%){arch_note}"
                ),
            }
        )


def _check_path_references(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    text: str,
    project_root: str,
    thresholds: dict[str, Any],
) -> None:
    path_refs = _extract_path_refs(text)
    max_refs = int(thresholds.get("max_path_references", 50))

    if not path_refs:
        checks.append(
            {
                "check": "path_references",
                "status": "pass",
                "detail": "No path references found in backticks",
            }
        )
        return

    # Very large numbers of path references usually indicate the file is
    # carrying too much implementation detail for an agent context doc.
    if len(path_refs) > max_refs:
        checks.append(
            {
                "check": "path_reference_volume",
                "status": "warn",
                "detail": (f"{len(path_refs)} path references found (threshold: {max_refs})."),
            }
        )
        suggestions.append(
            "Reduce path-reference density by moving exhaustive file lists to "
            "separate docs and keeping this context file concise."
        )

    dead_paths = _find_dead_paths(path_refs, project_root)

    if dead_paths:
        dead_str = ", ".join(dead_paths[:5])
        more = f" (+{len(dead_paths) - 5} more)" if len(dead_paths) > 5 else ""
        checks.append(
            {
                "check": "path_references",
                "status": "warn",
                "detail": f"{len(dead_paths)} referenced path(s) don't exist: {dead_str}{more}",
            }
        )
        for p in dead_paths[:3]:
            suggestions.append(f"Remove or update dead path reference: `{p}`")
    else:
        checks.append(
            {
                "check": "path_references",
                "status": "pass",
                "detail": f"All {len(path_refs)} path references verified",
            }
        )


_PATH_EXTENSIONS = (".py", ".md", ".yaml", ".yml", ".toml", ".json")
_URL_PREFIXES = ("http://", "https://", "ftp://")

# Shell commands often appear in backticks; skip candidates that start with
# common CLI tools/runners to avoid treating them as file paths.
_SHELL_CMD_PREFIXES = (
    "uv ", "uv run ", "pip ", "python ", "python3 ", "npm ", "npx ",
    "cargo ", "go ", "git ", "docker ", "make ", "brew ", "curl ", "wget ",
    "grep ", "rg ", "find ", "cat ", "ls ", "cd ", "mkdir ", "rm ",
    "cp ", "mv ", "chmod ", "chown ", "sudo ", "apt ", "yum ",
)

# HuggingFace-style model IDs: vendor/model-variant (no file extension,
# no nested paths).  E.g. "meta-llama/Llama-3.1-8B".
_HF_MODEL_ID_RE = re.compile(
    r"^[A-Za-z0-9_-]+/[A-Za-z0-9._-]+$"
)


def _extract_path_refs(text: str) -> list[str]:
    """Extract likely file-path references from backtick-quoted text.

    Only considers inline backtick references (not inside code blocks).
    Applies heuristic filters to reduce false positives from shell commands,
    HuggingFace model IDs, and other non-path backtick content.
    """

    # Strip fenced code blocks before extracting backtick paths
    # This prevents tree-view diagrams and command examples from being parsed
    stripped = re.sub(r"```[\s\S]*?```", "", text)

    refs: list[str] = []
    for candidate in _BACKTICK_PATH_RE.findall(stripped):
        candidate = candidate.strip()
        is_path_like = "/" in candidate or candidate.endswith(_PATH_EXTENSIONS)
        if not is_path_like:
            continue
        if candidate.startswith(_URL_PREFIXES):
            continue
        # Skip multi-line content or things with tree-drawing characters
        if "\n" in candidate or "├" in candidate or "└" in candidate or "│" in candidate:
            continue
        if " " in candidate and os.sep not in candidate:
            continue

        # Skip shell commands (e.g., `uv run ruff check .`)
        candidate_lower = candidate.lower()
        if any(candidate_lower.startswith(prefix) for prefix in _SHELL_CMD_PREFIXES):
            continue

        # Skip HuggingFace-style model IDs (vendor/model-name, no extension,
        # no nested slashes).  E.g. `meta-llama/Llama-3.1-8B`.
        if (
            "/" in candidate
            and not any(candidate.endswith(ext) for ext in _PATH_EXTENSIONS)
            and candidate.count("/") == 1
            and _HF_MODEL_ID_RE.match(candidate)
        ):
            continue

        refs.append(candidate)
    return refs


def _find_dead_paths(path_refs: list[str], project_root: str) -> list[str]:
    """Check which path references don't exist on disk.

    Handles home-directory paths (``~/...``) by expanding ``~`` before
    checking, and searches common subdirectories (``src/``) for bare
    module names like ``cpid.py`` that aren't at the project root.
    """
    dead: list[str] = []
    for ref in path_refs:
        # Skip glob patterns
        if "*" in ref or "?" in ref:
            continue

        # Expand home directory paths (~/...) — these are absolute, not
        # relative to project_root.
        if ref.startswith("~/") or ref.startswith("~\\"):
            expanded = os.path.expanduser(ref)
            if not os.path.exists(expanded):
                dead.append(ref)
            continue

        ref_clean = ref.removeprefix("./")
        full_path = os.path.join(project_root, ref_clean)
        if os.path.exists(full_path):
            continue

        # For bare filenames (no directory component), search common
        # subdirectories before declaring dead.  Handles CLAUDE.md
        # architecture diagrams that reference `cpid.py` when the actual
        # file lives at `src/package/cpid.py`.
        if "/" not in ref_clean and "\\" not in ref_clean and _find_bare_name_in_project(ref_clean, project_root):
            continue

        dead.append(ref)
    return dead


def _find_bare_name_in_project(name: str, project_root: str) -> bool:
    """Check if a bare filename exists anywhere under common source dirs.

    Limits search depth to 3 levels to keep this fast.
    """
    search_roots = [project_root]
    for subdir in ("src", "lib", "app", "pkg"):
        candidate = os.path.join(project_root, subdir)
        if os.path.isdir(candidate):
            search_roots.append(candidate)

    for root in search_roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            # Limit depth: only search 3 levels deep from each search root
            depth = dirpath[len(root) :].count(os.sep)
            if depth > 3:
                continue
            if name in filenames:
                return True
    return False


# ─── Helpers ─────────────────────────────────────────────────────────────


def _extract_keywords(directives: set[str]) -> set[str]:
    """Extract significant keywords from directive text for overlap detection."""
    keywords: set[str] = set()
    for d in directives:
        # Split on whitespace and punctuation, keep words >3 chars
        words = re.findall(r"\b[a-z]{4,}\b", d)
        keywords.update(words)
    return keywords


def _coverage_tokens(text: str) -> set[str]:
    """Tokenize text for fuzzy directive↔rule coverage matching."""
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9_]{3,}", text.lower()):
        for part in re.split(r"[_\W]+", token):
            part = part.strip()
            if len(part) < 3:
                continue
            # Light singularization improves overlap matching for
            # "function" vs "functions" while keeping implementation simple.
            if part.endswith("s") and len(part) > 4:
                part = part[:-1]
            tokens.add(part)
    return tokens


_COVERAGE_STOPWORDS = {
    "that",
    "which",
    "with",
    "from",
    "this",
    "have",
    "does",
    "will",
    "should",
    "must",
    "into",
    "them",
    "than",
    "been",
    "each",
    "only",
    "also",
    "just",
}


# Concrete syntactic identifiers that a regex could plausibly match.
# These patterns detect specific named things that could appear in code.
_SYNTACTIC_IDENTIFIER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\w+(?:\.\w+)+"),  # dotted names: threading.Thread, os.path
    re.compile(r"`[^`]+`"),  # backtick-quoted identifiers
    re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b"),  # UPPER_CASE constants (4+ chars, skips NOT/AND)
    re.compile(r"\b\w+_\w+\(\)"),  # function call syntax: solve_task_abc()
    re.compile(r"\b\w+_\w+_\w+\b"),  # deep snake_case: solve_task_abc (2+ underscores)
]

# Architectural / process / behavioral cues — these describe decisions,
# not syntactic patterns.  Presence of these strongly indicates a directive
# that can't be regex-enforced.
_ARCHITECTURAL_CUE_RE = re.compile(
    r"\b(?:"
    r"approach|abstraction|bypass|coherence|constraint|discipline|"
    r"iterate|understand|verify|repeat|same\s+approach|one-off|"
    r"task-specific|ad[\s-]?hoc|shortcut|"
    r"instead\s+of|prefer\b.*\bover|"
    r"without\s+(?:understanding|checking|verifying|reading)|"
    r"hard\s+to|difficult|ensure|maintain|avoid\s+\w+ing"
    r")\b",
    re.IGNORECASE,
)


def _count_syntactic_ids(text: str) -> int:
    """Count distinct syntactic identifier matches in directive text."""
    matches: set[str] = set()
    for pat in _SYNTACTIC_IDENTIFIER_PATTERNS:
        for m in pat.findall(text):
            matches.add(m)
    return len(matches)


def _has_syntactic_id(text: str) -> bool:
    """Check if directive text contains any syntactic identifier."""
    return any(pat.search(text) for pat in _SYNTACTIC_IDENTIFIER_PATTERNS)


def _is_regex_enforceable(directive: str) -> bool:
    """Classify whether a DO NOT directive can be enforced via regex.

    Returns True for syntactic/API directives ("DO NOT use checkra1n"),
    False for architectural/process directives ("DO NOT bypass shared
    abstractions") that require human/LLM judgment.

    The heuristic: a directive is enforceable only if it references a
    concrete syntactic identifier (dotted name, backtick, UPPER_CASE,
    function call, deep snake_case) and does NOT have stronger
    architectural/process cues.
    """
    return classify_directive_enforceability(directive).classification == "enforceable"


@dataclass
class DirectiveClassification:
    """3-way classification of a DO NOT directive's enforceability.

    Attributes:
        classification: One of "enforceable", "architectural", or "uncertain".
        confidence: 0.0–1.0.  High for clear syntactic or clear architectural;
            low when signals conflict or are absent.
        reason: Short explanation of why the classifier chose this bucket.
    """

    classification: str  # "enforceable" | "architectural" | "uncertain"
    confidence: float = 1.0
    reason: str = ""


def classify_directive_enforceability(directive: str) -> DirectiveClassification:
    """3-way classifier: enforceable / architectural / uncertain.

    The ``uncertain`` bucket captures directives where the heuristic signals
    conflict (both syntactic and architectural cues with similar counts) or
    where no strong signal exists in either direction.  Bootstrap surfaces
    these to the calling agent via ``needs_review`` so ambiguity is resolved
    cheaply in-context rather than silently dropped.
    """
    text = directive.strip()

    has_syntactic = _has_syntactic_id(text)
    has_architectural = bool(_ARCHITECTURAL_CUE_RE.search(text))

    # Clear syntactic, no architectural → enforceable (high confidence)
    if has_syntactic and not has_architectural:
        return DirectiveClassification(
            classification="enforceable",
            confidence=0.95,
            reason="Contains syntactic identifier with no architectural cues.",
        )

    # Both present: count to decide
    if has_syntactic and has_architectural:
        syn_count = _count_syntactic_ids(text)
        arch_count = len(_ARCHITECTURAL_CUE_RE.findall(text))
        if syn_count > arch_count:
            return DirectiveClassification(
                classification="enforceable",
                confidence=0.7,
                reason=f"Syntactic signals ({syn_count}) outweigh architectural ({arch_count}).",
            )
        if arch_count > syn_count:
            return DirectiveClassification(
                classification="architectural",
                confidence=0.7,
                reason=f"Architectural cues ({arch_count}) outweigh syntactic ({syn_count}).",
            )
        # Equal counts → uncertain
        return DirectiveClassification(
            classification="uncertain",
            confidence=0.4,
            reason=f"Equal syntactic ({syn_count}) and architectural ({arch_count}) signals.",
        )

    # Architectural only → clear architectural
    if has_architectural and not has_syntactic:
        return DirectiveClassification(
            classification="architectural",
            confidence=0.9,
            reason="Architectural/process cues with no syntactic identifiers.",
        )

    # Neither signal → uncertain (bare English; could be a technology name
    # the heuristic doesn't recognise, or just a vague directive)
    return DirectiveClassification(
        classification="uncertain",
        confidence=0.3,
        reason="No syntactic or architectural signals detected.",
    )


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
) -> SessionReadiness:
    """Check if the session has sufficient theory context for deep supervision.

    Checks:
    - Theory profile has required facets (core_theory, problem_solving, alignment)
      with at least one claim each.
    - At least one enforceable rule exists.

    Args:
        project_root: Repository root.
        theory_profile: Pre-extracted theory profile (avoids re-extraction).

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

    if missing:
        parts = []
        if "no_theory_profile" in missing:
            parts.append("extract project theory")
        facet_missing = [m.split(":")[1] for m in missing if m.startswith("missing_facet:")]
        if facet_missing:
            parts.append(f"add claims for facets: {', '.join(facet_missing)}")
        if "no_enforceable_rules" in missing:
            parts.append("add enforceable rules to CLAUDE.md")
        recommendation = f"Run bootstrap_context_files to {'; '.join(parts)}."
    else:
        recommendation = ""

    return SessionReadiness(
        ready=not missing,
        missing=missing,
        recommendation=recommendation,
    )
