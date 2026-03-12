"""Tests for oracle-light executable property generation."""

from __future__ import annotations

import ast
import textwrap

import pytest

from lintgate.testing.oracle_light import (
    _extract_boundary_info,
    _extract_isinstance_type,
    _extract_self_attr,
    _parse_diff_changes,
    generate_executable_property,
)


def _make_func(code: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(code))
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _make_diff(orig: str, mutated: str) -> str:
    """Build a diff_summary in the format produced by mutant_reporting."""
    return f"- {orig}\n+ {mutated}"


# ── SWAP ──────────────────────────────────────────────────────────


class TestSwapProperty:
    def test_two_params_oracle_light(self):
        func_node = _make_func("def f(a, b): return a - b")
        prop = generate_executable_property(
            {"category": "SWAP", "mutant_id": "SWAP_0"},
            "mod::f", func_node,
        )
        assert prop.category == "SWAP"
        assert prop.needs_oracle is False
        assert "!=" in prop.assertion_code
        assert prop.confidence >= 0.5

    def test_one_param_needs_oracle(self):
        func_node = _make_func("def f(x): return x")
        prop = generate_executable_property(
            {"category": "SWAP", "mutant_id": "SWAP_0"},
            "mod::f", func_node,
        )
        assert prop.needs_oracle is True
        assert prop.confidence < 0.5

    def test_call_site_inputs_boost_confidence(self):
        func_node = _make_func("def f(a, b): return a - b")
        sites = [{"args": [10, 20]}]
        prop = generate_executable_property(
            {"category": "SWAP", "mutant_id": "SWAP_0"},
            "mod::f", func_node, sites,
        )
        assert prop.confidence >= 0.7
        assert "10" in prop.assertion_code
        assert "20" in prop.assertion_code

    def test_no_func_node_no_params(self):
        prop = generate_executable_property(
            {"category": "SWAP", "mutant_id": "SWAP_0"},
            "mod::f", None,
        )
        assert prop.needs_oracle is True


# ── BOUNDARY ──────────────────────────────────────────────────────


class TestBoundaryProperty:
    def test_extractable_boundary(self):
        diff = _make_diff(
            "def f(x):\n    return x < 10",
            "def f(x):\n    return x <= 10",
        )
        func_node = _make_func("def f(x): return x < 10")
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0",
             "diff_summary": diff},
            "mod::f", func_node,
        )
        assert prop.category == "BOUNDARY"
        assert prop.needs_oracle is False
        assert "10" in prop.assertion_code
        assert "9" in prop.assertion_code
        assert prop.confidence >= 0.8

    def test_no_diff_needs_oracle(self):
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0",
             "diff_summary": ""},
            "mod::f", None,
        )
        assert prop.needs_oracle is True
        assert prop.confidence <= 0.3

    def test_multi_param_with_call_sites(self):
        diff = _make_diff(
            "def calc(price, qty):\n    if qty < 10:\n        return price",
            "def calc(price, qty):\n    if qty <= 10:\n        return price",
        )
        func_node = _make_func(
            "def calc(price, qty):\n"
            "    if qty < 10:\n"
            "        return price\n"
            "    return 0"
        )
        sites = [{"args": [100, 5]}]
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0",
             "diff_summary": diff},
            "mod::calc", func_node, sites,
        )
        assert prop.needs_oracle is False
        assert "100" in prop.assertion_code  # non-boundary param filled

    def test_multi_param_no_sites_needs_oracle(self):
        diff = _make_diff(
            "def calc(price, qty):\n    if qty < 10:\n        return price",
            "def calc(price, qty):\n    if qty <= 10:\n        return price",
        )
        func_node = _make_func(
            "def calc(price, qty):\n"
            "    if qty < 10:\n"
            "        return price\n"
            "    return 0"
        )
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0",
             "diff_summary": diff},
            "mod::calc", func_node,
        )
        assert prop.needs_oracle is True  # ... in args
        assert "..." in prop.assertion_code

    def test_float_boundary(self):
        diff = _make_diff(
            "def f(x):\n    return x < 0.5",
            "def f(x):\n    return x <= 0.5",
        )
        func_node = _make_func("def f(x): return x < 0.5")
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0",
             "diff_summary": diff},
            "mod::f", func_node,
        )
        assert "0.5" in prop.assertion_code
        assert "0.4" in prop.assertion_code  # boundary - 0.1


# ── TYPE ──────────────────────────────────────────────────────────


class TestTypeProperty:
    def test_isinstance_extracted(self):
        diff = _make_diff(
            "def f(x):\n    if isinstance(x, str):\n        return x",
            "def f(x):\n    if True:\n        return x",
        )
        prop = generate_executable_property(
            {"category": "TYPE", "mutant_id": "TYPE_0",
             "diff_summary": diff},
            "mod::f",
        )
        assert prop.category == "TYPE"
        assert prop.needs_oracle is False
        assert "raises" in prop.assertion_code
        assert "42" in prop.assertion_code  # invalid for str

    def test_no_type_extractable(self):
        prop = generate_executable_property(
            {"category": "TYPE", "mutant_id": "TYPE_0",
             "diff_summary": ""},
            "mod::f",
        )
        assert prop.needs_oracle is True
        assert "TODO" in prop.assertion_code


# ── STATE ─────────────────────────────────────────────────────────


class TestStateProperty:
    def test_return_none_oracle_light(self):
        prop = generate_executable_property(
            {"category": "STATE",
             "mutant_id": "STATE_return_none_0",
             "description": "STATE_return_none_0: replace return with None"},
            "mod::f",
        )
        assert prop.needs_oracle is False
        assert "is not None" in prop.assertion_code

    def test_remove_assign_needs_oracle(self):
        diff = _make_diff(
            "def m(self):\n    self.count = 0",
            "def m(self):\n    pass",
        )
        prop = generate_executable_property(
            {"category": "STATE",
             "mutant_id": "STATE_remove_assign_0",
             "description": "STATE_remove_assign_0: remove state assignment",
             "diff_summary": diff},
            "mod::m",
        )
        assert prop.needs_oracle is True
        assert "count" in prop.assertion_code

    def test_remove_assign_no_diff(self):
        prop = generate_executable_property(
            {"category": "STATE",
             "mutant_id": "STATE_remove_assign_0",
             "description": "STATE_remove_assign_0: remove state assignment",
             "diff_summary": ""},
            "mod::m",
        )
        assert prop.needs_oracle is True
        assert prop.confidence < 0.3


# ── VALUE ─────────────────────────────────────────────────────────


class TestValueProperty:
    def test_always_needs_oracle(self):
        prop = generate_executable_property(
            {"category": "VALUE", "mutant_id": "VALUE_0"},
            "mod::f",
        )
        assert prop.needs_oracle is True
        assert "FILL" in prop.assertion_code

    def test_import_generated(self):
        prop = generate_executable_property(
            {"category": "VALUE", "mutant_id": "VALUE_0"},
            "mypackage.utils::compute",
        )
        assert "from mypackage.utils import compute" in prop.setup_code


# ── Generic ───────────────────────────────────────────────────────


class TestGenericProperty:
    def test_unknown_category(self):
        prop = generate_executable_property(
            {"category": "FUTURE_CAT", "mutant_id": "FC_0"},
            "mod::f",
        )
        assert prop.needs_oracle is True
        assert prop.confidence == 0.2


# ── Diff extractors ───────────────────────────────────────────────


class TestDiffExtractors:
    def test_boundary_info_lt(self):
        diff = _make_diff("x < 10", "x <= 10")
        info = _extract_boundary_info(diff)
        assert info is not None
        assert info["variable"] == "x"
        assert info["boundary_value"] == 10
        assert info["comparator"] == "<"

    def test_boundary_info_gte_float(self):
        diff = _make_diff("score >= 0.75", "score > 0.75")
        info = _extract_boundary_info(diff)
        assert info is not None
        assert info["variable"] == "score"
        assert info["boundary_value"] == pytest.approx(0.75)
        assert info["comparator"] == ">="

    def test_boundary_info_no_match(self):
        assert _extract_boundary_info("") is None
        assert _extract_boundary_info("no comparator here") is None

    def test_isinstance_type(self):
        diff = _make_diff("isinstance(x, str)", "True")
        assert _extract_isinstance_type(diff) == "str"

    def test_isinstance_dotted_type(self):
        diff = _make_diff("isinstance(x, ast.FunctionDef)", "True")
        assert _extract_isinstance_type(diff) == "ast.FunctionDef"

    def test_isinstance_no_match(self):
        assert _extract_isinstance_type("") is None

    def test_self_attr(self):
        diff = _make_diff("self.count = 0", "pass")
        assert _extract_self_attr(diff) == "count"

    def test_self_attr_no_match(self):
        assert _extract_self_attr("") is None

    def test_parse_diff_changes(self):
        diff = _make_diff("line1\nline2\nline3", "line1\nchanged\nline3")
        changes = _parse_diff_changes(diff)
        assert len(changes) == 1
        assert changes[0] == ("line2", "changed")

    def test_parse_diff_no_changes(self):
        assert _parse_diff_changes("") == []
        assert _parse_diff_changes("no plus marker") == []


# ── Integration with batch_regenerator ────────────────────────────


class TestBatchRegeneratorIntegration:
    def test_executable_section_emits_code(self):
        from lintgate.testing.batch_regenerator import (
            FunctionEnrichment,
            _build_function_section,
        )
        from lintgate.testing.oracle_light import ExecutableProperty

        prop = ExecutableProperty(
            category="SWAP", inputs={"a": "1", "b": "2"},
            setup_code="from mod import f",
            assertion_code='result = f(1, 2)\nassert result != f(2, 1)',
            preconditions=["a != b"],
            confidence=0.7, source_lenses=["mutation"],
            needs_oracle=False,
            function_key="mod::f", mutant_id="swap_0",
        )
        enr = FunctionEnrichment(
            function_key="mod::f", function_name="f",
            executable_properties=[prop],
        )
        section = _build_function_section(enr)
        assert "def test_f_swap_0():" in section
        assert "from mod import f" in section
        assert "assert result != f(2, 1)" in section
        assert "pass" not in section  # no pass stubs

    def test_fallback_to_prescriptions(self):
        from lintgate.testing.batch_regenerator import (
            FunctionEnrichment,
            _build_function_section,
        )

        enr = FunctionEnrichment(
            function_key="mod::f", function_name="f",
            prescriptions=[{
                "category": "VALUE",
                "assertion_shape": "assert f(x) == y",
                "suggested_input": "42",
                "confidence": 0.5,
                "source": "witness",
            }],
        )
        section = _build_function_section(enr)
        assert "def test_f_value_mutation():" in section
        assert "TODO" in section
        assert "pass" in section

    def test_oracle_tag_in_docstring(self):
        from lintgate.testing.batch_regenerator import (
            FunctionEnrichment,
            _build_function_section,
        )
        from lintgate.testing.oracle_light import ExecutableProperty

        prop = ExecutableProperty(
            category="VALUE", inputs={},
            setup_code="", assertion_code="assert result == ...",
            preconditions=["needs expected value"],
            confidence=0.3, source_lenses=["mutation"],
            needs_oracle=True,
            function_key="mod::f", mutant_id="value_0",
        )
        enr = FunctionEnrichment(
            function_key="mod::f", function_name="f",
            executable_properties=[prop],
        )
        section = _build_function_section(enr)
        assert "[needs oracle]" in section
