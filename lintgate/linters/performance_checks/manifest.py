"""Project-wide property inventory aggregator."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from lintgate.linters.performance_checks.algebra_types import (
    FunctionProperties,
    PropertyKind,
)
from lintgate.linters.performance_checks.properties import classify_properties
from lintgate.linters.performance_checks.purity import analyze_purity
from lintgate.state import PERF_CACHE_DIR


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize manifest to a dictionary."""
        return {
            "functions": {k: v.to_dict() for k, v in self.functions.items()},
            "pure_count": self.pure_count,
            "impure_count": self.impure_count,
            "property_distribution": {k.value: v for k, v in self.property_distribution.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropertyManifest:
        """Deserialize manifest from a dictionary."""
        functions = {}
        for k, v in data.get("functions", {}).items():
            functions[k] = FunctionProperties.from_dict(v)

        manifest = cls(functions=functions)
        manifest.update_metrics()
        return manifest


def build_manifest(project_root: str, python_files: list[str]) -> PropertyManifest:
    """
    Build a PropertyManifest by scanning python files, with caching.
    """
    PERF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    project_hash = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    cache_path = PERF_CACHE_DIR / f"{project_hash}.json"

    # 1. Load cache
    cached_manifest = PropertyManifest()
    cache_metadata: dict[str, dict[str, Any]] = {} # filepath -> {hash: str, functions: list[str]}

    if cache_path.exists():
        try:
            with open(cache_path) as f:
                cached_data = json.load(f)
                cached_manifest = PropertyManifest.from_dict(cached_data.get("manifest", {}))
                cache_metadata = cached_data.get("metadata", {})
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    # 2. Identify which files need re-scanning
    manifest = PropertyManifest()
    new_metadata: dict[str, dict[str, Any]] = {}

    for filepath in python_files:
        try:
            with open(filepath, "rb") as f:
                file_hash = hashlib.md5(f.read(), usedforsecurity=False).hexdigest()

            cached_entry = cache_metadata.get(filepath)
            if cached_entry and cached_entry.get("hash") == file_hash:
                # Reuse cached functions
                func_names = cached_entry.get("functions", [])
                for name in func_names:
                    if name in cached_manifest.functions:
                        manifest.functions[name] = cached_manifest.functions[name]
                new_metadata[filepath] = cached_entry
            else:
                # Must scan
                tree = None
                try:
                    with open(filepath, encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=filepath)
                except (SyntaxError, OSError):
                    continue

                if not tree:
                    continue

                # Purity analysis
                purity_results = analyze_purity(tree)

                # Function node discovery
                class FuncFinder(ast.NodeVisitor):
                    def __init__(self):
                        self.nodes = {}
                        self.class_stack = []

                    def visit_ClassDef(self, node):
                        self.class_stack.append(node.name)
                        self.generic_visit(node)
                        self.class_stack.pop()

                    def visit_FunctionDef(self, node):
                        qualname = f"{'.'.join(self.class_stack)}.{node.name}" if self.class_stack else node.name
                        self.nodes[qualname] = node

                    def visit_AsyncFunctionDef(self, node):
                        self.visit_FunctionDef(node)

                finder = FuncFinder()
                finder.visit(tree)

                found_funcs = []
                for qualname, purity in purity_results.items():
                    func_node = finder.nodes.get(qualname)
                    if not func_node:
                        continue

                    found_funcs.append(qualname)
                    if purity.is_pure:
                        props = classify_properties(func_node, purity)
                        manifest.functions[qualname] = props
                    else:
                        manifest.functions[qualname] = FunctionProperties(
                            purity=purity, properties=(), optimization_hints=()
                        )

                new_metadata[filepath] = {"hash": file_hash, "functions": found_funcs}
        except OSError:
            continue

    manifest.update_metrics()

    # 3. Save cache
    try:
        with open(cache_path, "w") as f:
            json.dump({
                "manifest": manifest.to_dict(),
                "metadata": new_metadata
            }, f)
    except OSError:
        pass

    return manifest
