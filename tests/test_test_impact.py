"""Tests for test-impact mapping with class-qualified test identities."""

from __future__ import annotations

from lintgate.specification.test_impact import (
    TestImpactMap,
    TestReference,
    build_test_impact_map,
)
from mcp_tools._mutation_impl import load_test_callables


def test_build_test_impact_map_preserves_class_method_identity(tmp_path):
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "class TestAlpha:",
                "    def test_duplicate_name(self):",
                "        target_alpha()",
                "",
                "class TestBeta:",
                "    def test_duplicate_name(self):",
                "        target_beta()",
            ]
        )
    )

    impact = build_test_impact_map([str(test_file)])

    alpha_refs = impact.tests_for("target_alpha")
    beta_refs = impact.tests_for("target_beta")

    assert len(alpha_refs) == 1
    assert len(beta_refs) == 1
    assert alpha_refs[0].test_function == "TestAlpha.test_duplicate_name"
    assert beta_refs[0].test_function == "TestBeta.test_duplicate_name"
    assert impact.total_test_functions == 2


def test_load_test_callables_imports_multiple_class_methods_with_same_name(tmp_path):
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "\n".join(
            [
                "SEEN = []",
                "",
                "def target_alpha():",
                "    return 1",
                "",
                "class TestAlpha:",
                "    def test_duplicate_name(self):",
                "        SEEN.append('alpha')",
                "        target_alpha()",
                "",
                "class TestBeta:",
                "    def test_duplicate_name(self):",
                "        SEEN.append('beta')",
                "        target_alpha()",
            ]
        )
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

    callables, diag = load_test_callables([str(test_file)], "target_alpha")

    assert diag.callables_loaded == 2
    assert diag.impact_map_refs == 2

    for fn in callables:
        fn()

    assert {fn.__self__.__class__.__name__ for fn in callables} == {"TestAlpha", "TestBeta"}


# ── to_dict VALUE mutation killers ───────────────────────────────


class TestReferencToDict:
    def test_exact_keys_and_values(self):
        ref = TestReference(test_file="tests/test_foo.py", test_function="test_bar")
        d = ref.to_dict()
        assert d == {"test_file": "tests/test_foo.py", "test_function": "test_bar"}

    def test_key_names_are_field_names(self):
        """VALUE: keys must be 'test_file' and 'test_function', not swapped."""
        ref = TestReference(test_file="a.py", test_function="test_x")
        d = ref.to_dict()
        assert "test_file" in d
        assert "test_function" in d
        assert d["test_file"] == "a.py"
        assert d["test_function"] == "test_x"


class TestImpactMapToDict:
    def test_empty_map(self):
        m = TestImpactMap()
        d = m.to_dict()
        assert d == {
            "total_test_functions": 0,
            "total_source_functions_covered": 0,
            "mappings": {},
        }

    def test_populated_map_exact_values(self):
        ref = TestReference(test_file="t.py", test_function="test_a")
        m = TestImpactMap(
            function_to_tests={"func_a": [ref]},
            total_test_functions=1,
            total_source_functions_covered=1,
        )
        d = m.to_dict()
        assert d["total_test_functions"] == 1
        assert d["total_source_functions_covered"] == 1
        assert d["mappings"] == {"func_a": [{"test_file": "t.py", "test_function": "test_a"}]}

    def test_key_names_match_fields(self):
        """VALUE: dict keys must be 'total_test_functions', 'total_source_functions_covered', 'mappings'."""
        m = TestImpactMap(total_test_functions=5, total_source_functions_covered=3)
        d = m.to_dict()
        assert set(d.keys()) == {
            "total_test_functions",
            "total_source_functions_covered",
            "mappings",
        }
