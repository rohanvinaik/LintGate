"""Tests for dead_code_checker linter."""

from __future__ import annotations

import ast
from unittest.mock import MagicMock, patch

from lintgate.linters.dead_code_checker import (
    DeadCodeChecker,
    _ast_dead_code_check,
    _classify_vulture_finding,
    _collect_definitions,
    _collect_references,
    _count_name_references,
    _get_all_exports,
    _is_function,
    _parse_file,
    _parse_vulture_output,
    _should_skip_definition,
    _suggestions_for_kind,
)
from lintgate.types import LinterContext

# ── _classify_vulture_finding ────────────────────────────────────────


def test_classify_import():
    assert _classify_vulture_finding("unused import 'os'") == "unused-import"


def test_classify_function():
    assert _classify_vulture_finding("unused function 'foo'") == "unused-function"


def test_classify_class():
    assert _classify_vulture_finding("unused class 'Bar'") == "unused-class"


def test_classify_variable():
    assert _classify_vulture_finding("unused variable 'x'") == "unused-variable"


def test_classify_attribute():
    assert _classify_vulture_finding("unused attribute 'y'") == "unused-attribute"


def test_classify_property():
    assert _classify_vulture_finding("unused property 'z'") == "unused-property"


def test_classify_unknown():
    assert _classify_vulture_finding("something else") == "dead-code"


# ── _suggestions_for_kind ────────────────────────────────────────────


def test_suggestions_unused_import():
    s = _suggestions_for_kind("unused-import")
    assert len(s) == 1
    assert "re-exported" in s[0]


def test_suggestions_unused_function():
    s = _suggestions_for_kind("unused-function")
    assert len(s) == 2


def test_suggestions_unused_class():
    s = _suggestions_for_kind("unused-class")
    assert len(s) == 2


def test_suggestions_unused_variable():
    s = _suggestions_for_kind("unused-variable")
    assert len(s) == 1


def test_suggestions_unused_attribute():
    s = _suggestions_for_kind("unused-attribute")
    assert len(s) == 2


def test_suggestions_default():
    s = _suggestions_for_kind("dead-code")
    assert len(s) == 1
    assert "maintenance" in s[0]


# ── _parse_vulture_output ────────────────────────────────────────────


def test_parse_vulture_output_basic():
    output = "foo.py:10: unused function 'bar' (80% confidence)\n"
    issues = list(_parse_vulture_output(output, "informational"))
    assert len(issues) == 1
    assert issues[0].kind == "unused-function"
    assert issues[0].file == "foo.py"
    assert issues[0].line == 10
    assert issues[0].confidence == 0.8


def test_parse_vulture_output_multiple():
    output = (
        "a.py:1: unused import 'os' (90% confidence)\nb.py:5: unused class 'Foo' (60% confidence)\n"
    )
    issues = list(_parse_vulture_output(output, "warning"))
    assert len(issues) == 2


def test_parse_vulture_output_no_match():
    output = "not a vulture line\n"
    issues = list(_parse_vulture_output(output, "informational"))
    assert issues == []


def test_parse_vulture_output_empty():
    issues = list(_parse_vulture_output("", "informational"))
    assert issues == []


# ── AST helpers ──────────────────────────────────────────────────────


def test_parse_file_valid(tmp_path):
    f = tmp_path / "good.py"
    f.write_text("x = 1\n")
    tree = _parse_file(str(f))
    assert tree is not None


def test_parse_file_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(\n")
    assert _parse_file(str(f)) is None


def test_parse_file_missing():
    assert _parse_file("/nonexistent/file.py") is None


def test_collect_definitions():
    src = "def foo(): pass\nclass Bar: pass\n"
    tree = ast.parse(src)
    defs = _collect_definitions(tree)
    assert "foo" in defs
    assert "Bar" in defs


def test_collect_definitions_skip_decorated():
    src = "@decorator\ndef foo(): pass\ndef bar(): pass\n"
    tree = ast.parse(src)
    defs = _collect_definitions(tree)
    assert "foo" not in defs
    assert "bar" in defs


def test_collect_references():
    src = "x = foo()\nbar.baz()\n"
    tree = ast.parse(src)
    refs = _collect_references(tree)
    assert "foo" in refs
    assert "bar" in refs


def test_get_all_exports_defined():
    src = "__all__ = ['foo', 'bar']\n"
    tree = ast.parse(src)
    exports = _get_all_exports(tree)
    assert exports == {"foo", "bar"}


def test_get_all_exports_not_defined():
    src = "x = 1\n"
    tree = ast.parse(src)
    assert _get_all_exports(tree) is None


def test_get_all_exports_tuple():
    src = "__all__ = ('foo',)\n"
    tree = ast.parse(src)
    exports = _get_all_exports(tree)
    assert exports == {"foo"}


def test_is_function_true():
    src = "def foo(): pass\nclass Bar: pass\n"
    tree = ast.parse(src)
    assert _is_function(tree, "foo") is True


def test_is_function_false():
    src = "def foo(): pass\nclass Bar: pass\n"
    tree = ast.parse(src)
    assert _is_function(tree, "Bar") is False


def test_count_name_references():
    src = "def foo(): pass\nfoo()\nfoo()\n"
    tree = ast.parse(src)
    assert _count_name_references(tree, "foo") == 2


def test_should_skip_private():
    tree = ast.parse("")
    assert _should_skip_definition("_private", None, set(), tree) is True


def test_should_skip_main():
    tree = ast.parse("")
    assert _should_skip_definition("main", None, set(), tree) is True


def test_should_skip_exported():
    tree = ast.parse("")
    assert _should_skip_definition("foo", {"foo"}, set(), tree) is True


def test_should_not_skip_unreferenced():
    tree = ast.parse("def foo(): pass\n")
    assert _should_skip_definition("foo", None, set(), tree) is False


# ── _ast_dead_code_check ─────────────────────────────────────────────


def test_ast_dead_code_finds_unused(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def unused_func(): pass\ndef used(): pass\nused()\n")
    issues = list(_ast_dead_code_check(str(f), "informational"))
    assert len(issues) == 1
    assert issues[0].kind == "unused-function"
    assert "unused_func" in issues[0].message


def test_ast_dead_code_skips_init(tmp_path):
    """__init__.py skip is in DeadCodeChecker.run(), not _ast_dead_code_check."""
    f = tmp_path / "__init__.py"
    f.write_text("def unused(): pass\n")
    # _ast_dead_code_check does NOT skip __init__.py; DeadCodeChecker.run() does
    issues = list(_ast_dead_code_check(str(f), "informational"))
    assert len(issues) == 1  # the low-level function reports it


def test_ast_dead_code_skips_private(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def _private(): pass\n")
    issues = list(_ast_dead_code_check(str(f), "informational"))
    assert issues == []


def test_ast_dead_code_bad_file():
    issues = list(_ast_dead_code_check("/nonexistent.py", "informational"))
    assert issues == []


# ── DeadCodeChecker class ────────────────────────────────────────────


def test_checker_always_available():
    assert DeadCodeChecker().available() is True


def test_checker_vulture_path(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    ctx = LinterContext(
        files=[str(f)],
        project_root=str(tmp_path),
        strictness="normal",
        config={},
    )
    checker = DeadCodeChecker()
    with (
        patch.object(checker, "_vulture_available", return_value=True),
        patch.object(checker, "_run_vulture", return_value=iter([])),
    ):
        list(checker.run(ctx))


def test_checker_ast_fallback(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def orphan(): pass\n")
    ctx = LinterContext(
        files=[str(f)],
        project_root=str(tmp_path),
        strictness="normal",
        config={},
    )
    checker = DeadCodeChecker()
    with patch.object(checker, "_vulture_available", return_value=False):
        issues = list(checker.run(ctx))
    assert len(issues) == 1


def test_checker_vulture_available_true():
    checker = DeadCodeChecker()
    with patch("shutil.which", return_value="/usr/bin/vulture"):
        assert checker._vulture_available() is True


def test_checker_vulture_available_false():
    checker = DeadCodeChecker()
    with patch("shutil.which", return_value=None):
        assert checker._vulture_available() is False


def test_checker_run_vulture_with_output(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    ctx = LinterContext(
        files=[str(f)],
        project_root=str(tmp_path),
        strictness="normal",
        config={"whitelist": ["allowed"], "ignore_decorators": ["@property"]},
    )
    checker = DeadCodeChecker()
    mock_result = MagicMock()
    mock_result.stdout = "a.py:1: unused variable 'x' (90% confidence)\n"
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._run_vulture(ctx, "informational", 60))
    assert len(issues) == 1


def test_checker_run_vulture_no_output(tmp_path):
    ctx = LinterContext(
        files=[],
        project_root=str(tmp_path),
        strictness="normal",
        config={},
    )
    checker = DeadCodeChecker()
    mock_result = MagicMock()
    mock_result.stdout = ""
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker._run_vulture(ctx, "informational", 60))
    assert issues == []
