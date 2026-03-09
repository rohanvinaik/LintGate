"""Target set building for the symbol coverage gate.

Combines git diff hunk parsing with AST symbol spans to determine which
functions/methods need coverage checking. Also resolves required_symbols
from configuration.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from lintgate.channels._symbol_extraction import (
    _canonicalize_symbol_key,
    extract_symbol_spans,
)
from lintgate.channels._symbol_types import SymbolSpan


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
