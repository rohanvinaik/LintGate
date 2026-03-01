"""Structure graph enrichment — reverse import graph, directed call graph, fan metrics.

Sits between the raw import graph (from structure_discovery) and consumer checks.
Provides fan-in/fan-out analysis, removal impact, and split-proposal annotation.

No I/O. Fully deterministic. Pure graph computations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..linters.structure_checks.cohesion_analysis import (
    _collect_name_references,
    _collect_top_level_defs,
)

if TYPE_CHECKING:
    import ast

    from lintgate.types import Prescription

# ── Reverse Import Graph ────────────────────────────────────────────────


def build_reverse_import_graph(
    import_graph: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Reverse the forward import graph: module B → set of modules that import B.

    Input: _build_import_graph() result (A→B if A imports B).
    Output: reverse (B→{A, C, ...} = all importers of B).

    All modules appearing in the forward graph (as keys or values) are
    guaranteed to appear in the result, even if they have zero importers.
    """
    reverse: dict[str, set[str]] = {}

    # Initialize all known modules
    for module, imports in import_graph.items():
        if module not in reverse:
            reverse[module] = set()
        for imported in imports:
            if imported not in reverse:
                reverse[imported] = set()

    # Build reverse edges
    for module, imports in import_graph.items():
        for imported in imports:
            reverse[imported].add(module)

    return reverse


def compute_module_fan_in(
    reverse_graph: dict[str, set[str]],
    file_map: dict[str, str],
) -> dict[str, int]:
    """Per-module fan-in count.

    Modules present in file_map but absent from reverse_graph get 0.
    """
    fan_in: dict[str, int] = {}

    for module in file_map:
        fan_in[module] = len(reverse_graph.get(module, set()))

    return fan_in


# ── Directed Call Graph (Intra-file) ─────────────────────────────────────


def build_directed_call_graph(
    tree: ast.Module,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Directed intra-file call graph.

    Returns (calls, called_by):
      calls[A] = {B, C}  — A calls/references B and C
      called_by[B] = {A}  — B is called by A

    Reuses _collect_top_level_defs and _collect_name_references
    from cohesion_analysis.py.

    Differs from cohesion_analysis._build_call_graph which is undirected.
    Self-references are excluded (recursive calls are not counted as edges).
    """
    defs = _collect_top_level_defs(tree)
    def_names = set(defs.keys())

    calls: dict[str, set[str]] = {name: set() for name in def_names}
    called_by: dict[str, set[str]] = {name: set() for name in def_names}

    for name, node in defs.items():
        referenced = _collect_name_references(node)
        for ref in referenced & def_names:
            if ref != name:  # Exclude self-references
                calls[name].add(ref)
                called_by[ref].add(name)

    return calls, called_by


def compute_function_fan_metrics(
    calls: dict[str, set[str]],
    called_by: dict[str, set[str]],
) -> dict[str, dict[str, int]]:
    """Returns {func: {"fan_in": N, "fan_out": M}} for all functions.

    fan_in = number of functions that reference this function
    fan_out = number of functions this function references
    """
    all_names = set(calls.keys()) | set(called_by.keys())
    metrics: dict[str, dict[str, int]] = {}

    for name in all_names:
        metrics[name] = {
            "fan_in": len(called_by.get(name, set())),
            "fan_out": len(calls.get(name, set())),
        }

    return metrics


# ── Removal Impact ───────────────────────────────────────────────────────


def compute_removal_impact(
    module: str,
    reverse_graph: dict[str, set[str]],
) -> dict[str, Any]:
    """Impact report for removing a module.

    Returns:
      direct_importers: list of modules that directly import this one
      importer_count: int
      safe_to_remove: bool (True if importer_count == 0)
    """
    importers = sorted(reverse_graph.get(module, set()))
    return {
        "direct_importers": importers,
        "importer_count": len(importers),
        "safe_to_remove": len(importers) == 0,
    }


# ── Split Proposal Annotation ───────────────────────────────────────────


def annotate_proposals_with_fan_in(
    proposals: list[Prescription],
    module_fan_in: dict[str, int],
    module_name: str,
) -> list[Prescription]:
    """Annotate split proposals with fan-in impact data.

    Adds expected_delta["importers_affected"] and appends
    caution note to action string when fan_in >= 3.

    Returns the same list (mutated in place for convenience).
    """
    fan_in = module_fan_in.get(module_name, 0)

    for proposal in proposals:
        proposal.expected_delta["importers_affected"] = fan_in

        if fan_in >= 3:
            caution = (
                f" ⚠ High fan-in ({fan_in} importers) — "
                f"splitting will require updating {fan_in} dependent modules"
            )
            if caution not in proposal.action:
                proposal.action += caution

    return proposals
