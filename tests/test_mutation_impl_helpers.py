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


def test_load_all_tests_from_files_accepts_non_test_prefix_classes(tmp_path):
    test_file = tmp_path / "tests" / "test_proof_auditor.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class ValidationSuite:\n"
        "    def test_accepts_valid_input(self):\n"
        "        assert True\n"
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
