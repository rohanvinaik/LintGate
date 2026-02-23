"""Project-wide property inventory aggregator."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from lintgate.linters.performance_checks.algebra_types import (
    FunctionProperties,
    PropertyKind,
)
from lintgate.linters.performance_checks.properties import classify_properties
from lintgate.linters.performance_checks.purity import analyze_purity


@dataclass
class PropertyManifest:
    """Project-wide function property inventory."""

    functions: dict[str, FunctionProperties] = field(default_factory=dict)
    pure_count: int = 0
    impure_count: int = 0
    property_distribution: dict[PropertyKind, int] = field(default_factory=dict)
    optimization_potential: list[tuple[str, list[str]]] = field(default_factory=list)

    def update_metrics(self) -> None:
        """Recalculate counts based on the current functions dictionary."""
        self.pure_count = sum(1 for f in self.functions.values() if f.purity.is_pure)
        self.impure_count = len(self.functions) - self.pure_count

        dist = dict.fromkeys(PropertyKind, 0)
        opps: list[tuple[str, list[str]]] = []

        for name, func in self.functions.items():
            for prop in func.properties:
                dist[prop.kind] = dist.get(prop.kind, 0) + 1
            if func.optimization_hints:
                opps.append((name, list(func.optimization_hints)))

        self.property_distribution = dist
        # Sort opportunities by number of hints descending
        self.optimization_potential = sorted(opps, key=lambda x: len(x[1]), reverse=True)


def build_manifest(project_root: str, python_files: list[str]) -> PropertyManifest:
    """
    Build a fresh PropertyManifest by scanning all provided python files.
    In the real implementation (Phase 3), this will load a cached JSON,
    statically re-parse only changed files, and re-run transitivity.
    """
    manifest = PropertyManifest()

    # 1. Parse all files and gather AST nodes per file
    file_asts: dict[str, ast.AST] = {}
    for filepath in python_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content, filename=filepath)
            file_asts[filepath] = tree
        except (SyntaxError, FileNotFoundError, OSError):
            continue

    # 2. To strictly support cross-module transitivity, we should merge the ASTs
    # or build a global call graph.
    # For Phase 1 foundational scope without dependencies, we'll build a pseudo-global AST
    # or just run purity on a concatenated pseudo-module (or run individually).
    # Since purity.py has a "conservatively impure" fallback for external calls,
    # running per-file is safe (lossy but sound).

    for _filepath, tree in file_asts.items():
        # First pass: purity
        purity_results = analyze_purity(tree)

        # We need the original FunctionDef nodes to run property classification
        class FuncFinder(ast.NodeVisitor):
            def __init__(self):
                self.nodes: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
                self.class_stack: list[str] = []

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                qualname = (
                    f"{'.'.join(self.class_stack)}.{node.name}" if self.class_stack else node.name
                )
                self.nodes[qualname] = node

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self.visit_FunctionDef(node)  # type: ignore[arg-type]

        finder = FuncFinder()
        finder.visit(tree)

        # Second pass: property classification
        for qualname, purity in purity_results.items():
            func_node = finder.nodes.get(qualname)
            if not func_node:
                continue

            if purity.is_pure:
                func_props = classify_properties(func_node, purity)
                manifest.functions[qualname] = func_props
            else:
                # Still store it as a FunctionProperty with no extra algebraic properties
                manifest.functions[qualname] = FunctionProperties(
                    purity=purity, properties=(), optimization_hints=()
                )

    manifest.update_metrics()
    return manifest
