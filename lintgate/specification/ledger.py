"""Specification ledger — build and cache per-function specification state.

Consumes PropertyManifest and TestEffectivenessManifest to build
a SpecificationLedger. Supports incremental rebuild via per-file hashing.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lintgate.keys import SCHEMA_VERSION, try_parse_function_key

from .predictor import PredictorInput, predict
from .risk_model import compute_risk_score
from .types import (
    ASTMetrics,
    FunctionSpecification,
    RiskProfile,
    SpecCore,
    SpecificationLedger,
    Traceability,
)

if TYPE_CHECKING:
    import ast

    from lintgate.linters.performance_checks.manifest import PropertyManifest
    from lintgate.linters.test_effectiveness.types import TestEffectivenessManifest
    from lintgate.specification.call_graph import CrossModuleCallGraph


def build_specification_ledger(
    property_manifest: PropertyManifest,
    teff_manifest: TestEffectivenessManifest,
    project_root: str,
    py_files: list[str] | None = None,
    test_files: list[str] | None = None,
    call_graph: CrossModuleCallGraph | None = None,
) -> SpecificationLedger:
    """Build specification ledger from existing channel manifests.

    Args:
        property_manifest: From performance channel prepass.
        teff_manifest: From test effectiveness channel prepass.
        project_root: Project root for relative path resolution.
        py_files: Python source files for AST parsing.
        test_files: Test files for traceability extraction.
        call_graph: Optional call graph for fan-in/fan-out risk scoring.
    """
    ledger = SpecificationLedger()
    test_coverage_map = _build_test_coverage_map(test_files or [])

    for func_key, func_props in property_manifest.functions.items():
        func_spec = _build_function_spec(
            func_key=func_key,
            func_props=func_props,
            teff_manifest=teff_manifest,
            project_root=project_root,
            test_coverage_map=test_coverage_map,
            call_graph=call_graph,
        )
        if func_spec is not None:
            ledger.functions[func_key] = func_spec

    ledger.update_metrics()
    return ledger


def _build_function_spec(
    func_key: str,
    func_props: Any,
    teff_manifest: TestEffectivenessManifest,
    project_root: str,
    test_coverage_map: dict[str, list[str]],
    call_graph: CrossModuleCallGraph | None = None,
) -> FunctionSpecification | None:
    """Build a single FunctionSpecification from channel data."""
    # Parse AST for the function
    source_file = func_props.source_file or ""
    func_node = _find_func_node(source_file, func_key)
    if func_node is None:
        return None

    # Resolve test effectiveness data
    # Both PropertyManifest and TestEffectivenessManifest use "relpath.py::qualname" keys
    parsed = try_parse_function_key(func_key)
    func_name = parsed[1] if parsed else func_key
    teff = teff_manifest.functions.get(func_key)
    if teff is None:
        # Fallback: try bare function name for backwards compatibility
        teff = teff_manifest.functions.get(func_name)

    semantic_ratio = 0.0
    weakness = ""
    assertion_count = 0
    if teff is not None:
        semantic_ratio = teff.quality_profile.semantic_ratio
        weakness = teff.weakness_taxonomy.value if teff.weakness_taxonomy else ""
        assertion_count = len(teff.assertions)

    # Run predictor
    signals = PredictorInput(
        is_pure=func_props.purity.is_pure,
        purity_confidence=func_props.purity.confidence,
        semantic_ratio=semantic_ratio,
        weakness_taxonomy=weakness,
        assertion_count=assertion_count,
    )
    result = predict(func_node, signals)

    # Risk model
    covering = test_coverage_map.get(func_name, [])
    fan_in = call_graph.fan_in(func_key) if call_graph else 0
    fan_out = call_graph.fan_out(func_key) if call_graph else 0
    risk = compute_risk_score(
        is_pure=func_props.purity.is_pure,
        fan_in=fan_in,
        fan_out=fan_out,
        is_public=not func_name.startswith("_"),
        testability_score=result.testability.testability_score,
        regime=result.regime,
    )

    # Traceability
    req_tags = _extract_requirement_tags(func_node)

    # Optimization gate stop criteria
    hints = list(func_props.optimization_hints)
    stop_met = _check_stop_criteria(result.spec_level, hints)

    return FunctionSpecification(
        function_key=func_key,
        source_file=source_file,
        core=SpecCore(
            estimated_sigma=result.sigma,
            sigma_confidence=result.sigma_confidence,
            regime=result.regime,
            specification_level=result.spec_level,
            behavioral_dimensions=result.sigma,
            phase=result.phase,
            is_pure=func_props.purity.is_pure,
            semantic_ratio=semantic_ratio,
            weakness_taxonomy=weakness,
        ),
        ast_metrics=ASTMetrics(
            ast_category_count=0,
            branch_count=0,
            parameter_count=len(func_node.args.args),
        ),
        design_signals=result.design_signals,
        testability=result.testability,
        tpa=result.tpa,
        risk=RiskProfile(
            risk_score=risk.risk_score,
            priority_band=risk.priority_band,
            risk_factors=risk.risk_factors,
        ),
        traceability=Traceability(
            requirement_tags=req_tags,
            covering_tests=covering,
            assertion_count=assertion_count,
        ),
        stop_criteria_met=stop_met,
        optimization_hints=hints,
        file_hash=_file_hash(source_file),
        computed_at=time.time(),
    )


# ── Cache ────────────────────────────────────────────────────────────


def load_cached_ledger(cache_dir: Path, project_hash: str) -> SpecificationLedger | None:
    """Load a cached ledger if it exists and schema matches."""
    cache_file = cache_dir / f"{project_hash}_v{SCHEMA_VERSION}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file) as f:
            data = json.load(f)
        if data.get("schema_version") != SCHEMA_VERSION:
            return None
        return _deserialize_ledger(data)
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def save_cached_ledger(cache_dir: Path, project_hash: str, ledger: SpecificationLedger) -> None:
    """Save ledger to cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{project_hash}_v{SCHEMA_VERSION}.json"
    try:
        with open(cache_file, "w") as f:
            json.dump(ledger.to_dict(), f)
    except OSError:
        pass


def _deserialize_ledger(data: dict) -> SpecificationLedger:
    """Deserialize ledger from dict, rebuilding dataclass instances."""
    ledger = SpecificationLedger(schema_version=data.get("schema_version", SCHEMA_VERSION))
    for key, fdata in data.get("functions", {}).items():
        ledger.functions[key] = _deserialize_func_spec(fdata)
    ledger.update_metrics()
    return ledger


def _deserialize_func_spec(d: dict) -> FunctionSpecification:
    """Deserialize a FunctionSpecification from a flat dict."""
    from .types import TestabilityProfile, TestDesignSignals, TPAResult

    return FunctionSpecification(
        function_key=d.get("function_key", ""),
        source_file=d.get("source_file", ""),
        core=SpecCore(
            estimated_sigma=d.get("estimated_sigma", 0),
            sigma_confidence=d.get("sigma_confidence", 1.0),
            regime=d.get("regime", "unknown"),
            specification_level=d.get("specification_level", 0.0),
            behavioral_dimensions=d.get("behavioral_dimensions", 0),
            phase=d.get("phase", "bulk"),
            is_pure=d.get("is_pure", False),
            semantic_ratio=d.get("semantic_ratio", 0.0),
            weakness_taxonomy=d.get("weakness_taxonomy", ""),
        ),
        ast_metrics=ASTMetrics(
            ast_category_count=d.get("ast_category_count", 0),
            branch_count=d.get("branch_count", 0),
            parameter_count=d.get("parameter_count", 0),
        ),
        design_signals=TestDesignSignals(
            boundary_points=d.get("boundary_points", 0),
            equivalence_partitions=d.get("equivalence_partitions", 0),
            decision_rule_count=d.get("decision_rule_count", 0),
            predicate_effect_links=d.get("predicate_effect_links", 0),
        ),
        testability=TestabilityProfile(
            testability_score=d.get("testability_score", 1.0),
            is_stateful=d.get("is_stateful", False),
        ),
        tpa=TPAResult(
            tpa_points=d.get("tpa_points", 0),
            tpa_confidence=d.get("tpa_confidence", 1.0),
        ),
        risk=RiskProfile(
            risk_score=d.get("risk_score", 0.0),
            priority_band=d.get("priority_band", "P2"),
        ),
        traceability=Traceability(
            requirement_tags=d.get("requirement_tags", []),
            covering_tests=d.get("covering_tests", []),
            prescription_history=d.get("prescription_history", []),
            assertion_count=d.get("assertion_count", 0),
        ),
        stop_criteria_met=d.get("stop_criteria_met", False),
        optimization_hints=d.get("optimization_hints", []),
        file_hash=d.get("file_hash", ""),
        computed_at=d.get("computed_at", 0.0),
    )


# ── Helpers ──────────────────────────────────────────────────────────


_AST_TREE_CACHE: dict[str, ast.Module | None] = {}


def _get_ast_tree(source_file: str) -> ast.Module | None:
    """Parse and cache the AST for a source file."""
    import ast as ast_mod

    if source_file not in _AST_TREE_CACHE:
        try:
            with open(source_file) as f:
                _AST_TREE_CACHE[source_file] = ast_mod.parse(f.read())
        except (OSError, SyntaxError):
            _AST_TREE_CACHE[source_file] = None
    return _AST_TREE_CACHE[source_file]


def _find_func_node(source_file: str, func_key: str) -> ast.FunctionDef | None:
    """Parse source file and find the function AST node.

    Resolves qualified names including nested classes:
    - "func"              → top-level function
    - "Class.method"      → method inside Class
    - "Outer.Inner.method" → method inside Outer.Inner
    """
    import ast as ast_mod

    if not source_file or not Path(source_file).exists():
        return None

    tree = _get_ast_tree(source_file)
    if tree is None:
        return None

    func_name = func_key.split("::")[-1] if "::" in func_key else func_key

    if "." not in func_name:
        # Top-level function: search module-level and class-level
        for node in ast_mod.walk(tree):
            if isinstance(node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef)) and node.name == func_name:
                return node
        return None

    # Qualified name: walk the chain (e.g., "Outer.Inner.method")
    parts = func_name.split(".")
    method_name = parts[-1]
    class_chain = parts[:-1]

    # Navigate class hierarchy
    scope: ast.AST = tree
    for class_name in class_chain:
        found = False
        children = scope.body if hasattr(scope, "body") else []
        for node in children:
            if isinstance(node, ast_mod.ClassDef) and node.name == class_name:
                scope = node
                found = True
                break
        if not found:
            return None

    # Find method within the resolved class scope
    for node in getattr(scope, "body", []):
        if isinstance(node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef)) and node.name == method_name:
            return node
    return None


_REQ_TAG_PATTERN = re.compile(r"(REQ-\d+|US-\d+|SPEC-\d+)", re.IGNORECASE)


def _extract_requirement_tags(func_node: ast.FunctionDef) -> list[str]:
    """Extract requirement tags from function docstring."""
    import ast as ast_mod

    docstring = ast_mod.get_docstring(func_node)
    if not docstring:
        return []
    return _REQ_TAG_PATTERN.findall(docstring)


def _build_test_coverage_map(test_files: list[str]) -> dict[str, list[str]]:
    """Scan test files for function name references to build coverage map."""
    coverage: dict[str, list[str]] = {}
    for tf in test_files:
        _scan_test_file(tf, coverage)
    return coverage


def _scan_test_file(filepath: str, coverage: dict[str, list[str]]) -> None:
    """Scan a single test file and update coverage map."""
    import ast as ast_mod

    try:
        with open(filepath) as f:
            tree = ast_mod.parse(f.read())
    except (OSError, SyntaxError):
        return

    test_funcs = [
        n
        for n in ast_mod.walk(tree)
        if isinstance(n, ast_mod.FunctionDef) and n.name.startswith("test_")
    ]
    for test_func in test_funcs:
        _collect_calls_in_test(test_func, coverage)


def _collect_calls_in_test(test_func: Any, coverage: dict[str, list[str]]) -> None:
    """Find function calls within a test and update coverage map."""
    import ast as ast_mod

    for child in ast_mod.walk(test_func):
        if not isinstance(child, ast_mod.Call):
            continue
        name = _extract_call_name(child)
        if name:
            coverage.setdefault(name, []).append(test_func.name)


def _extract_call_name(node: Any) -> str | None:
    """Extract simple name from a Call node."""
    import ast as ast_mod

    if isinstance(node.func, ast_mod.Name):
        return node.func.id
    if isinstance(node.func, ast_mod.Attribute):
        return node.func.attr
    return None


def _file_hash(filepath: str) -> str:
    """Compute hash of file contents for cache invalidation."""
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


_GATE_THRESHOLDS: dict[str, float] = {
    "cacheable": 0.6,
    "cache-without-invalidation": 0.8,
    "parallelizable": 0.7,
    "map-reduce-compatible": 0.8,
    "foldable": 0.5,
}


def _check_stop_criteria(spec_level: float, hints: list[str]) -> bool:
    """Check if specification level meets all hint gate thresholds."""
    if not hints:
        return False
    threshold = max(_GATE_THRESHOLDS.get(h, 0.0) for h in hints)
    return spec_level >= threshold if threshold > 0 else False
