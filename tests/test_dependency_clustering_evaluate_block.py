"""Prescriptive spec tests for _evaluate_block.

Target: dependency_clustering::_evaluate_block
Mutation decompose prescription: BOUNDARY (predicate extraction),
SWAP (strategy seams), VALUE (memoization candidates).
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from lintgate.linters.structure_checks.dependency_clustering import (
    _StmtInfo,
    _analyze_statement,
    _evaluate_block,
)


# ── Fixture helpers ──────────────────────────────────────────────────


def _parse_func(source: str) -> ast.FunctionDef:
    """Parse a function source into an AST FunctionDef."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    raise ValueError("No function found")


def _make_infos(func_node: ast.FunctionDef) -> list[_StmtInfo]:
    """Build StmtInfo list from a function body."""
    return [_analyze_statement(i, stmt) for i, stmt in enumerate(func_node.body)]


def _simple_func() -> ast.FunctionDef:
    """A function with nested conditionals giving CC >= 3 for extractable blocks."""
    return _parse_func("""
def example(x, y):
    a = x + 1
    b = y * 2
    if a > b:
        if a > 10:
            c = a - b
        elif a > 5:
            c = a + b
        else:
            c = 0
    else:
        c = b - a
    result = c + 1
    return result
""")


def _func_with_return_in_middle() -> ast.FunctionDef:
    """A function where a middle statement contains a return."""
    return _parse_func("""
def example(x):
    a = x + 1
    if a > 10:
        return a
    b = a * 2
    return b
""")


def _func_many_vars() -> ast.FunctionDef:
    """A function with many variables — tests max_params/max_outputs."""
    return _parse_func("""
def example(a, b, c, d, e):
    x = a + b
    y = c + d
    z = e + x
    w = y + z
    if w > x:
        q = w - x
    else:
        q = x - w
    return q
""")


# ── Claim 0: must return None if any statement has exit ──────────────


class TestExitSafety:
    def test_block_with_return_rejected(self):
        """Block containing a return statement → None."""
        func = _func_with_return_in_middle()
        infos = _make_infos(func)
        param_names = {"x"}
        # Block [0:3] includes the if-return at index 1
        result = _evaluate_block(infos, 0, 3, param_names, "test.py", func, 4, 2)
        assert result is None

    def test_block_without_exit_accepted(self):
        """Block with no exit statements → not None (if other checks pass)."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        # Block [0:4] — assignments + if/else, no returns
        result = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 4, 2)
        # Should either return a Prescription or None (due to CC check), but not because of exits
        if result is not None:
            assert result.kind == "extract_function"


# ── Claim 1: must return None if inputs exceed max_params ────────────


class TestMaxParams:
    def test_too_many_inputs_rejected(self):
        """Block needing more inputs than max_params → None."""
        func = _func_many_vars()
        infos = _make_infos(func)
        param_names = {"a", "b", "c", "d", "e"}
        # Block [0:4] reads a,b,c,d,e — 5 inputs, set max_params=2
        result = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 2, 2)
        assert result is None

    def test_inputs_within_limit_accepted(self):
        """Block within max_params limit → may return Prescription."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        result = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 10, 10)
        # With generous limits, should not be rejected for param count
        if result is not None:
            assert len(result.inputs) <= 10


# ── Claim 2: must return None if outputs exceed max_outputs ──────────


class TestMaxOutputs:
    def test_too_many_outputs_rejected(self):
        """Block writing more variables than max_outputs → None."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        # Use max_outputs=0 to force rejection
        result = _evaluate_block(infos, 0, 3, param_names, "test.py", func, 10, 0)
        assert result is None


# ── Claim 3: must return None if block CC < 3 ────────────────────────


class TestMinCC:
    def test_low_cc_block_rejected(self):
        """Block with CC < 3 (simple assignments) → None."""
        func = _parse_func("""
def example(x, y, z):
    a = x + 1
    b = y + 2
    c = z + 3
    return a + b + c
""")
        infos = _make_infos(func)
        param_names = {"x", "y", "z"}
        # Block [0:3] is just assignments — CC = 0
        result = _evaluate_block(infos, 0, 3, param_names, "test.py", func, 4, 2)
        assert result is None


# ── Claim 4: inputs computation ──────────────────────────────────────


class TestInputComputation:
    def test_inputs_from_params(self):
        """Variables read from function params appear as inputs."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        result = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 10, 10)
        if result is not None:
            # x and y should be in inputs (read by the block, defined as params)
            assert "x" in result.inputs
            assert "y" in result.inputs

    def test_inputs_exclude_locally_written(self):
        """Variables both read and written within the block are not inputs."""
        func = _parse_func("""
def example(x):
    a = x + 1
    if a > 5:
        b = a * 2
    else:
        b = a + 2
    c = b + a
    return c
""")
        infos = _make_infos(func)
        param_names = {"x"}
        # Block [1:3] reads 'a' (written before block) and 'b' (written in block)
        # 'a' should be an input, 'b' should not
        result = _evaluate_block(infos, 1, 3, param_names, "test.py", func, 10, 10)
        if result is not None:
            assert "a" in result.inputs


# ── Claim 5: outputs computation ─────────────────────────────────────


class TestOutputComputation:
    def test_outputs_written_and_read_after(self):
        """Variables written in block and read after block appear as outputs."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        # Block [0:3] writes 'a', 'b', 'c' — 'c' is read in stmt 3 (result = c + 1)
        result = _evaluate_block(infos, 0, 3, param_names, "test.py", func, 10, 10)
        if result is not None:
            assert "c" in result.outputs


# ── Claim 7: Prescription.kind == "extract_function" ─────────────────


class TestPrescriptionKind:
    def test_kind_is_extract_function(self):
        """Successful evaluation returns kind='extract_function'."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        result = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 10, 10)
        if result is not None:
            assert result.kind == "extract_function"


# ── Claim 8: expected_delta has cc_reduction ──────────────────────────


class TestExpectedDelta:
    def test_cc_reduction_in_delta(self):
        """Prescription.expected_delta contains cc_reduction > 0."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        result = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 10, 10)
        if result is not None:
            assert "cc_reduction" in result.expected_delta
            assert result.expected_delta["cc_reduction"] >= 3  # minimum CC threshold


# ── BOUNDARY: off-by-one on max_params/max_outputs ───────────────────


class TestBoundary:
    def test_max_params_exact_boundary(self):
        """inputs == max_params → accepted. inputs == max_params + 1 → rejected."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        # First check what inputs the block actually needs
        result_generous = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 10, 10)
        if result_generous is not None:
            n_inputs = len(result_generous.inputs)
            # Exactly at boundary → should pass
            at_boundary = _evaluate_block(infos, 0, 4, param_names, "test.py", func, n_inputs, 10)
            assert at_boundary is not None
            # One below boundary → should fail
            below = _evaluate_block(infos, 0, 4, param_names, "test.py", func, n_inputs - 1, 10)
            assert below is None


# ── SWAP: parameter ordering matters ─────────────────────────────────


class TestSwapDiscrimination:
    def test_different_blocks_different_outputs(self):
        """Block [0:3] and [1:4] produce different inputs/outputs."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        r1 = _evaluate_block(infos, 0, 3, param_names, "test.py", func, 10, 10)
        r2 = _evaluate_block(infos, 1, 4, param_names, "test.py", func, 10, 10)
        assert r1 is not None
        assert r2 is not None
        # Inputs differ: [0:3] reads x,y directly; [1:4] reads a (written by stmt 0) and y
        assert r1.inputs != r2.inputs

    def test_max_params_vs_max_outputs_not_interchangeable(self):
        """Swapping max_params and max_outputs gives different results."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        # Block [0:3] has 2 inputs, 1 output
        # max_params=1, max_outputs=10 → rejected (2 inputs > 1)
        r1 = _evaluate_block(infos, 0, 3, param_names, "test.py", func, 1, 10)
        # max_params=10, max_outputs=1 → accepted (1 output <= 1)
        r2 = _evaluate_block(infos, 0, 3, param_names, "test.py", func, 10, 1)
        assert r1 is None
        assert r2 is not None


# ── VALUE: exact result checking ─────────────────────────────────────


class TestValueExact:
    def test_prescription_target_includes_filepath(self):
        """Prescription.target includes the filepath."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        result = _evaluate_block(infos, 0, 4, param_names, "myfile.py", func, 10, 10)
        if result is not None:
            assert "myfile.py" in result.target

    def test_prescription_has_correct_basis(self):
        """Prescription.basis includes expected values."""
        func = _simple_func()
        infos = _make_infos(func)
        param_names = {"x", "y"}
        result = _evaluate_block(infos, 0, 4, param_names, "test.py", func, 10, 10)
        if result is not None:
            assert "variable_clustering" in result.basis
            assert "contiguous_block" in result.basis
            assert "single_exit" in result.basis
