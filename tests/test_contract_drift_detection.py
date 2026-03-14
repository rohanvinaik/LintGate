"""Tests for lintgate.channels._contract_drift_detection.

Covers detect_return_arity_change, detect_param_changes,
find_affected_test_sites, _find_unpack_mismatches, _find_call_sites,
and _get_call_name.
"""

from __future__ import annotations

import ast
import os
import textwrap

import pytest

from lintgate.channels._contract_drift_detection import (
    _find_call_sites,
    _find_unpack_mismatches,
    _get_call_name,
    detect_param_changes,
    detect_return_arity_change,
    find_affected_test_sites,
)
from lintgate.channels._contract_drift_types import (
    AffectedTestSite,
    SignatureChange,
)


# ── _get_call_name ──────────────────────────────────────────────────


class TestGetCallName:
    def test_simple_name(self):
        tree = ast.parse("foo()")
        call_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _get_call_name(call_node) == "foo"

    def test_attribute_access(self):
        tree = ast.parse("obj.method()")
        call_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _get_call_name(call_node) == "obj.method"

    def test_chained_attribute(self):
        tree = ast.parse("a.b.c()")
        call_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _get_call_name(call_node) == "a.b.c"

    def test_non_name_non_attribute(self):
        # e.g. func_list[0]() — Subscript, not Name or Attribute
        tree = ast.parse("funcs[0]()")
        call_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _get_call_name(call_node) == ""


# ── detect_return_arity_change ──────────────────────────────────────


class TestDetectReturnArityChange:
    def test_arity_change_detected(self):
        old_src = textwrap.dedent("""\
            def compute():
                return a, b
        """)
        new_src = textwrap.dedent("""\
            def compute():
                return a, b, c
        """)
        changes = detect_return_arity_change("pkg/mod.py", old_src, new_src)
        assert len(changes) == 1
        assert changes[0].function == "compute"
        assert changes[0].change_type == "return_arity"
        assert changes[0].old_value == 2
        assert changes[0].new_value == 3

    def test_no_change_same_arity(self):
        src = textwrap.dedent("""\
            def compute():
                return a, b
        """)
        changes = detect_return_arity_change("mod.py", src, src)
        assert changes == []

    def test_function_removed_not_reported(self):
        old_src = textwrap.dedent("""\
            def gone():
                return a, b
        """)
        new_src = textwrap.dedent("""\
            def other():
                return x
        """)
        changes = detect_return_arity_change("mod.py", old_src, new_src)
        assert changes == []

    def test_syntax_error_returns_empty(self):
        changes = detect_return_arity_change("mod.py", "def broken(", "def broken(")
        assert changes == []

    def test_annotated_return_arity_change(self):
        old_src = textwrap.dedent("""\
            def compute() -> tuple[int, str]:
                return 1, "x"
        """)
        new_src = textwrap.dedent("""\
            def compute() -> tuple[int, str, float]:
                return 1, "x", 0.5
        """)
        changes = detect_return_arity_change("mod.py", old_src, new_src)
        assert len(changes) == 1
        assert changes[0].old_value == 2
        assert changes[0].new_value == 3


# ── detect_param_changes ────────────────────────────────────────────


class TestDetectParamChanges:
    def test_param_added(self):
        old_src = textwrap.dedent("""\
            def process(x):
                pass
        """)
        new_src = textwrap.dedent("""\
            def process(x, y):
                pass
        """)
        changes = detect_param_changes("mod.py", old_src, new_src)
        assert len(changes) == 1
        assert changes[0].change_type == "param_added"
        assert changes[0].function == "process"
        assert "y" in changes[0].new_value
        assert "y" not in changes[0].old_value

    def test_param_removed(self):
        old_src = textwrap.dedent("""\
            def process(x, y):
                pass
        """)
        new_src = textwrap.dedent("""\
            def process(x):
                pass
        """)
        changes = detect_param_changes("mod.py", old_src, new_src)
        assert len(changes) == 1
        assert changes[0].change_type == "param_removed"

    def test_param_added_and_removed(self):
        old_src = textwrap.dedent("""\
            def process(x, y):
                pass
        """)
        new_src = textwrap.dedent("""\
            def process(x, z):
                pass
        """)
        changes = detect_param_changes("mod.py", old_src, new_src)
        # Both added (z) and removed (y)
        types = {c.change_type for c in changes}
        assert types == {"param_added", "param_removed"}

    def test_no_change(self):
        src = textwrap.dedent("""\
            def process(x, y):
                pass
        """)
        changes = detect_param_changes("mod.py", src, src)
        assert changes == []

    def test_syntax_error_returns_empty(self):
        changes = detect_param_changes("mod.py", "def bad(", "def bad(")
        assert changes == []


# ── _find_unpack_mismatches ─────────────────────────────────────────


class TestFindUnpackMismatches:
    def test_matching_unpack_detected(self):
        src = textwrap.dedent("""\
            a, b = compute()
        """)
        tree = ast.parse(src)
        sites = _find_unpack_mismatches(tree, "test_mod.py", "compute", old_arity=2)
        assert len(sites) == 1
        assert sites[0].test_file == "test_mod.py"
        assert sites[0].unpacking_arity == 2
        assert sites[0].call_expression == "compute"

    def test_non_matching_arity_not_detected(self):
        src = textwrap.dedent("""\
            a, b, c = compute()
        """)
        tree = ast.parse(src)
        sites = _find_unpack_mismatches(tree, "test_mod.py", "compute", old_arity=2)
        assert sites == []

    def test_attribute_call_matched(self):
        src = textwrap.dedent("""\
            x, y = mod.compute()
        """)
        tree = ast.parse(src)
        sites = _find_unpack_mismatches(tree, "test_mod.py", "compute", old_arity=2)
        assert len(sites) == 1
        assert sites[0].call_expression == "mod.compute"


# ── _find_call_sites ────────────────────────────────────────────────


class TestFindCallSites:
    def test_simple_call(self):
        src = textwrap.dedent("""\
            result = process(1, 2)
        """)
        tree = ast.parse(src)
        sites = _find_call_sites(tree, "test_mod.py", "process")
        assert len(sites) == 1
        assert sites[0].call_expression == "process"
        assert sites[0].line == 1

    def test_multiple_calls(self):
        src = textwrap.dedent("""\
            process(1)
            x = process(2)
            y = other()
        """)
        tree = ast.parse(src)
        sites = _find_call_sites(tree, "test_mod.py", "process")
        assert len(sites) == 2

    def test_no_matching_calls(self):
        src = textwrap.dedent("""\
            other_func(1)
        """)
        tree = ast.parse(src)
        sites = _find_call_sites(tree, "test_mod.py", "process")
        assert sites == []


# ── find_affected_test_sites ────────────────────────────────────────


class TestFindAffectedTestSites:
    def test_return_arity_finds_unpack_sites(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
                a, b = compute()
                x = compute()
            """),
            encoding="utf-8",
        )
        change = SignatureChange(
            module="mod",
            function="compute",
            file="mod.py",
            line=1,
            change_type="return_arity",
            old_value=2,
            new_value=3,
        )
        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 1
        assert sites[0].unpacking_arity == 2

    def test_param_change_finds_call_sites(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
                process(1, 2)
                other(3)
            """),
            encoding="utf-8",
        )
        change = SignatureChange(
            module="mod",
            function="process",
            file="mod.py",
            line=1,
            change_type="param_added",
            old_value=["x"],
            new_value=["x", "y"],
        )
        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 1
        assert sites[0].call_expression == "process"

    def test_missing_test_file_skipped(self):
        change = SignatureChange(
            module="mod",
            function="compute",
            file="mod.py",
            change_type="return_arity",
            old_value=2,
            new_value=3,
        )
        sites = find_affected_test_sites(change, ["/nonexistent/test.py"])
        assert sites == []

    def test_syntax_error_in_test_file_skipped(self, tmp_path):
        test_file = tmp_path / "test_bad.py"
        test_file.write_text("def bad syntax(:", encoding="utf-8")
        change = SignatureChange(
            module="mod",
            function="compute",
            file="mod.py",
            change_type="return_arity",
            old_value=2,
            new_value=3,
        )
        sites = find_affected_test_sites(change, [str(test_file)])
        assert sites == []
