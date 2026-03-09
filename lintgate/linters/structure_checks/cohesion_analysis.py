"""B2: File cohesion analysis — detect low-cohesion files and propose splits.

Computes intra-file cohesion by building a call graph of top-level definitions
and finding connected components. Files with multiple disconnected components
are candidates for splitting.

Heuristics:
- CLI detection: ``import argparse`` + >5 non-CLI functions → "mixed CLI/logic"
- Low cohesion with file-too-long → propose split along component boundaries
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ...types import Prescription


@dataclass
class CohesionResult:
    """Result of file cohesion analysis."""

    score: float  # 0.0 (no connections) to 1.0 (fully connected)
    components: list[list[str]]  # connected components of top-level names
    component_count: int
    total_defs: int
    is_cli_mixed: bool  # CLI entry point mixed with library logic
    split_proposals: list[Prescription]


def analyze_file_cohesion(
    tree: ast.Module,
    filepath: str,
    cohesion_threshold: float = 0.5,
) -> CohesionResult:
    """Analyze cohesion of a Python file's top-level definitions.

    Algorithm:
    1. Collect top-level function/class definitions
    2. Build intra-file call graph (A calls B → edge)
    3. Track shared module-level variable usage per definition
    4. Compute connected components
    5. Score: edges_within / total_possible_edges

    Args:
        tree: Parsed AST module.
        filepath: Source file path.
        cohesion_threshold: Below this score, propose splits.

    Returns:
        CohesionResult with score, components, and split proposals.
    """
    defs = _collect_top_level_defs(tree)
    if len(defs) < 3:
        # Too few definitions to meaningfully analyze cohesion
        return CohesionResult(
            score=1.0,
            components=[list(defs.keys())] if defs else [],
            component_count=1 if defs else 0,
            total_defs=len(defs),
            is_cli_mixed=False,
            split_proposals=[],
        )

    # Build call graph: edges between definitions that reference each other
    adj = _build_call_graph(defs)

    # Also connect definitions that share module-level variable usage
    module_vars = _collect_module_level_vars(tree)
    _connect_shared_vars(adj, defs, module_vars)

    # Find connected components
    components = _find_connected_components(set(defs.keys()), adj)

    # Compute cohesion score
    score = _compute_cohesion_score(defs, adj)

    # Detect CLI mixing
    is_cli_mixed = _detect_cli_mixing(tree, defs)

    # Generate split proposals if cohesion is low
    split_proposals: list[Prescription] = []
    if score < cohesion_threshold and len(components) > 1:
        split_proposals = _generate_split_proposals(filepath, components, is_cli_mixed)

    return CohesionResult(
        score=round(score, 3),
        components=components,
        component_count=len(components),
        total_defs=len(defs),
        is_cli_mixed=is_cli_mixed,
        split_proposals=split_proposals,
    )


# ── AST collection ──────────────────────────────────────────────────────


def _collect_top_level_defs(tree: ast.Module) -> dict[str, ast.AST]:
    """Collect top-level function and class definitions."""
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
    return defs


def _collect_module_level_vars(tree: ast.Module) -> set[str]:
    """Collect module-level variable names (assignments outside functions/classes)."""
    var_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            var_names.add(node.target.id)
    return var_names


# ── Call graph construction ──────────────────────────────────────────────


def _build_call_graph(
    defs: dict[str, ast.AST],
) -> dict[str, set[str]]:
    """Build adjacency list: A→B if A references B's name."""
    def_names = set(defs.keys())
    adj: dict[str, set[str]] = {name: set() for name in def_names}

    for name, node in defs.items():
        referenced = _collect_name_references(node)
        for ref in referenced & def_names:
            if ref != name:
                adj[name].add(ref)
                adj[ref].add(name)  # Undirected for cohesion analysis

    return adj


def _collect_name_references(node: ast.AST) -> set[str]:
    """Collect all Name references (Load context) in an AST subtree."""
    refs: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            refs.add(child.id)
    return refs


def _connect_shared_vars(
    adj: dict[str, set[str]],
    defs: dict[str, ast.AST],
    module_vars: set[str],
) -> None:
    """Connect definitions that share module-level variable usage."""
    if not module_vars:
        return

    # Map: variable → set of definitions that reference it
    var_users: dict[str, set[str]] = {}
    for name, node in defs.items():
        refs = _collect_name_references(node)
        for var in refs & module_vars:
            var_users.setdefault(var, set()).add(name)

    # Connect all definitions that share a module-level variable
    for users in var_users.values():
        user_list = list(users)
        for i in range(len(user_list)):
            for j in range(i + 1, len(user_list)):
                adj[user_list[i]].add(user_list[j])
                adj[user_list[j]].add(user_list[i])


# ── Graph algorithms ────────────────────────────────────────────────────


def _find_connected_components(
    nodes: set[str],
    adj: dict[str, set[str]],
) -> list[list[str]]:
    """Find connected components using BFS."""
    visited: set[str] = set()
    components: list[list[str]] = []

    for node in sorted(nodes):
        if node in visited:
            continue
        component: list[str] = []
        queue = [node]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for neighbor in sorted(adj.get(current, set())):
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(sorted(component))

    return components


def _compute_cohesion_score(
    defs: dict[str, ast.AST],
    adj: dict[str, set[str]],
) -> float:
    """Compute cohesion as ratio of actual edges to possible edges."""
    n = len(defs)
    if n < 2:
        return 1.0

    max_edges = n * (n - 1) / 2
    actual_edges = sum(len(neighbors) for neighbors in adj.values()) / 2
    return actual_edges / max_edges


# ── Heuristics ───────────────────────────────────────────────────────────


def _detect_cli_mixing(tree: ast.Module, defs: dict[str, ast.AST]) -> bool:
    """Detect if file mixes CLI entry point with library logic."""
    has_argparse = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "argparse":
                    has_argparse = True
        elif isinstance(node, ast.ImportFrom) and node.module == "argparse":
            has_argparse = True

    if not has_argparse:
        return False

    # Count non-CLI functions (exclude main, parse_args, etc.)
    cli_names = {"main", "parse_args", "cli", "run", "entry_point"}
    non_cli_funcs = sum(
        1
        for name, node in defs.items()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and name not in cli_names
    )
    return non_cli_funcs > 5


# ── Split proposals ─────────────────────────────────────────────────────


def _generate_split_proposals(
    filepath: str,
    components: list[list[str]],
    is_cli_mixed: bool,
) -> list[Prescription]:
    """Generate file split proposals from disconnected components."""
    proposals: list[Prescription] = []

    if is_cli_mixed:
        proposals.append(
            Prescription(
                kind="split_file",
                target=filepath,
                action="Separate CLI entry point from library logic",
                source="static",
                confidence=0.70,
                basis=["cohesion_analysis", "cli_detection"],
                expected_delta={"component_separation": len(components)},
            )
        )
        return proposals

    for i, component in enumerate(components):
        if len(component) < 2:
            continue  # Single-def components are not worth splitting into
        proposals.append(
            Prescription(
                kind="split_file",
                target=filepath,
                action=f"Extract component {i + 1} ({', '.join(component[:4])}"
                + (f", +{len(component) - 4} more" if len(component) > 4 else "")
                + ") into a separate module",
                source="static",
                confidence=0.55,
                inputs=component,
                basis=["cohesion_analysis", "connected_components"],
                expected_delta={"component_separation": len(components)},
            )
        )

    return proposals
