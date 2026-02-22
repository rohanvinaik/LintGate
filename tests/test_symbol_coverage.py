"""Tests for symbol coverage: canonicalization, AST extraction, coverage JSON parsing,
and symbol coverage checks.
"""

from __future__ import annotations

import json
import os
import textwrap

from lintgate.channels.symbol_coverage import (
    FileCoverage,
    SymbolSpan,
    _canonicalize_symbol_key,
    check_symbol_coverage,
    extract_symbol_spans,
    parse_coverage_json,
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
