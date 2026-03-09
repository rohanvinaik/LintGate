"""Core logic for test effectiveness analysis and reporting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .manifest import build_test_effectiveness_manifest
from .test_analyzer import (
    _discover_source_files,
    _discover_test_files,
)
from .test_analyzer import (
    analyze_function_effectiveness as _analyze,
)
from .types import (
    SEMANTIC_STRENGTH_THRESHOLD,
    TEFF_SCHEMA_VERSION,
    AnalysisState,
    AssertionKind,
)

if TYPE_CHECKING:
    from .types import TestEffectivenessManifest


def build_manifest_for_project(
    project_root: str,
    effective_weights: dict[AssertionKind, float] | None = None,
) -> tuple[TestEffectivenessManifest | None, list[str], list[str], list[str]]:
    """Common setup: validate project, discover files, build manifest."""
    from lintgate.channels.structure_channel import _discover_python_files

    py_files = _discover_python_files(project_root)
    test_files = _discover_test_files(project_root)
    source_files = _discover_source_files(project_root)

    manifest = (
        build_test_effectiveness_manifest(
            project_root, source_files, test_files, effective_weights=effective_weights
        )
        if py_files and test_files
        else None
    )
    return manifest, py_files, test_files, source_files


def build_summary(manifest: TestEffectivenessManifest, project_root: str) -> dict[str, Any]:
    """Build a compact summary of the effectiveness manifest."""
    vulnerable = sorted(
        (
            (name, fe)
            for name, fe in manifest.functions.items()
            if fe.mutation_vulnerability > 0.5 and fe.test_count > 0
        ),
        key=lambda x: x[1].mutation_vulnerability,
        reverse=True,
    )

    top_vulnerable = []
    for name, fe in vulnerable[:10]:
        v_data = {
            "function": name,
            "vulnerability": round(fe.mutation_vulnerability, 3),
            "effectiveness": round(fe.effectiveness_score, 3),
            "semantic_ratio": round(fe.quality_profile.semantic_ratio, 3),
            "test_count": fe.test_count,
            "assertion_count": len(fe.assertions),
        }
        if fe.weakness_taxonomy:
            v_data["weakness"] = fe.weakness_taxonomy.value
        top_vulnerable.append(v_data)

    untested = sorted(
        (name for name, fe in manifest.functions.items() if fe.test_count == 0),
        key=lambda x: x,
    )

    # Aggregate taxonomy counts
    taxonomy_counts: dict[str, int] = {}
    for fe in manifest.functions.values():
        if fe.weakness_taxonomy:
            tag = fe.weakness_taxonomy.value
            taxonomy_counts[tag] = taxonomy_counts.get(tag, 0) + 1

    return {
        "project": project_root,
        "schema_version": TEFF_SCHEMA_VERSION,
        "summary": {
            "effectiveness_score": round(manifest.project_score, 3),
            "functions_analyzed": manifest.functions_analyzed,
            "mutation_vulnerable_count": manifest.mutation_vulnerable_count,
            "untested_count": len(untested),
            "weakness_taxonomy_counts": taxonomy_counts,
        },
        "top_vulnerable": top_vulnerable,
        "untested_functions": untested[:20],
        "diagnostics": manifest.diagnostics.to_dict(),
    }


def handle_no_mapped_functions(
    manifest: TestEffectivenessManifest, source_files: list[str], test_files: list[str]
) -> str:
    """Handle the case where no functions were mapped to tests."""
    diag = manifest.diagnostics
    state = AnalysisState.NO_MAPPED_FUNCTIONS.value

    if diag.attempted == 0:
        hint = "Check if source functions are public and tests follow naming conventions."
        state = AnalysisState.UNMAPPED_TESTS.value
    elif diag.dominant_drop_reason == "ambiguous":
        hint = f"Found {diag.dropped_ambiguous} ambiguous mapping candidates. Try using more specific imports or avoiding duplicate names across files."
    elif diag.dominant_drop_reason == "no_candidate":
        hint = f"Found {diag.dropped_no_candidate} test calls with no matching source candidates. Check your module configuration or project root."
        state = AnalysisState.NO_SOURCE_SYMBOLS.value
    elif diag.dominant_drop_reason == "shadowed":
        hint = f"Found {diag.dropped_shadowed} test names shadowed by local helpers. Avoid giving test helpers the same name as source functions."
    else:
        hint = "Check your project root and if source functions are actually public/imported correctly."

    result = {
        "note": "No mapped functions analyzed.",
        "details": f"Scanned {len(source_files)} source files and {len(test_files)} test files. (Mapped: {diag.mapped} / Attempted: {diag.attempted})",
        "hint": hint,
        "state": state,
        "diagnostics": diag.to_dict(),
        "schema_version": TEFF_SCHEMA_VERSION,
    }
    return json.dumps(result)


_ASSERTION_UPGRADE_MAP: dict[AssertionKind, tuple[str, str, str]] = {
    # kind -> (current_template, suggested_template, reason)
    AssertionKind.ISINSTANCE_CHECK: (
        "assert isinstance({expr}, ...)",
        "assert {expr} == expected_value",
        "isinstance (0.3) \u2192 equality (0.9): type check doesn't verify value",
    ),
    AssertionKind.IS_NOT_NONE: (
        "assert {expr} is not None",
        "assert {expr} == expected_value",
        "is_not_none (0.3) \u2192 equality (0.9): catches value-altering mutants",
    ),
    AssertionKind.IS_TRUE: (
        "assert {expr}",
        "assert {expr} == expected",
        "bare assert (0.2) \u2192 equality (0.9): catches -1\u2192+1 sentinel mutations",
    ),
}


def build_assertion_upgrades(
    manifest: TestEffectivenessManifest,
) -> list[dict[str, str]]:
    """Identify high-leverage assertion upgrade opportunities."""
    upgrades: list[dict[str, str]] = []
    seen_patterns: set[tuple[str, str]] = set()

    for _name, fe in manifest.functions.items():
        for a in fe.assertions:
            if a.confidence != "structural" or a.strength >= 0.5:
                continue
            pattern = (a.kind.value, a.target_expression)
            if pattern in seen_patterns:
                continue
            seen_patterns.add(pattern)

            templates = _ASSERTION_UPGRADE_MAP.get(a.kind)
            if templates:
                current_tpl, suggested_tpl, reason = templates
                upgrades.append({
                    "current": current_tpl.format(expr=a.target_expression),
                    "suggested": suggested_tpl.format(expr=a.target_expression),
                    "reason": reason,
                })

            if len(upgrades) >= 10:
                return upgrades
    return upgrades


def apply_filters(
    result: dict[str, Any], file_filter: str | None, function_filter: str | None
) -> None:
    """Filter results by file and function name."""
    if not (file_filter or function_filter):
        return

    filtered_vuln = []
    for v in result["top_vulnerable"]:
        fname = v["function"]
        relpath, func = fname.split("::", 1) if "::" in fname else ("", fname)

        match = True
        if file_filter and file_filter.lower() not in relpath.lower():
            match = False
        if function_filter and function_filter.lower() not in func.lower():
            match = False

        if match:
            filtered_vuln.append(v)
    result["top_vulnerable"] = filtered_vuln

    filtered_untested = []
    for fname in result["untested_functions"]:
        relpath, func = fname.split("::", 1) if "::" in fname else ("", fname)

        match = True
        if file_filter and file_filter.lower() not in relpath.lower():
            match = False
        if function_filter and function_filter.lower() not in func.lower():
            match = False

        if match:
            filtered_untested.append(fname)
    result["untested_functions"] = filtered_untested

    result["filter_applied"] = {}
    if file_filter:
        result["filter_applied"]["file"] = file_filter
        result["file_filter"] = file_filter
    if function_filter:
        result["filter_applied"]["function"] = function_filter
        result["function_filter"] = function_filter


def reconcile_with_coverage(
    manifest: TestEffectivenessManifest, coverage_data: dict[str, Any]
) -> dict[str, Any]:
    """Reconcile test effectiveness manifest with coverage data."""
    report: dict[str, Any] = {
        "high_coverage_low_semantic": [],
        "low_coverage_high_semantic": [],
        "coverage_source": "json",
    }

    coverage_files = coverage_data.get("files", {})

    for full_name, fe in manifest.functions.items():
        rel_path, _ = full_name.split("::", 1) if "::" in full_name else (None, full_name)
        if not rel_path:
            continue

        file_cov = coverage_files.get(rel_path, {})
        percent = file_cov.get("summary", {}).get("percent_covered", 0.0)

        if percent > 80.0 and fe.quality_profile.semantic_ratio < 0.3:
            report["high_coverage_low_semantic"].append(
                {
                    "function": full_name,
                    "coverage": round(percent, 1),
                    "semantic_ratio": round(fe.quality_profile.semantic_ratio, 3),
                    "recommendation": "High coverage but weak assertions. Add equality checks to catch value mutations.",
                }
            )

        if percent < 20.0 and fe.quality_profile.semantic_ratio > 0.7:
            report["low_coverage_high_semantic"].append(
                {
                    "function": full_name,
                    "coverage": round(percent, 1),
                    "semantic_ratio": round(fe.quality_profile.semantic_ratio, 3),
                    "recommendation": "Strong assertions found but coverage is low. Expand test inputs to reach more branches.",
                }
            )

    return report


def analyze_function_effectiveness(
    func_name: str,
    assertions: list[Any],
    derivation_methods: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Analyze a single test function's assertions (wrapper for MCP tool)."""
    fe, anti_patterns = _analyze(func_name, assertions, derivation_methods)

    # Convert fe object to legacy dict format for backward compatibility
    func_data = fe.to_dict()
    sem_count = sum(1 for a in fe.assertions if a.strength >= SEMANTIC_STRENGTH_THRESHOLD)

    has_isolated_sentinel = any(w.get("kind") == "isolated_sentinel" for w in anti_patterns)

    func_data.update(
        {
            "semantic_count": sem_count,
            "structural_count": len(fe.assertions) - sem_count,
            "count": len(fe.assertions),
            "warnings": anti_patterns,
            "has_isolated_sentinel": has_isolated_sentinel,
        }
    )

    return func_data, anti_patterns
