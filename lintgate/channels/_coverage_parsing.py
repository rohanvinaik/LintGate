"""Coverage JSON parsing and per-symbol coverage checking.

Parses coverage.py JSON output and provides binary PASS/FAIL verdicts
per symbol span (lines + branches).
"""

from __future__ import annotations

import json
import os

from lintgate.channels._symbol_types import (
    FileCoverage,
    SymbolCoverageResult,
    SymbolSpan,
)


def parse_coverage_json(path: str) -> dict[str, FileCoverage]:
    """Parse coverage.py JSON output into FileCoverage per file.

    Keys are absolute file paths as reported by coverage.py.
    Filters synthetic branch arcs (to_line < 0).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    files_data = data.get("files", {})
    result: dict[str, FileCoverage] = {}

    for filepath, file_info in files_data.items():
        executed = set(file_info.get("executed_lines", []))
        missing = set(file_info.get("missing_lines", []))
        excluded = set(file_info.get("excluded_lines", []))

        # Parse branches — filter synthetic arcs (to_line < 0)
        raw_branches = file_info.get("missing_branches", [])
        branches: list[tuple[int, int]] = []
        for branch in raw_branches:
            if isinstance(branch, (list, tuple)) and len(branch) == 2:
                from_line, to_line = int(branch[0]), int(branch[1])
                if to_line >= 0:  # Filter synthetic arcs
                    branches.append((from_line, to_line))

        result[filepath] = FileCoverage(
            executed_lines=executed,
            missing_lines=missing,
            excluded_lines=excluded,
            missing_branches=branches,
        )

    return result


def check_symbol_coverage(symbol: SymbolSpan, file_cov: FileCoverage) -> SymbolCoverageResult:
    """Check if a symbol span is fully covered (lines + branches).

    Binary: covered = (missing_lines intersection span == empty) AND
                      (missing_branches with from_line in span == empty)
    """
    span = range(symbol.start_line, symbol.end_line + 1)
    span_set = set(span)

    missing_in_span = sorted(file_cov.missing_lines & span_set)
    executed_in_span = file_cov.executed_lines & span_set

    # Exclude excluded lines from total count
    excluded_in_span = file_cov.excluded_lines & span_set
    countable_lines = span_set - excluded_in_span
    total_lines = len(countable_lines)

    # Missing branches where from_line is within the span
    missing_branches = [(f, t) for f, t in file_cov.missing_branches if f in span_set]

    covered = len(missing_in_span) == 0 and len(missing_branches) == 0

    return SymbolCoverageResult(
        symbol=symbol,
        covered=covered,
        missing_lines=missing_in_span,
        missing_branches=missing_branches,
        total_lines_in_span=total_lines,
        executed_lines_in_span=len(executed_in_span),
    )


def find_file_coverage(
    abs_path: str,
    coverage_data: dict[str, FileCoverage],
    project_root: str,
) -> FileCoverage | None:
    """Find coverage data for a file, trying both absolute and relative paths."""
    # Try absolute path first
    if abs_path in coverage_data:
        return coverage_data[abs_path]

    # Try relative path
    try:
        rel = os.path.relpath(abs_path, project_root)
        if rel in coverage_data:
            return coverage_data[rel]
    except ValueError:
        pass

    # Try matching by filename suffix
    norm_abs = os.path.normpath(abs_path)
    for key, cov in coverage_data.items():
        if os.path.normpath(key) == norm_abs:
            return cov
        # coverage.py often uses absolute paths
        try:
            if os.path.normpath(os.path.join(project_root, key)) == norm_abs:
                return cov
        except (ValueError, TypeError):
            pass

    return None
