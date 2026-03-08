"""Tests for lintgate.specification.mutation_filter — Monty Hall filtering."""

from __future__ import annotations

import ast

from lintgate.specification.mutation_engine import MutationCategory
from lintgate.specification.mutation_filter import filter_categories


def _parse_func(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    msg = "No function found"
    raise ValueError(msg)


class TestFilterCategories:
    def test_pure_two_params_no_comparisons(self):
        func = _parse_func("def add(a, b): return a + b")
        cats = filter_categories(func, is_pure=True)
        assert MutationCategory.VALUE in cats
        assert MutationCategory.SWAP in cats
        assert MutationCategory.STATE not in cats  # pure excludes STATE
        assert MutationCategory.BOUNDARY not in cats  # no comparisons

    def test_stateful_method_with_comparisons(self):
        func = _parse_func(
            "def process(self, x):\n"
            "    if x > 0:\n"
            "        self.value = x\n"
            "    return self.value"
        )
        cats = filter_categories(func, is_pure=False)
        assert MutationCategory.VALUE in cats
        assert MutationCategory.SWAP in cats
        assert MutationCategory.BOUNDARY in cats
        assert MutationCategory.STATE in cats

    def test_zero_params_excludes_swap(self):
        func = _parse_func("def f(): return 42")
        cats = filter_categories(func, is_pure=False)
        assert MutationCategory.SWAP not in cats

    def test_one_param_excludes_swap(self):
        func = _parse_func("def f(x): return x + 1")
        cats = filter_categories(func, is_pure=False)
        assert MutationCategory.SWAP not in cats

    def test_isinstance_includes_type(self):
        func = _parse_func("def f(x): return isinstance(x, int)")
        cats = filter_categories(func, is_pure=False)
        assert MutationCategory.TYPE in cats

    def test_no_isinstance_excludes_type(self):
        func = _parse_func("def f(x): return x + 1")
        cats = filter_categories(func, is_pure=False)
        assert MutationCategory.TYPE not in cats

    def test_pure_excludes_state(self):
        func = _parse_func(
            "def f(self, x):\n"
            "    self.value = x\n"
            "    return x"
        )
        cats = filter_categories(func, is_pure=True)
        assert MutationCategory.STATE not in cats

    def test_value_always_included(self):
        func = _parse_func("def f(): pass")
        cats = filter_categories(func, is_pure=True)
        assert MutationCategory.VALUE in cats

    def test_global_nonlocal_includes_state(self):
        func = _parse_func(
            "def f(x):\n"
            "    global counter\n"
            "    counter = x\n"
            "    return counter"
        )
        cats = filter_categories(func, is_pure=False)
        assert MutationCategory.STATE in cats
