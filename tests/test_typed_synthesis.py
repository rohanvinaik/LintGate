"""Tests for lintgate/testing/typed_synthesis.py.

Covers value synthesis from type annotations, dataclass resolution,
factory generation, annotation parsing, and post-generation validation.
"""

from __future__ import annotations

from lintgate.testing.typed_synthesis import (
    _parse_annotation_str,
    synthesize_factory,
    synthesize_value,
    validate_test_file,
)

# ── _parse_annotation_str ────────────────────────────────────────


class TestParseAnnotationStr:
    def test_simple_name(self):
        base, args = _parse_annotation_str("str")
        assert base == "str"
        assert args == []

    def test_list_subscript(self):
        base, args = _parse_annotation_str("list[int]")
        assert base == "list"
        assert args == ["int"]

    def test_dict_subscript(self):
        base, args = _parse_annotation_str("dict[str, int]")
        assert base == "dict"
        assert args == ["str", "int"]

    def test_optional_union(self):
        base, args = _parse_annotation_str("str | None")
        assert base == "str"

    def test_ast_dump_name(self):
        base, args = _parse_annotation_str("Name(id='LintIssue')")
        assert base == "LintIssue"
        assert args == []

    def test_empty_string(self):
        base, args = _parse_annotation_str("")
        assert base == ""

    def test_none_literal(self):
        base, _ = _parse_annotation_str("None")
        assert base == "None"

    def test_list_of_dataclass(self):
        base, args = _parse_annotation_str("list[LintIssue]")
        assert base == "list"
        assert args == ["LintIssue"]


# ── synthesize_value ─────────────────────────────────────────────


class TestSynthesizeValue:
    def test_str(self):
        v = synthesize_value("str")
        assert v.code == '""'
        assert v.imports == []
        assert v.is_placeholder is False
        assert v.type_name == "str"

    def test_int(self):
        v = synthesize_value("int")
        assert v.code == "0"
        assert v.is_placeholder is False

    def test_float(self):
        v = synthesize_value("float")
        assert v.code == "0.0"

    def test_bool(self):
        v = synthesize_value("bool")
        assert v.code == "False"

    def test_none_type(self):
        v = synthesize_value("None")
        assert v.code == "None"
        assert v.is_placeholder is False

    def test_empty_annotation_fallback(self):
        v = synthesize_value("")
        assert v.is_placeholder is True

    def test_list_empty(self):
        v = synthesize_value("list")
        assert v.code == "[]"
        assert v.is_placeholder is False

    def test_dict_empty(self):
        v = synthesize_value("dict")
        assert v.code == "{}"

    def test_set_empty(self):
        v = synthesize_value("set")
        assert v.code == "set()"

    def test_optional_resolves_inner(self):
        v = synthesize_value("Optional", "name")
        # Optional with no args falls back to None
        assert v.code == "None"

    def test_any_is_placeholder(self):
        v = synthesize_value("Any")
        assert v.is_placeholder is True

    def test_name_heuristic_path(self):
        v = synthesize_value("", "file_path")
        assert "test" in v.code or "py" in v.code

    def test_name_heuristic_count(self):
        v = synthesize_value("", "item_count")
        assert v.code == "0"

    def test_name_heuristic_flag(self):
        v = synthesize_value("", "is_enabled")
        assert v.code == "False"

    def test_str_with_name_heuristic(self):
        v = synthesize_value("str", "file_path")
        assert "test" in v.code  # name heuristic produces better default

    def test_dataclass_lint_issue(self):
        v = synthesize_value("LintIssue", "", "lintgate.types")
        assert v.is_placeholder is False
        assert "LintIssue(" in v.code
        assert "linter=" in v.code
        assert "kind=" in v.code
        assert len(v.imports) == 1
        assert "from lintgate.types import LintIssue" in v.imports[0]

    def test_dataclass_linter_result(self):
        v = synthesize_value("LinterResult", "", "lintgate.types")
        assert v.is_placeholder is False
        assert "LinterResult(" in v.code
        assert "linter_name=" in v.code

    def test_list_of_dataclass(self):
        v = synthesize_value("list[LintIssue]", "", "lintgate.types")
        assert v.is_placeholder is False
        assert "LintIssue(" in v.code
        assert v.code.startswith("[")
        assert v.code.endswith("]")

    def test_unknown_type_is_placeholder(self):
        v = synthesize_value("SomeUnknownType", "", "")
        assert v.is_placeholder is True

    def test_union_with_none_uses_non_none(self):
        v = synthesize_value("str | None")
        assert v.code == '""'
        assert v.type_name == "str"


# ── synthesize_factory ───────────────────────────────────────────


class TestSynthesizeFactory:
    def test_lint_issue_factory(self):
        result = synthesize_factory("LintIssue", "lintgate.types")
        assert result is not None
        code, imports = result
        assert "def _issue(" in code
        assert "linter=" in code
        assert "kind=" in code
        assert "-> LintIssue:" in code
        assert any("LintIssue" in imp for imp in imports)

    def test_linter_result_factory(self):
        result = synthesize_factory("LinterResult", "lintgate.types")
        assert result is not None
        code, imports = result
        assert "def _make_linterresult(" in code
        assert "linter_name=" in code

    def test_unknown_type_returns_none(self):
        result = synthesize_factory("CompletelyUnknownType", "")
        assert result is None


# ── validate_test_file ───────────────────────────────────────────


class TestValidateTestFile:
    def test_valid_test(self):
        code = """
def test_foo():
    assert 1 == 1
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_syntax_error(self):
        valid, errors = validate_test_file("def test_foo(:\n")
        assert valid is False
        assert any("SyntaxError" in e for e in errors)

    def test_pass_only_body(self):
        code = """
def test_foo():
    pass
"""
        valid, errors = validate_test_file(code)
        assert valid is False
        assert any("empty body" in e for e in errors)

    def test_no_assert(self):
        code = """
def test_foo():
    x = 1 + 2
"""
        valid, errors = validate_test_file(code)
        assert valid is False
        assert any("no assert" in e for e in errors)

    def test_non_test_function_ignored(self):
        code = """
def helper():
    pass

def test_foo():
    assert True
"""
        valid, errors = validate_test_file(code)
        assert valid is True

    def test_pytest_raises_counts_as_assert(self):
        code = """
import pytest

def test_foo():
    with pytest.raises(ValueError):
        raise ValueError()
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_multiple_tests_mixed(self):
        code = """
def test_good():
    assert 1 == 1

def test_bad():
    pass
"""
        valid, errors = validate_test_file(code)
        assert valid is False
        assert len(errors) == 2  # empty body + no assert
        assert all("test_bad" in e for e in errors)

    # ── Undefined name detection ──────────────────────────────────

    def test_undefined_name_detected(self):
        """Catches 'd = obj.to_dict()' when obj is never defined."""
        code = """
def test_foo():
    d = obj.to_dict()
    assert d["x"] == 1
"""
        valid, errors = validate_test_file(code)
        assert valid is False
        assert any("undefined name 'obj'" in e for e in errors)

    def test_imported_name_not_flagged(self):
        code = """
from os.path import join

def test_join():
    result = join("a", "b")
    assert result == "a/b"
"""
        valid, errors = validate_test_file(code)
        assert valid is True

    def test_locally_assigned_name_not_flagged(self):
        code = """
def test_foo():
    obj = {"x": 1}
    d = obj.copy()
    assert d["x"] == 1
"""
        valid, errors = validate_test_file(code)
        assert valid is True

    def test_for_target_not_flagged(self):
        code = """
def test_foo():
    items = [1, 2, 3]
    for x in items:
        assert x > 0
"""
        valid, errors = validate_test_file(code)
        assert valid is True

    def test_with_target_not_flagged(self):
        code = """
import pytest

def test_foo():
    with pytest.raises(ValueError) as exc_info:
        raise ValueError("boom")
    assert "boom" in str(exc_info.value)
"""
        valid, errors = validate_test_file(code)
        assert valid is True

    def test_builtin_not_flagged(self):
        code = """
def test_foo():
    x = len([1, 2, 3])
    assert x == 3
"""
        valid, errors = validate_test_file(code)
        assert valid is True

    def test_function_param_not_flagged(self):
        code = """
import pytest

@pytest.fixture
def my_fixture():
    return 42

def test_foo(my_fixture):
    assert my_fixture == 42
"""
        valid, errors = validate_test_file(code)
        assert valid is True

    def test_duplicate_test_name_detected(self):
        code = """
def test_dup():
    assert True

def test_dup():
    assert True
"""
        valid, errors = validate_test_file(code)
        assert valid is False
        assert any("duplicate test name" in e for e in errors)

    def test_runtime_validation_catches_crashing_test(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def build_value():\n    return 0\n")
        code = """
from mod import build_value

def test_runtime_crash():
    obj = build_value()
    d = obj.to_dict()
    assert d["x"] == 1
"""
        valid, errors = validate_test_file(
            code,
            project_root=str(tmp_path),
            run_pytest=True,
        )
        assert valid is False
        assert any("test_runtime_crash" in e for e in errors)

    # ── Additional branch coverage for validate_test_file ────────────

    def test_docstring_only_body_detected(self):
        """A test with only a docstring (no assert, no pass) → 'no assert'."""
        code = '''
def test_docs_only():
    """This test does nothing."""
'''
        valid, errors = validate_test_file(code)
        assert valid is False
        assert any("no assert" in e for e in errors)

    def test_multiple_errors_from_multiple_tests(self):
        """Each bad test produces its own error(s); errors accumulate."""
        code = """
def test_a():
    pass

def test_b():
    x = undeclared_name
    assert x
"""
        valid, errors = validate_test_file(code)
        assert valid is False
        # test_a: empty body + no assert; test_b: undefined name
        assert any("test_a" in e and "empty body" in e for e in errors)
        assert any("test_a" in e and "no assert" in e for e in errors)
        assert any("test_b" in e and "undefined name 'undeclared_name'" in e for e in errors)

    def test_run_pytest_false_skips_runtime(self):
        """run_pytest=False (default) → no runtime validation even with project_root."""
        code = """
def test_ok():
    assert 1 == 1
"""
        valid, errors = validate_test_file(code, project_root="/tmp", run_pytest=False)
        assert valid is True
        assert errors == []

    def test_run_pytest_true_without_project_root_skips_runtime(self):
        """run_pytest=True but no project_root → runtime validation skipped."""
        code = """
def test_ok():
    assert 1 == 1
"""
        valid, errors = validate_test_file(code, project_root=None, run_pytest=True)
        assert valid is True
        assert errors == []

    def test_duplicate_names_exact_error_format(self):
        """Duplicate test name error includes line number and exact message."""
        code = """
def test_dup():
    assert True

def test_dup():
    assert False
"""
        valid, errors = validate_test_file(code)
        assert valid is False
        assert len(errors) == 1
        assert "test_dup" in errors[0]
        assert "duplicate test name" in errors[0]
        assert "line" in errors[0]

    def test_syntax_error_exact_return_shape(self):
        """SyntaxError → (False, ['SyntaxError: ...']) with exactly one error."""
        valid, errors = validate_test_file("def test_broken(:\n")
        assert valid is False
        assert len(errors) == 1
        assert errors[0].startswith("SyntaxError:")

    def test_assert_in_nested_if_still_counts(self):
        """Assert inside an if block still counts as having an assertion."""
        code = """
def test_conditional():
    x = 42
    if x > 0:
        assert x == 42
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_walrus_operator_target_not_flagged(self):
        """Named expression (walrus) target should not be flagged as undefined."""
        code = """
def test_walrus():
    items = [1, 2, 3]
    if (n := len(items)) > 0:
        assert n == 3
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_module_level_assignment_name_not_flagged(self):
        """Module-level assigned names are available to tests."""
        code = """
CONSTANT = 42

def test_uses_constant():
    assert CONSTANT == 42
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_tuple_unpack_assignment_names_not_flagged(self):
        """Tuple unpack at module level makes all names available."""
        code = """
A, B = 1, 2

def test_tuple_unpack():
    assert A == 1
    assert B == 2
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_class_level_test_ignored(self):
        """Tests inside a class are walked but class test_ functions at
        module level are what get checked."""
        code = """
class TestGroup:
    def test_inner(self):
        assert True

def test_top():
    assert True
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_comprehension_target_not_flagged(self):
        """Comprehension variable should not be flagged as undefined."""
        code = """
def test_comprehension():
    result = [x * 2 for x in range(5)]
    assert len(result) == 5
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []

    def test_valid_returns_true_empty_errors(self):
        """Exact return shape for a valid test file."""
        code = """
def test_simple():
    assert 1 + 1 == 2
"""
        valid, errors = validate_test_file(code)
        assert valid is True
        assert errors == []
        assert isinstance(valid, bool)
        assert isinstance(errors, list)


# ── Additional synthesize_value branch coverage ──────────────────


class TestSynthesizeValueAdditional:
    """Cover remaining branches in synthesize_value."""

    def test_nonetype_string(self):
        """'NoneType' string treated same as 'None'."""
        v = synthesize_value("NoneType")
        assert v.code == "None"
        assert v.is_placeholder is False
        assert v.type_name == "None"

    def test_tuple_type(self):
        v = synthesize_value("tuple")
        assert v.code == "()"
        assert v.is_placeholder is False
        assert v.type_name == "tuple"

    def test_bytes_primitive(self):
        v = synthesize_value("bytes")
        assert v.code == 'b""'
        assert v.is_placeholder is False
        assert v.type_name == "bytes"

    def test_list_with_primitive_arg(self):
        """list[int] → empty list (inner is primitive, not dataclass)."""
        v = synthesize_value("list[int]")
        assert v.code == "[]"
        assert v.is_placeholder is False
        assert v.type_name == "list"

    def test_optional_with_inner_type(self):
        """Optional[str] resolves to the inner str."""
        v = synthesize_value("Optional[str]")
        assert v.code == '""'
        assert v.is_placeholder is False
        assert v.type_name == "str"

    def test_set_type(self):
        v = synthesize_value("Set")
        assert v.code == "set()"
        assert v.type_name == "set"

    def test_dict_type(self):
        v = synthesize_value("Dict")
        assert v.code == "{}"
        assert v.type_name == "dict"

    def test_list_type_alias(self):
        v = synthesize_value("List")
        assert v.code == "[]"
        assert v.type_name == "list"

    def test_fallback_no_name(self):
        """Empty annotation + empty param_name → None placeholder."""
        v = synthesize_value("", "")
        assert v.code == "None"
        assert v.is_placeholder is True
        assert v.type_name == "unknown"

    def test_fallback_dir_param(self):
        """Fallback heuristic for 'dir' in param name."""
        v = synthesize_value("", "output_dir")
        assert v.code == '"test.py"'
        assert v.is_placeholder is True

    def test_str_with_message_param(self):
        """str annotation + 'message' param_name → name heuristic."""
        v = synthesize_value("str", "error_message")
        assert v.code == '"test message"'

    def test_str_with_linter_param(self):
        """str annotation + 'linter' param_name → 'name' heuristic wins
        because _default_for_field checks 'name' before 'linter'."""
        v = synthesize_value("str", "linter_name")
        # 'name' check fires first in _default_for_field → '"test"'
        assert v.code == '"test"'

    def test_str_with_pure_linter_param(self):
        """str annotation + 'linter' (no 'name' substring) → 'ruff'."""
        v = synthesize_value("str", "linter")
        assert v.code == '"ruff"'
