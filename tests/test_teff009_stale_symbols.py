"""Tests for TEFF009 — stale test symbol detection.

Verifies that test_symbol_resolver correctly identifies tests that
reference deleted symbols and that the test channel emits TEFF009 findings.
"""

from __future__ import annotations

import textwrap

import pytest

from lintgate.channels.test_symbol_resolver import (
    _detect_project_packages,
    _extract_monkeypatch_target,
    _find_symbol_in_ast,
    _is_project_module,
    _symbol_exists,
    build_stale_test_findings,
    check_test_symbol_resolution,
)


@pytest.fixture()
def project_tree(tmp_path):
    """Create a minimal project tree with a package and test file."""
    # Create package: mypackage/
    pkg = tmp_path / "mypackage"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    # Create module: mypackage/core.py with some symbols
    (pkg / "core.py").write_text(
        textwrap.dedent("""\
        def existing_func():
            return 42

        class ExistingClass:
            pass

        EXISTING_VAR = "hello"
    """)
    )

    # Create subpackage: mypackage/utils/
    utils = pkg / "utils"
    utils.mkdir()
    (utils / "__init__.py").write_text("from .helpers import helper_func\n")
    (utils / "helpers.py").write_text(
        textwrap.dedent("""\
        def helper_func():
            return "help"
    """)
    )

    return tmp_path


class TestDetectProjectPackages:
    def test_finds_package_with_init(self, project_tree):
        packages = _detect_project_packages(str(project_tree))
        assert "mypackage" in packages

    def test_ignores_directories_without_init(self, project_tree):
        (project_tree / "notapackage").mkdir()
        packages = _detect_project_packages(str(project_tree))
        assert "notapackage" not in packages


class TestIsProjectModule:
    def test_project_module(self):
        assert _is_project_module("mypackage.core", {"mypackage"})

    def test_stdlib_module(self):
        assert not _is_project_module("os.path", {"mypackage"})

    def test_third_party_module(self):
        assert not _is_project_module("pytest.fixtures", {"mypackage"})


class TestFindSymbolInAst:
    def test_finds_function(self):
        import ast

        tree = ast.parse("def my_func(): pass")
        assert _find_symbol_in_ast(tree, "my_func")

    def test_finds_class(self):
        import ast

        tree = ast.parse("class MyClass: pass")
        assert _find_symbol_in_ast(tree, "MyClass")

    def test_finds_variable(self):
        import ast

        tree = ast.parse("MY_VAR = 42")
        assert _find_symbol_in_ast(tree, "MY_VAR")

    def test_finds_annotated_variable(self):
        import ast

        tree = ast.parse("MY_VAR: int = 42")
        assert _find_symbol_in_ast(tree, "MY_VAR")

    def test_finds_import_reexport(self):
        import ast

        tree = ast.parse("from .sub import my_func")
        assert _find_symbol_in_ast(tree, "my_func")

    def test_missing_symbol(self):
        import ast

        tree = ast.parse("def other_func(): pass")
        assert not _find_symbol_in_ast(tree, "my_func")


class TestSymbolExists:
    def test_existing_function(self, project_tree):
        assert _symbol_exists("mypackage.core", "existing_func", str(project_tree))

    def test_existing_class(self, project_tree):
        assert _symbol_exists("mypackage.core", "ExistingClass", str(project_tree))

    def test_existing_variable(self, project_tree):
        assert _symbol_exists("mypackage.core", "EXISTING_VAR", str(project_tree))

    def test_deleted_symbol(self, project_tree):
        assert not _symbol_exists("mypackage.core", "deleted_func", str(project_tree))

    def test_reexport_from_init(self, project_tree):
        assert _symbol_exists("mypackage.utils", "helper_func", str(project_tree))


class TestCheckTestSymbolResolution:
    def test_all_symbols_resolve(self, project_tree):
        """Test file that imports existing symbols → valid_failure verdict."""
        test_file = project_tree / "tests" / "test_core.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
            from mypackage.core import existing_func

            def test_it():
                assert existing_func() == 42
        """)
        )

        result = check_test_symbol_resolution(str(test_file), str(project_tree))
        assert result.verdict == "valid_failure"
        assert result.resolved_count == 1
        assert len(result.unresolved) == 0

    def test_deleted_symbol_detected(self, project_tree):
        """Test file that imports a deleted symbol → stale_test verdict."""
        test_file = project_tree / "tests" / "test_stale.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
            from mypackage.core import deleted_function

            def test_it():
                assert deleted_function() == 99
        """)
        )

        result = check_test_symbol_resolution(str(test_file), str(project_tree))
        assert result.verdict == "stale_test"
        assert result.confidence == 0.95
        assert len(result.unresolved) == 1
        assert result.unresolved[0].symbol == "deleted_function"
        assert result.unresolved[0].module == "mypackage.core"
        assert result.unresolved[0].source == "import"

    def test_mixed_resolved_and_unresolved(self, project_tree):
        """Test with both existing and deleted imports → stale_test verdict."""
        test_file = project_tree / "tests" / "test_mixed.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
            from mypackage.core import existing_func, removed_helper

            def test_it():
                pass
        """)
        )

        result = check_test_symbol_resolution(str(test_file), str(project_tree))
        assert result.verdict == "stale_test"
        assert result.resolved_count == 1
        assert len(result.unresolved) == 1
        assert result.unresolved[0].symbol == "removed_helper"

    def test_monkeypatch_string_target(self, project_tree):
        """Monkeypatch with string target referencing deleted symbol."""
        test_file = project_tree / "tests" / "test_mp.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
            def test_it(monkeypatch):
                monkeypatch.setattr("mypackage.core.deleted_func", lambda: 1)
        """)
        )

        result = check_test_symbol_resolution(str(test_file), str(project_tree))
        assert result.verdict == "stale_test"
        assert len(result.unresolved) == 1
        assert result.unresolved[0].source == "monkeypatch"
        assert result.unresolved[0].symbol == "deleted_func"

    def test_ignores_third_party_imports(self, project_tree):
        """Third-party imports should not be checked for symbol resolution."""
        test_file = project_tree / "tests" / "test_third.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
            from pytest import nonexistent_fixture

            def test_it():
                pass
        """)
        )

        result = check_test_symbol_resolution(str(test_file), str(project_tree))
        # Third-party import should be ignored, not flagged
        assert result.verdict == "valid_failure"
        assert len(result.unresolved) == 0

    def test_syntax_error_graceful(self, project_tree):
        """Test file with syntax error should not crash."""
        test_file = project_tree / "tests" / "test_broken.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text("def test_( broken syntax")

        result = check_test_symbol_resolution(str(test_file), str(project_tree))
        assert result.verdict == "valid_failure"


class TestBuildStaleTestFindings:
    def test_returns_findings_for_deleted_symbols(self, project_tree):
        test_file = project_tree / "tests" / "test_stale.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
            from mypackage.core import deleted_function

            def test_it():
                pass
        """)
        )

        findings = build_stale_test_findings(str(test_file), str(project_tree))
        assert len(findings) == 1
        assert findings[0]["symbol"] == "deleted_function"
        assert findings[0]["verdict"] == "stale_test"

    def test_returns_empty_for_valid_imports(self, project_tree):
        test_file = project_tree / "tests" / "test_valid.py"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
            from mypackage.core import existing_func

            def test_it():
                pass
        """)
        )

        findings = build_stale_test_findings(str(test_file), str(project_tree))
        assert len(findings) == 0


class TestExtractMonkeypatchTarget:
    """Test the monkeypatch.setattr pattern extraction."""

    def test_string_target_pattern(self):
        import ast
        from typing import cast

        code = 'monkeypatch.setattr("mypackage.core.my_func", lambda: 1)'
        tree = ast.parse(code)
        call = cast("ast.Expr", tree.body[0]).value  # The Call node
        result = _extract_monkeypatch_target(call, {"mypackage"})  # type: ignore[arg-type]  # .value is expr, actually Call
        assert result is not None
        assert result["module"] == "mypackage.core"
        assert result["symbol"] == "my_func"
        assert result["source"] == "monkeypatch"

    def test_non_project_module_ignored(self):
        import ast
        from typing import cast

        code = 'monkeypatch.setattr("os.path.exists", lambda: True)'
        tree = ast.parse(code)
        call = cast("ast.Expr", tree.body[0]).value
        result = _extract_monkeypatch_target(call, {"mypackage"})  # type: ignore[arg-type]  # .value is expr, actually Call
        assert result is None

    def test_non_setattr_ignored(self):
        import ast
        from typing import cast

        code = 'monkeypatch.delattr("mypackage.core.func")'
        tree = ast.parse(code)
        call = cast("ast.Expr", tree.body[0]).value
        result = _extract_monkeypatch_target(call, {"mypackage"})  # type: ignore[arg-type]  # .value is expr, actually Call
        assert result is None
