"""Tests for test-impact mapping with class-qualified test identities."""

from __future__ import annotations

import ast

from lintgate.specification.test_impact import (
    TestImpactMap,
    TestReference,
    _AMBIGUOUS_BARE_NAMES,
    _extract_covers_directives,
    _extract_imports,
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


# ── _extract_imports tests ────────────────────────────────────────


def test_extract_imports_simple():
    tree = ast.parse("import os")
    result = _extract_imports(tree)
    assert result == {"os": "os"}


def test_extract_imports_from():
    tree = ast.parse("from os.path import join")
    result = _extract_imports(tree)
    assert result == {"join": "os.path.join"}


def test_extract_imports_alias():
    tree = ast.parse("from mymod import func as f")
    result = _extract_imports(tree)
    assert result == {"f": "mymod.func"}


def test_extract_imports_import_alias():
    tree = ast.parse("import numpy as np")
    result = _extract_imports(tree)
    assert result == {"np": "numpy"}


def test_extract_imports_multiple():
    tree = ast.parse("import os\nfrom os.path import join\nimport sys as system")
    result = _extract_imports(tree)
    assert result == {"os": "os", "join": "os.path.join", "system": "sys"}


# ── Qualified call tracking tests ─────────────────────────────────


def test_qualified_call_tracking(tmp_path):
    """from mymodule import helper; helper() -> maps to both 'helper' and 'mymodule.helper'."""
    test_file = tmp_path / "test_qual.py"
    test_file.write_text(
        "\n".join([
            "from mymodule import helper",
            "",
            "def test_it():",
            "    helper()",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    # Qualified name should be present
    assert len(impact.tests_for("mymodule.helper")) == 1
    # Bare name should also be present
    assert len(impact.tests_for("helper")) == 1


def test_attribute_call_qualified(tmp_path):
    """import utils; utils.helper() -> maps to both 'helper' and 'utils.helper'."""
    test_file = tmp_path / "test_attr.py"
    test_file.write_text(
        "\n".join([
            "import utils",
            "",
            "def test_it():",
            "    utils.helper()",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    assert len(impact.tests_for("utils.helper")) == 1
    assert len(impact.tests_for("helper")) == 1


# ── Covers directive tests ────────────────────────────────────────


def test_covers_escape_hatch(tmp_path):
    """# lintgate: covers mypackage.mymodule::my_func -> mapping appears."""
    test_file = tmp_path / "test_covers.py"
    test_file.write_text(
        "\n".join([
            "# lintgate: covers mypackage.mymodule::my_func",
            "def test_it():",
            "    pass",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    refs = impact.tests_for("mypackage.mymodule::my_func")
    assert len(refs) == 1
    assert refs[0].test_function == "test_it"


def test_covers_directive_extraction():
    source = "\n".join([
        "# lintgate: covers pkg.mod::func_a",
        "def test_alpha():",
        "    pass",
        "",
        "# lintgate: covers pkg.mod::func_b",
        "def test_beta():",
        "    pass",
    ])
    func_lines = {"test_alpha": 2, "test_beta": 6}
    result = _extract_covers_directives(source, func_lines)
    assert "test_alpha" in result
    assert result["test_alpha"] == ["pkg.mod::func_a"]
    assert "test_beta" in result
    assert result["test_beta"] == ["pkg.mod::func_b"]


# ── Ambiguous bare name blocking ──────────────────────────────────


def test_ambiguous_bare_name_blocked():
    """from_dict lookup returns empty when only bare entry exists."""
    ref = TestReference(test_file="t.py", test_function="test_x")
    m = TestImpactMap(function_to_tests={"from_dict": [ref]})
    # Direct bare lookup works
    assert len(m.tests_for("from_dict")) == 1
    # But qualified lookup with ambiguous bare fallback is blocked
    assert m.tests_for("some_module.from_dict") == []


def test_ambiguous_bare_names_list():
    """Verify known ambiguous names are in the blocklist."""
    assert "from_dict" in _AMBIGUOUS_BARE_NAMES
    assert "to_dict" in _AMBIGUOUS_BARE_NAMES
    assert "__init__" in _AMBIGUOUS_BARE_NAMES
    assert "run" in _AMBIGUOUS_BARE_NAMES


# ── tests_for qualified primary lookup ────────────────────────────


def test_tests_for_qualified_primary():
    """Qualified name lookup succeeds directly without fallback."""
    ref = TestReference(test_file="t.py", test_function="test_x")
    m = TestImpactMap(function_to_tests={"mymodule.helper": [ref]})
    result = m.tests_for("mymodule.helper")
    assert len(result) == 1
    assert result[0].test_function == "test_x"


def test_tests_for_bare_fallback_non_ambiguous():
    """Bare fallback works for non-ambiguous names when qualified key is missing."""
    ref = TestReference(test_file="t.py", test_function="test_x")
    m = TestImpactMap(function_to_tests={"compute": [ref]})
    # Qualified lookup falls back to bare "compute" (not ambiguous)
    result = m.tests_for("mymodule.compute")
    assert len(result) == 1


# ── Naming-strategy synthetic targets ────────────────────────────


def test_class_name_synth_links_snake_case_private_target(tmp_path):
    """TestComputeScore.test_* links to _compute_score via naming-strategy fallback.

    The test body doesn't call _compute_score directly — it wraps the call
    via an external helper — so only the class-name-to-snake-case synthesis
    can produce the linkage.
    """
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "\n".join([
            "class TestComputeScore:",
            "    def test_solo_dev_full_setup(self):",
            "        external_wrapper()",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    refs = impact.tests_for("_compute_score")
    assert len(refs) == 1
    assert refs[0].test_function == "TestComputeScore.test_solo_dev_full_setup"


def test_class_name_synth_also_links_non_private_target(tmp_path):
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "\n".join([
            "class TestComputeScore:",
            "    def test_basic(self):",
            "        pass",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    assert len(impact.tests_for("compute_score")) == 1
    assert len(impact.tests_for("ComputeScore")) == 1


def test_free_test_synth_links_private_target(tmp_path):
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "\n".join([
            "def test_detect_lockfile():",
            "    wrapper()",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    assert len(impact.tests_for("detect_lockfile")) == 1
    assert len(impact.tests_for("_detect_lockfile")) == 1


# ── 1-hop helper follow ──────────────────────────────────────────


def test_one_hop_helper_exposes_wrapped_call(tmp_path):
    """Test calling a same-file helper links to what the helper calls."""
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "\n".join([
            "def _run_full_setup():",
            "    return deep_target()",
            "",
            "def test_solo_dev():",
            "    _run_full_setup()",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    # Direct call to helper still maps
    assert len(impact.tests_for("_run_full_setup")) == 1
    # 1-hop: helper's call to deep_target flows through to the test
    refs = impact.tests_for("deep_target")
    assert len(refs) == 1
    assert refs[0].test_function == "test_solo_dev"


def test_one_hop_does_not_recurse(tmp_path):
    """Only one hop is followed — helper-of-helper is not inlined."""
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "\n".join([
            "def _inner():",
            "    return second_level()",
            "",
            "def _outer():",
            "    return _inner()",
            "",
            "def test_it():",
            "    _outer()",
        ])
    )
    impact = build_test_impact_map([str(test_file)])
    # Outer's direct call (_inner) becomes visible via 1-hop
    assert len(impact.tests_for("_inner")) == 1
    # But _inner's call (second_level) is NOT reached — that would require 2 hops
    assert len(impact.tests_for("second_level")) == 0
