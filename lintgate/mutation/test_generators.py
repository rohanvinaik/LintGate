"""Category-specific test template generators for mutation prescriptions.

Each generator produces compilable pytest code targeting specific mutation
survivor categories. Templates are suggestions — the agent writes them
to files via the Write tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintgate.mutation.state import _path_to_mutmut_module

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintgate.mutation.state import FunctionMutationState


def generate_arithmetic_template(
    state: FunctionMutationState, project_root: str | None = None
) -> str:
    """Generate exact-value assertion tests for arithmetic mutation survivors."""
    func = state.function_name
    module = _path_to_mutmut_module(state.file_path, project_root=project_root)
    return (
        f'"""Tests targeting arithmetic mutation survivors in {func}."""\n'
        f"import pytest\n"
        f"from {module} import {func}\n\n\n"
        f"class Test{func.title().replace('_', '')}Arithmetic:\n"
        f'    """Exact-value assertions to kill arithmetic mutants."""\n\n'
        f"    def test_known_output(self):\n"
        f'        """Verify exact return value, not just type."""\n'
        f"        # TODO: Replace with actual inputs and expected output\n"
        f"        result = {func}(1, 2)\n"
        f"        assert result == 3  # Exact value, not isinstance check\n\n"
        f"    def test_zero_input(self):\n"
        f'        """Zero is the identity for addition, absorber for multiplication."""\n'
        f"        result = {func}(0, 5)\n"
        f"        assert result == 5  # Catches +/- operator swaps\n\n"
        f"    def test_negative_input(self):\n"
        f'        """Negative inputs catch sign-flip mutations."""\n'
        f"        result = {func}(-1, 3)\n"
        f"        assert result == 2  # Exact expected value\n"
    )


def generate_conditional_template(
    state: FunctionMutationState, project_root: str | None = None
) -> str:
    """Generate branch-coverage tests for conditional mutation survivors."""
    func = state.function_name
    module = _path_to_mutmut_module(state.file_path, project_root=project_root)
    return (
        f'"""Tests targeting conditional mutation survivors in {func}."""\n'
        f"import pytest\n"
        f"from {module} import {func}\n\n\n"
        f"class Test{func.title().replace('_', '')}Branches:\n"
        f'    """Branch coverage tests to kill conditional mutants."""\n\n'
        f"    def test_true_branch(self):\n"
        f'        """Input that takes the True branch."""\n'
        f"        # TODO: Choose input that satisfies the condition\n"
        f"        result = {func}(True)\n"
        f"        assert result is not None\n\n"
        f"    def test_false_branch(self):\n"
        f'        """Input that takes the False branch."""\n'
        f"        # TODO: Choose input that fails the condition\n"
        f"        result = {func}(False)\n"
        f"        assert result is not None\n\n"
        f"    def test_boundary_condition(self):\n"
        f'        """Edge case at the decision boundary."""\n'
        f"        # TODO: Input at the exact threshold (==, <=, >= boundaries)\n"
        f"        result = {func}(0)\n"
        f"        assert result is not None\n"
    )


def generate_boundary_template(
    state: FunctionMutationState, project_root: str | None = None
) -> str:
    """Generate boundary-value tests for off-by-one mutation survivors."""
    func = state.function_name
    module = _path_to_mutmut_module(state.file_path, project_root=project_root)
    return (
        f'"""Tests targeting boundary mutation survivors in {func}."""\n'
        f"import pytest\n"
        f"from {module} import {func}\n\n\n"
        f"class Test{func.title().replace('_', '')}Boundaries:\n"
        f'    """Boundary-value tests to kill off-by-one mutants."""\n\n'
        f"    @pytest.mark.parametrize('value', [0, -1, 1, 2**31-1, -(2**31)])\n"
        f"    def test_boundary_values(self, value):\n"
        f'        """Test at common boundary values."""\n'
        f"        result = {func}(value)\n"
        f"        # TODO: Assert exact expected result for each boundary\n"
        f"        assert result is not None\n\n"
        f"    def test_empty_input(self):\n"
        f'        """Empty/zero-length input."""\n'
        f"        # TODO: Adapt for function's actual input type\n"
        f"        result = {func}([])\n"
        f"        assert result is not None\n\n"
        f"    def test_single_element(self):\n"
        f'        """Single-element input (minimal non-empty)."""\n'
        f"        result = {func}([1])\n"
        f"        assert result is not None\n"
    )


def generate_string_template(state: FunctionMutationState, project_root: str | None = None) -> str:
    """Generate exact-match tests for string mutation survivors."""
    func = state.function_name
    module = _path_to_mutmut_module(state.file_path, project_root=project_root)
    return (
        f'"""Tests targeting string mutation survivors in {func}."""\n'
        f"from {module} import {func}\n\n\n"
        f"class Test{func.title().replace('_', '')}Strings:\n"
        f'    """Exact string matching to kill string mutants."""\n\n'
        f"    def test_exact_output(self):\n"
        f'        """Assert exact string, not substring or truthiness."""\n'
        f"        result = {func}('input')\n"
        f"        assert result == 'expected_exact_output'  # Not 'in' or bool()\n\n"
        f"    def test_empty_string(self):\n"
        f'        """Empty string input — catches empty-string mutations."""\n'
        f"        result = {func}('')\n"
        f"        assert result == ''  # Or whatever the expected output is\n"
    )


# Map survivor categories to template generators
CATEGORY_GENERATORS: dict[str, Callable[..., str]] = {
    "arithmetic": generate_arithmetic_template,
    "conditional": generate_conditional_template,
    "boundary": generate_boundary_template,
    "string": generate_string_template,
}


def generate_template_for_category(
    category: str,
    state: FunctionMutationState,
    project_root: str | None = None,
) -> str | None:
    """Generate a test template for a specific survivor category.

    Returns None if no generator exists for the category.
    """
    generator = CATEGORY_GENERATORS.get(category)
    if generator is None:
        return None
    return generator(state, project_root=project_root)
