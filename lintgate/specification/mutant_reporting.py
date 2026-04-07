"""Mutant reporting — human-readable survivor/killed records.

Converts raw MutantResult + Mutant pairs into structured records
suitable for grounded prescriptions and cached state.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from Wesker.engine import MutantResult


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
    original_node: ast.AST,
    mutated_node: ast.AST,
) -> str:
    """Compute a human-readable diff summary between original and mutated AST.

    Attempts to unparse both nodes. Falls back to description-only
    when unparsing fails (e.g., partial AST fragments).

    Compares full unparsed source BEFORE truncating so that mutations
    deep inside a function body are still visible.
    """
    try:
        orig_src = ast.unparse(original_node)
        mut_src = ast.unparse(mutated_node)
    except Exception:
        return "AST mutation (unparse unavailable)"

    if orig_src == mut_src:
        return "No visible diff (mutation may be in nested structure)"

    # Show only the differing lines for readability
    orig_lines = orig_src.splitlines()
    mut_lines = mut_src.splitlines()
    diff_parts: list[str] = []
    for ol, ml in zip(orig_lines, mut_lines, strict=False):
        if ol != ml:
            diff_parts.append(f"- {ol[:120]}")
            diff_parts.append(f"+ {ml[:120]}")
    # Handle length differences (added/removed lines)
    if len(orig_lines) > len(mut_lines):
        for ol in orig_lines[len(mut_lines) :]:
            diff_parts.append(f"- {ol[:120]}")
    elif len(mut_lines) > len(orig_lines):
        for ml in mut_lines[len(orig_lines) :]:
            diff_parts.append(f"+ {ml[:120]}")

    if not diff_parts:
        return "No visible diff (mutation may be in nested structure)"

    # Cap total output to 3 diff pairs for readability
    return "\n".join(diff_parts[:6])
