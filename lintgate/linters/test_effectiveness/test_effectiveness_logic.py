"""Core logic for test effectiveness analysis and reporting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .manifest import build_test_effectiveness_manifest
from .test_analyzer import _discover_source_files, _discover_test_files
from .types import (
    SEMANTIC_STRENGTH_THRESHOLD,
    STRENGTH_MAP,
    TEFF_SCHEMA_VERSION,
    AnalysisState,
    AssertionInfo,
    AssertionKind,
    FunctionEffectiveness,
)

if TYPE_CHECKING:
    from .types import TestEffectivenessManifest


def build_manifest_for_project(
    project_root: str,
) -> tuple[TestEffectivenessManifest | None, list[str], list[str], list[str]]:
    """Common setup: validate project, discover files, build manifest."""
    from lintgate.channels.structure_channel import _discover_python_files

    py_files = _discover_python_files(project_root)
    test_files = _discover_test_files(project_root)
    source_files = _discover_source_files(project_root)

    manifest = (
        build_test_effectiveness_manifest(project_root, source_files, test_files)
        if py_files and test_files
        else None
    )
    return manifest, py_files, test_files, source_files


def build_summary(manifest: Any, project_root: str) -> dict[str, Any]:
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

    top_vulnerable = [
        {
            "function": name,
            "vulnerability": round(fe.mutation_vulnerability, 3),
            "effectiveness": round(fe.effectiveness_score, 3),
            "semantic_ratio": round(fe.quality_profile.semantic_ratio, 3),
            "test_count": fe.test_count,
            "assertion_count": len(fe.assertions),
        }
        for name, fe in vulnerable[:10]
    ]

    untested = sorted(
        (name for name, fe in manifest.functions.items() if fe.test_count == 0),
        key=lambda x: x,
    )

    return {
        "project": project_root,
        "schema_version": TEFF_SCHEMA_VERSION,
        "summary": {
            "effectiveness_score": round(manifest.project_score, 3),
            "functions_analyzed": manifest.functions_analyzed,
            "mutation_vulnerable_count": manifest.mutation_vulnerable_count,
            "untested_count": len(untested),
        },
        "top_vulnerable": top_vulnerable,
        "untested_functions": untested[:20],
    }


def handle_no_mapped_functions(
    manifest: Any, source_files: list[str], test_files: list[str]
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

    return json.dumps(
        {
            "note": "No mapped functions analyzed.",
            "details": f"Scanned {len(source_files)} source files and {len(test_files)} test files. (Mapped: {diag.mapped} / Attempted: {diag.attempted})",
            "hint": hint,
            "state": state,
            "diagnostics": diag.to_dict(),
        }
    )


def build_assertion_upgrades(manifest: Any) -> list[dict[str, str]]:
    """Identify high-leverage assertion upgrade opportunities."""
    upgrades: list[dict[str, str]] = []
    seen_patterns = set()

    # Find functions with low semantic ratio but high vulnerability
    for _name, fe in manifest.functions.items():
        for a in fe.assertions:
            if a.confidence == "structural" and a.strength < 0.5:
                # Propose upgrade to equality if it's a bare assert or isinstance
                pattern = (a.kind.value, a.target_expression)
                if pattern in seen_patterns:
                    continue
                seen_patterns.add(pattern)

                if a.kind == AssertionKind.ISINSTANCE_CHECK:
                    upgrades.append(
                        {
                            "current": f"assert isinstance({a.target_expression}, ...)",
                            "suggested": f"assert {a.target_expression} == expected_value",
                            "reason": "isinstance (0.3) \u2192 equality (0.9): type check doesn't verify value",
                        }
                    )
                elif a.kind == AssertionKind.IS_NOT_NONE:
                    upgrades.append(
                        {
                            "current": f"assert {a.target_expression} is not None",
                            "suggested": f"assert {a.target_expression} == expected_value",
                            "reason": "is_not_none (0.3) \u2192 equality (0.9): catches value-altering mutants",
                        }
                    )
                elif a.kind == AssertionKind.IS_TRUE:
                    upgrades.append(
                        {
                            "current": f"assert {a.target_expression}",
                            "suggested": f"assert {a.target_expression} == expected",
                            "reason": "bare assert (0.2) \u2192 equality (0.9): catches -1\u2192+1 sentinel mutations",
                        }
                    )

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


def detect_sentinel_patterns(
    assertions: list[AssertionInfo], func_name: str
) -> tuple[list[AssertionInfo], set[str], set[str]]:
    """Apply sentinel pairing and return-type inference."""
    semantic_roots = set()
    for a in assertions:
        if a.strength >= SEMANTIC_STRENGTH_THRESHOLD:
            semantic_roots.add(a.target_root)

    updated_assertions = []
    sentinel_targets = set()
    for a in assertions:
        new_a = a
        if a.kind == AssertionKind.IS_NOT_NONE:
            sentinel_targets.add(a.target_root)
            if a.target_root in semantic_roots:
                new_a.strength = 0.5
        elif a.kind == AssertionKind.IS_NONE:
            sentinel_targets.add(a.target_root)
        updated_assertions.append(new_a)

    if len(updated_assertions) == 1:
        a = updated_assertions[0]
        if a.kind in (AssertionKind.IS_NONE, AssertionKind.IS_NOT_NONE):
            name_match = any(
                p in func_name.lower() or p in a.target_expression.lower()
                for p in ("check_", "validate_", "detect_")
            )
            if name_match:
                a.kind = AssertionKind.SENTINEL_CHECK
                a.strength = STRENGTH_MAP[AssertionKind.SENTINEL_CHECK]
                a.confidence = "heuristic"

    return updated_assertions, sentinel_targets, semantic_roots


def detect_isolated_sentinels(
    assertions: list[AssertionInfo],
    sentinel_targets: set[str],
    semantic_roots: set[str],
) -> list[dict[str, Any]]:
    """Detect isolated sentinels and generate warnings."""
    warnings: list[dict[str, Any]] = []
    for root in sentinel_targets:
        if root and root not in semantic_roots and root not in ("True", "False", "None"):
            guard_line = next(
                (
                    a.line
                    for a in assertions
                    if a.kind in (AssertionKind.IS_NOT_NONE, AssertionKind.IS_NONE)
                    and a.target_root == root
                ),
                None,
            )
            msg = f"Anti-pattern: isolated sentinel guard on '{root}'. No semantic value checks found for this target."
            warnings.append(
                {
                    "kind": "isolated_sentinel",
                    "message": msg,
                    "remediation": f"Verify the state of '{root}' after checking existence.",
                    "missing_followup_pattern": {
                        "expected_followup": f"assert {root}.<field> == <value>",
                        "guard_line": guard_line,
                    },
                }
            )
    return warnings


def detect_hasattr_chains(assertions: list[AssertionInfo]) -> list[dict[str, Any]]:
    """Detect hasattr chain anti-patterns."""
    warnings: list[dict[str, Any]] = []
    hasattr_chains: dict[str, list[int]] = {}
    current_target_expr = None
    current_lines = []

    for a in assertions:
        if a.kind == AssertionKind.HASATTR_CHECK:
            if a.target_expression == current_target_expr:
                current_lines.append(a.line)
            else:
                if len(current_lines) >= 3:
                    hasattr_chains[current_target_expr] = current_lines[:]  # type: ignore
                current_target_expr = a.target_expression
                current_lines = [a.line]
        else:
            if len(current_lines) >= 3:
                hasattr_chains[current_target_expr] = current_lines[:]  # type: ignore
            current_target_expr = None
            current_lines = []

    if len(current_lines) >= 3:
        hasattr_chains[current_target_expr] = current_lines[:]  # type: ignore

    for target, lines in hasattr_chains.items():
        warnings.append(
            {
                "kind": "hasattr_chain",
                "message": f"Anti-pattern: chain of {len(lines)} hasattr checks on '{target}' (lines {lines[0]}-{lines[-1]}).",
                "remediation": f"Replace with attribute equality: assert {target}.field == expected",
            }
        )
    return warnings


def analyze_function_effectiveness(
    func_name: str,
    assertions: list[AssertionInfo],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Analyze a single test function's assertions."""
    updated_assertions, sentinel_targets, semantic_roots = detect_sentinel_patterns(
        assertions, func_name
    )

    warnings = detect_isolated_sentinels(updated_assertions, sentinel_targets, semantic_roots)
    warnings.extend(detect_hasattr_chains(updated_assertions))

    func_data = {
        "assertions": [a.to_dict() for a in updated_assertions],
        "count": len(updated_assertions),
        "warnings": warnings,
        "has_isolated_sentinel": any(w["kind"] == "isolated_sentinel" for w in warnings),
    }

    anti_patterns = []
    is_structural_only = all(
        a.kind
        in (
            AssertionKind.HASATTR_CHECK,
            AssertionKind.ISINSTANCE_CHECK,
            AssertionKind.IS_TRUE,
            AssertionKind.IS_NONE,
            AssertionKind.IS_NOT_NONE,
        )
        for a in updated_assertions
    )
    has_hasattr = any(a.kind == AssertionKind.HASATTR_CHECK for a in updated_assertions)
    if is_structural_only and has_hasattr and len(updated_assertions) > 0:
        anti_patterns.append(
            {
                "function": func_name,
                "reason": "Exclusively hasattr/isinstance checks with no value assertions.",
                "remediation": "This test verifies interface existence but not state. Add equality assertions.",
            }
        )

    fe = FunctionEffectiveness(function_name=func_name, assertions=updated_assertions)
    fe.compute_scores()

    sem_count = sum(1 for a in updated_assertions if a.strength >= SEMANTIC_STRENGTH_THRESHOLD)
    func_data.update(
        {
            "semantic_count": sem_count,
            "structural_count": len(updated_assertions) - sem_count,
            "effectiveness_score": fe.effectiveness_score,
            "quality_profile": fe.quality_profile.to_dict(),
        }
    )

    return func_data, anti_patterns
