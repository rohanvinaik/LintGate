"""Monty Hall filtering — exclude irrelevant mutation categories (§6.1).

If a function has no comparisons, boundary mutants cannot survive
(there's nothing to mutate), so generating them wastes budget.
The filter reveals which "doors" have no prize before opening them.
"""

from __future__ import annotations

import ast

from .mutation_engine import MutationCategory


def filter_categories(
    func_node: ast.FunctionDef,
    is_pure: bool = False,
) -> set[MutationCategory]:
    """Layer 1: Exclusionary filtering (§6.1).

    Returns the set of categories relevant to this function.
    Categories where the function has no structural support are excluded.
    """
    relevant: set[MutationCategory] = set()

    param_count = len(func_node.args.args)
    has_comparisons = False
    has_self_assigns = False
    has_global_nonlocal = False
    has_isinstance = False

    for node in ast.walk(func_node):
        if isinstance(node, ast.Compare):
            has_comparisons = True
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                has_self_assigns = True
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            has_global_nonlocal = True
        elif isinstance(node, ast.Return) and node.value is not None:
            pass  # Return tracking removed — return mutations are separate from state
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
        ):
            has_isinstance = True

    # VALUE: always relevant if function has any constants
    relevant.add(MutationCategory.VALUE)

    # SWAP: need ≥ 2 parameters
    if param_count >= 2:
        relevant.add(MutationCategory.SWAP)

    # BOUNDARY: need comparisons
    if has_comparisons:
        relevant.add(MutationCategory.BOUNDARY)

    # STATE: need self.* assignments or global/nonlocal — but not if pure
    # has_returns is excluded: return mutations are a separate concern from state mutations
    if not is_pure and (has_self_assigns or has_global_nonlocal):
        relevant.add(MutationCategory.STATE)

    # TYPE: need isinstance calls
    if has_isinstance:
        relevant.add(MutationCategory.TYPE)

    return relevant
