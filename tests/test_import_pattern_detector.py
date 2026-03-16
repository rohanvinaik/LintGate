"""Tests for lintgate.linters.import_pattern_detector — optional import detection."""

from __future__ import annotations

import ast
import textwrap
from typing import cast

from lintgate.linters.import_pattern_detector import (
    OptionalImport,
    OptionalImportReport,
    _catches_import_error,
    _find_fallback_assignment,
    detect_optional_imports,
    is_finding_on_guarded_import,
)

# ── detect_optional_imports ──────────────────────────────────────────


class TestDetectOptionalImports:
    """Tests for the main detection entry point."""

    def test_simple_try_import(self) -> None:
        src = textwrap.dedent("""\
            try:
                import foo
            except ImportError:
                foo = None
        """)
        report = detect_optional_imports(src)
        assert len(report.optional_imports) == 1
        assert report.optional_imports[0].module == "foo"
        assert report.optional_imports[0].names == ["foo"]
        assert 2 in report.guarded_lines
        assert "foo" in report.guarded_names

    def test_from_import_with_fallback(self) -> None:
        src = textwrap.dedent("""\
            try:
                from bar import baz
            except ImportError:
                baz = None
        """)
        report = detect_optional_imports(src)
        assert len(report.optional_imports) == 1
        assert report.optional_imports[0].module == "bar"
        assert report.optional_imports[0].names == ["baz"]
        assert "baz" in report.guarded_names
        assert "bar" in report.guarded_names  # top-level module also guarded

    def test_module_not_found_error(self) -> None:
        src = textwrap.dedent("""\
            try:
                import qux
            except ModuleNotFoundError:
                pass
        """)
        report = detect_optional_imports(src)
        assert len(report.optional_imports) == 1
        assert report.optional_imports[0].module == "qux"

    def test_syntax_error_returns_empty(self) -> None:
        report = detect_optional_imports("def ??? broken")
        assert report.optional_imports == []
        assert report.guarded_lines == set()
        assert report.guarded_names == set()

    def test_no_try_except_returns_empty(self) -> None:
        src = "import os\nimport sys\n"
        report = detect_optional_imports(src)
        assert report.optional_imports == []

    def test_try_except_not_import_error(self) -> None:
        src = textwrap.dedent("""\
            try:
                import foo
            except ValueError:
                pass
        """)
        report = detect_optional_imports(src)
        assert report.optional_imports == []


# ── _catches_import_error ────────────────────────────────────────────


class TestCatchesImportError:
    """Tests for handler-type detection."""

    def test_bare_except_returns_true(self) -> None:
        tree = ast.parse("try:\n    pass\nexcept:\n    pass\n")
        try_node = cast("ast.Try", tree.body[0])
        assert _catches_import_error(try_node) is True

    def test_import_error_handler(self) -> None:
        tree = ast.parse("try:\n    pass\nexcept ImportError:\n    pass\n")
        try_node = cast("ast.Try", tree.body[0])
        assert _catches_import_error(try_node) is True

    def test_tuple_handler_with_import_error(self) -> None:
        tree = ast.parse("try:\n    pass\nexcept (ImportError, ValueError):\n    pass\n")
        try_node = cast("ast.Try", tree.body[0])
        assert _catches_import_error(try_node) is True

    def test_value_error_only_returns_false(self) -> None:
        tree = ast.parse("try:\n    pass\nexcept ValueError:\n    pass\n")
        try_node = cast("ast.Try", tree.body[0])
        assert _catches_import_error(try_node) is False


# ── _find_fallback_assignment ────────────────────────────────────────


class TestFindFallbackAssignment:
    """Tests for fallback value detection in except bodies."""

    def test_finds_none_assignment(self) -> None:
        src = "try:\n    pass\nexcept ImportError:\n    foo = None\n"
        tree = ast.parse(src)
        handlers = cast("ast.Try", tree.body[0]).handlers
        result = _find_fallback_assignment("foo", handlers)
        assert result is not None
        assert "None" in result

    def test_no_matching_name_returns_none(self) -> None:
        src = "try:\n    pass\nexcept ImportError:\n    bar = None\n"
        tree = ast.parse(src)
        handlers = cast("ast.Try", tree.body[0]).handlers
        result = _find_fallback_assignment("foo", handlers)
        assert result is None

    def test_empty_handler_body(self) -> None:
        src = "try:\n    pass\nexcept ImportError:\n    pass\n"
        tree = ast.parse(src)
        handlers = cast("ast.Try", tree.body[0]).handlers
        result = _find_fallback_assignment("foo", handlers)
        assert result is None


# ── is_finding_on_guarded_import ─────────────────────────────────────


class TestIsFindingOnGuardedImport:
    """Tests for matching lint findings to guarded imports."""

    def test_empty_report_returns_false(self) -> None:
        report = OptionalImportReport()
        assert is_finding_on_guarded_import(10, "F821", "undefined name", report) is False

    def test_line_match(self) -> None:
        report = OptionalImportReport(
            optional_imports=[
                OptionalImport(module="foo", names=["foo"], line=5, fallback_value=None)
            ],
            guarded_lines={5},
            guarded_names={"foo"},
        )
        assert is_finding_on_guarded_import(5, "F821", "something", report) is True

    def test_name_in_message(self) -> None:
        report = OptionalImportReport(
            optional_imports=[
                OptionalImport(module="bar", names=["bar"], line=5, fallback_value=None)
            ],
            guarded_lines={5},
            guarded_names={"bar"},
        )
        assert is_finding_on_guarded_import(99, "F821", "bar is not defined", report) is True

    def test_no_match(self) -> None:
        report = OptionalImportReport(
            optional_imports=[
                OptionalImport(module="foo", names=["foo"], line=5, fallback_value=None)
            ],
            guarded_lines={5},
            guarded_names={"foo"},
        )
        assert is_finding_on_guarded_import(99, "F821", "baz is not defined", report) is False
