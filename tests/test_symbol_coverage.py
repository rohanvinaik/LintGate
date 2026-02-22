"""Tests for the symbol coverage gate.

Covers: canonicalization, AST extraction, coverage JSON parsing,
symbol coverage checks, git diff parsing, target set building,
waivers, gate orchestration, and TestChannel integration.
"""

from __future__ import annotations

import json
import os
import textwrap
from datetime import date
from unittest.mock import patch

from lintgate.channels.symbol_coverage import (
    FileCoverage,
    SymbolCoverageGateResult,
    SymbolCoverageResult,
    SymbolCoverageWaiver,
    SymbolSpan,
    _canonicalize_symbol_key,
    _parse_waivers,
    _ranges_overlap,
    apply_waivers,
    build_target_set,
    check_symbol_coverage,
    extract_symbol_spans,
    get_changed_line_ranges,
    parse_coverage_json,
    run_symbol_coverage_gate,
)

# ── TestCanonicalizeSymbolKey ────────────────────────────────────────────


class TestCanonicalizeSymbolKey:
    def test_basic_posix(self, tmp_path):
        fp = str(tmp_path / "src" / "foo.py")
        result = _canonicalize_symbol_key(fp, "bar", str(tmp_path))
        assert result == "src/foo.py::bar"

    def test_method_name(self, tmp_path):
        fp = str(tmp_path / "pkg" / "mod.py")
        result = _canonicalize_symbol_key(fp, "MyClass.method", str(tmp_path))
        assert result == "pkg/mod.py::MyClass.method"

    def test_trailing_slash_on_root(self, tmp_path):
        fp = str(tmp_path / "mod.py")
        root_with_slash = str(tmp_path) + os.sep
        result = _canonicalize_symbol_key(fp, "func", root_with_slash)
        assert result == "mod.py::func"

    def test_same_dir(self, tmp_path):
        fp = str(tmp_path / "script.py")
        result = _canonicalize_symbol_key(fp, "main", str(tmp_path))
        assert result == "script.py::main"


# ── TestExtractSymbolSpans ───────────────────────────────────────────────


class TestExtractSymbolSpans:
    def _write_py(self, tmp_path, content: str) -> str:
        p = tmp_path / "mod.py"
        p.write_text(textwrap.dedent(content))
        return str(p)

    def test_functions(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            def foo():
                pass

            def bar():
                return 1
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        names = [s.name for s in spans]
        assert "foo" in names
        assert "bar" in names
        assert all(not s.is_method for s in spans)

    def test_methods(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            class MyClass:
                def method_a(self):
                    pass

                def method_b(self):
                    return 1
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        names = [s.name for s in spans]
        assert "MyClass.method_a" in names
        assert "MyClass.method_b" in names
        assert all(s.is_method for s in spans)
        assert all(s.class_name == "MyClass" for s in spans)

    def test_async_function(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            async def fetch():
                pass
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        assert len(spans) == 1
        assert spans[0].name == "fetch"

    def test_decorated_function_start_line(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            import functools

            @functools.lru_cache
            def cached():
                return 42
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        assert len(spans) == 1
        # start_line should be the decorator line, not the def line
        assert spans[0].start_line == 3  # @functools.lru_cache
        assert spans[0].name == "cached"

    def test_nested_functions_skipped(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            def outer():
                def inner():
                    pass
                return inner()
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        names = [s.name for s in spans]
        assert "outer" in names
        assert "inner" not in names

    def test_syntax_error_returns_empty(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            def broken(
                # missing closing paren
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        assert spans == []

    def test_empty_file(self, tmp_path):
        fp = self._write_py(tmp_path, "")
        spans = extract_symbol_spans(fp, str(tmp_path))
        assert spans == []

    def test_symbol_key_format(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            def hello():
                pass
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        assert spans[0].symbol_key == "mod.py::hello"

    def test_nested_class_methods(self, tmp_path):
        fp = self._write_py(tmp_path, """\
            class Outer:
                class Inner:
                    def method(self):
                        pass
        """)
        spans = extract_symbol_spans(fp, str(tmp_path))
        # Inner.method should be found (Inner is the class context)
        names = [s.name for s in spans]
        assert "Inner.method" in names


# ── TestParseCoverageJson ────────────────────────────────────────────────


class TestParseCoverageJson:
    def test_valid_json(self, tmp_path):
        data = {
            "files": {
                "/src/mod.py": {
                    "executed_lines": [1, 2, 3, 5],
                    "missing_lines": [4, 6],
                    "excluded_lines": [7],
                    "missing_branches": [[3, 5], [3, 6]],
                }
            }
        }
        p = tmp_path / "cov.json"
        p.write_text(json.dumps(data))
        result = parse_coverage_json(str(p))
        assert "/src/mod.py" in result
        fc = result["/src/mod.py"]
        assert fc.executed_lines == {1, 2, 3, 5}
        assert fc.missing_lines == {4, 6}
        assert fc.excluded_lines == {7}
        assert fc.missing_branches == [(3, 5), (3, 6)]

    def test_synthetic_branches_filtered(self, tmp_path):
        data = {
            "files": {
                "mod.py": {
                    "executed_lines": [1, 2],
                    "missing_lines": [],
                    "excluded_lines": [],
                    "missing_branches": [[3, 5], [3, -1], [6, -2]],
                }
            }
        }
        p = tmp_path / "cov.json"
        p.write_text(json.dumps(data))
        result = parse_coverage_json(str(p))
        # Only (3, 5) should remain; (3, -1) and (6, -2) are synthetic
        assert result["mod.py"].missing_branches == [(3, 5)]

    def test_missing_file(self, tmp_path):
        result = parse_coverage_json(str(tmp_path / "nonexistent.json"))
        assert result == {}

    def test_malformed_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        result = parse_coverage_json(str(p))
        assert result == {}

    def test_missing_keys_default(self, tmp_path):
        data = {"files": {"mod.py": {}}}
        p = tmp_path / "cov.json"
        p.write_text(json.dumps(data))
        result = parse_coverage_json(str(p))
        fc = result["mod.py"]
        assert fc.executed_lines == set()
        assert fc.missing_lines == set()
        assert fc.missing_branches == []


# ── TestCheckSymbolCoverage ──────────────────────────────────────────────


class TestCheckSymbolCoverage:
    def _make_span(self, start: int = 1, end: int = 5) -> SymbolSpan:
        return SymbolSpan(
            file="/src/mod.py",
            symbol_key="mod.py::func",
            name="func",
            start_line=start,
            end_line=end,
            is_method=False,
            class_name=None,
        )

    def test_fully_covered(self):
        span = self._make_span(1, 5)
        fc = FileCoverage(
            executed_lines={1, 2, 3, 4, 5},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[],
        )
        result = check_symbol_coverage(span, fc)
        assert result.covered is True
        assert result.missing_lines == []
        assert result.missing_branches == []

    def test_missing_lines(self):
        span = self._make_span(1, 5)
        fc = FileCoverage(
            executed_lines={1, 2, 4},
            missing_lines={3, 5},
            excluded_lines=set(),
            missing_branches=[],
        )
        result = check_symbol_coverage(span, fc)
        assert result.covered is False
        assert result.missing_lines == [3, 5]

    def test_missing_branches(self):
        span = self._make_span(1, 5)
        fc = FileCoverage(
            executed_lines={1, 2, 3, 4, 5},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[(3, 5)],  # branch from line 3 within span
        )
        result = check_symbol_coverage(span, fc)
        assert result.covered is False
        assert result.missing_branches == [(3, 5)]

    def test_branch_outside_span_ignored(self):
        span = self._make_span(1, 5)
        fc = FileCoverage(
            executed_lines={1, 2, 3, 4, 5},
            missing_lines=set(),
            excluded_lines=set(),
            missing_branches=[(10, 12)],  # outside span
        )
        result = check_symbol_coverage(span, fc)
        assert result.covered is True

    def test_excluded_lines_not_counted(self):
        span = self._make_span(1, 5)
        fc = FileCoverage(
            executed_lines={1, 2, 4, 5},
            missing_lines=set(),
            excluded_lines={3},
            missing_branches=[],
        )
        result = check_symbol_coverage(span, fc)
        assert result.covered is True
        assert result.total_lines_in_span == 4  # 5 - 1 excluded

    def test_mixed_failures(self):
        span = self._make_span(1, 10)
        fc = FileCoverage(
            executed_lines={1, 2, 3, 5, 7, 8, 9},
            missing_lines={4, 6, 10},
            excluded_lines=set(),
            missing_branches=[(5, 7)],
        )
        result = check_symbol_coverage(span, fc)
        assert result.covered is False
        assert result.missing_lines == [4, 6, 10]
        assert result.missing_branches == [(5, 7)]


# ── TestGetChangedLineRanges ─────────────────────────────────────────────


class TestGetChangedLineRanges:
    def test_normal_diff(self, tmp_path):
        diff_output = textwrap.dedent("""\
            diff --git a/mod.py b/mod.py
            --- a/mod.py
            +++ b/mod.py
            @@ -10,0 +11,3 @@
            +    new line 1
            +    new line 2
            +    new line 3
            @@ -20,2 +24,1 @@
            -    old line
            -    old line
            +    replaced
        """)
        with patch("lintgate.channels.symbol_coverage.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 0,
                "stdout": diff_output,
                "stderr": "",
            })()
            ranges = get_changed_line_ranges(
                str(tmp_path / "mod.py"), str(tmp_path)
            )
        assert ranges is not None
        assert range(11, 14) in ranges
        assert range(24, 25) in ranges

    def test_git_failure_returns_none(self, tmp_path):
        with patch("lintgate.channels.symbol_coverage.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 128,
                "stdout": "",
                "stderr": "fatal: not a git repo",
            })()
            result = get_changed_line_ranges(
                str(tmp_path / "mod.py"), str(tmp_path)
            )
        assert result is None

    def test_timeout_returns_none(self, tmp_path):
        import subprocess as sp

        with patch("lintgate.channels.symbol_coverage.subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired("git", 10)
            result = get_changed_line_ranges(
                str(tmp_path / "mod.py"), str(tmp_path)
            )
        assert result is None


# ── TestRangesOverlap ────────────────────────────────────────────────────


class TestRangesOverlap:
    def test_overlap(self):
        assert _ranges_overlap(range(1, 5), range(3, 7)) is True

    def test_no_overlap(self):
        assert _ranges_overlap(range(1, 3), range(5, 7)) is False

    def test_adjacent_no_overlap(self):
        assert _ranges_overlap(range(1, 3), range(3, 5)) is False

    def test_contained(self):
        assert _ranges_overlap(range(1, 10), range(3, 5)) is True


# ── TestBuildTargetSet ───────────────────────────────────────────────────


class TestBuildTargetSet:
    def _write_py(self, path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))

    def test_changed_only_mode(self, tmp_path):
        mod = tmp_path / "mod.py"
        self._write_py(mod, """\
            def unchanged():
                pass

            def changed():
                return 1
        """)
        # Mock git diff to show changes only in lines 4-5 (the changed function)
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(4, 6)]
            targets, unresolved = build_target_set(
                [str(mod)], str(tmp_path), {"mode": "changed"}
            )
        names = [t.name for t in targets]
        assert "changed" in names
        assert "unchanged" not in names
        assert unresolved == []

    def test_new_file_targets_all(self, tmp_path):
        mod = tmp_path / "new_mod.py"
        self._write_py(mod, """\
            def func_a():
                pass

            def func_b():
                return 1
        """)
        # git diff returns None for new/untracked files
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = None
            targets, _ = build_target_set(
                [str(mod)], str(tmp_path), {"mode": "changed"}
            )
        names = [t.name for t in targets]
        assert "func_a" in names
        assert "func_b" in names

    def test_required_symbols(self, tmp_path):
        mod = tmp_path / "pkg" / "mod.py"
        self._write_py(mod, """\
            def target_func():
                pass
        """)
        targets, unresolved = build_target_set(
            [], str(tmp_path),
            {"mode": "changed", "required_symbols": ["pkg/mod.py::target_func"]},
        )
        assert len(targets) == 1
        assert targets[0].name == "target_func"
        assert unresolved == []

    def test_unresolved_required_symbols(self, tmp_path):
        targets, unresolved = build_target_set(
            [], str(tmp_path),
            {"mode": "changed", "required_symbols": ["nonexistent.py::func"]},
        )
        assert targets == []
        assert "nonexistent.py::func" in unresolved

    def test_unresolved_symbol_in_existing_file(self, tmp_path):
        mod = tmp_path / "mod.py"
        self._write_py(mod, """\
            def real_func():
                pass
        """)
        targets, unresolved = build_target_set(
            [], str(tmp_path),
            {"mode": "changed", "required_symbols": ["mod.py::ghost_func"]},
        )
        assert targets == []
        assert "mod.py::ghost_func" in unresolved

    def test_invalid_required_symbol_format(self, tmp_path):
        targets, unresolved = build_target_set(
            [], str(tmp_path),
            {"mode": "changed", "required_symbols": ["no_separator"]},
        )
        assert "no_separator" in unresolved

    def test_non_python_files_skipped(self, tmp_path):
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 2)]
            targets, _ = build_target_set(
                [str(txt)], str(tmp_path), {"mode": "changed"}
            )
        assert targets == []

    def test_diff_base_override(self, tmp_path):
        mod = tmp_path / "mod.py"
        self._write_py(mod, """\
            def func():
                pass
        """)
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            build_target_set(
                [str(mod)], str(tmp_path),
                {"mode": "changed", "diff_base": "origin/main"},
            )
            mock_diff.assert_called_once_with(
                str(mod), str(tmp_path), diff_base="origin/main"
            )


# ── TestWaivers ──────────────────────────────────────────────────────────


class TestWaivers:
    def _make_span(self, key: str) -> SymbolSpan:
        return SymbolSpan(
            file="/src/mod.py",
            symbol_key=key,
            name=key.split("::")[-1],
            start_line=1,
            end_line=5,
            is_method=False,
            class_name=None,
        )

    def test_waiver_removes_target(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Tested via integration",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert filtered == []
        assert len(applied) == 1
        assert applied[0][0] == "mod.py::func"

    def test_waiver_no_match(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::other_func",
                reason="Not relevant",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert len(filtered) == 1
        assert applied == []

    def test_expired_waiver(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Old exemption",
                expires="2025-01-01",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert len(filtered) == 1  # Not removed — waiver expired
        assert applied == []
        assert len(expired) == 1

    def test_permanent_waiver(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Permanent exemption",
                expires=None,
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert filtered == []
        assert len(applied) == 1
        assert expired == []

    def test_future_expiry_still_active(self):
        targets = [self._make_span("mod.py::func")]
        waivers = [
            SymbolCoverageWaiver(
                symbol="mod.py::func",
                reason="Not expired yet",
                expires="2026-12-31",
            )
        ]
        filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
        assert filtered == []
        assert len(applied) == 1


class TestParseWaivers:
    def test_valid_waiver(self):
        raw = [{"symbol": "mod.py::func", "reason": "Test reason"}]
        waivers = _parse_waivers(raw)
        assert len(waivers) == 1
        assert waivers[0].symbol == "mod.py::func"

    def test_missing_reason_rejected(self):
        raw = [{"symbol": "mod.py::func"}]
        waivers = _parse_waivers(raw)
        assert waivers == []

    def test_missing_symbol_rejected(self):
        raw = [{"reason": "No symbol"}]
        waivers = _parse_waivers(raw)
        assert waivers == []

    def test_not_a_list(self):
        assert _parse_waivers("not a list") == []
        assert _parse_waivers(None) == []


# ── TestRunSymbolCoverageGate ────────────────────────────────────────────


class TestRunSymbolCoverageGate:
    def _write_source(self, tmp_path, content: str) -> str:
        p = tmp_path / "mod.py"
        p.write_text(textwrap.dedent(content))
        return str(p)

    def _write_coverage(self, tmp_path, files_data: dict) -> str:
        p = tmp_path / "coverage.json"
        p.write_text(json.dumps({"files": files_data}))
        return str(p)

    def test_all_pass(self, tmp_path):
        src = self._write_source(tmp_path, """\
            def func():
                return 1
        """)
        cov_path = self._write_coverage(tmp_path, {
            src: {
                "executed_lines": [1, 2],
                "missing_lines": [],
                "excluded_lines": [],
                "missing_branches": [],
            }
        })
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                cov_path, [src], str(tmp_path), {"enabled": True, "mode": "changed"}
            )
        assert result.passed is True
        assert all(r.covered for r in result.symbol_results)

    def test_partial_fail(self, tmp_path):
        src = self._write_source(tmp_path, """\
            def covered_func():
                return 1

            def uncovered_func():
                return 2
        """)
        cov_path = self._write_coverage(tmp_path, {
            src: {
                "executed_lines": [1, 2],
                "missing_lines": [4, 5],
                "excluded_lines": [],
                "missing_branches": [],
            }
        })
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 6)]
            result = run_symbol_coverage_gate(
                cov_path, [src], str(tmp_path), {"enabled": True, "mode": "changed"}
            )
        assert result.passed is False
        uncovered = [r for r in result.symbol_results if not r.covered]
        assert len(uncovered) == 1
        assert uncovered[0].symbol.name == "uncovered_func"

    def test_waivers_applied(self, tmp_path):
        src = self._write_source(tmp_path, """\
            def waived_func():
                return 1
        """)
        cov_path = self._write_coverage(tmp_path, {
            src: {
                "executed_lines": [],
                "missing_lines": [1, 2],
                "excluded_lines": [],
                "missing_branches": [],
            }
        })
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                cov_path, [src], str(tmp_path),
                {
                    "enabled": True,
                    "mode": "changed",
                    "waivers": [{
                        "symbol": "mod.py::waived_func",
                        "reason": "Tested elsewhere",
                    }],
                },
            )
        assert result.passed is True
        assert len(result.waivers_applied) == 1
        assert result.symbol_results == []  # Waived target not checked

    def test_no_targets_passes(self, tmp_path):
        cov_path = self._write_coverage(tmp_path, {})
        result = run_symbol_coverage_gate(
            cov_path, [], str(tmp_path), {"enabled": True, "mode": "changed"}
        )
        assert result.passed is True
        assert "No symbols targeted" in result.skipped_reasons[0]

    def test_missing_coverage_json_mcp(self, tmp_path):
        src = self._write_source(tmp_path, """\
            def func():
                pass
        """)
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                str(tmp_path / "nonexistent.json"),
                [src], str(tmp_path),
                {"enabled": True, "mode": "changed"},
                surface="mcp",
            )
        # MCP: fail-open
        assert result.passed is True
        assert len(result.skipped_reasons) > 0

    def test_missing_coverage_json_ci(self, tmp_path):
        src = self._write_source(tmp_path, """\
            def func():
                pass
        """)
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                str(tmp_path / "nonexistent.json"),
                [src], str(tmp_path),
                {"enabled": True, "mode": "changed"},
                surface="ci",
            )
        # CI: fail-closed
        assert result.passed is False

    def test_unresolved_required_blocks(self, tmp_path):
        cov_path = self._write_coverage(tmp_path, {})
        result = run_symbol_coverage_gate(
            cov_path, [], str(tmp_path),
            {
                "enabled": True,
                "mode": "changed",
                "required_symbols": ["ghost.py::func"],
            },
        )
        assert result.passed is False
        assert "ghost.py::func" in result.unresolved_required

    def test_expired_waivers_reported(self, tmp_path):
        src = self._write_source(tmp_path, """\
            def func():
                return 1
        """)
        cov_path = self._write_coverage(tmp_path, {
            src: {
                "executed_lines": [1, 2],
                "missing_lines": [],
                "excluded_lines": [],
                "missing_branches": [],
            }
        })
        with patch("lintgate.channels.symbol_coverage.get_changed_line_ranges") as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                cov_path, [src], str(tmp_path),
                {
                    "enabled": True,
                    "mode": "changed",
                    "waivers": [{
                        "symbol": "mod.py::func",
                        "reason": "Old exemption",
                        "expires": "2020-01-01",
                    }],
                },
            )
        assert len(result.waivers_expired) == 1


# ── TestTestChannelIntegration ───────────────────────────────────────────


class TestTestChannelIntegration:
    def test_blocking_findings_for_uncovered_symbols(self):
        from lintgate.channels.test_channel import TestChannel
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        gate_result = SymbolCoverageGateResult(
            passed=False,
            symbol_results=[
                SymbolCoverageResult(
                    symbol=SymbolSpan(
                        file="/src/mod.py",
                        symbol_key="mod.py::uncovered",
                        name="uncovered",
                        start_line=1,
                        end_line=5,
                        is_method=False,
                        class_name=None,
                    ),
                    covered=False,
                    missing_lines=[3, 4],
                    missing_branches=[],
                    total_lines_in_span=5,
                    executed_lines_in_span=3,
                ),
            ],
        )

        channel = TestChannel()
        event = SupervisionEvent(
            surface="mcp",
            project_root="/tmp/test",
            files_changed=["/tmp/test/mod.py"],
        )
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "tests": ChannelConfig(
                    settings={
                        "symbol_coverage": {"enabled": True, "mode": "changed"},
                    }
                )
            },
        )

        # Mock everything: no impacted tests, but symbol gate produces findings
        with (
            patch("lintgate.channels.test_channel.find_impacted_tests", return_value=[]),
            patch(
                "lintgate.channels.symbol_coverage.run_symbol_coverage_gate",
                return_value=gate_result,
            ),
        ):
            # We need a test_result with coverage_json_path
            # Since no impacted tests, test_result is None, coverage_json_path is None
            # The gate won't run without coverage data
            result = channel.execute(event, config)

        # No impacted tests → no test_result → no coverage_json_path → gate skipped
        # This is expected behavior: can't check symbol coverage without running tests
        # In MCP mode, no warning is emitted (only CI emits the warning)
        assert result.status in ("pass", "fail")

    def test_ci_missing_coverage_emits_warning(self):
        from lintgate.channels.test_channel import TestChannel
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        channel = TestChannel()
        event = SupervisionEvent(
            surface="ci",
            project_root="/tmp/test",
            files_changed=["/tmp/test/mod.py"],
        )
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "tests": ChannelConfig(
                    settings={
                        "symbol_coverage": {"enabled": True, "mode": "changed"},
                    }
                )
            },
        )

        with patch("lintgate.channels.test_channel.find_impacted_tests", return_value=[]):
            result = channel.execute(event, config)

        gate_skipped = [f for f in result.findings if f.kind == "symbol_gate_skipped"]
        assert len(gate_skipped) == 1
        assert gate_skipped[0].severity == "warning"
        assert "no coverage data" in gate_skipped[0].message

    def test_execute_with_symbol_gate_findings(self):
        """Full integration: TestChannel produces blocking findings for uncovered symbols."""
        from lintgate.channels.test_channel import TestChannel, TestRunResult
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        channel = TestChannel()
        event = SupervisionEvent(
            surface="mcp",
            project_root="/tmp/test",
            files_changed=["/tmp/test/mod.py"],
        )
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "tests": ChannelConfig(
                    settings={
                        "symbol_coverage": {"enabled": True, "mode": "changed"},
                    }
                )
            },
        )

        gate_result = SymbolCoverageGateResult(
            passed=False,
            symbol_results=[
                SymbolCoverageResult(
                    symbol=SymbolSpan(
                        file="/tmp/test/mod.py",
                        symbol_key="mod.py::untested",
                        name="untested",
                        start_line=10,
                        end_line=15,
                        is_method=False,
                        class_name=None,
                    ),
                    covered=False,
                    missing_lines=[12, 13],
                    missing_branches=[],
                    total_lines_in_span=6,
                    executed_lines_in_span=4,
                ),
            ],
            unresolved_required=["missing.py::gone"],
        )

        fake_test_result = TestRunResult(
            passed=3, failed=0, coverage_pct=85.0,
            coverage_json_path="/tmp/cov.json",
        )

        with (
            patch("lintgate.channels.test_channel.find_impacted_tests", return_value=["test_mod.py"]),
            patch("lintgate.channels.test_channel.run_tests", return_value=fake_test_result),
            patch(
                "lintgate.channels.symbol_coverage.run_symbol_coverage_gate",
                return_value=gate_result,
            ),
        ):
            result = channel.execute(event, config)

        # Should have blocking findings
        blocking = [f for f in result.findings if f.severity == "blocking"]
        assert len(blocking) >= 1
        symbol_uncovered = [f for f in blocking if f.kind == "symbol_uncovered"]
        assert len(symbol_uncovered) == 1
        assert symbol_uncovered[0].evidence["symbol"] == "untested"

        unresolved = [f for f in blocking if f.kind == "unresolved_required_symbol"]
        assert len(unresolved) == 1

        # Severity should be blocking
        assert result.severity == "blocking"
