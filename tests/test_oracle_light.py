"""Tests for oracle-light executable property generation."""

from __future__ import annotations

import ast
import textwrap

import pytest

from lintgate.testing.oracle_light import (
    _extract_assign_rhs,
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
            "mod::f",
            func_node,
        )
        assert prop.category == "SWAP"
        assert prop.needs_oracle is False
        assert "!=" in prop.assertion_code
        assert prop.confidence >= 0.5

    def test_one_param_needs_oracle(self):
        func_node = _make_func("def f(x): return x")
        prop = generate_executable_property(
            {"category": "SWAP", "mutant_id": "SWAP_0"},
            "mod::f",
            func_node,
        )
        assert prop.needs_oracle is True
        assert prop.confidence < 0.5

    def test_call_site_inputs_boost_confidence(self):
        func_node = _make_func("def f(a, b): return a - b")
        sites = [{"args": [10, 20]}]
        prop = generate_executable_property(
            {"category": "SWAP", "mutant_id": "SWAP_0"},
            "mod::f",
            func_node,
            sites,
        )
        assert prop.confidence >= 0.7
        assert "10" in prop.assertion_code
        assert "20" in prop.assertion_code

    def test_no_func_node_no_params(self):
        prop = generate_executable_property(
            {"category": "SWAP", "mutant_id": "SWAP_0"},
            "mod::f",
            None,
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
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0", "diff_summary": diff},
            "mod::f",
            func_node,
        )
        assert prop.category == "BOUNDARY"
        assert prop.needs_oracle is False
        assert "10" in prop.assertion_code
        assert "9" in prop.assertion_code
        assert prop.confidence >= 0.8

    def test_no_diff_needs_oracle(self):
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0", "diff_summary": ""},
            "mod::f",
            None,
        )
        assert prop.needs_oracle is True
        assert prop.confidence <= 0.3

    def test_multi_param_with_call_sites(self):
        diff = _make_diff(
            "def calc(price, qty):\n    if qty < 10:\n        return price",
            "def calc(price, qty):\n    if qty <= 10:\n        return price",
        )
        func_node = _make_func(
            "def calc(price, qty):\n    if qty < 10:\n        return price\n    return 0"
        )
        sites = [{"args": [100, 5]}]
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0", "diff_summary": diff},
            "mod::calc",
            func_node,
            sites,
        )
        assert prop.needs_oracle is False
        assert "100" in prop.assertion_code  # non-boundary param filled

    def test_multi_param_no_sites_needs_oracle(self):
        diff = _make_diff(
            "def calc(price, qty):\n    if qty < 10:\n        return price",
            "def calc(price, qty):\n    if qty <= 10:\n        return price",
        )
        func_node = _make_func(
            "def calc(price, qty):\n    if qty < 10:\n        return price\n    return 0"
        )
        prop = generate_executable_property(
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0", "diff_summary": diff},
            "mod::calc",
            func_node,
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
            {"category": "BOUNDARY", "mutant_id": "BOUNDARY_0", "diff_summary": diff},
            "mod::f",
            func_node,
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
            {"category": "TYPE", "mutant_id": "TYPE_0", "diff_summary": diff},
            "mod::f",
        )
        assert prop.category == "TYPE"
        assert prop.needs_oracle is False
        assert "raises" in prop.assertion_code
        assert "42" in prop.assertion_code  # invalid for str

    def test_no_type_extractable(self):
        prop = generate_executable_property(
            {"category": "TYPE", "mutant_id": "TYPE_0", "diff_summary": ""},
            "mod::f",
        )
        assert prop.needs_oracle is True
        assert "TODO" in prop.assertion_code


# ── STATE ─────────────────────────────────────────────────────────


class TestStateProperty:
    def test_return_none_oracle_light(self):
        prop = generate_executable_property(
            {
                "category": "STATE",
                "mutant_id": "STATE_return_none_0",
                "description": "STATE_return_none_0: replace return with None",
            },
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
            {
                "category": "STATE",
                "mutant_id": "STATE_remove_assign_0",
                "description": "STATE_remove_assign_0: remove state assignment",
                "diff_summary": diff,
            },
            "mod::m",
        )
        assert prop.needs_oracle is True
        assert "count" in prop.assertion_code

    def test_remove_assign_no_diff(self):
        prop = generate_executable_property(
            {
                "category": "STATE",
                "mutant_id": "STATE_remove_assign_0",
                "description": "STATE_remove_assign_0: remove state assignment",
                "diff_summary": "",
            },
            "mod::m",
        )
        assert prop.needs_oracle is True
        assert prop.confidence < 0.3

    def test_remove_assign_literal_fast_path(self):
        """self.count = 0 with class context → oracle-free."""
        diff = _make_diff(
            "def reset(self):\n    self.count = 0",
            "def reset(self):\n    pass",
        )
        prop = generate_executable_property(
            {
                "category": "STATE",
                "mutant_id": "STATE_remove_assign_0",
                "description": "STATE_remove_assign_0: remove state assignment",
                "diff_summary": diff,
            },
            "mod::Counter.reset",
        )
        assert prop.needs_oracle is False
        assert "obj.count == 0" in prop.assertion_code
        assert "Counter" in prop.assertion_code
        assert "state_fast_path" in prop.source_lenses

    def test_remove_assign_param_fast_path(self):
        """self.name = name with class context + func_node → oracle-free."""
        diff = _make_diff(
            "def __init__(self, name):\n    self.name = name",
            "def __init__(self, name):\n    pass",
        )
        func_node = _make_func("def __init__(self, name): pass")
        prop = generate_executable_property(
            {
                "category": "STATE",
                "mutant_id": "STATE_remove_assign_0",
                "description": "STATE_remove_assign_0: remove state assignment",
                "diff_summary": diff,
            },
            "mod::Foo.__init__",
            func_node,
        )
        assert prop.needs_oracle is False
        assert "obj.name == name" in prop.assertion_code

    def test_remove_assign_complex_rhs_stays_oracle(self):
        """self.x = compute(a) is not a simple param/literal → oracle-dependent."""
        diff = _make_diff(
            "def setup(self, a):\n    self.x = compute(a)",
            "def setup(self, a):\n    pass",
        )
        func_node = _make_func("def setup(self, a): pass")
        prop = generate_executable_property(
            {
                "category": "STATE",
                "mutant_id": "STATE_remove_assign_0",
                "description": "STATE_remove_assign_0: remove state assignment",
                "diff_summary": diff,
            },
            "mod::Widget.setup",
            func_node,
        )
        assert prop.needs_oracle is True

    def test_remove_assign_no_class_stays_oracle(self):
        """self.count = 0 but no class in func_key → fallback."""
        diff = _make_diff(
            "def reset(self):\n    self.count = 0",
            "def reset(self):\n    pass",
        )
        prop = generate_executable_property(
            {
                "category": "STATE",
                "mutant_id": "STATE_remove_assign_0",
                "description": "STATE_remove_assign_0: remove state assignment",
                "diff_summary": diff,
            },
            "mod::reset",
        )
        assert prop.needs_oracle is True


# ── _extract_assign_rhs ──────────────────────────────────────────


class TestExtractAssignRhs:
    def test_param_reference(self):
        diff = _make_diff("self.name = name", "pass")
        result = _extract_assign_rhs(diff, ["name", "age"])
        assert result == ("param", "name")

    def test_int_literal(self):
        diff = _make_diff("self.count = 0", "pass")
        result = _extract_assign_rhs(diff, [])
        assert result == ("literal", "0")

    def test_float_literal(self):
        diff = _make_diff("self.rate = 3.14", "pass")
        result = _extract_assign_rhs(diff, [])
        assert result == ("literal", "3.14")

    def test_string_literal(self):
        diff = _make_diff('self.label = "default"', "pass")
        result = _extract_assign_rhs(diff, [])
        assert result == ("literal", '"default"')

    def test_bool_literal(self):
        diff = _make_diff("self.active = True", "pass")
        result = _extract_assign_rhs(diff, [])
        assert result == ("literal", "True")

    def test_none_literal(self):
        diff = _make_diff("self.cache = None", "pass")
        result = _extract_assign_rhs(diff, [])
        assert result == ("literal", "None")

    def test_complex_expr_returns_none(self):
        diff = _make_diff("self.x = compute(a)", "pass")
        result = _extract_assign_rhs(diff, ["a"])
        assert result is None

    def test_method_call_returns_none(self):
        diff = _make_diff("self.data = self.load()", "pass")
        result = _extract_assign_rhs(diff, [])
        assert result is None

    def test_empty_diff_returns_none(self):
        result = _extract_assign_rhs("", ["x"])
        assert result is None


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
            category="SWAP",
            inputs={"a": "1", "b": "2"},
            setup_code="from mod import f",
            assertion_code="result = f(1, 2)\nassert result != f(2, 1)",
            preconditions=["a != b"],
            confidence=0.7,
            source_lenses=["mutation"],
            needs_oracle=False,
            function_key="mod::f",
            mutant_id="swap_0",
        )
        enr = FunctionEnrichment(
            function_key="mod::f",
            function_name="f",
            executable_properties=[prop],
        )
        section = _build_function_section(enr)
        assert "def test_f_swap_0():" in section
        assert "from mod import f" in section
        assert "assert result != f(2, 1)" in section
        assert "pass" not in section  # no pass stubs

    def test_prescriptions_only_returns_empty(self):
        """Auto-lane policy: prescription-only enrichments produce no test code.

        Prescription stubs with pass/TODO are not executable — they route
        to manual_contract_candidates instead.
        """
        from lintgate.testing.batch_regenerator import (
            FunctionEnrichment,
            _build_function_section,
        )

        enr = FunctionEnrichment(
            function_key="mod::f",
            function_name="f",
            prescriptions=[
                {
                    "category": "VALUE",
                    "assertion_shape": "assert f(x) == y",
                    "suggested_input": "42",
                    "confidence": 0.5,
                    "source": "witness",
                }
            ],
        )
        section = _build_function_section(enr)
        assert section == ""  # no executable witness → empty section

    def test_oracle_needing_property_skipped(self):
        from lintgate.testing.batch_regenerator import (
            FunctionEnrichment,
            _build_function_section,
        )
        from lintgate.testing.oracle_light import ExecutableProperty

        prop = ExecutableProperty(
            category="VALUE",
            inputs={},
            setup_code="",
            assertion_code="assert result == ...",
            preconditions=["needs expected value"],
            confidence=0.3,
            source_lenses=["mutation"],
            needs_oracle=True,
            function_key="mod::f",
            mutant_id="value_0",
        )
        enr = FunctionEnrichment(
            function_key="mod::f",
            function_name="f",
            executable_properties=[prop],
        )
        section = _build_function_section(enr)
        assert section == ""


# ── Field enumeration ────────────────────────────────────────────


class TestEnumerateReturnFields:
    def test_to_dict_detected(self):
        from lintgate.testing.oracle_light import _to_dict_field_assertions

        # ScheduledItem is a known dataclass with to_dict
        result = _to_dict_field_assertions(
            "lintgate/specification/scheduler.py::ScheduledItem.to_dict"
        )
        assert result is not None
        assert "function_key" in result
        assert "file_path" in result
        assert "priority" in result

    def test_to_dict_unknown_class(self):
        from lintgate.testing.oracle_light import _to_dict_field_assertions

        result = _to_dict_field_assertions("mod.py::UnknownClass.to_dict")
        assert result is None

    def test_value_property_with_dataclass_return(self):
        func = _make_func("""
def get_item() -> ScheduledItem:
    pass
""")
        # Simulate a VALUE survivor
        survivor = {"category": "VALUE", "mutant_id": "v1"}
        prop = generate_executable_property(
            survivor,
            "lintgate/specification/scheduler.py::get_item",
            func,
        )
        assert prop.category == "VALUE"
        # Should have field-enumeration assertions if return type resolves
        if "field_enumeration" in prop.source_lenses:
            assert "function_key" in prop.assertion_code
            assert "FILL" in prop.assertion_code

    def test_value_property_with_qualified_to_dict_uses_field_enumeration(self):
        func = _make_func("""
def to_dict(self):
    return {"status": self.status}
""")
        survivor = {"category": "VALUE", "mutant_id": "v1"}
        prop = generate_executable_property(
            survivor,
            "lintgate/specification/static_empirical_reconciliation.py::EmpiricalOverlay.to_dict",
            func,
        )
        assert prop.needs_oracle is False
        assert "obj = EmpiricalOverlay()" in prop.assertion_code
        assert '"status" in d' in prop.assertion_code


# ── Survivor-aware SWAP gating ───────────────────────────────────


class TestShouldEmitSwapTest:
    def test_swap_survivor_gates_true(self):
        from lintgate.testing.oracle_light import should_emit_swap_test

        survivors = [{"category": "SWAP", "mutant_id": "s1"}]
        assert should_emit_swap_test(None, survivors) is True

    def test_no_survivors_no_params(self):
        from lintgate.testing.oracle_light import should_emit_swap_test

        assert should_emit_swap_test(None, None) is False

    def test_non_commutative_param_names(self):
        from lintgate.testing.oracle_light import should_emit_swap_test

        func = _make_func("def f(start, end): pass")
        assert should_emit_swap_test(func, None) is True

    def test_commutative_params_no_survivor_gates_false(self):
        from lintgate.testing.oracle_light import should_emit_swap_test

        func = _make_func("def f(a, b): pass")
        assert should_emit_swap_test(func, None) is False

    def test_distinct_prefix_params(self):
        from lintgate.testing.oracle_light import should_emit_swap_test

        func = _make_func("def f(static_spec_level, empirical_spec_level): pass")
        assert should_emit_swap_test(func, None) is True

    def test_single_param_gates_false(self):
        from lintgate.testing.oracle_light import should_emit_swap_test

        func = _make_func("def f(x): pass")
        assert should_emit_swap_test(func, None) is False

    def test_self_excluded(self):
        from lintgate.testing.oracle_light import should_emit_swap_test

        func = _make_func("def f(self, start, end): pass")
        assert should_emit_swap_test(func, None) is True


# ── Round-trip pair detection ────────────────────────────────────


class TestDetectRoundTripPairs:
    def test_to_dict_with_from_dict(self, tmp_path):
        from lintgate.testing.oracle_light import detect_round_trip_pairs

        src = tmp_path / "model.py"
        src.write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Item:\n"
            "    name: str = ''\n"
            "    def to_dict(self): return {'name': self.name}\n"
            "    @classmethod\n"
            "    def from_dict(cls, d): return cls(name=d['name'])\n"
        )
        pairs = detect_round_trip_pairs(str(src))
        assert len(pairs) == 1
        assert pairs[0][0] == "Item"
        assert pairs[0][1] == "to_dict"
        assert "from_dict" in pairs[0][2]

    def test_to_dict_with_module_level_deserializer(self, tmp_path):
        from lintgate.testing.oracle_light import detect_round_trip_pairs

        src = tmp_path / "model.py"
        src.write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Item:\n"
            "    name: str = ''\n"
            "    def to_dict(self): return {'name': self.name}\n"
            "\ndef _item_from_dict(d): return Item(name=d['name'])\n"
        )
        pairs = detect_round_trip_pairs(str(src))
        assert len(pairs) == 1
        assert pairs[0][2] == "_item_from_dict"

    def test_no_to_dict(self, tmp_path):
        from lintgate.testing.oracle_light import detect_round_trip_pairs

        src = tmp_path / "model.py"
        src.write_text("class Item:\n    pass\n")
        pairs = detect_round_trip_pairs(str(src))
        assert pairs == []

    def test_module_level_deserializer_matched_by_returned_class(self, tmp_path):
        from lintgate.testing.oracle_light import detect_round_trip_pairs

        src = tmp_path / "model.py"
        src.write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Item:\n"
            "    name: str = ''\n"
            "    def to_dict(self): return {'name': self.name}\n"
            "@dataclass\n"
            "class Status:\n"
            "    count: int = 0\n"
            "    def to_dict(self): return {'count': self.count}\n"
            "\ndef _item_from_dict(d): return Item(name=d['name'])\n"
        )
        pairs = detect_round_trip_pairs(str(src))
        assert pairs == [("Item", "to_dict", "_item_from_dict")]

    def test_generate_round_trip_test(self):
        from lintgate.testing.oracle_light import generate_round_trip_test

        prop = generate_round_trip_test(
            "ScheduledItem",
            "to_dict",
            "_item_from_dict",
            "lintgate/specification/scheduler.py",
        )
        assert prop.category == "ROUND_TRIP"
        assert prop.needs_oracle is False
        assert prop.confidence == 0.9
        assert "original" in prop.assertion_code
        assert "reconstructed" in prop.assertion_code
        assert "function_key" in prop.assertion_code  # field assertion
