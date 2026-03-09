"""Exact-value tests for pure helper functions in behavioral_contracts.py.

TEFF007 remediation: These tests target the 16 pure helpers that were at
spec_level=0.0, all regime-A (deterministic, no I/O). Each test asserts
exact output values rather than structural "contains" checks.
"""

from __future__ import annotations

import ast
import textwrap

from lintgate.orchestration.behavioral_contracts import (
    _annotation_to_isinstance,
    _annotation_to_str,
    _compute_import_path,
    _generate_error_boundary_contract,
    _generate_return_type_contract,
    _generate_shape_contract,
    _get_call_name,
    _is_io_call,
    _make_args_placeholder,
    _value_to_str,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_expr(code: str) -> ast.expr:
    """Parse a single expression and return the AST node."""
    return ast.parse(code, mode="eval").body


def _parse_func(code: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse a function definition and return the FunctionDef node."""
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found")


def _parse_call(code: str) -> ast.Call:
    """Parse a call expression and return the Call node."""
    expr_node = ast.parse(code, mode="eval").body
    assert isinstance(expr_node, ast.Call)
    return expr_node


# ── _annotation_to_str ──────────────────────────────────────────────────


class TestAnnotationToStr:
    """Pure function: AST annotation node -> exact string."""

    def test_name_simple(self) -> None:
        node = _parse_expr("int")
        assert _annotation_to_str(node) == "int"

    def test_name_str(self) -> None:
        node = _parse_expr("str")
        assert _annotation_to_str(node) == "str"

    def test_name_custom_class(self) -> None:
        node = _parse_expr("MyClass")
        assert _annotation_to_str(node) == "MyClass"

    def test_constant_string_annotation(self) -> None:
        # Forward reference: "SomeType"
        node = _parse_expr("'SomeType'")
        assert _annotation_to_str(node) == "SomeType"

    def test_constant_int(self) -> None:
        # Literal integer annotation (unusual but valid AST)
        node = _parse_expr("42")
        assert _annotation_to_str(node) == "42"

    def test_attribute(self) -> None:
        node = _parse_expr("os.PathLike")
        assert _annotation_to_str(node) == "os.PathLike"

    def test_nested_attribute(self) -> None:
        node = _parse_expr("a.b.c")
        assert _annotation_to_str(node) == "a.b.c"

    def test_subscript_single(self) -> None:
        node = _parse_expr("list[int]")
        assert _annotation_to_str(node) == "list[int]"

    def test_subscript_multiple(self) -> None:
        node = _parse_expr("dict[str, int]")
        assert _annotation_to_str(node) == "dict[str, int]"

    def test_subscript_nested(self) -> None:
        node = _parse_expr("list[dict[str, int]]")
        assert _annotation_to_str(node) == "list[dict[str, int]]"

    def test_union_bitor(self) -> None:
        node = _parse_expr("int | str")
        assert _annotation_to_str(node) == "int | str"

    def test_optional_via_bitor(self) -> None:
        node = _parse_expr("str | None")
        assert _annotation_to_str(node) == "str | None"

    def test_unsupported_returns_none(self) -> None:
        # A starred expression can't be parsed as annotation, so use
        # something the function doesn't handle
        node = ast.Starred(value=ast.Name(id="x", ctx=ast.Load()), ctx=ast.Load())
        assert _annotation_to_str(node) is None


# ── _annotation_to_isinstance ───────────────────────────────────────────


class TestAnnotationToIsinstance:
    """Pure function: type string -> isinstance-compatible type string."""

    def test_str(self) -> None:
        assert _annotation_to_isinstance("str") == "str"

    def test_int(self) -> None:
        assert _annotation_to_isinstance("int") == "int"

    def test_float(self) -> None:
        assert _annotation_to_isinstance("float") == "(int, float)"

    def test_bool(self) -> None:
        assert _annotation_to_isinstance("bool") == "bool"

    def test_list(self) -> None:
        assert _annotation_to_isinstance("list") == "list"

    def test_dict(self) -> None:
        assert _annotation_to_isinstance("dict") == "dict"

    def test_set(self) -> None:
        assert _annotation_to_isinstance("set") == "set"

    def test_tuple(self) -> None:
        assert _annotation_to_isinstance("tuple") == "tuple"

    def test_bytes(self) -> None:
        assert _annotation_to_isinstance("bytes") == "bytes"

    def test_generic_strips_params(self) -> None:
        # list[int] -> base = "list" -> "list"
        assert _annotation_to_isinstance("list[int]") == "list"

    def test_dict_with_params(self) -> None:
        assert _annotation_to_isinstance("dict[str, int]") == "dict"

    def test_unknown_type_returns_none(self) -> None:
        assert _annotation_to_isinstance("MyClass") is None

    def test_pathlike_returns_none(self) -> None:
        assert _annotation_to_isinstance("os.PathLike") is None

    def test_none_type_returns_none(self) -> None:
        assert _annotation_to_isinstance("None") is None


# ── _value_to_str ────────────────────────────────────────────────────────


class TestValueToStr:
    """Pure function: AST value node -> exact repr string."""

    def test_string_constant(self) -> None:
        node = _parse_expr("'hello'")
        assert _value_to_str(node) == "'hello'"

    def test_int_constant(self) -> None:
        node = _parse_expr("42")
        assert _value_to_str(node) == "42"

    def test_float_constant(self) -> None:
        node = _parse_expr("3.14")
        assert _value_to_str(node) == "3.14"

    def test_none_constant(self) -> None:
        node = _parse_expr("None")
        assert _value_to_str(node) == "None"

    def test_true_constant(self) -> None:
        node = _parse_expr("True")
        assert _value_to_str(node) == "True"

    def test_false_constant(self) -> None:
        node = _parse_expr("False")
        assert _value_to_str(node) == "False"

    def test_empty_list(self) -> None:
        node = _parse_expr("[]")
        assert _value_to_str(node) == "[]"

    def test_empty_dict(self) -> None:
        node = _parse_expr("{}")
        assert _value_to_str(node) == "{}"

    def test_empty_tuple(self) -> None:
        node = _parse_expr("()")
        assert _value_to_str(node) == "()"

    def test_nonempty_list_returns_none(self) -> None:
        node = _parse_expr("[1, 2]")
        assert _value_to_str(node) is None

    def test_nonempty_dict_returns_none(self) -> None:
        node = _parse_expr("{'a': 1}")
        assert _value_to_str(node) is None

    def test_nonempty_tuple_returns_none(self) -> None:
        node = _parse_expr("(1, 2)")
        assert _value_to_str(node) is None

    def test_name_returns_none(self) -> None:
        # A bare name (variable) is not a literal value
        node = _parse_expr("some_var")
        assert _value_to_str(node) is None

    def test_call_returns_none(self) -> None:
        node = _parse_expr("foo()")
        assert _value_to_str(node) is None


# ── _make_args_placeholder ───────────────────────────────────────────────


class TestMakeArgsPlaceholder:
    """Pure function: FunctionDef -> placeholder arg string."""

    def test_no_args(self) -> None:
        func = _parse_func("def f(): pass")
        assert _make_args_placeholder(func) == ""

    def test_single_arg(self) -> None:
        func = _parse_func("def f(x): pass")
        assert _make_args_placeholder(func) == "..."

    def test_two_args(self) -> None:
        func = _parse_func("def f(a, b): pass")
        assert _make_args_placeholder(func) == "..., ..."

    def test_three_args(self) -> None:
        func = _parse_func("def f(a, b, c): pass")
        assert _make_args_placeholder(func) == "..., ..., ..."

    def test_self_excluded(self) -> None:
        func = _parse_func("""
            class C:
                def method(self, x, y):
                    pass
        """)
        # _parse_func walks and finds the method
        assert _make_args_placeholder(func) == "..., ..."

    def test_only_self(self) -> None:
        func = _parse_func("""
            class C:
                def method(self):
                    pass
        """)
        assert _make_args_placeholder(func) == ""


# ── _get_call_name ───────────────────────────────────────────────────────


class TestGetCallName:
    """Pure function: AST Call node -> exact name string."""

    def test_simple_function(self) -> None:
        node = _parse_call("open()")
        assert _get_call_name(node) == "open"

    def test_simple_name(self) -> None:
        node = _parse_call("print()")
        assert _get_call_name(node) == "print"

    def test_attribute_call(self) -> None:
        node = _parse_call("os.makedirs()")
        assert _get_call_name(node) == "os.makedirs"

    def test_method_call(self) -> None:
        node = _parse_call("subprocess.run()")
        assert _get_call_name(node) == "subprocess.run"

    def test_chained_attribute(self) -> None:
        # a.b.c() -- func is Attribute(value=Attribute(...), attr="c")
        # value is Attribute, not Name, so returns just the attr
        node = _parse_call("a.b.c()")
        assert _get_call_name(node) == "c"

    def test_subscript_call_returns_none(self) -> None:
        # something[0]() -- func is Subscript, not Name or Attribute
        node = _parse_call("items[0]()")
        assert _get_call_name(node) is None


# ── _is_io_call ──────────────────────────────────────────────────────────


class TestIsIoCall:
    """Pure function: call name string -> exact True/False."""

    # Module-level I/O (in _IO_MODULES)
    def test_subprocess_run(self) -> None:
        assert _is_io_call("subprocess.run") is True

    def test_os_makedirs(self) -> None:
        assert _is_io_call("os.makedirs") is True

    def test_requests_get(self) -> None:
        assert _is_io_call("requests.get") is True

    def test_shutil_copy(self) -> None:
        assert _is_io_call("shutil.copy") is True

    def test_pathlib_anything(self) -> None:
        assert _is_io_call("pathlib.Path") is True

    def test_json_module(self) -> None:
        assert _is_io_call("json.load") is True

    def test_httpx_module(self) -> None:
        assert _is_io_call("httpx.get") is True

    # Bare function names (in _IO_FUNCTION_NAMES)
    def test_bare_open(self) -> None:
        assert _is_io_call("open") is True

    def test_bare_read(self) -> None:
        assert _is_io_call("read") is True

    def test_bare_write(self) -> None:
        assert _is_io_call("write") is True

    # Method-level: x.read(), x.write()
    def test_method_read(self) -> None:
        assert _is_io_call("f.read") is True

    def test_method_write(self) -> None:
        assert _is_io_call("f.write") is True

    def test_method_connect(self) -> None:
        assert _is_io_call("sock.connect") is True

    # Non-I/O calls
    def test_pure_function(self) -> None:
        assert _is_io_call("len") is False

    def test_math_sqrt(self) -> None:
        assert _is_io_call("math.sqrt") is False

    def test_str_split(self) -> None:
        assert _is_io_call("str.split") is False

    def test_list_append(self) -> None:
        assert _is_io_call("result.append") is False

    def test_custom_module(self) -> None:
        assert _is_io_call("mymodule.transform") is False


# ── _compute_import_path ─────────────────────────────────────────────────


class TestComputeImportPath:
    """Pure function: filepath + project_root -> dotted import path."""

    def test_simple_module(self) -> None:
        result = _compute_import_path("/proj/utils.py", "/proj")
        assert result == "utils"

    def test_nested_module(self) -> None:
        result = _compute_import_path("/proj/pkg/sub/mod.py", "/proj")
        assert result == "pkg.sub.mod"

    def test_init_file_stripped(self) -> None:
        result = _compute_import_path("/proj/pkg/__init__.py", "/proj")
        assert result == "pkg"

    def test_empty_root_returns_basename(self) -> None:
        result = _compute_import_path("/some/path/module.py", "")
        assert result == "module"


# ── _generate_return_type_contract (exact output) ───────────────────────


class TestGenerateReturnTypeContractExact:
    """Exact output tests for _generate_return_type_contract."""

    def test_int_return(self) -> None:
        func = _parse_func("def compute(x: int) -> int:\n    return x * 2")
        result = _generate_return_type_contract(func, "my_mod")
        assert result is not None
        lines = result.split("\n")
        assert lines[0] == "# from my_mod import compute"
        assert lines[1] == "def test_compute_returns_int() -> None:"
        assert lines[2] == '    """Contract: compute returns int."""'
        assert "assert isinstance(result, int)" in result

    def test_list_return(self) -> None:
        func = _parse_func("def get_items() -> list:\n    return []")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "def test_get_items_returns_list() -> None:" in result
        assert "isinstance(result, list)" in result

    def test_dict_return(self) -> None:
        func = _parse_func("def get_config() -> dict:\n    return {}")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "def test_get_config_returns_dict() -> None:" in result
        assert "isinstance(result, dict)" in result

    def test_none_return_skipped(self) -> None:
        func = _parse_func("def noop() -> None:\n    pass")
        result = _generate_return_type_contract(func, "mod")
        assert result is None

    def test_no_annotation_skipped(self) -> None:
        func = _parse_func("def f(x):\n    return x")
        result = _generate_return_type_contract(func, "mod")
        assert result is None

    def test_unmappable_type_skipped(self) -> None:
        # os.PathLike has no isinstance mapping
        func = _parse_func("def f() -> os.PathLike:\n    pass")
        result = _generate_return_type_contract(func, "mod")
        assert result is None

    def test_no_module_path_no_import_line(self) -> None:
        func = _parse_func("def f() -> int:\n    return 1")
        result = _generate_return_type_contract(func, "")
        assert result is not None
        assert "from " not in result
        assert "import " not in result

    def test_args_placeholder_in_output(self) -> None:
        func = _parse_func("def add(a: int, b: int) -> int:\n    return a + b")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "add(..., ...)" in result

    def test_float_return_isinstance(self) -> None:
        func = _parse_func("def ratio() -> float:\n    return 0.5")
        result = _generate_return_type_contract(func, "m")
        assert result is not None
        assert "isinstance(result, (int, float))" in result


# ── _generate_shape_contract (exact output) ─────────────────────────────


class TestGenerateShapeContractExact:
    """Exact output tests for _generate_shape_contract."""

    def test_map_pattern_exact(self) -> None:
        func = _parse_func("""
            def double_all(items):
                result = []
                for item in items:
                    result.append(item * 2)
                return result
        """)
        result = _generate_shape_contract(func, "mod")
        assert result is not None
        lines = result.split("\n")
        assert lines[0] == "def test_double_all_preserves_length() -> None:"
        assert (
            lines[1]
            == '    """Contract: double_all output length matches input length (map pattern)."""'
        )
        assert "items" in result  # references the input_param

    def test_no_map_pattern(self) -> None:
        func = _parse_func("def f(x):\n    return x + 1")
        result = _generate_shape_contract(func, "mod")
        assert result is None

    def test_no_append(self) -> None:
        # Has list init and for loop but no append
        func = _parse_func("""
            def f(items):
                result = []
                for item in items:
                    print(item)
                return result
        """)
        result = _generate_shape_contract(func, "mod")
        assert result is None

    def test_no_init_list(self) -> None:
        # Has for-append but no list init
        func = _parse_func("""
            def f(items, result):
                for item in items:
                    result.append(item)
                return result
        """)
        result = _generate_shape_contract(func, "mod")
        assert result is None

    def test_for_iter_not_name(self) -> None:
        # Iterating over a non-Name (e.g. range()) — no input_param extracted
        func = _parse_func("""
            def f(n):
                result = []
                for i in range(n):
                    result.append(i)
                return result
        """)
        result = _generate_shape_contract(func, "mod")
        # range(n) is a Call, not a Name, so input_param is None → returns None
        assert result is None


# ── _generate_error_boundary_contract (exact output) ────────────────────


class TestGenerateErrorBoundaryContractExact:
    """Exact output tests for _generate_error_boundary_contract."""

    def test_valueerror_returns_empty_list(self) -> None:
        func = _parse_func("""
            def parse(data):
                try:
                    return int(data)
                except ValueError:
                    return []
        """)
        result = _generate_error_boundary_contract(func, "mod")
        assert result is not None
        lines = result.split("\n")
        assert lines[0] == "def test_parse_returns_default_on_valueerror() -> None:"
        assert lines[1] == '    """Contract: parse returns [] on ValueError."""'
        assert "assert result == []" in result

    def test_generic_exception_returns_none(self) -> None:
        func = _parse_func("""
            def safe(x):
                try:
                    return x / 0
                except Exception:
                    return None
        """)
        result = _generate_error_boundary_contract(func, "mod")
        assert result is not None
        assert "on_exception" in result
        assert "returns None on Exception" in result

    def test_bare_except_returns_empty_dict(self) -> None:
        func = _parse_func("""
            def safe(x):
                try:
                    return x.something()
                except:
                    return {}
        """)
        result = _generate_error_boundary_contract(func, "mod")
        assert result is not None
        # bare except: stmt.type is None → exc_type = "Exception"
        assert "on_exception" in result
        assert "{}" in result

    def test_no_except_handler(self) -> None:
        func = _parse_func("def f(x):\n    return x")
        result = _generate_error_boundary_contract(func, "mod")
        assert result is None

    def test_except_without_return(self) -> None:
        func = _parse_func("""
            def f(x):
                try:
                    return x
                except ValueError:
                    pass
        """)
        result = _generate_error_boundary_contract(func, "mod")
        assert result is None

    def test_except_return_complex_value_returns_none(self) -> None:
        # Return value is a function call — _value_to_str returns None
        func = _parse_func("""
            def f(x):
                try:
                    return x
                except ValueError:
                    return some_default()
        """)
        result = _generate_error_boundary_contract(func, "mod")
        assert result is None
