"""Context auditor check implementations and helpers.

Individual audit checks for CLAUDE.md/AGENTS.md quality, path validation,
directive enforceability classification, and helper utilities.

Extracted from context_auditor.py for module size compliance.
Now delegates to focused sub-modules:
- context_auditor_rule_coverage: rule coverage & enforceability classification
- context_auditor_path_refs: path reference validation
- context_auditor_contradictions: contradiction detection
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .context_auditor_contradictions import (  # noqa: F401
    _detect_negation_pairs,
    _extract_keywords,
    check_contradictions,
)
from .context_auditor_path_refs import (  # noqa: F401
    _BACKTICK_PATH_RE,
    _HF_MODEL_ID_RE,
    _PATH_EXTENSIONS,
    _SHELL_CMD_PREFIXES,
    _URL_PREFIXES,
    _detect_generated_patterns,
    _find_bare_name_in_project,
    _matches_generated_pattern,
    check_path_references,
    extract_path_refs,
    find_dead_paths,
)

# ── Re-exports from focused modules ──────────────────────────────────
# All public and private names that were originally defined here are
# re-exported so that every existing import path continues to work.
from .context_auditor_rule_coverage import (  # noqa: F401
    _ARCHITECTURAL_CUE_RE,
    _COVERAGE_STOPWORDS,
    _SYNTACTIC_IDENTIFIER_PATTERNS,
    DirectiveClassification,
    _count_syntactic_ids,
    _coverage_tokens,
    _directive_has_matching_rule,
    _has_syntactic_id,
    _is_regex_enforceable,
    check_rule_coverage,
    classify_directive_enforceability,
)

_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


# ── Check Implementations (kept here — small, self-contained) ────────


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
