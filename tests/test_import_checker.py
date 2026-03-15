"""Tests for lintgate.linters.import_checker — AST-based import verification."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.import_checker import (
    ImportChecker,
    _collect_guarded_import_lines,
    _collect_import_lines_from_stmts,
    _handler_catches_import_error,
)
from lintgate.types import LinterContext

# ── _handler_catches_import_error ─────────────────────────────────────


def _parse_handler(code: str) -> ast.ExceptHandler:
    """Parse a try/except and return the first except handler."""
    tree = ast.parse(textwrap.dedent(code))
    try_node = tree.body[0]
    assert isinstance(try_node, ast.Try)
    return try_node.handlers[0]


class TestHandlerCatchesImportError:
    def test_bare_except_catches_all(self):
        handler = _parse_handler(
            """\
            try:
                pass
            except:
                pass
            """
        )
        assert _handler_catches_import_error(handler) is True

    def test_import_error_name(self):
        handler = _parse_handler(
            """\
            try:
                pass
            except ImportError:
                pass
            """
        )
        assert _handler_catches_import_error(handler) is True

    def test_module_not_found_error_name(self):
        handler = _parse_handler(
            """\
            try:
                pass
            except ModuleNotFoundError:
                pass
            """
        )
        assert _handler_catches_import_error(handler) is True

    def test_tuple_with_import_error(self):
        handler = _parse_handler(
            """\
            try:
                pass
            except (ValueError, ImportError):
                pass
            """
        )
        assert _handler_catches_import_error(handler) is True

    def test_unrelated_exception_returns_false(self):
        handler = _parse_handler(
            """\
            try:
                pass
            except ValueError:
                pass
            """
        )
        assert _handler_catches_import_error(handler) is False

    def test_tuple_without_import_error_returns_false(self):
        handler = _parse_handler(
            """\
            try:
                pass
            except (ValueError, TypeError):
                pass
            """
        )
        assert _handler_catches_import_error(handler) is False


# ── _collect_import_lines_from_stmts ──────────────────────────────────


class TestCollectImportLinesFromStmts:
    def test_simple_import(self):
        tree = ast.parse("import os\nimport sys\n")
        lines = _collect_import_lines_from_stmts(tree.body)
        assert lines == {1, 2}

    def test_from_import(self):
        tree = ast.parse("from os import path\n")
        lines = _collect_import_lines_from_stmts(tree.body)
        assert lines == {1}

    def test_no_imports_returns_empty(self):
        tree = ast.parse("x = 1\ny = 2\n")
        lines = _collect_import_lines_from_stmts(tree.body)
        assert lines == set()


# ── _collect_guarded_import_lines ─────────────────────────────────────


class TestCollectGuardedImportLines:
    def test_guarded_import_in_try_body(self):
        code = textwrap.dedent("""\
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
        """)
        tree = ast.parse(code)
        guarded = _collect_guarded_import_lines(tree)
        assert 2 in guarded  # import tomllib
        assert 4 in guarded  # import tomli as tomllib

    def test_unguarded_try_except_not_included(self):
        code = textwrap.dedent("""\
            try:
                import tomllib
            except ValueError:
                pass
        """)
        tree = ast.parse(code)
        guarded = _collect_guarded_import_lines(tree)
        assert guarded == set()

    def test_no_try_blocks_returns_empty(self):
        code = "import os\nimport sys\n"
        tree = ast.parse(code)
        guarded = _collect_guarded_import_lines(tree)
        assert guarded == set()


# ── ImportChecker._module_exists ──────────────────────────────────────


class TestModuleExists:
    def setup_method(self):
        self.checker = ImportChecker()

    def test_stdlib_module_exists(self):
        assert self.checker._module_exists("os", "/nonexistent") is True

    def test_nonexistent_module(self):
        assert self.checker._module_exists("totally_fake_module_xyz_999", "/nonexistent") is False

    def test_local_module_file(self, tmp_path):
        (tmp_path / "mymod.py").write_text("x = 1\n")
        assert self.checker._module_exists("mymod", str(tmp_path)) is True

    def test_local_package_init(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        assert self.checker._module_exists("mypkg", str(tmp_path)) is True


# ── ImportChecker._check_file_imports ─────────────────────────────────


class TestCheckFileImports:
    def setup_method(self):
        self.checker = ImportChecker()

    def test_valid_imports_produce_no_issues(self, tmp_path):
        src = tmp_path / "good.py"
        src.write_text("import os\nimport sys\n")
        ctx = LinterContext(files=[str(src)], project_root=str(tmp_path))
        issues = list(self.checker._check_file_imports(str(src), ctx))
        assert issues == []

    def test_invalid_import_produces_issue(self, tmp_path):
        src = tmp_path / "bad.py"
        src.write_text("import nonexistent_module_xyz_999\n")
        ctx = LinterContext(files=[str(src)], project_root=str(tmp_path))
        issues = list(self.checker._check_file_imports(str(src), ctx))
        assert len(issues) == 1
        assert issues[0].kind == "unresolved-import"
        assert issues[0].linter == "import_checker"
        assert issues[0].severity == "warning"
        assert issues[0].confidence == 0.85
        assert "nonexistent_module_xyz_999" in issues[0].message

    def test_guarded_import_skipped(self, tmp_path):
        src = tmp_path / "compat.py"
        src.write_text(
            textwrap.dedent("""\
            try:
                import nonexistent_module_xyz_999
            except ImportError:
                pass
        """)
        )
        ctx = LinterContext(files=[str(src)], project_root=str(tmp_path))
        issues = list(self.checker._check_file_imports(str(src), ctx))
        assert issues == []

    def test_unresolved_from_import(self, tmp_path):
        src = tmp_path / "bad_from.py"
        src.write_text("from nonexistent_pkg_abc import thing\n")
        ctx = LinterContext(files=[str(src)], project_root=str(tmp_path))
        issues = list(self.checker._check_file_imports(str(src), ctx))
        assert len(issues) == 1
        assert issues[0].kind == "unresolved-import"
        assert "nonexistent_pkg_abc" in issues[0].message

    def test_nonexistent_file_yields_nothing(self, tmp_path):
        ctx = LinterContext(files=[], project_root=str(tmp_path))
        issues = list(self.checker._check_file_imports("/no/such/file.py", ctx))
        assert issues == []

    def test_syntax_error_file_yields_nothing(self, tmp_path):
        src = tmp_path / "broken.py"
        src.write_text("def f(:\n")
        ctx = LinterContext(files=[str(src)], project_root=str(tmp_path))
        issues = list(self.checker._check_file_imports(str(src), ctx))
        assert issues == []


# ── ImportChecker.run (integration) ───────────────────────────────────


class TestImportCheckerRun:
    def test_run_checks_all_files(self, tmp_path):
        good = tmp_path / "good.py"
        good.write_text("import os\n")
        bad = tmp_path / "bad.py"
        bad.write_text("import fake_module_zzz_123\n")
        ctx = LinterContext(files=[str(good), str(bad)], project_root=str(tmp_path))
        checker = ImportChecker()
        issues = list(checker.run(ctx))
        assert len(issues) == 1
        assert issues[0].file == str(bad)
