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

from lintgate.keys import SCHEMA_VERSION, canonical_function_key, try_parse_function_key

from .predictor import PredictorInput, detect_phase_from_trajectory, predict, update_trajectory
from .risk_model import compute_risk_score
from .types import (
    ASTMetrics,
    FunctionSpecification,
    RiskProfile,
    SpecCore,
    SpecificationLedger,
    Traceability,
    TrajectoryState,
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
    prior_ledger: SpecificationLedger | None = None,
    mutation_cache: dict[str, dict] | None = None,
) -> SpecificationLedger:
    """Build specification ledger from existing channel manifests.

    Args:
        property_manifest: From performance channel prepass.
        teff_manifest: From test effectiveness channel prepass.
        project_root: Project root for relative path resolution.
        py_files: Python source files for AST parsing.
        test_files: Test files for traceability extraction.
        call_graph: Optional call graph for fan-in/fan-out risk scoring.
        prior_ledger: Previous ledger for cross-run trajectory accumulation.
        mutation_cache: Maps function_key → mutation result dict. When present,
            spec_level is derived from ``1.0 - survival_rate`` (ground truth)
            instead of static ``assertion_count / sigma``.
    """
    ledger = SpecificationLedger()
    test_coverage_map, test_file_coverage_map = _build_test_coverage_map(test_files or [])

    for func_key, func_props in property_manifest.functions.items():
        prior_spec = prior_ledger.functions.get(func_key) if prior_ledger else None
        func_spec = _build_function_spec(
            func_key=func_key,
            func_props=func_props,
            teff_manifest=teff_manifest,
            project_root=project_root,
            test_coverage_map=test_coverage_map,
            test_file_coverage_map=test_file_coverage_map,
            call_graph=call_graph,
            prior_spec=prior_spec,
            mutation_cache=mutation_cache,
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
    test_file_coverage_map: dict[str, set[str]] | None = None,
    call_graph: CrossModuleCallGraph | None = None,
    prior_spec: FunctionSpecification | None = None,
    mutation_cache: dict[str, dict] | None = None,
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

    # Derive spec_level from mutation data when available (ground truth)
    mutation_spec_level = None
    mutation_data_source = None
    if mutation_cache:
        mut_state = mutation_cache.get(func_key)
        if mut_state:
            survival = mut_state.get("survival_rate")
            total = mut_state.get("total_mutants", 0)
            depth = mut_state.get("coverage_depth", "")
            if survival is not None and total > 0:
                mutation_spec_level = 1.0 - float(survival)
                mutation_data_source = f"mutation_{depth}" if depth else "mutation"

    # Run predictor
    signals = PredictorInput(
        is_pure=func_props.purity.is_pure,
        purity_confidence=func_props.purity.confidence,
        semantic_ratio=semantic_ratio,
        weakness_taxonomy=weakness,
        assertion_count=assertion_count,
        mutation_spec_level=mutation_spec_level,
        mutation_data_source=mutation_data_source,
    )
    result = predict(func_node, signals)

    # Risk model
    covering = test_coverage_map.get(func_name, [])
    # Call graph is indexed by simple name (node.name from ast.walk), not
    # qualified "Class.method" names.  Extract the bare name for lookup.
    # func_name may be "Class.method" — we need just "method".
    bare_name = func_name.rsplit(".", 1)[-1]
    graph_key = canonical_function_key(parsed[0], bare_name) if parsed else func_key
    fan_in = call_graph.fan_in(graph_key) if call_graph else 0
    fan_out = call_graph.fan_out(graph_key) if call_graph else 0
    risk = compute_risk_score(
        is_pure=func_props.purity.is_pure,
        fan_in=fan_in,
        fan_out=fan_out,
        is_public=not func_name.startswith("_"),
        testability_score=result.testability.testability_score,
        regime=result.regime,
    )

    # Traceability + coupling surface
    req_tags = _extract_requirement_tags(func_node)
    covering_files: list[str] = []
    coupling_surface = 0
    if test_file_coverage_map:
        file_set = test_file_coverage_map.get(func_name, set())
        covering_files = sorted(file_set)
        coupling_surface = len(file_set)

    # Optimization gate stop criteria
    hints = list(func_props.optimization_hints)
    stop_met = _check_stop_criteria(result.spec_level, hints)

    # Cross-run trajectory (Thm 3.4): accumulate ΔK from prior ledger
    trajectory = result.trajectory
    phase = result.phase
    if prior_spec is not None:
        prior_traj = prior_spec.trajectory
        prior_level = prior_spec.core.specification_level
        trajectory = update_trajectory(
            prior_traj,
            result.spec_level,
            prior_level,
            sigma=result.sigma,
        )
        phase = detect_phase_from_trajectory(trajectory, result.spec_level)

    return FunctionSpecification(
        function_key=func_key,
        source_file=source_file,
        core=SpecCore(
            estimated_sigma=result.sigma,
            sigma_confidence=result.sigma_confidence,
            regime=result.regime,
            regime_rationale=result.regime_rationale,
            specification_level=result.spec_level,
            data_source=result.data_source,
            behavioral_dimensions=result.sigma,
            phase=phase,
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
            covering_test_files=covering_files,
            assertion_count=assertion_count,
            coupling_surface=coupling_surface,
        ),
        trajectory=trajectory,
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
            regime_rationale=d.get("regime_rationale", ""),
            specification_level=d.get("specification_level", 0.0),
            data_source=d.get("data_source", "static"),
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
            covering_test_files=d.get("covering_test_files", []),
            prescription_history=d.get("prescription_history", []),
            assertion_count=d.get("assertion_count", 0),
            coupling_surface=d.get("coupling_surface", 0),
        ),
        trajectory=_deserialize_trajectory(d.get("trajectory", {})),
        stop_criteria_met=d.get("stop_criteria_met", False),
        optimization_hints=d.get("optimization_hints", []),
        file_hash=d.get("file_hash", ""),
        computed_at=d.get("computed_at", 0.0),
    )


def _deserialize_trajectory(d: dict | None) -> TrajectoryState:
    """Deserialize a TrajectoryState from dict."""
    if not d or not isinstance(d, dict):
        return TrajectoryState()
    return TrajectoryState(
        delta_k=d.get("delta_k", []),
        transition_index=d.get("transition_index"),
        estimated_remaining=d.get("estimated_remaining", 0),
        convergence_rate=d.get("convergence_rate", 0.0),
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


def _resolve_class_scope(tree: ast.AST, class_chain: list[str]) -> ast.AST | None:
    """Navigate a class hierarchy in an AST, returning the innermost class node."""
    import ast as ast_mod

    scope: ast.AST = tree
    for class_name in class_chain:
        children = scope.body if hasattr(scope, "body") else []
        match = next(
            (n for n in children if isinstance(n, ast_mod.ClassDef) and n.name == class_name),
            None,
        )
        if match is None:
            return None
        scope = match
    return scope


def _find_func_in_scope(scope: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find a function/method by name in an AST scope's direct body."""
    import ast as ast_mod

    for node in getattr(scope, "body", []):
        if isinstance(node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _find_func_node(
    source_file: str, func_key: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
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
        for node in ast_mod.walk(tree):
            if (
                isinstance(node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef))
                and node.name == func_name
            ):
                return node
        return None

    parts = func_name.split(".")
    scope = _resolve_class_scope(tree, parts[:-1])
    if scope is None:
        return None
    return _find_func_in_scope(scope, parts[-1])


_REQ_TAG_PATTERN = re.compile(r"(REQ-\d+|US-\d+|SPEC-\d+)", re.IGNORECASE)


def _extract_requirement_tags(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract requirement tags from function docstring."""
    import ast as ast_mod

    docstring = ast_mod.get_docstring(func_node)
    if not docstring:
        return []
    return _REQ_TAG_PATTERN.findall(docstring)


def _build_test_coverage_map(
    test_files: list[str],
) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """Scan test files for function name references to build coverage map.

    Returns:
        (func_name → [test_func_names], func_name → {test_file_paths})
    """
    coverage: dict[str, list[str]] = {}
    file_coverage: dict[str, set[str]] = {}
    for tf in test_files:
        _scan_test_file(tf, coverage, file_coverage)
    return coverage, file_coverage


def _scan_test_file(
    filepath: str,
    coverage: dict[str, list[str]],
    file_coverage: dict[str, set[str]] | None = None,
) -> None:
    """Scan a single test file and update coverage map."""
    import ast as ast_mod

    try:
        with open(filepath) as f:
            tree = ast_mod.parse(f.read())
    except (OSError, SyntaxError):
        return

    _collect_tests_from_body(tree.body, coverage, filepath, file_coverage)


def _collect_tests_from_body(
    body: list[Any],
    coverage: dict[str, list[str]],
    filepath: str,
    file_coverage: dict[str, set[str]] | None = None,
    prefix: str = "",
) -> None:
    """Collect test functions and methods with qualified class context."""
    import ast as ast_mod

    for node in body:
        if isinstance(
            node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            qualified = f"{prefix}{node.name}" if prefix else node.name
            _collect_calls_in_test(node, coverage, filepath, file_coverage, qualified)
        elif isinstance(node, ast_mod.ClassDef):
            _collect_tests_from_body(
                node.body,
                coverage,
                filepath,
                file_coverage,
                prefix=f"{prefix}{node.name}.",
            )


def _collect_calls_in_test(
    test_func: Any,
    coverage: dict[str, list[str]],
    test_filepath: str | None = None,
    file_coverage: dict[str, set[str]] | None = None,
    test_name: str | None = None,
) -> None:
    """Find function calls within a test and update coverage map."""
    import ast as ast_mod

    label: str = test_name or str(getattr(test_func, "name", ""))
    for child in ast_mod.walk(test_func):
        if not isinstance(child, ast_mod.Call):
            continue
        name = _extract_call_name(child)
        if name:
            coverage.setdefault(name, []).append(label)
            if file_coverage is not None and test_filepath:
                file_coverage.setdefault(name, set()).add(test_filepath)


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
