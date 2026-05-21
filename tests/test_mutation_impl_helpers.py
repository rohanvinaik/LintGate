"""Tests for mutation helper internals: discovery and callable loading."""

from __future__ import annotations

from unittest.mock import patch

from mcp_tools._mutation_impl import (
    _load_all_tests_from_files,
    discover_test_files,
    load_test_callables,
)


def test_discover_test_files_recurses_nested_dirs(tmp_path):
    src = tmp_path / "proof_auditor.py"
    src.write_text("def check(x):\n    return x > 0\n")

    nested = tmp_path / "tests" / "unit" / "core"
    nested.mkdir(parents=True)
    test_file = nested / "test_proof_auditor.py"
    test_file.write_text("def test_check():\n    assert True\n")

    found = discover_test_files(str(tmp_path), str(src))
    assert str(test_file) in found


def test_discover_test_files_finds_generated_path_safe_name(tmp_path):
    src = tmp_path / "lintgate" / "foo" / "bar.py"
    src.parent.mkdir(parents=True)
    src.write_text("def check(x):\n    return x > 0\n")

    gen_dir = tmp_path / "tests" / "generated"
    gen_dir.mkdir(parents=True)
    gen_file = gen_dir / "test_lintgate_foo_bar.py"
    gen_file.write_text("def test_check():\n    assert True\n")

    found = discover_test_files(str(tmp_path), str(src))
    assert str(gen_file) in found


def test_discover_test_files_strips_leading_underscore_from_private_module_names(tmp_path):
    src = tmp_path / "lintgate" / "_dep_health_checks.py"
    src.parent.mkdir(parents=True)
    src.write_text("def check(x):\n    return x > 0\n")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    test_file = tests_dir / "test_dep_health_checks.py"
    test_file.write_text("def test_check():\n    assert True\n")

    found = discover_test_files(str(tmp_path), str(src))
    assert str(test_file) in found


def test_load_all_tests_from_files_accepts_non_test_prefix_classes(tmp_path):
    test_file = tmp_path / "tests" / "test_proof_auditor.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class ValidationSuite:\n    def test_accepts_valid_input(self):\n        assert True\n"
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")

    loaded = _load_all_tests_from_files([str(test_file)])
    names = sorted(fn.__name__ for fn in loaded)

    assert "test_accepts_valid_input" in names


def test_load_test_callables_falls_back_when_ref_imports_resolve_to_empty():
    class _Impact:
        def tests_for(self, _func_name: str):
            return [object()]  # Non-empty refs path

    fallback = [lambda: None]

    with (
        patch(
            "lintgate.specification.test_impact.build_test_impact_map",
            return_value=_Impact(),
        ),
        patch("mcp_tools._mutation_impl._import_test_functions", return_value=[]),
        patch("mcp_tools._mutation_impl._load_all_tests_from_files", return_value=fallback),
    ):
        loaded, diag = load_test_callables(["tests/test_demo.py"], "target_func")

    assert loaded == fallback
    assert diag.fallback_used is True


def test_load_test_callables_flags_weak_linkage_when_fallback_loads_too_few(tmp_path):
    test_file = tmp_path / "tests" / "test_dep_health_checks.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "\n".join(
            [
                "class TestHealth:",
                "    def test_one(self):",
                "        assert True",
                "    def test_two(self):",
                "        assert True",
                "    def test_three(self):",
                "        assert True",
            ]
        )
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

    with patch("mcp_tools._mutation_impl._load_all_tests_from_files", return_value=[lambda: None]):
        loaded, diag = load_test_callables([str(test_file)], "target_func")

    assert len(loaded) == 1
    assert diag.fallback_used is True
    assert diag.ast_test_callables == 3
    assert diag.weak_linkage_suspected is True
    assert diag.to_dict()["sanity_warning"]


def test_load_test_callables_flags_weak_linkage_on_zero_impact_refs(tmp_path):
    """New signal: when fallback is used and impact_map_refs=0 yet tests exist,
    flag weak linkage even if the callable count would otherwise look fine."""
    test_file = tmp_path / "tests" / "test_unrelated_module.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "\n".join([
            "def test_alpha():",
            "    assert True",
            "def test_beta():",
            "    assert True",
            "def test_gamma():",
            "    assert True",
            "def test_delta():",
            "    assert True",
        ])
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

    fallback = [lambda: None for _ in range(4)]
    with patch("mcp_tools._mutation_impl._load_all_tests_from_files", return_value=fallback):
        _, diag = load_test_callables([str(test_file)], "nonexistent_target_fn")

    assert diag.fallback_used is True
    assert diag.impact_map_refs == 0
    assert diag.ast_test_callables == 4
    assert diag.callables_loaded == 4
    # Weak linkage is flagged because nothing linked, even though callable count is fine
    assert diag.weak_linkage_suspected is True


def test_load_test_callables_records_linkage_divergence(tmp_path):
    """When static returns refs for a function but dynamic finds disjoint tests,
    the divergence diagnostic fires."""
    from lintgate.specification.test_impact import TestReference

    test_file = tmp_path / "tests" / "test_demo.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "\n".join([
            "def test_alpha():",
            "    target_func()",
        ])
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

    # Dynamic finds a totally different test than static would.
    dynamic_refs = [
        TestReference(test_file=str(test_file), test_function="test_beta"),
    ]

    with (
        patch(
            "mcp_tools._mutation_impl._resolve_dynamic_linkage",
            return_value=dynamic_refs,
        ),
        patch(
            "mcp_tools._mutation_impl._import_test_functions",
            return_value=[lambda: None],
        ),
    ):
        _, diag = load_test_callables(
            [str(test_file)],
            "target_func",
            project_root=str(tmp_path),
            func_key="demo.py::target_func",
        )

    assert diag.linkage_divergence == "disjoint"
    assert diag.dynamic_refs_count == 1
    assert diag.static_refs_count == 1
