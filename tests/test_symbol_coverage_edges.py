"""Real integration tests for symbol_coverage.py edge-case code paths.

No mocks — exercises actual functions with crafted inputs.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

from lintgate.channels.symbol_coverage import (
    FileCoverage,
    SymbolCoverageWaiver,
    SymbolSpan,
    _add_overlapping_spans,
    _collect_changed_symbols,
    _find_file_coverage,
    _parse_waivers,
    _resolve_required_symbols,
    apply_waivers,
    run_symbol_coverage_gate,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── _collect_changed_symbols: test file skip + no spans ─────────────


class TestCollectChangedSymbolsEdges:
    def test_skips_test_prefixed_files(self, tmp_path: Path) -> None:
        f = tmp_path / "test_example.py"
        f.write_text("def test_it():\n    pass\n")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        _collect_changed_symbols([str(f)], str(tmp_path), "HEAD", targets, seen)
        assert len(targets) == 0

    def test_skips_test_suffix_files(self, tmp_path: Path) -> None:
        f = tmp_path / "module_test.py"
        f.write_text("def test_it():\n    pass\n")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        _collect_changed_symbols([str(f)], str(tmp_path), "HEAD", targets, seen)
        assert len(targets) == 0

    def test_skips_conftest(self, tmp_path: Path) -> None:
        f = tmp_path / "conftest.py"
        f.write_text("import pytest\n")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        _collect_changed_symbols([str(f)], str(tmp_path), "HEAD", targets, seen)
        assert len(targets) == 0

    def test_no_symbols_file_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_text("# just a comment\nX = 42\n")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        _collect_changed_symbols([str(f)], str(tmp_path), "HEAD", targets, seen)
        assert len(targets) == 0

    def test_non_test_file_included(self, tmp_path: Path) -> None:
        f = tmp_path / "real_module.py"
        f.write_text("def greet():\n    return 'hi'\n")
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        _collect_changed_symbols([str(f)], str(tmp_path), "HEAD", targets, seen)
        assert len(targets) == 1
        assert "greet" in targets[0].symbol_key


# ── _add_overlapping_spans: dedup via seen_keys ─────────────────────


class TestAddOverlappingSpansDedup:
    def test_duplicate_key_skipped(self) -> None:
        span = SymbolSpan(
            file="/a/b.py",
            symbol_key="b.py::func",
            name="func",
            start_line=1,
            end_line=5,
            is_method=False,
            class_name=None,
        )
        targets: list[SymbolSpan] = []
        seen: set[str] = {"b.py::func"}
        _add_overlapping_spans([span], [range(1, 6)], targets, seen)
        assert len(targets) == 0

    def test_non_overlapping_not_added(self) -> None:
        span = SymbolSpan(
            file="/a/b.py",
            symbol_key="b.py::func",
            name="func",
            start_line=10,
            end_line=20,
            is_method=False,
            class_name=None,
        )
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        _add_overlapping_spans([span], [range(1, 5)], targets, seen)
        assert len(targets) == 0


# ── _resolve_required_symbols: non-list + dedup ─────────────────────


class TestResolveRequiredEdges:
    def test_non_list_returns_empty(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        result = _resolve_required_symbols("not-a-list", "/root", targets, seen)  # type: ignore[arg-type]
        assert result == []

    def test_already_seen_symbol_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("def func():\n    pass\n")
        targets: list[SymbolSpan] = []
        seen: set[str] = {"mod.py::func"}
        result = _resolve_required_symbols(["mod.py::func"], str(tmp_path), targets, seen)
        assert result == []
        assert len(targets) == 0

    def test_invalid_format_unresolved(self) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        result = _resolve_required_symbols(["no-double-colon"], "/root", targets, seen)
        assert "no-double-colon" in result

    def test_missing_file_unresolved(self, tmp_path: Path) -> None:
        targets: list[SymbolSpan] = []
        seen: set[str] = set()
        result = _resolve_required_symbols(["nonexistent.py::func"], str(tmp_path), targets, seen)
        assert "nonexistent.py::func" in result


# ── apply_waivers: invalid date format ──────────────────────────────


class TestApplyWaiversInvalidDate:
    def test_invalid_expiry_date_skipped(self) -> None:
        target = SymbolSpan(
            file="/a.py",
            symbol_key="a.py::func",
            name="func",
            start_line=1,
            end_line=5,
            is_method=False,
            class_name=None,
        )
        waiver = SymbolCoverageWaiver(
            symbol="a.py::func",
            reason="test",
            expires="not-a-date",
        )
        filtered, applied, expired = apply_waivers([target], [waiver], date.today())
        assert len(filtered) == 1
        assert len(applied) == 0

    def test_valid_future_expiry_applies(self) -> None:
        target = SymbolSpan(
            file="/a.py",
            symbol_key="a.py::func",
            name="func",
            start_line=1,
            end_line=5,
            is_method=False,
            class_name=None,
        )
        waiver = SymbolCoverageWaiver(
            symbol="a.py::func",
            reason="temporary waiver",
            expires="2099-12-31",
        )
        filtered, applied, expired = apply_waivers([target], [waiver], date.today())
        assert len(filtered) == 0
        assert len(applied) == 1


# ── _find_file_coverage: relative path + suffix matching ────────────


class TestFindFileCoverageEdges:
    def test_relative_path_match(self) -> None:
        cov = FileCoverage(
            executed_lines={1, 2},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        data = {"src/mod.py": cov}
        result = _find_file_coverage("/project/src/mod.py", data, "/project")
        assert result is cov

    def test_absolute_key_match(self) -> None:
        cov = FileCoverage(
            executed_lines={1},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        data = {"/project/src/mod.py": cov}
        result = _find_file_coverage("/project/src/mod.py", data, "/project")
        assert result is cov

    def test_normpath_key_match(self) -> None:
        """Key with double-slash matches after normpath."""
        cov = FileCoverage(
            executed_lines={1},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        data = {"/project//src/mod.py": cov}
        result = _find_file_coverage("/project/src/mod.py", data, "/other")
        assert result is cov

    def test_join_relative_key_match(self) -> None:
        """Relative key that joins with project_root to match abs_path."""
        cov = FileCoverage(
            executed_lines={1},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        data = {"./src/mod.py": cov}
        result = _find_file_coverage("/project/src/mod.py", data, "/project")
        assert result is cov

    def test_no_match_returns_none(self) -> None:
        cov = FileCoverage(
            executed_lines={1},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        data = {"other/file.py": cov}
        result = _find_file_coverage("/project/src/mod.py", data, "/project")
        assert result is None


# ── _parse_waivers: non-dict entry + non-list input ─────────────────


class TestParseWaiversEdges:
    def test_non_dict_entry_skipped(self) -> None:
        result = _parse_waivers(
            [
                {"symbol": "a.py::func", "reason": "valid"},
                "not-a-dict",
                42,
            ]
        )
        assert len(result) == 1
        assert result[0].symbol == "a.py::func"

    def test_non_list_returns_empty(self) -> None:
        result = _parse_waivers("not-a-list")  # type: ignore[arg-type]
        assert result == []

    def test_missing_symbol_skipped(self) -> None:
        result = _parse_waivers([{"reason": "no symbol key"}])
        assert len(result) == 0

    def test_missing_reason_skipped(self) -> None:
        result = _parse_waivers([{"symbol": "a.py::func"}])
        assert len(result) == 0


# ── run_symbol_coverage_gate: real empty-coverage scenario ──────────


class TestRunGateNoCoverageForFile:
    def test_file_not_in_coverage_data_is_uncovered(self, tmp_path: Path) -> None:
        """Source file with function but no coverage data → uncovered."""
        src = tmp_path / "mod.py"
        src.write_text("def greet():\n    return 'hi'\n")
        cov_path = tmp_path / "coverage.json"
        # Valid coverage JSON with no matching file data
        cov_path.write_text(
            json.dumps(
                {
                    "meta": {"version": "7.0"},
                    "files": {
                        "other/unrelated.py": {
                            "executed_lines": [1, 2],
                            "missing_lines": [],
                            "excluded_lines": [],
                            "summary": {},
                        },
                    },
                }
            )
        )
        result = run_symbol_coverage_gate(
            coverage_json_path=str(cov_path),
            changed_files=[str(src)],
            project_root=str(tmp_path),
            settings={"enabled": True, "mode": "changed", "diff_base": "HEAD"},
            surface="mcp",
        )
        uncovered = [r for r in result.symbol_results if not r.covered]
        assert len(uncovered) >= 1
        assert any("greet" in r.symbol.symbol_key for r in uncovered)
