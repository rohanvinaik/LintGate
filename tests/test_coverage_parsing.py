"""Tests for lintgate/channels/_coverage_parsing.py — all 3 functions."""

from __future__ import annotations

import json
import os
import tempfile

from lintgate.channels._coverage_parsing import (
    check_symbol_coverage,
    find_file_coverage,
    parse_coverage_json,
)
from lintgate.channels._symbol_types import FileCoverage, SymbolSpan


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_span(
    start: int = 1,
    end: int = 10,
    name: str = "func",
    file: str = "/proj/mod.py",
) -> SymbolSpan:
    return SymbolSpan(
        file=file,
        symbol_key=f"mod.py::{name}",
        name=name,
        start_line=start,
        end_line=end,
        is_method=False,
        class_name=None,
    )


def _write_coverage_json(data: dict) -> str:
    """Write coverage JSON to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


# ─── parse_coverage_json ─────────────────────────────────────────────────


def test_parse_basic_coverage():
    """Parses executed and missing lines from coverage.py JSON."""
    path = _write_coverage_json({
        "files": {
            "/proj/mod.py": {
                "executed_lines": [1, 2, 3],
                "missing_lines": [4, 5],
                "excluded_lines": [],
                "missing_branches": [],
            }
        }
    })
    result = parse_coverage_json(path)
    assert "/proj/mod.py" in result
    cov = result["/proj/mod.py"]
    assert cov.executed_lines == {1, 2, 3}
    assert cov.missing_lines == {4, 5}
    assert cov.excluded_lines == set()
    assert cov.missing_branches == []


def test_parse_filters_synthetic_branches():
    """Synthetic branch arcs (to_line < 0) are filtered out."""
    path = _write_coverage_json({
        "files": {
            "a.py": {
                "executed_lines": [1],
                "missing_lines": [],
                "excluded_lines": [],
                "missing_branches": [[5, 10], [7, -1]],
            }
        }
    })
    result = parse_coverage_json(path)
    assert result["a.py"].missing_branches == [(5, 10)]


def test_parse_nonexistent_file():
    """Nonexistent path returns empty dict."""
    result = parse_coverage_json("/no/such/file.json")
    assert result == {}


def test_parse_invalid_json():
    """Invalid JSON returns empty dict."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write("not json{{{")
    result = parse_coverage_json(path)
    assert result == {}


def test_parse_empty_files_section():
    """Coverage JSON with empty files section returns empty dict."""
    path = _write_coverage_json({"files": {}})
    result = parse_coverage_json(path)
    assert result == {}


# ─── check_symbol_coverage ────────────────────────────────────────────────


def test_fully_covered_symbol():
    """Symbol with no missing lines and no missing branches is covered=True."""
    span = _make_span(start=1, end=5)
    cov = FileCoverage(
        executed_lines={1, 2, 3, 4, 5},
        missing_lines=set(),
        excluded_lines=set(),
        missing_branches=[],
    )
    result = check_symbol_coverage(span, cov)
    assert result.covered is True
    assert result.missing_lines == []
    assert result.missing_branches == []
    assert result.total_lines_in_span == 5
    assert result.executed_lines_in_span == 5


def test_partially_covered_symbol():
    """Symbol with missing lines is covered=False."""
    span = _make_span(start=1, end=5)
    cov = FileCoverage(
        executed_lines={1, 2, 3},
        missing_lines={4, 5},
        excluded_lines=set(),
        missing_branches=[],
    )
    result = check_symbol_coverage(span, cov)
    assert result.covered is False
    assert result.missing_lines == [4, 5]
    assert result.executed_lines_in_span == 3


def test_missing_branch_fails_coverage():
    """Symbol with missing branch in span is covered=False even if all lines executed."""
    span = _make_span(start=1, end=5)
    cov = FileCoverage(
        executed_lines={1, 2, 3, 4, 5},
        missing_lines=set(),
        excluded_lines=set(),
        missing_branches=[(3, 6)],  # from_line=3 is in span
    )
    result = check_symbol_coverage(span, cov)
    assert result.covered is False
    assert result.missing_branches == [(3, 6)]


def test_branch_outside_span_ignored():
    """Missing branch outside the symbol span does not affect coverage."""
    span = _make_span(start=1, end=5)
    cov = FileCoverage(
        executed_lines={1, 2, 3, 4, 5},
        missing_lines=set(),
        excluded_lines=set(),
        missing_branches=[(10, 12)],  # from_line=10 outside span
    )
    result = check_symbol_coverage(span, cov)
    assert result.covered is True


def test_excluded_lines_reduce_total():
    """Excluded lines within span are subtracted from total_lines_in_span."""
    span = _make_span(start=1, end=5)
    cov = FileCoverage(
        executed_lines={1, 2, 3},
        missing_lines=set(),
        excluded_lines={4, 5},
        missing_branches=[],
    )
    result = check_symbol_coverage(span, cov)
    assert result.covered is True
    assert result.total_lines_in_span == 3  # 5 - 2 excluded


# ─── find_file_coverage ──────────────────────────────────────────────────


def _make_file_cov() -> FileCoverage:
    return FileCoverage(
        executed_lines={1, 2},
        missing_lines=set(),
        excluded_lines=set(),
        missing_branches=[],
    )


def test_find_by_absolute_path():
    """Direct absolute path match returns coverage."""
    cov = _make_file_cov()
    data = {"/proj/mod.py": cov}
    result = find_file_coverage("/proj/mod.py", data, "/proj")
    assert result is cov
    assert result.executed_lines == {1, 2}
    assert result.missing_lines == set()
    assert result.excluded_lines == set()
    assert result.missing_branches == []


def test_find_by_relative_path():
    """Relative path match when absolute key is absent."""
    cov = _make_file_cov()
    data = {"mod.py": cov}
    result = find_file_coverage("/proj/mod.py", data, "/proj")
    assert result is cov
    assert result.executed_lines == {1, 2}
    assert result.missing_lines == set()


def test_find_by_join_match():
    """Coverage key resolved via os.path.join(project_root, key)."""
    cov = _make_file_cov()
    data = {"src/mod.py": cov}
    result = find_file_coverage("/proj/src/mod.py", data, "/proj")
    assert result is cov
    assert result.executed_lines == {1, 2}
    assert result.missing_lines == set()


def test_find_returns_none_when_absent():
    """No match returns None."""
    result = find_file_coverage("/proj/unknown.py", {}, "/proj")
    assert result is None
    assert find_file_coverage("/proj/other.py", {"/proj/mod.py": _make_file_cov()}, "/proj") is None
