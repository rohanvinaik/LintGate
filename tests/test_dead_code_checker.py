"""Tests for dead_code_checker.py — all public and private functions.

Covers 16 functions across vulture parsing, AST fallback, classification,
and skip-logic with exact return-value assertions, parameter sensitivity,
and boundary conditions.
"""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.dead_code_checker import (
    DeadCodeChecker,
    _VULTURE_RE,
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


# ── helpers ──────────────────────────────────────────────────────────


def _tree(source: str) -> ast.Module:
    """Parse dedented source into an AST module."""
    return ast.parse(textwrap.dedent(source))


# ── DeadCodeChecker metadata ────────────────────────────────────────


class TestDeadCodeCheckerMetadata:
    def test_name(self):
        checker = DeadCodeChecker()
        assert checker.name == "dead_code_checker"

    def test_tier(self):
        checker = DeadCodeChecker()
        assert checker.tier == 3

    def test_timeout_ms(self):
        checker = DeadCodeChecker()
        assert checker.timeout_ms == 8000

    def test_required_tool_is_none(self):
        checker = DeadCodeChecker()
        assert checker.required_tool is None

    def test_available_always_true(self):
        checker = DeadCodeChecker()
        assert checker.available() is True
        assert checker.available("/nonexistent") is True


# ── _VULTURE_RE ──────────────────────────────────────────────────────


class TestVultureRegex:
    def test_matches_standard_line(self):
        line = "src/foo.py:42: unused function 'bar' (90% confidence)"
        m = _VULTURE_RE.match(line)
        assert m is not None
        assert m.group(1) == "src/foo.py"
        assert m.group(2) == "42"
        assert m.group(3) == "unused function 'bar'"
        assert m.group(4) == "90"

    def test_matches_import_line(self):
        line = "pkg/util.py:1: unused import 'os' (100% confidence)"
        m = _VULTURE_RE.match(line)
        assert m is not None
        assert m.group(3) == "unused import 'os'"
        assert m.group(4) == "100"

    def test_no_match_on_garbage(self):
        assert _VULTURE_RE.match("not a vulture line") is None

    def test_no_match_missing_confidence(self):
        assert _VULTURE_RE.match("foo.py:1: unused function 'bar'") is None


# ── _classify_vulture_finding ────────────────────────────────────────


class TestClassifyVultureFinding:
    def test_import(self):
        assert _classify_vulture_finding("unused import 'os'") == "unused-import"

    def test_function(self):
        assert _classify_vulture_finding("unused function 'bar'") == "unused-function"

    def test_class(self):
        assert _classify_vulture_finding("unused class 'Foo'") == "unused-class"

    def test_variable(self):
        assert _classify_vulture_finding("unused variable 'x'") == "unused-variable"

    def test_attribute(self):
        assert _classify_vulture_finding("unused attribute 'y'") == "unused-attribute"

    def test_property(self):
        assert _classify_vulture_finding("unused property 'z'") == "unused-property"

    def test_fallback(self):
        assert _classify_vulture_finding("something else entirely") == "dead-code"

    def test_case_insensitive(self):
        assert _classify_vulture_finding("Unused IMPORT 'os'") == "unused-import"
        assert _classify_vulture_finding("Unused FUNCTION 'f'") == "unused-function"

    def test_priority_import_over_function(self):
        # "import" checked first — if both keywords appear, import wins
        assert _classify_vulture_finding("unused import function") == "unused-import"


# ── _suggestions_for_kind ────────────────────────────────────────────


class TestSuggestionsForKind:
    def test_unused_import(self):
        result = _suggestions_for_kind("unused-import")
        assert len(result) == 1
        assert "import" in result[0].lower() or "re-exported" in result[0].lower()

    def test_unused_function(self):
        result = _suggestions_for_kind("unused-function")
        assert len(result) == 2

    def test_unused_class(self):
        result = _suggestions_for_kind("unused-class")
        assert len(result) == 2
        assert any("base class" in s.lower() for s in result)

    def test_unused_variable(self):
        result = _suggestions_for_kind("unused-variable")
        assert len(result) == 1
        assert "_" in result[0]

    def test_unused_attribute(self):
        result = _suggestions_for_kind("unused-attribute")
        assert len(result) == 2

    def test_unknown_kind_returns_default(self):
        result = _suggestions_for_kind("something-new")
        assert len(result) == 1
        assert "dead code" in result[0].lower()

    def test_dead_code_kind_returns_default(self):
        result = _suggestions_for_kind("dead-code")
        assert len(result) == 1
        assert "maintenance" in result[0].lower()


# ── _parse_vulture_output ────────────────────────────────────────────


class TestParseVultureOutput:
    def test_single_finding(self):
        output = "src/foo.py:10: unused function 'helper' (80% confidence)\n"
        issues = list(_parse_vulture_output(output, "informational"))
        assert len(issues) == 1
        issue = issues[0]
        assert issue.linter == "dead_code"
        assert issue.kind == "unused-function"
        assert issue.file == "src/foo.py"
        assert issue.line == 10
        assert issue.severity == "informational"
        assert issue.confidence == 0.8
        assert issue.evidence == {"vulture_confidence": 80}
        assert issue.message == "unused function 'helper'"

    def test_multiple_findings(self):
        output = (
            "a.py:1: unused import 'os' (100% confidence)\n"
            "b.py:5: unused class 'Foo' (60% confidence)\n"
        )
        issues = list(_parse_vulture_output(output, "warning"))
        assert len(issues) == 2
        assert issues[0].kind == "unused-import"
        assert issues[0].severity == "warning"
        assert issues[1].kind == "unused-class"
        assert issues[1].confidence == 0.6

    def test_empty_output(self):
        assert list(_parse_vulture_output("", "informational")) == []

    def test_non_matching_lines_skipped(self):
        output = "this is not vulture output\nalso not\n"
        assert list(_parse_vulture_output(output, "warning")) == []

    def test_mixed_valid_and_invalid_lines(self):
        output = (
            "garbage line\n"
            "foo.py:3: unused variable 'x' (70% confidence)\n"
            "another garbage\n"
        )
        issues = list(_parse_vulture_output(output, "informational"))
        assert len(issues) == 1
        assert issues[0].kind == "unused-variable"
        assert issues[0].confidence == 0.7

    def test_severity_passthrough(self):
        output = "f.py:1: unused function 'g' (90% confidence)\n"
        issue = list(_parse_vulture_output(output, "blocking"))[0]
        assert issue.severity == "blocking"

    def test_suggestions_attached(self):
        output = "f.py:1: unused import 'os' (100% confidence)\n"
        issue = list(_parse_vulture_output(output, "informational"))[0]
        assert issue.suggestions == _suggestions_for_kind("unused-import")


# ── _parse_file ──────────────────────────────────────────────────────


class TestParseFile:
    def test_nonexistent_file_returns_none(self):
        assert _parse_file("/nonexistent/path/to/file.py") is None

    def test_valid_python_file(self, tmp_path):
        p = tmp_path / "valid.py"
        p.write_text("x = 1\n")
        result = _parse_file(str(p))
        assert isinstance(result, ast.Module)

    def test_syntax_error_returns_none(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def foo(\n")
        assert _parse_file(str(p)) is None

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.py"
        p.write_text("")
        result = _parse_file(str(p))
        assert isinstance(result, ast.Module)
        assert result.body == []


# ── _collect_definitions ─────────────────────────────────────────────


class TestCollectDefinitions:
    def test_function_def(self):
        tree = _tree("def foo(): pass\ndef bar(): pass\n")
        defs = _collect_definitions(tree)
        assert "foo" in defs
        assert "bar" in defs
        assert defs["foo"] == 1
        assert defs["bar"] == 2

    def test_class_def(self):
        tree = _tree("class Foo: pass\n")
        defs = _collect_definitions(tree)
        assert "Foo" in defs

    def test_async_function_def(self):
        tree = _tree("async def afoo(): pass\n")
        defs = _collect_definitions(tree)
        assert "afoo" in defs

    def test_decorated_functions_excluded(self):
        tree = _tree("""\
            @decorator
            def decorated(): pass
            def plain(): pass
        """)
        defs = _collect_definitions(tree)
        assert "decorated" not in defs
        assert "plain" in defs

    def test_decorated_class_excluded(self):
        tree = _tree("""\
            @dataclass
            class MyData: pass
            class Plain: pass
        """)
        defs = _collect_definitions(tree)
        assert "MyData" not in defs
        assert "Plain" in defs

    def test_nested_definitions_ignored(self):
        tree = _tree("""\
            def outer():
                def inner():
                    pass
        """)
        defs = _collect_definitions(tree)
        assert "outer" in defs
        # inner is nested, not at module level
        assert "inner" not in defs

    def test_empty_module(self):
        tree = _tree("")
        assert _collect_definitions(tree) == {}

    def test_only_assignments(self):
        tree = _tree("x = 1\ny = 2\n")
        assert _collect_definitions(tree) == {}


# ── _collect_references ──────────────────────────────────────────────


class TestCollectReferences:
    def test_name_references(self):
        tree = _tree("x = foo()\nbar(x)\n")
        refs = _collect_references(tree)
        assert "foo" in refs
        assert "bar" in refs
        assert "x" in refs

    def test_attribute_references(self):
        tree = _tree("obj.method()\n")
        refs = _collect_references(tree)
        assert "obj" in refs

    def test_no_references_in_empty_module(self):
        tree = _tree("")
        assert _collect_references(tree) == set()

    def test_import_names_not_collected(self):
        # import statements use ast.alias, not ast.Name
        tree = _tree("import os\n")
        refs = _collect_references(tree)
        # 'os' is an alias target, not an ast.Name
        assert "os" not in refs

    def test_function_call_reference(self):
        tree = _tree("result = helper(1, 2)\n")
        refs = _collect_references(tree)
        assert "helper" in refs
        assert "result" in refs


# ── _get_all_exports ─────────────────────────────────────────────────


class TestGetAllExports:
    def test_list_all(self):
        tree = _tree("__all__ = ['foo', 'bar']\n")
        exports = _get_all_exports(tree)
        assert exports == {"foo", "bar"}

    def test_tuple_all(self):
        tree = _tree("__all__ = ('foo',)\n")
        exports = _get_all_exports(tree)
        assert exports == {"foo"}

    def test_no_all_returns_none(self):
        tree = _tree("x = 1\n")
        assert _get_all_exports(tree) is None

    def test_empty_all(self):
        tree = _tree("__all__ = []\n")
        exports = _get_all_exports(tree)
        assert exports == set()

    def test_non_string_elements_ignored(self):
        tree = _tree("__all__ = ['foo', 123, 'bar']\n")
        exports = _get_all_exports(tree)
        assert exports == {"foo", "bar"}

    def test_all_not_list_or_tuple(self):
        tree = _tree("__all__ = {'foo', 'bar'}\n")
        # Set literal is not List or Tuple, should return None
        assert _get_all_exports(tree) is None

    def test_all_after_other_statements(self):
        tree = _tree("""\
            x = 1
            __all__ = ['exported']
        """)
        exports = _get_all_exports(tree)
        assert exports == {"exported"}


# ── _count_name_references ───────────────────────────────────────────


class TestCountNameReferences:
    def test_zero_references(self):
        tree = _tree("def foo(): pass\n")
        assert _count_name_references(tree, "foo") == 0

    def test_one_reference(self):
        tree = _tree("def foo(): pass\nfoo()\n")
        assert _count_name_references(tree, "foo") == 1

    def test_multiple_references(self):
        tree = _tree("def foo(): pass\nfoo()\nx = foo\nfoo()\n")
        assert _count_name_references(tree, "foo") == 3

    def test_definition_not_counted(self):
        # The function name in the def statement uses ast.Store, not ast.Load
        tree = _tree("x = 1\n")
        # 'x' on the left side of assignment is Store context
        assert _count_name_references(tree, "x") == 0

    def test_nonexistent_name(self):
        tree = _tree("x = 1\n")
        assert _count_name_references(tree, "nonexistent") == 0


# ── _is_function ─────────────────────────────────────────────────────


class TestIsFunction:
    def test_function(self):
        tree = _tree("def foo(): pass\n")
        assert _is_function(tree, "foo") is True

    def test_async_function(self):
        tree = _tree("async def afoo(): pass\n")
        assert _is_function(tree, "afoo") is True

    def test_class_is_not_function(self):
        tree = _tree("class Foo: pass\n")
        assert _is_function(tree, "Foo") is False

    def test_nonexistent_name(self):
        tree = _tree("x = 1\n")
        assert _is_function(tree, "x") is False

    def test_nested_function_not_found(self):
        tree = _tree("""\
            def outer():
                def inner():
                    pass
        """)
        # inner is not at module level
        assert _is_function(tree, "inner") is False
        assert _is_function(tree, "outer") is True


# ── _should_skip_definition ──────────────────────────────────────────


class TestShouldSkipDefinition:
    def test_underscore_prefix_skipped(self):
        tree = _tree("def _helper(): pass\n")
        assert _should_skip_definition("_helper", None, set(), tree) is True

    def test_dunder_prefix_skipped(self):
        tree = _tree("def __init__(): pass\n")
        assert _should_skip_definition("__init__", None, set(), tree) is True

    def test_in_exported_names_skipped(self):
        tree = _tree("def foo(): pass\n")
        assert _should_skip_definition("foo", {"foo"}, set(), tree) is True

    def test_not_in_exported_names_not_skipped(self):
        tree = _tree("def foo(): pass\n")
        assert _should_skip_definition("foo", {"bar"}, set(), tree) is False

    def test_exported_names_none_not_skipped(self):
        tree = _tree("def foo(): pass\n")
        assert _should_skip_definition("foo", None, set(), tree) is False

    def test_main_always_skipped(self):
        tree = _tree("def main(): pass\n")
        assert _should_skip_definition("main", None, set(), tree) is True

    def test_referenced_name_with_load_context_skipped(self):
        tree = _tree("def foo(): pass\nfoo()\n")
        refs = _collect_references(tree)
        assert _should_skip_definition("foo", None, refs, tree) is True

    def test_unreferenced_public_name_not_skipped(self):
        tree = _tree("def orphan(): pass\nx = 1\n")
        refs = _collect_references(tree)
        assert _should_skip_definition("orphan", None, refs, tree) is False

    def test_name_in_refs_but_zero_load_references(self):
        # If name is in referenced set but _count_name_references returns 0,
        # the condition is `name in referenced and count > 0` → False → not skipped
        tree = _tree("x = 1\n")
        # 'x' appears in Store context, so _count_name_references('x') == 0
        # but if we put 'x' in the refs set manually:
        refs = {"x"}
        assert _should_skip_definition("x", None, refs, tree) is False


# ── _ast_dead_code_check ─────────────────────────────────────────────


class TestAstDeadCodeCheck:
    def test_unused_function_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def orphan():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 1
        assert issues[0].kind == "unused-function"
        assert issues[0].message == "'orphan' appears to be unused within this module"
        assert issues[0].confidence == 0.7
        assert issues[0].severity == "informational"
        assert issues[0].linter == "dead_code"

    def test_unused_class_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("class Orphan:\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "warning"))
        assert len(issues) == 1
        assert issues[0].kind == "unused-class"
        assert issues[0].severity == "warning"

    def test_used_function_not_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def helper():\n    pass\nhelper()\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 0

    def test_private_function_not_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def _private():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 0

    def test_main_function_not_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def main():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 0

    def test_decorated_function_not_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("@decorator\ndef decorated():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 0

    def test_exported_in_all_not_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("__all__ = ['foo']\ndef foo():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 0

    def test_syntax_error_file_no_crash(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def foo(\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert issues == []

    def test_nonexistent_file_no_crash(self):
        issues = list(_ast_dead_code_check("/nonexistent/file.py", "informational"))
        assert issues == []

    def test_multiple_unused_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def alpha():\n    pass\ndef beta():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 2
        names = {i.message for i in issues}
        assert "'alpha' appears to be unused within this module" in names
        assert "'beta' appears to be unused within this module" in names

    def test_severity_passthrough(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def orphan():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "blocking"))
        assert issues[0].severity == "blocking"

    def test_file_path_in_issue(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def orphan():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert issues[0].file == str(p)

    def test_line_number_correct(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("x = 1\n\ndef orphan():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert issues[0].line == 3

    def test_async_function_reported(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("async def unused_coro():\n    pass\n")
        issues = list(_ast_dead_code_check(str(p), "informational"))
        assert len(issues) == 1
        assert issues[0].kind == "unused-function"
