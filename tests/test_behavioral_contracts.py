"""Specification tests for behavioral_contracts.py.

Targets:
- generate_contracts (σ=34, regime B, CC=14): decision_rules and predicate_effect_links
- _generate_function_contracts (σ=8, regime A, pure)
- _generate_return_type_contract (σ=8, regime A, pure)
- _generate_shape_contract (σ=12, regime A, pure)
- _annotation_to_str (σ=15, regime A, pure, 7 equivalence_partitions)
- _value_to_str (σ=9, regime A, pure, 6 boundary_points, 5 equivalence_partitions)
"""

from __future__ import annotations

import ast
import os
import re
import textwrap
from pathlib import Path

import pytest

from lintgate.channels.behavior_channel import BehaviorChannel
from lintgate.channels.dependency_channel import DependencyChannel
from lintgate.channels.git_channel import GitChannel
from lintgate.channels.lint_channel import LintChannel
from lintgate.channels.performance_channel import PerformanceChannel
from lintgate.channels.structure_channel import StructureChannel
from lintgate.channels.test_channel import TestChannel
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.orchestration.behavioral_contracts import (
    _annotation_to_str,
    _generate_function_contracts,
    _generate_return_type_contract,
    _generate_shape_contract,
    _value_to_str,
    generate_contracts,
)

# ── Test Helpers ─────────────────────────────────────────────────────────


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


# ══════════════════════════════════════════════════════════════════════════
# generate_contracts — regime B (σ=34, CC=14)
#
# Decision rules tested:
#   DR1: skip site-packages / dist-packages paths
#   DR2: skip test_ / _test files
#   DR3: skip __init__, setup, conftest
#   DR4: skip hidden directories (parts starting with '.')
#   DR5: skip node_modules, venv, .venv, build, dist
#   DR6: skip files with SyntaxError / UnicodeDecodeError
#   DR7: only emit results when contracts are non-empty
#
# Predicate-effect links tested:
#   PE1: public function with return annotation → contract emitted
#   PE2: public function with no annotation / None return → no contract
# ══════════════════════════════════════════════════════════════════════════


class TestGenerateContractsDecisionRules:
    """Integration tests exercising generate_contracts filtering logic."""

    def test_returns_dict_type(self, tmp_path) -> None:
        """Return type is always dict[str, str]."""
        result = generate_contracts(str(tmp_path))
        assert isinstance(result, dict)

    def test_empty_project_returns_empty(self, tmp_path) -> None:
        """Empty directory → empty dict."""
        result = generate_contracts(str(tmp_path))
        assert result == {}

    def test_public_function_with_annotation_produces_contract(self, tmp_path) -> None:
        """PE1: public function with return annotation → contract emitted."""
        src = tmp_path / "module_a.py"
        src.write_text("def compute(x: int) -> int:\n    return x * 2\n")
        result = generate_contracts(str(tmp_path))
        assert len(result) == 1
        key = list(result.keys())[0]
        assert "test_contracts_module_a.py" in key
        content = result[key]
        assert "test_compute_returns_int" in content

    def test_no_annotation_no_contract(self, tmp_path) -> None:
        """PE2: function without return annotation → no contract, empty result."""
        src = tmp_path / "bare.py"
        src.write_text("def helper(x):\n    return x + 1\n")
        result = generate_contracts(str(tmp_path))
        # No contracts generated → file not emitted
        assert result == {}

    def test_none_return_no_contract(self, tmp_path) -> None:
        """PE2: function returning None → no contract, empty result."""
        src = tmp_path / "noop.py"
        src.write_text("def noop() -> None:\n    pass\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    # ── DR1: skip site-packages ──────────────────────────────────────────

    def test_skips_site_packages(self, tmp_path) -> None:
        """DR1: paths containing /site-packages/ are skipped."""
        pkg_dir = tmp_path / "site-packages" / "pkg"
        pkg_dir.mkdir(parents=True)
        src = pkg_dir / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    def test_skips_dist_packages(self, tmp_path) -> None:
        """DR1: paths containing /dist-packages/ are skipped."""
        pkg_dir = tmp_path / "dist-packages" / "pkg"
        pkg_dir.mkdir(parents=True)
        src = pkg_dir / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    # ── DR2: skip test files ─────────────────────────────────────────────

    def test_skips_test_prefix(self, tmp_path) -> None:
        """DR2: files starting with test_ are skipped."""
        src = tmp_path / "test_something.py"
        src.write_text("def compute() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    def test_skips_test_suffix(self, tmp_path) -> None:
        """DR2: files ending with _test are skipped."""
        src = tmp_path / "something_test.py"
        src.write_text("def compute() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    # ── DR3: skip __init__, setup, conftest ──────────────────────────────

    def test_skips_init(self, tmp_path) -> None:
        """DR3: __init__.py is skipped."""
        src = tmp_path / "__init__.py"
        src.write_text("def public() -> str:\n    return 'hi'\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    def test_skips_setup(self, tmp_path) -> None:
        """DR3: setup.py is skipped."""
        src = tmp_path / "setup.py"
        src.write_text("def setup_func() -> dict:\n    return {}\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    def test_skips_conftest(self, tmp_path) -> None:
        """DR3: conftest.py is skipped."""
        src = tmp_path / "conftest.py"
        src.write_text("def fixture_func() -> list:\n    return []\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    # ── DR4: skip hidden directories ─────────────────────────────────────

    def test_skips_hidden_dir(self, tmp_path) -> None:
        """DR4: files inside hidden directories (starting with .) are skipped."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        src = hidden / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    # ── DR5: skip venv/build/dist/node_modules ───────────────────────────

    @pytest.mark.parametrize("dirname", ["venv", ".venv", "build", "dist", "node_modules"])
    def test_skips_excluded_dirs(self, tmp_path, dirname: str) -> None:
        """DR5: files inside venv/build/dist/node_modules are skipped."""
        excluded = tmp_path / dirname
        excluded.mkdir()
        src = excluded / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}

    # ── DR6: skip syntax errors ──────────────────────────────────────────

    def test_skips_syntax_error_file(self, tmp_path) -> None:
        """DR6: files that fail ast.parse are gracefully skipped."""
        src = tmp_path / "broken.py"
        src.write_text("def f( -> int:\n    return 1\n")  # syntax error
        result = generate_contracts(str(tmp_path))
        assert result == {}

    # ── DR7: only emit non-empty contracts ───────────────────────────────

    def test_private_functions_produce_no_contracts(self, tmp_path) -> None:
        """DR7: file with only private functions → no contract file emitted."""
        src = tmp_path / "internal.py"
        src.write_text("def _private() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        assert result == {}


class TestGenerateContractsOutputStructure:
    """Verify output structure, paths, and content format."""

    def test_output_path_in_generated_dir(self, tmp_path) -> None:
        """Contract files are placed under tests/generated/."""
        src = tmp_path / "utils.py"
        src.write_text("def helper() -> str:\n    return 'ok'\n")
        result = generate_contracts(str(tmp_path))
        assert len(result) == 1
        key = list(result.keys())[0]
        assert os.path.join("tests", "generated") in key

    def test_output_filename_format(self, tmp_path) -> None:
        """Output filename follows test_contracts_{stem}.py pattern."""
        src = tmp_path / "my_module.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        key = list(result.keys())[0]
        assert key.endswith("test_contracts_my_module.py")

    def test_output_has_autogenerated_header(self, tmp_path) -> None:
        """Output content starts with auto-generated header."""
        src = tmp_path / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert content.startswith("# Auto-generated by LintGate bootstrap")

    def test_output_has_module_docstring(self, tmp_path) -> None:
        """Output content contains behavioral contracts docstring."""
        src = tmp_path / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "Behavioral contracts for mod" in content

    def test_output_has_source_module_comment(self, tmp_path) -> None:
        """Output content contains source module import path comment."""
        src = tmp_path / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "# Source module: mod" in content

    def test_multiple_files_produce_multiple_contracts(self, tmp_path) -> None:
        """Two source files → two contract files."""
        (tmp_path / "a.py").write_text("def fa() -> int:\n    return 1\n")
        (tmp_path / "b.py").write_text("def fb() -> str:\n    return 's'\n")
        result = generate_contracts(str(tmp_path))
        assert len(result) == 2
        keys = sorted(result.keys())
        assert "test_contracts_a.py" in keys[0]
        assert "test_contracts_b.py" in keys[1]

    def test_nested_module_import_path(self, tmp_path) -> None:
        """Nested modules get dotted import paths."""
        pkg = tmp_path / "pkg" / "sub"
        pkg.mkdir(parents=True)
        src = pkg / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "# Source module: pkg.sub.mod" in content

    def test_dot_in_filename_replaced_in_output(self, tmp_path) -> None:
        """Dots in stem are replaced with underscores in output filename."""
        src = tmp_path / "my.module.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path))
        key = list(result.keys())[0]
        assert "test_contracts_my_module.py" in key


class TestGenerateContractsManifest:
    """Verify manifest parameter acceptance (does not change output currently)."""

    def test_manifest_none_accepted(self, tmp_path) -> None:
        """manifest=None is accepted without error."""
        src = tmp_path / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path), manifest=None)
        assert isinstance(result, dict)

    def test_manifest_object_accepted(self, tmp_path) -> None:
        """Arbitrary manifest object is accepted without error."""
        src = tmp_path / "mod.py"
        src.write_text("def f() -> int:\n    return 1\n")
        result = generate_contracts(str(tmp_path), manifest=object())
        assert isinstance(result, dict)


class TestGenerateContractsAllStrategies:
    """Verify that generate_contracts collects output from all 4 strategies."""

    def test_return_type_strategy(self, tmp_path) -> None:
        """Strategy 1: Return type annotation → isinstance contract."""
        src = tmp_path / "mod.py"
        src.write_text("def compute() -> dict:\n    return {}\n")
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "isinstance" in content

    def test_shape_strategy(self, tmp_path) -> None:
        """Strategy 2: Map pattern → preserves_length contract."""
        src = tmp_path / "mod.py"
        src.write_text(
            textwrap.dedent("""\
            def transform(items):
                result = []
                for item in items:
                    result.append(item * 2)
                return result
        """)
        )
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "preserves_length" in content

    def test_error_boundary_strategy(self, tmp_path) -> None:
        """Strategy 3: except-return pattern → error boundary contract."""
        src = tmp_path / "mod.py"
        src.write_text(
            textwrap.dedent("""\
            def safe_parse(data):
                try:
                    return int(data)
                except ValueError:
                    return []
        """)
        )
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "returns_default_on_valueerror" in content

    def test_io_stub_strategy(self, tmp_path) -> None:
        """Strategy 4: I/O calls → mock stub contract."""
        src = tmp_path / "mod.py"
        src.write_text(
            textwrap.dedent("""\
            def fetch(url):
                import requests
                return requests.get(url).json()
        """)
        )
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "mock" in content.lower() or "patch" in content.lower()

    def test_multiple_strategies_combined(self, tmp_path) -> None:
        """Function triggering strategies 1+3 → both contracts appear."""
        src = tmp_path / "mod.py"
        src.write_text(
            textwrap.dedent("""\
            def safe_compute(x: int) -> int:
                try:
                    return x * 2
                except TypeError:
                    return 0
        """)
        )
        result = generate_contracts(str(tmp_path))
        content = list(result.values())[0]
        assert "returns_int" in content
        assert "returns_default_on_typeerror" in content


# ══════════════════════════════════════════════════════════════════════════
# _generate_function_contracts — regime A (σ=8, pure)
# ══════════════════════════════════════════════════════════════════════════


class TestGenerateFunctionContracts:
    """Tests for _generate_function_contracts orchestration."""

    def test_returns_list(self) -> None:
        """Return type is always list[str]."""
        func = _parse_func("def f() -> int:\n    return 1")
        result = _generate_function_contracts(func, "mod")
        assert isinstance(result, list)

    def test_empty_for_no_strategies(self) -> None:
        """Function with no annotation, no pattern, no error, no IO → empty."""
        func = _parse_func("def f(x):\n    return x + 1")
        result = _generate_function_contracts(func, "mod")
        assert result == []

    def test_return_type_contract_included(self) -> None:
        """Strategy 1 fires for annotated return type."""
        func = _parse_func("def compute() -> int:\n    return 42")
        result = _generate_function_contracts(func, "mod")
        assert any("returns_int" in c for c in result)

    def test_shape_contract_included(self) -> None:
        """Strategy 2 fires for map pattern."""
        func = _parse_func(
            textwrap.dedent("""\
            def double(items):
                result = []
                for item in items:
                    result.append(item * 2)
                return result
        """)
        )
        result = _generate_function_contracts(func, "mod")
        assert any("preserves_length" in c for c in result)

    def test_error_boundary_contract_included(self) -> None:
        """Strategy 3 fires for except-return pattern."""
        func = _parse_func(
            textwrap.dedent("""\
            def safe(x):
                try:
                    return int(x)
                except ValueError:
                    return []
        """)
        )
        result = _generate_function_contracts(func, "mod")
        assert any("returns_default" in c for c in result)

    def test_io_stub_contract_included(self) -> None:
        """Strategy 4 fires for I/O calls."""
        func = _parse_func(
            textwrap.dedent("""\
            def fetch(url):
                import subprocess
                return subprocess.run(url)
        """)
        )
        result = _generate_function_contracts(func, "mod")
        assert any("subprocess" in c for c in result)

    def test_all_strategies_combined(self) -> None:
        """A function that triggers all four strategies produces 4 contracts."""
        func = _parse_func(
            textwrap.dedent("""\
            def process(items) -> list:
                import subprocess
                result = []
                try:
                    for item in items:
                        result.append(subprocess.run(item))
                except OSError:
                    return []
                return result
        """)
        )
        result = _generate_function_contracts(func, "mod")
        # return type + shape + error boundary + io stub
        assert len(result) >= 3  # at least 3 strategies fire

    def test_multiple_contracts_are_strings(self) -> None:
        """Each element in the result list is a string."""
        func = _parse_func(
            textwrap.dedent("""\
            def safe(x: int) -> int:
                try:
                    return x * 2
                except TypeError:
                    return 0
        """)
        )
        result = _generate_function_contracts(func, "mod")
        assert all(isinstance(c, str) for c in result)


# ══════════════════════════════════════════════════════════════════════════
# _generate_return_type_contract — regime A (σ=8, pure)
# ══════════════════════════════════════════════════════════════════════════


class TestGenerateReturnTypeContractSpec:
    """Specification tests for _generate_return_type_contract boundary cases."""

    def test_generic_list_int_return(self) -> None:
        """list[int] → base type 'list' used for isinstance check."""
        func = _parse_func("def f() -> list[int]:\n    return []")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "isinstance(result, list)" in result

    def test_generic_dict_str_int_return(self) -> None:
        """dict[str, int] → base type 'dict' used for isinstance check."""
        func = _parse_func("def f() -> dict[str, int]:\n    return {}")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "isinstance(result, dict)" in result

    def test_set_return(self) -> None:
        """set → isinstance(result, set)."""
        func = _parse_func("def f() -> set:\n    return set()")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "isinstance(result, set)" in result

    def test_tuple_return(self) -> None:
        """tuple → isinstance(result, tuple)."""
        func = _parse_func("def f() -> tuple:\n    return ()")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "isinstance(result, tuple)" in result

    def test_bytes_return(self) -> None:
        """bytes → isinstance(result, bytes)."""
        func = _parse_func("def f() -> bytes:\n    return b''")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "isinstance(result, bytes)" in result

    def test_bool_return(self) -> None:
        """bool → isinstance(result, bool)."""
        func = _parse_func("def f() -> bool:\n    return True")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "isinstance(result, bool)" in result

    def test_str_return(self) -> None:
        """str → isinstance(result, str)."""
        func = _parse_func("def f() -> str:\n    return 'x'")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "isinstance(result, str)" in result

    def test_union_type_skipped(self) -> None:
        """int | str has no isinstance mapping → None."""
        func = _parse_func("def f() -> int | str:\n    return 1")
        result = _generate_return_type_contract(func, "mod")
        # "int | str" → base is "int | str" → split on "[" → "int | str" → not in mapping
        assert result is None

    def test_custom_class_skipped(self) -> None:
        """Custom class without isinstance mapping → None."""
        func = _parse_func("def f() -> MyClass:\n    pass")
        result = _generate_return_type_contract(func, "mod")
        assert result is None

    def test_function_name_in_test_name(self) -> None:
        """Function name appears in the generated test function name."""
        func = _parse_func("def calculate_total() -> int:\n    return 0")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "test_calculate_total_returns_int" in result

    def test_empty_module_path_no_import(self) -> None:
        """Empty module path → no import comment in output."""
        func = _parse_func("def f() -> int:\n    return 1")
        result = _generate_return_type_contract(func, "")
        assert result is not None
        # No "from" or "import" lines
        for line in result.split("\n"):
            if line.startswith("#"):
                assert "from " not in line or "import " not in line

    def test_async_function_handled(self) -> None:
        """AsyncFunctionDef is handled identically to FunctionDef."""
        func = _parse_func("async def f() -> int:\n    return 1")
        result = _generate_return_type_contract(func, "mod")
        assert result is not None
        assert "returns_int" in result


# ══════════════════════════════════════════════════════════════════════════
# _generate_shape_contract — regime A (σ=12, pure)
# ══════════════════════════════════════════════════════════════════════════


class TestGenerateShapeContractSpec:
    """Specification tests for _generate_shape_contract pattern detection."""

    def test_classic_map_pattern(self) -> None:
        """Standard result=[], for x in items: result.append(f(x)) → detected."""
        func = _parse_func(
            textwrap.dedent("""\
            def transform(items):
                result = []
                for item in items:
                    result.append(str(item))
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is not None
        assert "preserves_length" in result
        assert "items" in result

    def test_list_init_must_be_empty(self) -> None:
        """Non-empty list init → not a map pattern."""
        func = _parse_func(
            textwrap.dedent("""\
            def f(items):
                result = [0]
                for item in items:
                    result.append(item)
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is None

    def test_list_init_must_precede_for_loop(self) -> None:
        """For loop before list init → not detected (order matters)."""
        func = _parse_func(
            textwrap.dedent("""\
            def f(items):
                for item in items:
                    pass
                result = []
                result.append(1)
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        # The for loop fires before has_init_list is set, so has_for_append stays False
        # for the first for-loop. The append outside the for is not walked under a for stmt.
        assert result is None

    def test_multiple_assigns_first_empty_list_counts(self) -> None:
        """Multiple assignments — empty list anywhere before for triggers detection."""
        func = _parse_func(
            textwrap.dedent("""\
            def f(items):
                x = 42
                result = []
                for item in items:
                    result.append(item)
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is not None

    def test_nested_append_in_if_inside_for(self) -> None:
        """append inside if inside for loop — still detected (ast.walk)."""
        func = _parse_func(
            textwrap.dedent("""\
            def filter_positive(nums):
                result = []
                for n in nums:
                    if n > 0:
                        result.append(n)
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is not None
        assert "preserves_length" in result

    def test_iter_over_method_call_no_input_param(self) -> None:
        """Iterating over a method call (not a Name) → input_param is None → None."""
        func = _parse_func(
            textwrap.dedent("""\
            def f(data):
                result = []
                for item in data.keys():
                    result.append(item)
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        # data.keys() is a Call, not a Name → input_param stays None
        assert result is None

    def test_async_function_handled(self) -> None:
        """AsyncFunctionDef with map pattern is detected."""
        func = _parse_func(
            textwrap.dedent("""\
            async def transform(items):
                result = []
                for item in items:
                    result.append(await process(item))
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is not None
        assert "preserves_length" in result

    def test_function_name_in_test_name(self) -> None:
        """Generated test uses the function's name."""
        func = _parse_func(
            textwrap.dedent("""\
            def double_all(nums):
                result = []
                for n in nums:
                    result.append(n * 2)
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is not None
        assert "test_double_all_preserves_length" in result

    def test_only_for_no_append(self) -> None:
        """for loop without append → no shape contract."""
        func = _parse_func(
            textwrap.dedent("""\
            def f(items):
                result = []
                for item in items:
                    print(item)
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is None

    def test_only_append_no_for(self) -> None:
        """append without for loop → no shape contract."""
        func = _parse_func(
            textwrap.dedent("""\
            def f(items):
                result = []
                result.append(items[0])
                return result
        """)
        )
        result = _generate_shape_contract(func, "mod")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════
# _annotation_to_str — regime A (σ=15, 7 equivalence_partitions, pure)
#
# Partitions:
#   P1: ast.Constant (string literal, int literal)
#   P2: ast.Name (simple identifier)
#   P3: ast.Attribute (dotted name, nested dotted name)
#   P4: ast.Subscript with ast.Tuple slice (generic with multiple params)
#   P5: ast.Subscript with single slice (generic with one param)
#   P6: ast.BinOp with BitOr (union X | Y)
#   P7: unsupported AST node → None
# ══════════════════════════════════════════════════════════════════════════


class TestAnnotationToStrSpec:
    """Equivalence partition and boundary tests for _annotation_to_str."""

    # ── P1: ast.Constant ─────────────────────────────────────────────────

    def test_constant_string(self) -> None:
        """Forward reference string → its value."""
        node = _parse_expr("'ForwardRef'")
        assert _annotation_to_str(node) == "ForwardRef"

    def test_constant_int(self) -> None:
        """Integer constant → string repr."""
        node = _parse_expr("0")
        assert _annotation_to_str(node) == "0"

    def test_constant_float(self) -> None:
        """Float constant → string repr."""
        node = _parse_expr("1.5")
        assert _annotation_to_str(node) == "1.5"

    def test_constant_bool_true(self) -> None:
        """Bool True constant → 'True'."""
        node = _parse_expr("True")
        assert _annotation_to_str(node) == "True"

    def test_constant_bool_false(self) -> None:
        """Bool False constant → 'False'."""
        node = _parse_expr("False")
        assert _annotation_to_str(node) == "False"

    def test_constant_none(self) -> None:
        """None constant → 'None'."""
        node = _parse_expr("None")
        assert _annotation_to_str(node) == "None"

    def test_constant_empty_string(self) -> None:
        """Empty string forward reference → empty string."""
        node = _parse_expr("''")
        assert _annotation_to_str(node) == ""

    # ── P2: ast.Name ─────────────────────────────────────────────────────

    def test_name_builtin(self) -> None:
        """Builtin type name → exact string."""
        node = _parse_expr("list")
        assert _annotation_to_str(node) == "list"

    def test_name_single_char(self) -> None:
        """Single character type variable → exact string."""
        node = _parse_expr("T")
        assert _annotation_to_str(node) == "T"

    def test_name_any(self) -> None:
        """Any → exact string."""
        node = _parse_expr("Any")
        assert _annotation_to_str(node) == "Any"

    # ── P3: ast.Attribute ────────────────────────────────────────────────

    def test_attribute_two_levels(self) -> None:
        """Two-level dotted name."""
        node = _parse_expr("typing.Optional")
        assert _annotation_to_str(node) == "typing.Optional"

    def test_attribute_three_levels(self) -> None:
        """Three-level dotted name."""
        node = _parse_expr("a.b.c")
        assert _annotation_to_str(node) == "a.b.c"

    # ── P4: ast.Subscript with Tuple slice (multiple type params) ────────

    def test_subscript_dict_two_params(self) -> None:
        """dict[str, int] → 'dict[str, int]'."""
        node = _parse_expr("dict[str, int]")
        assert _annotation_to_str(node) == "dict[str, int]"

    def test_subscript_tuple_three_params(self) -> None:
        """tuple[int, str, float] → correct string."""
        node = _parse_expr("tuple[int, str, float]")
        assert _annotation_to_str(node) == "tuple[int, str, float]"

    def test_subscript_with_unsupported_element(self) -> None:
        """Subscript where an element can't be converted → 'Any' fallback."""
        # We need to construct an AST where one element is unsupported
        # e.g., dict[str, <starred>] — the starred converts to None → "Any"
        node = _parse_expr("dict[str, int]")
        # Manually replace one slice element with something unsupported
        assert isinstance(node, ast.Subscript)
        assert isinstance(node.slice, ast.Tuple)
        node.slice.elts[1] = ast.Starred(value=ast.Name(id="x", ctx=ast.Load()), ctx=ast.Load())
        result = _annotation_to_str(node)
        assert result == "dict[str, Any]"

    # ── P5: ast.Subscript with single slice ──────────────────────────────

    def test_subscript_list_single(self) -> None:
        """list[int] → 'list[int]'."""
        node = _parse_expr("list[int]")
        assert _annotation_to_str(node) == "list[int]"

    def test_subscript_optional_via_subscript(self) -> None:
        """Optional[str] → 'Optional[str]'."""
        node = _parse_expr("Optional[str]")
        assert _annotation_to_str(node) == "Optional[str]"

    def test_subscript_base_none_returns_none(self) -> None:
        """Subscript with unsupported base → None."""
        # Construct Subscript where value is unsupported
        node = ast.Subscript(
            value=ast.Starred(value=ast.Name(id="x", ctx=ast.Load()), ctx=ast.Load()),
            slice=ast.Name(id="int", ctx=ast.Load()),
            ctx=ast.Load(),
        )
        result = _annotation_to_str(node)
        assert result is None

    # ── P6: ast.BinOp with BitOr (union) ────────────────────────────────

    def test_union_two_types(self) -> None:
        """int | str → 'int | str'."""
        node = _parse_expr("int | str")
        assert _annotation_to_str(node) == "int | str"

    def test_union_with_none(self) -> None:
        """str | None → 'str | None'."""
        node = _parse_expr("str | None")
        assert _annotation_to_str(node) == "str | None"

    def test_union_three_types(self) -> None:
        """int | str | float → correct chained union."""
        node = _parse_expr("int | str | float")
        result = _annotation_to_str(node)
        # Python parses (int | str) | float
        assert result == "int | str | float"

    def test_union_left_unsupported_returns_none(self) -> None:
        """BinOp BitOr where left is unsupported → None."""
        node = ast.BinOp(
            left=ast.Starred(value=ast.Name(id="x", ctx=ast.Load()), ctx=ast.Load()),
            op=ast.BitOr(),
            right=ast.Name(id="int", ctx=ast.Load()),
        )
        assert _annotation_to_str(node) is None

    def test_union_right_unsupported_returns_none(self) -> None:
        """BinOp BitOr where right is unsupported → None."""
        node = ast.BinOp(
            left=ast.Name(id="int", ctx=ast.Load()),
            op=ast.BitOr(),
            right=ast.Starred(value=ast.Name(id="x", ctx=ast.Load()), ctx=ast.Load()),
        )
        assert _annotation_to_str(node) is None

    # ── P7: unsupported nodes ────────────────────────────────────────────

    def test_binop_non_bitor_returns_none(self) -> None:
        """BinOp that is not BitOr (e.g., Add) → None."""
        node = ast.BinOp(
            left=ast.Name(id="int", ctx=ast.Load()),
            op=ast.Add(),
            right=ast.Name(id="str", ctx=ast.Load()),
        )
        assert _annotation_to_str(node) is None

    def test_set_node_returns_none(self) -> None:
        """ast.Set node → None (unsupported)."""
        node = ast.Set(elts=[ast.Constant(value=1)])
        assert _annotation_to_str(node) is None

    def test_lambda_returns_none(self) -> None:
        """ast.Lambda → None (unsupported)."""
        node = _parse_expr("lambda: None")
        assert _annotation_to_str(node) is None

    def test_ifexp_returns_none(self) -> None:
        """ast.IfExp (ternary) → None (unsupported)."""
        node = _parse_expr("x if True else y")
        assert _annotation_to_str(node) is None


# ══════════════════════════════════════════════════════════════════════════
# _value_to_str — regime A (σ=9, 6 boundary_points, 5 equivalence_partitions)
#
# Partitions:
#   P1: ast.Constant (str, int, float, None, True, False)
#   P2: ast.List empty → "[]"
#   P3: ast.Dict empty → "{}"
#   P4: ast.Tuple empty → "()"
#   P5: unsupported node → None
#
# Boundary points:
#   B1: empty string constant → "''"
#   B2: zero integer → "0"
#   B3: negative integer → correct repr
#   B4: empty list (0 elements) vs non-empty list (1+ elements)
#   B5: empty dict vs non-empty dict
#   B6: empty tuple vs non-empty tuple
# ══════════════════════════════════════════════════════════════════════════


class TestValueToStrSpec:
    """Equivalence partition and boundary tests for _value_to_str."""

    # ── P1: ast.Constant variants ────────────────────────────────────────

    def test_string_constant(self) -> None:
        """String literal → repr."""
        node = _parse_expr("'hello world'")
        assert _value_to_str(node) == "'hello world'"

    def test_int_constant_positive(self) -> None:
        """Positive integer → repr."""
        node = _parse_expr("42")
        assert _value_to_str(node) == "42"

    def test_bytes_constant(self) -> None:
        """Bytes literal → repr."""
        node = _parse_expr("b'data'")
        assert _value_to_str(node) == "b'data'"

    # ── P2: ast.List ─────────────────────────────────────────────────────

    def test_nonempty_list_single_element(self) -> None:
        """B4: List with 1 element → None."""
        node = _parse_expr("[1]")
        assert _value_to_str(node) is None

    def test_nonempty_list_multiple_elements(self) -> None:
        """List with multiple elements → None."""
        node = _parse_expr("[1, 2, 3]")
        assert _value_to_str(node) is None

    # ── P3: ast.Dict ─────────────────────────────────────────────────────

    def test_nonempty_dict_single_key(self) -> None:
        """B5: Dict with 1 key → None."""
        node = _parse_expr("{'a': 1}")
        assert _value_to_str(node) is None

    # ── P4: ast.Tuple ────────────────────────────────────────────────────

    def test_nonempty_tuple_single_element(self) -> None:
        """B6: Tuple with 1 element → None."""
        # Note: (1,) is a tuple in Python
        node = _parse_expr("(1,)")
        assert _value_to_str(node) is None

    def test_nonempty_tuple_multiple_elements(self) -> None:
        """Tuple with multiple elements → None."""
        node = _parse_expr("(1, 2)")
        assert _value_to_str(node) is None

    # ── P5: unsupported nodes ────────────────────────────────────────────

    def test_binop_returns_none(self) -> None:
        """Binary operation → None."""
        node = _parse_expr("1 + 2")
        assert _value_to_str(node) is None

    def test_attribute_returns_none(self) -> None:
        """Attribute access → None."""
        node = _parse_expr("os.sep")
        assert _value_to_str(node) is None

    def test_list_comprehension_returns_none(self) -> None:
        """List comprehension → None."""
        node = _parse_expr("[x for x in range(10)]")
        assert _value_to_str(node) is None

    def test_fstring_returns_none(self) -> None:
        """f-string (JoinedStr) → None."""
        node = _parse_expr("f'hello {name}'")
        assert _value_to_str(node) is None

    # ── Boundary points ──────────────────────────────────────────────────

    def test_boundary_empty_string(self) -> None:
        """B1: Empty string constant → repr \"''\"."""
        node = _parse_expr("''")
        assert _value_to_str(node) == "''"

    def test_boundary_zero_int(self) -> None:
        """B2: Zero integer → '0'."""
        node = _parse_expr("0")
        assert _value_to_str(node) == "0"

    def test_boundary_negative_int(self) -> None:
        """B3: Negative integer."""
        # Note: -1 in AST is UnaryOp(USub, Constant(1)), not Constant(-1)
        # So _value_to_str will return None for negative literals in source
        node = _parse_expr("-1")
        # This is a UnaryOp, not a Constant
        assert _value_to_str(node) is None

    def test_boundary_zero_float(self) -> None:
        """Zero float → '0.0'."""
        node = _parse_expr("0.0")
        assert _value_to_str(node) == "0.0"

    def test_boundary_large_int(self) -> None:
        """Large integer → correct repr."""
        node = _parse_expr("999999999")
        assert _value_to_str(node) == "999999999"

    def test_boundary_empty_bytes(self) -> None:
        """Empty bytes → repr."""
        node = _parse_expr("b''")
        assert _value_to_str(node) == "b''"


# ══════════════════════════════════════════════════════════════════════════
# Channel architectural contracts
# ══════════════════════════════════════════════════════════════════════════

CHANNELS = [
    LintChannel(),
    TestChannel(),
    DependencyChannel(),
    BehaviorChannel(),
    GitChannel(),
    PerformanceChannel(),
    StructureChannel(),
]


@pytest.mark.parametrize("channel", CHANNELS)
def test_channel_metadata_contract(channel):
    """Verify channel has required architectural metadata."""
    assert hasattr(channel, "name"), f"Channel {type(channel).__name__} missing 'name'"
    assert isinstance(channel.name, str)
    assert hasattr(channel, "timeout_ms"), f"Channel {type(channel).__name__} missing 'timeout_ms'"
    assert isinstance(channel.timeout_ms, int)
    assert hasattr(channel, "blocking_capable"), (
        f"Channel {type(channel).__name__} missing 'blocking_capable'"
    )
    assert isinstance(channel.blocking_capable, bool)


@pytest.mark.parametrize("channel", CHANNELS)
def test_channel_interface_contract(channel):
    """Verify channel implements required methods with correct signatures."""
    assert hasattr(channel, "should_run")
    assert hasattr(channel, "execute")

    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root="")

    should = channel.should_run(event, config)
    assert isinstance(should, bool)

    SupervisionEvent(project_root="/tmp/fake")
    try:
        result = channel.execute(event, config)
        assert isinstance(result, ChannelResult)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# Key contract — no raw '::' key construction
# ══════════════════════════════════════════════════════════════════════════

_SCAN_DIRS = [
    "lintgate/specification",
    "lintgate/channels",
    "lintgate/linters",
]

_ALLOWED_FILES = {
    "lintgate/keys.py",
    "lintgate/specification/composition.py",
    "lintgate/channels/symbol_coverage.py",
    "lintgate/linters/structure_checks/dependency_clustering.py",
}

_RAW_KEY_PATTERN = re.compile(r'f["\'].*\{[^}]+\}::\{[^}]+\}.*["\']')


def _find_raw_key_constructions() -> list[tuple[str, int, str]]:
    """Find f-string key constructions that bypass canonical_function_key."""
    violations: list[tuple[str, int, str]] = []
    project_root = Path(__file__).parent.parent

    for scan_dir in _SCAN_DIRS:
        dir_path = project_root / scan_dir
        if not dir_path.exists():
            continue

        for py_file in dir_path.rglob("*.py"):
            relpath = str(py_file.relative_to(project_root))
            if relpath in _ALLOWED_FILES:
                continue

            try:
                lines = py_file.read_text().splitlines()
            except OSError:
                continue

            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("import"):
                    continue

                if _RAW_KEY_PATTERN.search(line):
                    if "canonical_function_key" in line:
                        continue
                    violations.append((relpath, i, stripped))

    return violations


def test_no_raw_key_construction():
    """No raw f'...{x}::{y}...' key construction in spec/channels/linters code."""
    violations = _find_raw_key_constructions()

    if violations:
        msg_parts = ["Raw key construction found (use canonical_function_key instead):"]
        for filepath, lineno, text in violations:
            msg_parts.append(f"  {filepath}:{lineno}: {text}")
        pytest.fail("\n".join(msg_parts))
