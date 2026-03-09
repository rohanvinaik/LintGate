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
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import PurePosixPath
from typing import Any

# ── Data Structures ──────────────────────────────────────────────────────


@dataclass
class SymbolSpan:
    """A function or method span in a source file."""

    file: str  # Absolute path
    symbol_key: str  # Canonical: "relative/path.py::ClassName.method_name"
    name: str  # "func_name" or "ClassName.method_name"
    start_line: int  # min(first_decorator.lineno, node.lineno)
    end_line: int  # ast.FunctionDef.end_lineno
    is_method: bool
    class_name: str | None


@dataclass
class FileCoverage:
    """Coverage data for a single file."""

    executed_lines: set[int]
    missing_lines: set[int]
    excluded_lines: set[int]
    missing_branches: list[tuple[int, int]]  # (from_line, to_line)


@dataclass
class SymbolCoverageResult:
    """Coverage verdict for a single symbol."""

    symbol: SymbolSpan
    covered: bool  # Binary: no missing lines AND no missing branches
    missing_lines: list[int]
    missing_branches: list[tuple[int, int]]
    total_lines_in_span: int
    executed_lines_in_span: int


@dataclass
class SymbolCoverageWaiver:
    """Explicit per-symbol exemption from coverage gate."""

    symbol: str  # Canonical key
    reason: str  # Required, non-empty
    expires: str | None = None  # ISO date "2025-09-01" or None


@dataclass
class SymbolCoverageGateResult:
    """Aggregate result from the symbol coverage gate."""

    passed: bool
    symbol_results: list[SymbolCoverageResult] = field(default_factory=list)
    waivers_applied: list[tuple[str, SymbolCoverageWaiver]] = field(default_factory=list)
    waivers_expired: list[SymbolCoverageWaiver] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)
    unresolved_required: list[str] = field(default_factory=list)


# ── Symbol Key Canonicalization ──────────────────────────────────────────


def _canonicalize_symbol_key(filepath: str, symbol_name: str, project_root: str) -> str:
    """Normalize to POSIX relative path + :: + symbol name.

    Handles Windows paths, trailing slashes, absolute vs relative.
    """
    # Normalize project root (strip trailing separators)
    root = os.path.normpath(project_root)
    fpath = os.path.normpath(filepath)

    # Make relative to project root
    try:
        rel = os.path.relpath(fpath, root)
    except ValueError:
        # Different drives on Windows
        rel = fpath

    # Convert to POSIX
    posix_rel = str(PurePosixPath(*rel.split(os.sep))) if os.sep != "/" else rel

    return f"{posix_rel}::{symbol_name}"


# ── AST Extraction ───────────────────────────────────────────────────────


def extract_symbol_spans(filepath: str, project_root: str) -> list[SymbolSpan]:
    """Extract function/method spans from a Python file via AST.

    Skips nested functions (their lines subsume under the outer function).
    Decorator-aware: start_line includes first decorator if present.
    """
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return []

    spans: list[SymbolSpan] = []
    _visit_node(tree, filepath, project_root, spans, current_class=None, depth=0)
    return spans


def _visit_node(
    node: ast.AST,
    filepath: str,
    project_root: str,
    spans: list[SymbolSpan],
    current_class: str | None,
    depth: int,
) -> None:
    """Recursively visit AST nodes to extract function/method spans."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            # Visit class body with class context
            _visit_node(child, filepath, project_root, spans, child.name, depth)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip nested functions (depth > 0 within a function)
            if depth > 0:
                continue

            end_line = getattr(child, "end_lineno", None)
            if end_line is None:
                continue

            # Decorator-aware start line
            if child.decorator_list:
                start_line = min(child.decorator_list[0].lineno, child.lineno)
            else:
                start_line = child.lineno

            is_method = current_class is not None
            name = f"{current_class}.{child.name}" if is_method else child.name

            symbol_key = _canonicalize_symbol_key(filepath, name, project_root)

            spans.append(
                SymbolSpan(
                    file=filepath,
                    symbol_key=symbol_key,
                    name=name,
                    start_line=start_line,
                    end_line=end_line,
                    is_method=is_method,
                    class_name=current_class,
                )
            )

            # Visit nested functions at depth+1 (they will be skipped)
            _visit_node(child, filepath, project_root, spans, current_class, depth + 1)


# ── Coverage JSON Parsing ────────────────────────────────────────────────


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


# ── Symbol Coverage Check ────────────────────────────────────────────────


def check_symbol_coverage(symbol: SymbolSpan, file_cov: FileCoverage) -> SymbolCoverageResult:
    """Check if a symbol span is fully covered (lines + branches).

    Binary: covered = (missing_lines ∩ span == ∅) AND
                      (missing_branches with from_line ∈ span == ∅)
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


# ── Git Diff Parsing ─────────────────────────────────────────────────────


def get_changed_line_ranges(
    filepath: str, project_root: str, *, diff_base: str = "HEAD"
) -> list[range] | None:
    """Parse git diff hunk headers to get changed line ranges.

    Returns None on git failure (distinct from empty list).
    """
    try:
        rel_path = os.path.relpath(filepath, project_root)
    except ValueError:
        return None

    try:
        result = subprocess.run(
            ["git", "diff", diff_base, "--unified=0", "--", rel_path],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=project_root,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        # Check if it's an untracked file (no diff available)
        return None

    ranges: list[range] = []
    for line in result.stdout.splitlines():
        # Parse @@ -old,count +new,count @@ header
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) else 1
            if count > 0:
                ranges.append(range(start, start + count))

    return ranges


# ── Target Set Building ──────────────────────────────────────────────────


def build_target_set(
    changed_files: list[str],
    project_root: str,
    settings: dict[str, Any],
    *,
    surface: str = "mcp",
) -> tuple[list[SymbolSpan], list[str]]:
    """Build the set of symbols to check coverage for.

    Returns (targets, unresolved_required).
    Union of changed functions (from git diff + AST) and required_symbols.
    """
    targets: list[SymbolSpan] = []
    seen_keys: set[str] = set()

    mode = settings.get("mode", "changed")
    diff_base = settings.get("diff_base", "HEAD")

    if mode in ("changed", "all"):
        _collect_changed_symbols(changed_files, project_root, diff_base, targets, seen_keys)

    unresolved = _resolve_required_symbols(
        settings.get("required_symbols", []),
        project_root,
        targets,
        seen_keys,
    )

    return targets, unresolved


def _collect_changed_symbols(
    changed_files: list[str],
    project_root: str,
    diff_base: str,
    targets: list[SymbolSpan],
    seen_keys: set[str],
) -> None:
    """Intersect git diff hunks with AST spans to find changed symbols."""
    for filepath in changed_files:
        if not filepath.endswith(".py") or not os.path.isfile(filepath):
            continue

        # Skip test files — they are tests, not source requiring coverage
        basename = os.path.basename(filepath)
        if (
            basename.startswith("test_")
            or basename.endswith("_test.py")
            or basename == "conftest.py"
        ):
            continue

        spans = extract_symbol_spans(filepath, project_root)
        if not spans:
            continue

        changed_ranges = get_changed_line_ranges(filepath, project_root, diff_base=diff_base)

        if not changed_ranges:
            # Git failure or new/untracked file: target ALL symbols
            _add_spans(spans, targets, seen_keys)
        else:
            _add_overlapping_spans(spans, changed_ranges, targets, seen_keys)


def _add_spans(
    spans: list[SymbolSpan],
    targets: list[SymbolSpan],
    seen_keys: set[str],
) -> None:
    """Add all spans to targets, deduplicating by symbol_key."""
    for span in spans:
        if span.symbol_key not in seen_keys:
            targets.append(span)
            seen_keys.add(span.symbol_key)


def _add_overlapping_spans(
    spans: list[SymbolSpan],
    changed_ranges: list[range],
    targets: list[SymbolSpan],
    seen_keys: set[str],
) -> None:
    """Add spans that overlap with any changed range."""
    for span in spans:
        if span.symbol_key in seen_keys:
            continue
        span_range = range(span.start_line, span.end_line + 1)
        if any(_ranges_overlap(span_range, cr) for cr in changed_ranges):
            targets.append(span)
            seen_keys.add(span.symbol_key)


def _resolve_required_symbols(
    required_symbols: Any,
    project_root: str,
    targets: list[SymbolSpan],
    seen_keys: set[str],
) -> list[str]:
    """Resolve required_symbols config entries to AST spans.

    Returns list of symbols that could not be resolved (blocking errors).
    """
    if not isinstance(required_symbols, list):
        return []

    unresolved: list[str] = []
    for req in required_symbols:
        if not isinstance(req, str) or "::" not in req:
            unresolved.append(str(req))
            continue

        rel_path, symbol_name = req.split("::", 1)
        abs_path = os.path.join(project_root, rel_path)

        if not os.path.isfile(abs_path):
            unresolved.append(req)
            continue

        canonical = _canonicalize_symbol_key(abs_path, symbol_name, project_root)
        if canonical in seen_keys:
            continue

        span = _find_span_by_key(abs_path, project_root, canonical)
        if span:
            targets.append(span)
            seen_keys.add(canonical)
        else:
            unresolved.append(req)

    return unresolved


def _find_span_by_key(filepath: str, project_root: str, canonical_key: str) -> SymbolSpan | None:
    """Find a specific symbol span by its canonical key."""
    for span in extract_symbol_spans(filepath, project_root):
        if span.symbol_key == canonical_key:
            return span
    return None


def _ranges_overlap(a: range, b: range) -> bool:
    """Check if two ranges overlap."""
    return a.start < b.stop and b.start < a.stop


# ── Waiver Application ──────────────────────────────────────────────────


def apply_waivers(
    targets: list[SymbolSpan],
    waivers: list[SymbolCoverageWaiver],
    today: date,
) -> tuple[list[SymbolSpan], list[tuple[str, SymbolCoverageWaiver]], list[SymbolCoverageWaiver]]:
    """Apply waivers to the target set.

    Returns (filtered_targets, applied_waivers, expired_waivers).
    """
    expired: list[SymbolCoverageWaiver] = []
    active_waivers: dict[str, SymbolCoverageWaiver] = {}

    for waiver in waivers:
        if waiver.expires:
            try:
                exp_date = date.fromisoformat(waiver.expires)
                if exp_date < today:
                    expired.append(waiver)
                    continue
            except ValueError:
                continue  # Invalid date format — skip waiver
        active_waivers[waiver.symbol] = waiver

    # Separate exact-match and glob-pattern waivers
    exact_waivers: dict[str, SymbolCoverageWaiver] = {}
    glob_waivers: list[SymbolCoverageWaiver] = []
    for sym, waiver in active_waivers.items():
        if "*" in sym:
            glob_waivers.append(waiver)
        else:
            exact_waivers[sym] = waiver

    filtered: list[SymbolSpan] = []
    applied: list[tuple[str, SymbolCoverageWaiver]] = []

    for target in targets:
        if target.symbol_key in exact_waivers:
            applied.append((target.symbol_key, exact_waivers[target.symbol_key]))
        else:
            matched = False
            for gw in glob_waivers:
                from fnmatch import fnmatch

                if fnmatch(target.symbol_key, gw.symbol):
                    applied.append((target.symbol_key, gw))
                    matched = True
                    break
            if not matched:
                filtered.append(target)

    return filtered, applied, expired


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


def _find_file_coverage(
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


def _parse_waivers(raw: Any) -> list[SymbolCoverageWaiver]:
    """Parse waiver config entries into SymbolCoverageWaiver objects."""
    if not isinstance(raw, list):
        return []

    waivers: list[SymbolCoverageWaiver] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol", "")
        reason = entry.get("reason", "")
        if not symbol or not reason:
            continue  # Strict: must have symbol and reason
        waivers.append(
            SymbolCoverageWaiver(
                symbol=str(symbol),
                reason=str(reason),
                expires=str(entry["expires"]) if entry.get("expires") else None,
            )
        )
    return waivers
