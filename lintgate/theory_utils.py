"""Utility functions and staleness checking for theory extraction."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_REQUIRED_THEORY_FACETS = (
    "core_theory",
    "problem_solving",
    "alignment",
)


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


def _filter_uncommitted_py_files(git_context: dict[str, Any]) -> list[str]:
    """Filter git context to uncommitted Python source files (excluding tests and __pycache__)."""
    modified = git_context.get("modified_files", [])
    untracked = git_context.get("untracked_files", [])
    return [
        f
        for f in modified + untracked
        if f.endswith(".py")
        and not f.startswith("tests/")
        and not f.startswith("test_")
        and "__pycache__" not in f
    ]


def _collect_covered_sources(theory_profile: dict[str, Any]) -> set[str]:
    """Extract the set of source file paths already covered by theory claims."""
    covered: set[str] = set()
    for facet_entries in theory_profile.values():
        if not isinstance(facet_entries, list):
            continue
        for entry in facet_entries:
            source = entry.get("source", "")
            if ":" in source:
                covered.add(source.split(":")[0])
            else:
                covered.add(source)
    return covered


def _has_substantial_docstring(abs_path: str) -> bool:
    """Check if a Python file has a module-level docstring of >=30 chars."""
    try:
        import ast

        source = Path(abs_path).read_text(errors="replace")
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
        return bool(docstring and len(docstring.strip()) >= 30)
    except (SyntaxError, OSError):
        return False


def _find_uncovered_files(
    py_files: list[str],
    theory_profile: dict[str, Any],
    project_root: str,
) -> list[str]:
    """Find uncommitted Python files with docstrings not covered by theory claims."""
    covered_sources = _collect_covered_sources(theory_profile)

    uncovered: list[str] = []
    for fpath in py_files:
        if fpath in covered_sources:
            continue
        abs_path = os.path.join(project_root, fpath)
        if not os.path.isfile(abs_path):
            continue
        if _has_substantial_docstring(abs_path):
            uncovered.append(fpath)
    return uncovered


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
    py_files = _filter_uncommitted_py_files(git_context)

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

    uncovered = _find_uncovered_files(py_files, theory_profile, project_root)

    if uncovered:
        result["stale"] = True
        result["uncovered_files"] = uncovered[:20]
        result["recommendation"] = (
            f"Theory profile doesn't cover {len(uncovered)} uncommitted file(s) "
            "with design docstrings. Run `build_theory_pack` to extract design intent "
            f"from working tree. Files: {', '.join(uncovered[:5])}"
        )

    return result
