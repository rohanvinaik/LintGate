"""Symbol-level coverage gate — binary PASS/FAIL per changed function/method.

Composed into TestChannel, not a separate channel. Takes coverage.py JSON output
+ AST spans and produces per-symbol coverage verdicts. A function is either fully
covered (lines + branches) or it's not. No percentages.

Key design decisions:
- Binary: missing_lines ∩ span == ∅ AND missing_branches with from_line ∈ span == ∅
- Changed functions only by default (git diff + AST intersection)
- Decorator-aware spans (start_line includes first decorator)
- Synthetic branch filtering (negative to_line arcs excluded)
- Nested functions skipped (v1 — their lines subsume under outer function)
- Canonical symbol keys: "relative/path.py::ClassName.method_name" (POSIX)

Sub-modules:
- _symbol_types: Data structures (SymbolSpan, FileCoverage, etc.)
- _symbol_extraction: AST-based symbol span extraction
- _coverage_parsing: Coverage JSON parsing + per-symbol checks
- _target_building: Git diff parsing + target set construction
- _waiver_logic: Waiver parsing and application
"""

from __future__ import annotations

from datetime import date
from typing import Any

from lintgate.channels._coverage_parsing import (  # noqa: F401
    check_symbol_coverage,
    parse_coverage_json,
)
from lintgate.channels._coverage_parsing import (
    find_file_coverage as _find_file_coverage,
)
from lintgate.channels._symbol_extraction import (  # noqa: F401
    _canonicalize_symbol_key,
    _visit_node,
    extract_symbol_spans,
)

# ── Re-exports from sub-modules (backward compatibility) ─────────────────
#
# All public names that were previously defined here are re-exported so that
# existing imports like `from lintgate.channels.symbol_coverage import X`
# continue to work unchanged.
from lintgate.channels._symbol_types import (  # noqa: F401
    FileCoverage,
    SymbolCoverageGateResult,
    SymbolCoverageResult,
    SymbolCoverageWaiver,
    SymbolSpan,
)
from lintgate.channels._target_building import (  # noqa: F401
    _add_overlapping_spans,
    _add_spans,
    _collect_changed_symbols,
    _find_span_by_key,
    _ranges_overlap,
    _resolve_required_symbols,
    build_target_set,
    get_changed_line_ranges,
)
from lintgate.channels._waiver_logic import (  # noqa: F401
    apply_waivers,
)
from lintgate.channels._waiver_logic import (
    parse_waivers as _parse_waivers,
)

# ── Gate Orchestrator ────────────────────────────────────────────────────


def run_symbol_coverage_gate(
    coverage_json_path: str,
    changed_files: list[str],
    project_root: str,
    settings: dict[str, Any],
    *,
    surface: str = "mcp",
) -> SymbolCoverageGateResult:
    """Run the full symbol coverage gate.

    1. Build targets (changed functions + required symbols)
    2. Apply waivers
    3. Parse coverage JSON
    4. Check each target — binary PASS/FAIL
    5. Return aggregate result
    """
    # Build target set
    targets, unresolved_required = build_target_set(
        changed_files, project_root, settings, surface=surface
    )

    if not targets and not unresolved_required:
        return SymbolCoverageGateResult(
            passed=True,
            skipped_reasons=["No symbols targeted for coverage check"],
        )

    # Apply waivers
    raw_waivers = _parse_waivers(settings.get("waivers", []))
    today = date.today()
    filtered_targets, applied_waivers, expired_waivers = apply_waivers(targets, raw_waivers, today)

    # Parse coverage JSON
    coverage_data = parse_coverage_json(coverage_json_path)
    if not coverage_data:
        if surface == "ci":
            return SymbolCoverageGateResult(
                passed=False,
                skipped_reasons=[f"Failed to parse coverage data from {coverage_json_path}"],
                unresolved_required=unresolved_required,
            )
        else:
            return SymbolCoverageGateResult(
                passed=len(unresolved_required) == 0,
                skipped_reasons=[f"Failed to parse coverage data from {coverage_json_path}"],
                waivers_applied=applied_waivers,
                waivers_expired=expired_waivers,
                unresolved_required=unresolved_required,
            )

    # Check each target
    symbol_results: list[SymbolCoverageResult] = []
    for target in filtered_targets:
        # Find coverage data for this file
        file_cov = _find_file_coverage(target.file, coverage_data, project_root)
        if file_cov is None:
            # No coverage data for this file — treat as uncovered
            symbol_results.append(
                SymbolCoverageResult(
                    symbol=target,
                    covered=False,
                    missing_lines=list(range(target.start_line, target.end_line + 1)),
                    missing_branches=[],
                    total_lines_in_span=target.end_line - target.start_line + 1,
                    executed_lines_in_span=0,
                )
            )
        else:
            symbol_results.append(check_symbol_coverage(target, file_cov))

    any_uncovered = any(not r.covered for r in symbol_results)
    passed = not any_uncovered and len(unresolved_required) == 0

    return SymbolCoverageGateResult(
        passed=passed,
        symbol_results=symbol_results,
        waivers_applied=applied_waivers,
        waivers_expired=expired_waivers,
        unresolved_required=unresolved_required,
    )
