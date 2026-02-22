"""Context auditor check implementations and helpers.

Individual audit checks for CLAUDE.md/AGENTS.md quality, path validation,
directive enforceability classification, and helper utilities.

Extracted from context_auditor.py for module size compliance.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")


# ── Check Implementations ────────────────────────────────────────────


def check_length(
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


def check_structure(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    text: str,
    lines: list[str],
) -> None:
    headers = _HEADER_RE.findall(text)
    has_sections = len(headers) >= 2
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
        checks.append({"check": "structure", "status": "pass", "detail": detail})


def check_staleness(
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


def check_contradictions(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    guidance: dict[str, Any],
) -> None:
    directives = guidance.get("directives", {})
    do_directives = {d.lower() for d in directives.get("do", [])}
    do_not_directives = {d.lower() for d in directives.get("do_not", [])}

    do_keywords = _extract_keywords(do_directives)
    do_not_keywords = _extract_keywords(do_not_directives)

    overlap = do_keywords & do_not_keywords
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


def check_rule_coverage(
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

    enforceable: list[str] = []
    architectural: list[str] = []
    for directive in do_not_directives:
        if _is_regex_enforceable(directive):
            enforceable.append(directive)
        else:
            architectural.append(directive)

    forbid_rules = [r for r in existing_rules if r.get("kind") == "forbid_regex"]
    require_rules = [r for r in existing_rules if r.get("kind") == "require_regex"]
    all_matching_rules = forbid_rules + require_rules

    covered = 0
    uncovered_directives: list[str] = []

    for directive in enforceable:
        directive_lower = directive.lower()
        directive_words = _coverage_tokens(directive_lower) - _COVERAGE_STOPWORDS

        has_rule = _directive_has_matching_rule(
            directive_lower,
            directive_words,
            all_matching_rules,
        )
        if has_rule:
            covered += 1
        else:
            uncovered_directives.append(directive)

    total_enforceable = len(enforceable)

    if total_enforceable == 0:
        arch_note = (
            f" ({len(architectural)} architectural directive(s) noted)" if architectural else ""
        )
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


def _directive_has_matching_rule(
    directive_lower: str,
    directive_words: set[str],
    all_matching_rules: list[dict[str, Any]],
) -> bool:
    """Check if a directive has a matching rule via text or keyword overlap."""
    for r in all_matching_rules:
        rule_text = " ".join(
            [
                str(r.get("message", "")),
                str(r.get("source", "")),
                str(r.get("pattern", "")),
            ]
        ).lower()

        if directive_lower in rule_text:
            return True

        if directive_words:
            rule_words = _coverage_tokens(rule_text)
            overlap = directive_words & rule_words
            min_overlap = 1 if len(directive_words) <= 3 else 2
            if len(overlap) >= min_overlap:
                return True
    return False


# ── Path Reference Checking ──────────────────────────────────────────

_PATH_EXTENSIONS = (".py", ".md", ".yaml", ".yml", ".toml", ".json")
_URL_PREFIXES = ("http://", "https://", "ftp://")

_SHELL_CMD_PREFIXES = (
    "uv ",
    "uv run ",
    "pip ",
    "python ",
    "python3 ",
    "npm ",
    "npx ",
    "cargo ",
    "go ",
    "git ",
    "docker ",
    "make ",
    "brew ",
    "curl ",
    "wget ",
    "grep ",
    "rg ",
    "find ",
    "cat ",
    "ls ",
    "cd ",
    "mkdir ",
    "rm ",
    "cp ",
    "mv ",
    "chmod ",
    "chown ",
    "sudo ",
    "apt ",
    "yum ",
)

_HF_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9._-]+$")


def check_path_references(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    text: str,
    project_root: str,
    thresholds: dict[str, Any],
) -> None:
    path_refs = extract_path_refs(text)
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

    if len(path_refs) > max_refs:
        checks.append(
            {
                "check": "path_reference_volume",
                "status": "warn",
                "detail": f"{len(path_refs)} path references found (threshold: {max_refs}).",
            }
        )
        suggestions.append(
            "Reduce path-reference density by moving exhaustive file lists to "
            "separate docs and keeping this context file concise."
        )

    dead_paths = find_dead_paths(path_refs, project_root)

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


def extract_path_refs(text: str) -> list[str]:
    """Extract likely file-path references from backtick-quoted text."""
    stripped = re.sub(r"```[\s\S]*?```", "", text)
    refs: list[str] = []
    for candidate in _BACKTICK_PATH_RE.findall(stripped):
        candidate = candidate.strip()
        is_path_like = "/" in candidate or candidate.endswith(_PATH_EXTENSIONS)
        if not is_path_like:
            continue
        if candidate.startswith(_URL_PREFIXES):
            continue
        if "\n" in candidate or "├" in candidate or "└" in candidate or "│" in candidate:
            continue
        if " " in candidate and os.sep not in candidate:
            continue
        candidate_lower = candidate.lower()
        if any(candidate_lower.startswith(prefix) for prefix in _SHELL_CMD_PREFIXES):
            continue
        if (
            "/" in candidate
            and not any(candidate.endswith(ext) for ext in _PATH_EXTENSIONS)
            and candidate.count("/") == 1
            and _HF_MODEL_ID_RE.match(candidate)
        ):
            continue
        refs.append(candidate)
    return refs


def find_dead_paths(path_refs: list[str], project_root: str) -> list[str]:
    """Check which path references don't exist on disk."""
    dead: list[str] = []
    for ref in path_refs:
        if "*" in ref or "?" in ref:
            continue
        if ref.startswith("~/") or ref.startswith("~\\"):
            expanded = os.path.expanduser(ref)
            if not os.path.exists(expanded):
                dead.append(ref)
            continue
        ref_clean = ref.removeprefix("./")
        full_path = os.path.join(project_root, ref_clean)
        if os.path.exists(full_path):
            continue
        if (
            "/" not in ref_clean
            and "\\" not in ref_clean
            and _find_bare_name_in_project(ref_clean, project_root)
        ):
            continue
        dead.append(ref)
    return dead


def _find_bare_name_in_project(name: str, project_root: str) -> bool:
    """Check if a bare filename exists anywhere under common source dirs."""
    search_roots = [project_root]
    for subdir in ("src", "lib", "app", "pkg"):
        candidate = os.path.join(project_root, subdir)
        if os.path.isdir(candidate):
            search_roots.append(candidate)

    for root in search_roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            depth = dirpath[len(root) :].count(os.sep)
            if depth > 3:
                continue
            if name in filenames:
                return True
    return False


# ── Keyword / Token Helpers ──────────────────────────────────────────


def _extract_keywords(directives: set[str]) -> set[str]:
    """Extract significant keywords from directive text for overlap detection."""
    keywords: set[str] = set()
    for d in directives:
        words = re.findall(r"\b[a-z]{4,}\b", d)
        keywords.update(words)
    return keywords


def _coverage_tokens(text: str) -> set[str]:
    """Tokenize text for fuzzy directive-to-rule coverage matching."""
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9_]{3,}", text.lower()):
        for part in re.split(r"[_\W]+", token):
            part = part.strip()
            if len(part) < 3:
                continue
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


# ── Directive Enforceability Classification ──────────────────────────

_SYNTACTIC_IDENTIFIER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\w+(?:\.\w+)+"),
    re.compile(r"`[^`]+`"),
    re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b"),
    re.compile(r"\b\w+_\w+\(\)"),
    re.compile(r"\b\w+_\w+_\w+\b"),
]

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
    matches: set[str] = set()
    for pat in _SYNTACTIC_IDENTIFIER_PATTERNS:
        for m in pat.findall(text):
            matches.add(m)
    return len(matches)


def _has_syntactic_id(text: str) -> bool:
    return any(pat.search(text) for pat in _SYNTACTIC_IDENTIFIER_PATTERNS)


@dataclass
class DirectiveClassification:
    """3-way classification of a DO NOT directive's enforceability."""

    classification: str  # "enforceable" | "architectural" | "uncertain"
    confidence: float = 1.0
    reason: str = ""


def classify_directive_enforceability(directive: str) -> DirectiveClassification:
    """3-way classifier: enforceable / architectural / uncertain."""
    text = directive.strip()
    has_syntactic = _has_syntactic_id(text)
    has_architectural = bool(_ARCHITECTURAL_CUE_RE.search(text))

    if has_syntactic and not has_architectural:
        return DirectiveClassification(
            classification="enforceable",
            confidence=0.95,
            reason="Contains syntactic identifier with no architectural cues.",
        )

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
        return DirectiveClassification(
            classification="uncertain",
            confidence=0.4,
            reason=f"Equal syntactic ({syn_count}) and architectural ({arch_count}) signals.",
        )

    if has_architectural and not has_syntactic:
        return DirectiveClassification(
            classification="architectural",
            confidence=0.9,
            reason="Architectural/process cues with no syntactic identifiers.",
        )

    return DirectiveClassification(
        classification="uncertain",
        confidence=0.3,
        reason="No syntactic or architectural signals detected.",
    )


def _is_regex_enforceable(directive: str) -> bool:
    """Classify whether a DO NOT directive can be enforced via regex."""
    return classify_directive_enforceability(directive).classification == "enforceable"
