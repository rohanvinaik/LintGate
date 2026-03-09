"""Single-file specification analyzer — build spec data for one file at a time.

Avoids full-project discovery. Builds manifests, ledger, and prescriptions
scoped to a single source file. Designed for interactive MCP use where
analyzing one file should be fast and resource-bounded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileSpecResult:
    """Result of analyzing a single file's specification state."""

    file: str
    project_root: str
    functions: dict[str, Any] = field(default_factory=dict)
    prescriptions: list[dict[str, Any]] = field(default_factory=list)
    total_sigma: int = 0
    mean_spec_level: float = 0.0
    regime_distribution: dict[str, int] = field(default_factory=dict)
    risk_distribution: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "file": self.file,
            "project_root": self.project_root,
            "total_functions": len(self.functions),
            "total_sigma": self.total_sigma,
            "mean_spec_level": round(self.mean_spec_level, 3),
            "regime_distribution": self.regime_distribution,
            "risk_distribution": self.risk_distribution,
            "functions": self.functions,
        }
        if self.prescriptions:
            d["prescriptions"] = self.prescriptions
        if self.error:
            d["error"] = self.error
        return d


def analyze_file(
    file_path: str,
    project_root: str,
    include_prescriptions: bool = False,
    max_prescriptions: int = 10,
    enrich: bool = True,
) -> FileSpecResult:
    """Analyze specification complexity for a single Python file.

    Args:
        file_path: Absolute path to the Python source file.
        project_root: Project root for import/key resolution.
        include_prescriptions: Whether to generate test prescriptions.
        max_prescriptions: Max prescriptions per function.
        enrich: If True (default), build property/test-effectiveness manifests
            and call graph for full analysis. If False, use pure AST analysis
            only (symbolic baseline — no manifest dependencies).

    Returns:
        FileSpecResult with per-function spec data.
    """
    result = FileSpecResult(
        file=os.path.relpath(file_path, project_root),
        project_root=project_root,
    )

    if not os.path.isfile(file_path):
        result.error = f"File not found: {file_path}"
        return result

    if not file_path.endswith(".py"):
        result.error = "Not a Python file"
        return result

    try:
        if enrich:
            return _do_analyze(
                file_path, project_root, include_prescriptions, max_prescriptions, result
            )
        return _do_analyze_symbolic(file_path, project_root, result)
    except Exception as e:
        result.error = f"Analysis failed: {e}"
        return result


def _do_analyze(
    file_path: str,
    project_root: str,
    include_prescriptions: bool,
    max_prescriptions: int,
    result: FileSpecResult,
) -> FileSpecResult:
    """Core analysis logic, separated for clean error handling."""
    import hashlib

    from lintgate.linters.performance_checks.manifest import build_manifest
    from lintgate.linters.test_effectiveness.manifest import build_test_effectiveness_manifest
    from lintgate.specification.call_graph import build_cross_module_call_graph
    from lintgate.specification.ledger import (
        build_specification_ledger,
        load_cached_ledger,
        save_cached_ledger,
    )
    from lintgate.state import SPEC_CACHE_DIR

    py_files = [file_path]

    # Load mutation cache for ground-truth spec_level override (Fix #2)
    mutation_cache = _load_mutation_cache(project_root, file_path)

    # Build manifests scoped to this single file
    prop_manifest = build_manifest(
        project_root,
        py_files,
        mutation_cache=mutation_cache,
    )
    # Scope test discovery to files relevant to this source file
    # instead of triggering full-project test discovery.
    scoped_test_files = _discover_relevant_test_files(file_path, project_root)
    teff_manifest = build_test_effectiveness_manifest(
        project_root, py_files, test_files=scoped_test_files
    )
    call_graph = build_cross_module_call_graph(py_files, project_root)

    # Load prior ledger for trajectory accumulation (Fix #5)
    project_hash = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    prior_ledger = load_cached_ledger(SPEC_CACHE_DIR, project_hash)

    ledger = build_specification_ledger(
        prop_manifest,
        teff_manifest,
        project_root,
        py_files=py_files,
        call_graph=call_graph,
        prior_ledger=prior_ledger,
        mutation_cache=mutation_cache,
    )

    # Merge single-file ledger into the full project cache so we don't
    # discard trajectory state for functions in other files.
    if prior_ledger is not None:
        merged = prior_ledger
        for key, fs in ledger.functions.items():
            merged.functions[key] = fs
        merged.update_metrics()
        save_cached_ledger(SPEC_CACHE_DIR, project_hash, merged)
    else:
        save_cached_ledger(SPEC_CACHE_DIR, project_hash, ledger)

    if not ledger.functions:
        return result

    # Build per-function output
    total_spec = 0.0
    for key, fs in ledger.functions.items():
        result.functions[key] = {
            "sigma": fs.core.estimated_sigma,
            "sigma_confidence": round(fs.core.sigma_confidence, 3),
            "regime": fs.core.regime,
            "regime_rationale": fs.core.regime_rationale,
            "specification_level": round(fs.core.specification_level, 3),
            "data_source": fs.core.data_source,
            "phase": fs.core.phase,
            "is_pure": fs.core.is_pure,
            "risk_score": round(fs.risk.risk_score, 3),
            "priority_band": fs.risk.priority_band,
            "testability_score": round(fs.testability.testability_score, 3),
            "design_signals": {
                "boundary_points": fs.design_signals.boundary_points,
                "equivalence_partitions": fs.design_signals.equivalence_partitions,
                "decision_rule_count": fs.design_signals.decision_rule_count,
                "predicate_effect_links": fs.design_signals.predicate_effect_links,
            },
            "optimization_hints": fs.optimization_hints,
            "stop_criteria_met": fs.stop_criteria_met,
        }
        result.total_sigma += fs.core.estimated_sigma
        total_spec += fs.core.specification_level

        regime = fs.core.regime
        result.regime_distribution[regime] = result.regime_distribution.get(regime, 0) + 1
        band = fs.risk.priority_band
        result.risk_distribution[band] = result.risk_distribution.get(band, 0) + 1

    if result.functions:
        result.mean_spec_level = total_spec / len(result.functions)

    # Prescriptions
    if include_prescriptions:
        from lintgate.specification.prescriptions import prescribe

        for _key, fs in ledger.functions.items():
            rxs = prescribe(fs, max_prescriptions=max_prescriptions)
            for rx in rxs:
                result.prescriptions.append(
                    {
                        "function": rx.function_key,
                        "kind": rx.prescription_kind,
                        "description": rx.description,
                        "info_gain": round(rx.estimated_info_gain, 3),
                        "priority_band": rx.priority_band,
                        "uncovered_dimension": rx.uncovered_dimension,
                        "suggested_assertion": rx.suggested_assertion,
                    }
                )

        band_order = {"P0": 0, "P1": 1, "P2": 2}
        result.prescriptions.sort(
            key=lambda p: (band_order.get(p["priority_band"], 3), -p["info_gain"])
        )

    return result


def _do_analyze_symbolic(
    file_path: str,
    project_root: str,
    result: FileSpecResult,
) -> FileSpecResult:
    """Symbolic-only analysis — pure AST, no manifest dependencies.

    Parses the file, walks top-level and class-level function defs,
    runs the predictor with default PredictorInput (no purity, no
    semantic_ratio, no assertion_count). Produces baseline sigma,
    regime, phase, and testability from AST structure alone.
    """
    import ast

    from lintgate.keys import canonical_function_key
    from lintgate.specification.predictor import PredictorInput, predict

    with open(file_path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=file_path)

    rel_path = os.path.relpath(file_path, project_root)
    default_input = PredictorInput()

    total_spec = 0.0
    for qualname, node in _walk_functions(tree):
        func_key = canonical_function_key(rel_path, qualname)
        pred = predict(node, default_input)

        result.functions[func_key] = {
            "sigma": pred.sigma,
            "sigma_confidence": round(pred.sigma_confidence, 3),
            "regime": pred.regime,
            "regime_rationale": pred.regime_rationale,
            "specification_level": round(pred.spec_level, 3),
            "phase": pred.phase,
            "is_pure": False,  # unknown without manifests
            "risk_score": 0.0,  # no risk model without manifests
            "priority_band": "P2",
            "testability_score": round(pred.testability.testability_score, 3),
            "design_signals": {
                "boundary_points": pred.design_signals.boundary_points,
                "equivalence_partitions": pred.design_signals.equivalence_partitions,
                "decision_rule_count": pred.design_signals.decision_rule_count,
                "predicate_effect_links": pred.design_signals.predicate_effect_links,
            },
            "trajectory": {
                "convergence_rate": pred.trajectory.convergence_rate,
                "estimated_remaining": pred.trajectory.estimated_remaining,
            },
            "optimization_hints": [],
            "stop_criteria_met": False,
        }
        result.total_sigma += pred.sigma
        total_spec += pred.spec_level

        regime = pred.regime
        result.regime_distribution[regime] = result.regime_distribution.get(regime, 0) + 1
        result.risk_distribution["P2"] = result.risk_distribution.get("P2", 0) + 1

    if result.functions:
        result.mean_spec_level = total_spec / len(result.functions)

    return result


def _load_mutation_cache(project_root: str, file_path: str) -> dict[str, dict] | None:
    """Load mutation cache entries relevant to a source file.

    Returns a dict mapping function_key → mutation result dict,
    or None if no mutation data exists. Lightweight: reads only
    JSON files from the per-project mutation cache directory.
    """
    from pathlib import Path

    cache_dir = Path(project_root) / ".lintgate" / "mutation"
    if not cache_dir.exists():
        return None

    rel_path = os.path.relpath(file_path, project_root)
    cache: dict[str, dict] = {}
    for cache_file in cache_dir.glob("*.json"):
        if cache_file.name == "scheduler_state.json":
            continue
        try:
            with open(cache_file, encoding="utf-8") as f:
                import json

                data = json.load(f)
        except (OSError, ValueError):
            continue
        func_key = data.get("function_key", "")
        # Only load entries for the file being analyzed
        if rel_path in func_key:
            cache[func_key] = data

    return cache if cache else None


def _discover_relevant_test_files(file_path: str, project_root: str) -> list[str]:
    """Discover test files relevant to a single source file.

    Strategy:
    1. Look for conventional test file names (test_<module>.py) in the
       project's test directories.
    2. If no conventional matches found, return an empty list so that
       build_test_effectiveness_manifest gets an explicit empty set
       rather than falling through to full-project discovery.

    This avoids triggering full-project test discovery for single-file
    analysis, which is wasteful and slow on large projects.
    """
    basename = os.path.basename(file_path)
    module_name = basename.removesuffix(".py")

    # Candidate test file names for this module
    candidates = {
        f"test_{module_name}.py",
        f"{module_name}_test.py",
    }

    # Search common test locations
    test_dirs = ["tests", "test", "."]
    found: list[str] = []
    root = os.path.abspath(project_root)

    for test_dir in test_dirs:
        search_root = os.path.join(root, test_dir) if test_dir != "." else root
        if not os.path.isdir(search_root):
            continue
        for dirpath, dirnames, filenames in os.walk(search_root):
            # Skip hidden and cache dirs
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
            for fname in filenames:
                if fname in candidates:
                    found.append(os.path.join(dirpath, fname))

    return found


def _walk_functions(
    tree: Any,
) -> list[tuple[str, Any]]:
    """Walk AST and yield (qualname, node) for every function/method.

    Builds qualified names so that A.process and B.process produce
    distinct keys ("A.process" vs "B.process") instead of both
    collapsing to "process".
    """
    results: list[tuple[str, Any]] = []
    _walk_scope(tree, "", results)
    return results


def _walk_scope(scope: Any, prefix: str, out: list[tuple[str, Any]]) -> None:
    """Recursively walk a scope (module or class) collecting functions."""
    import ast

    for node in getattr(scope, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{node.name}" if prefix else node.name
            out.append((qualname, node))
            # Recurse into function body to discover nested/inner functions
            func_prefix = f"{qualname}.<locals>."
            _walk_scope(node, func_prefix, out)
        elif isinstance(node, ast.ClassDef):
            class_prefix = f"{prefix}{node.name}." if prefix else f"{node.name}."
            _walk_scope(node, class_prefix, out)
