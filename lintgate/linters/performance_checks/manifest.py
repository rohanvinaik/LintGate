"""Project-wide property inventory aggregator."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from lintgate.keys import canonical_function_key, canonical_relpath
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

    def get_source_file(self, func_name: str) -> str | None:
        """Look up the source file for a function by qualified name."""
        func = self.functions.get(func_name)
        return func.source_file if func else None

    def get_pure_function_names(self) -> set[str]:
        """Return set of qualified names for all pure functions."""
        return {name for name, f in self.functions.items() if f.purity.is_pure}

    def get_pure_functions_by_file(self) -> dict[str, list[str]]:
        """Group pure function names by their source file."""
        by_file: dict[str, list[str]] = {}
        for name, func in self.functions.items():
            if func.purity.is_pure and func.source_file:
                by_file.setdefault(func.source_file, []).append(name)
        return by_file

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


# Side-effect kinds that indicate I/O dependency (unsafe to extract naively)
_IO_SIDE_EFFECT_KINDS = frozenset({"io_call"})


def _classify_extraction_safety(func: FunctionProperties) -> str:
    """Classify how safely a function can be extracted to another module.

    Returns:
        ``"safe"``  — pure function, freely extractable
        ``"needs_module_state"``  — reads/writes globals or module state
        ``"unsafe"``  — performs I/O (file, network, subprocess)
    """
    if func.purity.is_pure:
        return "safe"
    for se in func.purity.side_effects:
        if se.kind in _IO_SIDE_EFFECT_KINDS:
            return "unsafe"
    return "needs_module_state"


# ── Manifest builder helpers ────────────────────────────────────────────


class _FuncFinder(ast.NodeVisitor):
    """Collect qualified function names and their AST nodes from a module."""

    def __init__(self) -> None:
        self.nodes: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        qualname = f"{'.'.join(self._class_stack)}.{node.name}" if self._class_stack else node.name
        self.nodes[qualname] = node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]


def _compute_file_hash(filepath: str) -> str:
    """Compute MD5 hash of a file's contents."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()


def _load_manifest_cache(
    cache_path: Any,
) -> tuple[PropertyManifest, dict[str, dict[str, Any]]]:
    """Load cached manifest and metadata from disk, returning empty defaults on failure."""
    if not cache_path.exists():
        return PropertyManifest(), {}
    try:
        with open(cache_path) as f:
            cached_data = json.load(f)
            return (
                PropertyManifest.from_dict(cached_data.get("manifest", {})),
                cached_data.get("metadata", {}),
            )
    except (json.JSONDecodeError, OSError, KeyError):
        return PropertyManifest(), {}


def _restore_cached_functions(
    manifest: PropertyManifest,
    filepath: str,
    cached_manifest: PropertyManifest,
    cached_entry: dict[str, Any],
) -> None:
    """Restore function entries from cache for an unchanged file."""
    for name in cached_entry.get("functions", []):
        if name in cached_manifest.functions:
            manifest.functions[name] = cached_manifest.functions[name]


def _scan_file(
    manifest: PropertyManifest,
    filepath: str,
    project_root: str,
    enforcement_mode: str = "audit",
    mutation_cache: dict[str, dict] | None = None,
) -> list[str]:
    """Parse a Python file, run purity + property analysis, and populate the manifest.

    ``mutation_cache`` maps ``function_key → mutation result dict`` (with
    ``survival_rate``, ``total_mutants``, ``coverage_depth``).  When present,
    mutation state is forwarded to :func:`classify_properties` for gate
    evaluation.  Loading the cache is the caller's responsibility — this
    function only reads from it.

    Returns the list of qualified function names found, or empty on parse failure.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (SyntaxError, OSError):
        return []

    purity_results = analyze_purity(tree)

    finder = _FuncFinder()
    finder.visit(tree)

    found_funcs: list[str] = []
    relpath = canonical_relpath(filepath, project_root)

    for qualname, purity in purity_results.items():
        func_node = finder.nodes.get(qualname)
        if not func_node:
            continue

        unique_key = canonical_function_key(relpath, qualname)
        found_funcs.append(unique_key)

        if purity.is_pure:
            mut_state = (mutation_cache or {}).get(unique_key)
            props = classify_properties(
                func_node,
                purity,
                mutation_state=mut_state,
                enforcement_mode=enforcement_mode,
            )
            func_props = FunctionProperties(
                purity=props.purity,
                properties=props.properties,
                optimization_hints=props.optimization_hints,
                source_file=filepath,
            )
        else:
            func_props = FunctionProperties(
                purity=purity,
                properties=(),
                optimization_hints=(),
                source_file=filepath,
            )
        # Compute extraction safety from side-effect classification
        safety = _classify_extraction_safety(func_props)
        if safety != "safe":
            # Replace with updated extraction_safety (frozen dataclass)
            func_props = FunctionProperties(
                purity=func_props.purity,
                properties=func_props.properties,
                optimization_hints=func_props.optimization_hints,
                source_file=func_props.source_file,
                extraction_safety=safety,
            )
        manifest.functions[unique_key] = func_props

    return found_funcs


def _save_manifest_cache(
    cache_path: Any,
    manifest: PropertyManifest,
    metadata: dict[str, dict[str, Any]],
) -> None:
    """Persist manifest and per-file metadata to disk cache."""
    try:
        with open(cache_path, "w") as f:
            json.dump(
                {
                    "manifest": manifest.to_dict(),
                    "metadata": metadata,
                },
                f,
            )
    except OSError:
        pass


# ── Public API ──────────────────────────────────────────────────────────


def build_manifest(
    project_root: str,
    python_files: list[str],
    enforcement_mode: str = "audit",
    mutation_cache: dict[str, dict] | None = None,
) -> PropertyManifest:
    """Build a PropertyManifest by scanning Python files, with incremental caching.

    ``mutation_cache`` maps ``function_key → mutation result dict``.  When
    provided, the gate in :func:`classify_properties` modulates confidence
    and suppresses optimization hints for underspecified pure functions.
    """
    PERF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    project_hash = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    cache_path = PERF_CACHE_DIR / f"{project_hash}_v4.json"

    cached_manifest, cache_metadata = _load_manifest_cache(cache_path)

    manifest = PropertyManifest()
    new_metadata: dict[str, dict[str, Any]] = {}

    for filepath in python_files:
        try:
            file_hash = _compute_file_hash(filepath)
        except OSError:
            continue

        cached_entry = cache_metadata.get(filepath)
        # Invalidate cache when mutation_cache has data for any function
        # in this file — stale cache would skip the gate update.
        mutation_invalidates = False
        if mutation_cache and cached_entry:
            for name in cached_entry.get("functions", []):
                if name in mutation_cache:
                    mutation_invalidates = True
                    break

        if cached_entry and cached_entry.get("hash") == file_hash and not mutation_invalidates:
            _restore_cached_functions(manifest, filepath, cached_manifest, cached_entry)
            new_metadata[filepath] = cached_entry
        else:
            found_funcs = _scan_file(
                manifest,
                filepath,
                project_root,
                enforcement_mode,
                mutation_cache=mutation_cache,
            )
            new_metadata[filepath] = {"hash": file_hash, "functions": found_funcs}

    manifest.update_metrics()
    _save_manifest_cache(cache_path, manifest, new_metadata)

    return manifest
