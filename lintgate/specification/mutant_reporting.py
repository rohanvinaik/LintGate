"""Mutant reporting — human-readable survivor/killed records.

Converts raw MutantResult + Mutant pairs into structured records
suitable for grounded prescriptions and cached state.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.specification.mutation_engine import MutantResult


def build_survivor_record(result: MutantResult) -> dict[str, Any]:
    """Build a survivor record from a MutantResult where killed=False."""
    m = result.mutant
    return {
        "mutant_id": m.mutant_id,
        "category": m.category.value,
        "location": m.location,
        "description": m.description,
        "diff_summary": _compute_diff_summary(m.original_node, m.mutated_node),
        "status": "survived",
        "elapsed_ms": round(result.elapsed_ms, 1),
    }


def build_killed_record(result: MutantResult) -> dict[str, Any]:
    """Build a killed record from a MutantResult where killed=True."""
    m = result.mutant
    return {
        "mutant_id": m.mutant_id,
        "category": m.category.value,
        "location": m.location,
        "description": m.description,
        "status": "killed",
        "killed_by": result.killed_by,
        "killed_by_test": result.test_name,
        "elapsed_ms": round(result.elapsed_ms, 1),
    }


def _compute_diff_summary(
    original_node: ast.AST, mutated_node: ast.AST,
) -> str:
    """Compute a human-readable diff summary between original and mutated AST.

    Attempts to unparse both nodes. Falls back to description-only
    when unparsing fails (e.g., partial AST fragments).
    """
    try:
        orig_src = ast.unparse(original_node)
        mut_src = ast.unparse(mutated_node)
    except Exception:
        return "AST mutation (unparse unavailable)"

    # Truncate for readability
    max_len = 120
    if len(orig_src) > max_len:
        orig_src = orig_src[:max_len] + "..."
    if len(mut_src) > max_len:
        mut_src = mut_src[:max_len] + "..."

    if orig_src == mut_src:
        return "No visible diff (mutation may be in nested structure)"

    return f"- {orig_src}\n+ {mut_src}"
