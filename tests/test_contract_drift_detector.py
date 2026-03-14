"""Tests for lintgate/channels/contract_drift_detector.py."""

from __future__ import annotations

import os
import textwrap

from lintgate.channels.contract_drift_detector import (
    AffectedTestSite,
    ContractDriftResult,
    SignatureChange,
    _arity_from_annotation,
    _build_advisory,
    _extract_function_params,
    _extract_function_return_arities,
    _filepath_to_module,
    _find_call_sites,
    _find_function_line,
    _find_unpack_mismatches,
    _get_call_name,
    analyze_contract_drift,
    detect_param_changes,
    detect_return_arity_change,
    find_affected_test_sites,
)

import ast


# ── _arity_from_annotation ─────────────────────────────────────────────


def test_arity_from_annotation_tuple_3():
    src = "def foo() -> tuple[int, str, bool]: pass"
    tree = ast.parse(src)
    func = tree.body[0]
    assert _arity_from_annotation(func) == 3


def test_arity_from_annotation_no_annotation():
    src = "def foo(): pass"
    tree = ast.parse(src)
    func = tree.body[0]
    assert _arity_from_annotation(func) is None


def test_arity_from_annotation_non_tuple():
    src = "def foo() -> int: pass"
    tree = ast.parse(src)
    func = tree.body[0]
    assert _arity_from_annotation(func) is None


# ── _extract_function_return_arities ────────────────────────────────────


def test_extract_function_return_arities_annotation():
    src = textwrap.dedent("""\
        def two() -> tuple[int, str]:
            return 1, "a"
        def scalar() -> int:
            return 42
    """)
    tree = ast.parse(src)
    arities = _extract_function_return_arities(tree)
    assert arities == {"two": 2}


def test_extract_function_return_arities_tuple_return():
    src = textwrap.dedent("""\
        def pair():
            return (1, 2)
    """)
    tree = ast.parse(src)
    arities = _extract_function_return_arities(tree)
    assert arities == {"pair": 2}


# ── _extract_function_params ───────────────────────────────────────────


def test_extract_function_params_basic():
    src = textwrap.dedent("""\
        def foo(self, x, y, z=3):
            pass
    """)
    tree = ast.parse(src)
    params = _extract_function_params(tree)
    assert params["foo"] == {"x", "y", "z"}


def test_extract_function_params_varargs():
    src = textwrap.dedent("""\
        def bar(*args, **kwargs):
            pass
    """)
    tree = ast.parse(src)
    params = _extract_function_params(tree)
    assert "*args" in params["bar"]
    assert "**kwargs" in params["bar"]


# ── _find_function_line ────────────────────────────────────────────────


def test_find_function_line_found():
    src = "x = 1\ndef target():\n    pass\n"
    tree = ast.parse(src)
    assert _find_function_line(tree, "target") == 2


def test_find_function_line_not_found():
    src = "x = 1\n"
    tree = ast.parse(src)
    assert _find_function_line(tree, "nope") == 0


# ── _filepath_to_module ────────────────────────────────────────────────


def test_filepath_to_module_basic():
    result = _filepath_to_module("lintgate/channels/foo.py")
    # Should produce a dotted module path
    assert "lintgate" in result
    assert "channels" in result


# ── _get_call_name ─────────────────────────────────────────────────────


def test_get_call_name_simple():
    src = "foo()"
    tree = ast.parse(src)
    call = tree.body[0].value
    assert _get_call_name(call) == "foo"


def test_get_call_name_attribute():
    src = "obj.method()"
    tree = ast.parse(src)
    call = tree.body[0].value
    assert _get_call_name(call) == "obj.method"


def test_get_call_name_nested():
    src = "a.b.c()"
    tree = ast.parse(src)
    call = tree.body[0].value
    assert _get_call_name(call) == "a.b.c"


# ── detect_return_arity_change ─────────────────────────────────────────


def test_detect_return_arity_change_detected():
    old = "def foo() -> tuple[int, str]:\n    return 1, 'a'\n"
    new = "def foo() -> tuple[int, str, bool]:\n    return 1, 'a', True\n"
    changes = detect_return_arity_change("test.py", old, new)
    assert len(changes) == 1
    assert changes[0].function == "foo"
    assert changes[0].change_type == "return_arity"
    assert changes[0].old_value == 2
    assert changes[0].new_value == 3


def test_detect_return_arity_change_no_change():
    src = "def foo() -> tuple[int, str]:\n    return 1, 'a'\n"
    changes = detect_return_arity_change("test.py", src, src)
    assert changes == []


def test_detect_return_arity_change_syntax_error():
    changes = detect_return_arity_change("test.py", "def (:", "def (:")
    assert changes == []


# ── detect_param_changes ──────────────────────────────────────────────


def test_detect_param_changes_added():
    old = "def foo(x): pass\n"
    new = "def foo(x, y): pass\n"
    changes = detect_param_changes("test.py", old, new)
    assert any(c.change_type == "param_added" for c in changes)


def test_detect_param_changes_removed():
    old = "def foo(x, y): pass\n"
    new = "def foo(x): pass\n"
    changes = detect_param_changes("test.py", old, new)
    assert any(c.change_type == "param_removed" for c in changes)


def test_detect_param_changes_no_change():
    src = "def foo(x): pass\n"
    changes = detect_param_changes("test.py", src, src)
    assert changes == []


# ── _find_unpack_mismatches ────────────────────────────────────────────


def test_find_unpack_mismatches():
    test_src = textwrap.dedent("""\
        a, b = foo()
        x, y, z = bar()
    """)
    tree = ast.parse(test_src)
    sites = _find_unpack_mismatches(tree, "test.py", "foo", 2)
    assert len(sites) == 1
    assert sites[0].unpacking_arity == 2
    assert sites[0].call_expression == "foo"


def test_find_unpack_mismatches_no_match():
    test_src = "x = foo()\n"
    tree = ast.parse(test_src)
    sites = _find_unpack_mismatches(tree, "test.py", "foo", 2)
    assert sites == []


# ── _find_call_sites ──────────────────────────────────────────────────


def test_find_call_sites_basic():
    src = textwrap.dedent("""\
        foo(1)
        bar(2)
        foo(3)
    """)
    tree = ast.parse(src)
    sites = _find_call_sites(tree, "test.py", "foo")
    assert len(sites) == 2
    assert all(s.call_expression == "foo" for s in sites)


def test_find_call_sites_attribute_call():
    src = "obj.foo(1)\n"
    tree = ast.parse(src)
    sites = _find_call_sites(tree, "test.py", "foo")
    assert len(sites) == 1
    assert sites[0].call_expression == "obj.foo"


# ── _build_advisory ─────────────────────────────────────────────────────


def test_build_advisory_no_affected():
    change = SignatureChange(module="m", function="foo", file="f.py")
    assert _build_advisory(change, []) == ""


def test_build_advisory_return_arity():
    change = SignatureChange(
        module="m",
        function="foo",
        file="f.py",
        change_type="return_arity",
        old_value=2,
        new_value=3,
    )
    sites = [AffectedTestSite(test_file="test_f.py", line=10, unpacking_arity=2)]
    advisory = _build_advisory(change, sites)
    assert "foo()" in advisory
    assert "2" in advisory
    assert "3" in advisory
    assert "test_f.py:10" in advisory


def test_build_advisory_param_added():
    change = SignatureChange(
        module="m",
        function="bar",
        file="f.py",
        change_type="param_added",
        old_value=["x"],
        new_value=["x", "y"],
    )
    sites = [AffectedTestSite(test_file="test_f.py", line=5)]
    advisory = _build_advisory(change, sites)
    assert "gained parameter" in advisory
    assert "y" in advisory


def test_build_advisory_param_removed():
    change = SignatureChange(
        module="m",
        function="baz",
        file="f.py",
        change_type="param_removed",
        old_value=["x", "y"],
        new_value=["x"],
    )
    sites = [AffectedTestSite(test_file="test_f.py", line=3)]
    advisory = _build_advisory(change, sites)
    assert "lost parameter" in advisory
    assert "y" in advisory


# ── analyze_contract_drift ──────────────────────────────────────────────


def test_analyze_contract_drift_no_changes():
    src = "def foo(x): return x\n"
    results = analyze_contract_drift("f.py", src, src, [])
    assert results == []


def test_analyze_contract_drift_detects_arity_change(tmp_path):
    old = "def foo() -> tuple[int, str]:\n    return 1, 'a'\n"
    new = "def foo() -> tuple[int, str, bool]:\n    return 1, 'a', True\n"
    test_file = tmp_path / "test_foo.py"
    test_file.write_text("a, b = foo()\n")
    results = analyze_contract_drift("f.py", old, new, [str(test_file)])
    assert len(results) >= 1
    assert results[0].change.function == "foo"
    assert len(results[0].affected_sites) == 1
