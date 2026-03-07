"""Specification predictor — 6-path decision tree from static signals.

Consumes purity, test effectiveness, and AST data to estimate
specification complexity (sigma), regime, phase, and specification level.
Enhanced with DFT scoring, statefulness detection, and test design signals.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .test_design_signals import extract_all as extract_design_signals
from .tpa_calibration import calibrate_sigma, compute_tpa_points
from .types import PredictionResult, TestabilityProfile


@dataclass
class PredictorInput:
    """Input signals for the specification predictor."""

    is_pure: bool = False
    purity_confidence: float = 0.5
    semantic_ratio: float = 0.0
    weakness_taxonomy: str = ""
    assertion_count: int = 0


def predict(
    func_node: ast.FunctionDef,
    signals: PredictorInput,
) -> PredictionResult:
    """Run the 6-path decision tree to estimate specification complexity."""
    branch_count = count_branches(func_node)
    ast_cats = count_ast_categories(func_node)
    param_count = len(func_node.args.args)

    # Compute test design signals
    design_signals = extract_design_signals(func_node)

    # DFT scoring
    testability = compute_dft_score(func_node)

    # 6-path decision tree → base sigma
    sigma_base, regime = _decision_tree(
        is_pure=signals.is_pure,
        semantic_ratio=signals.semantic_ratio,
        weakness_taxonomy=signals.weakness_taxonomy,
        ast_category_count=ast_cats,
        branch_count=branch_count,
        parameter_count=param_count,
    )

    # Design signals can only increase sigma, never decrease
    sigma_design = max(
        design_signals.boundary_points,
        design_signals.equivalence_partitions,
        design_signals.decision_rule_count,
        design_signals.predicate_effect_links,
    )
    sigma_raw = max(sigma_base, sigma_design)

    # TPA calibration
    tpa_points = compute_tpa_points(func_node)
    tpa = calibrate_sigma(sigma_raw, tpa_points)
    sigma = tpa.tpa_sigma if tpa.tpa_sigma > 0 else sigma_raw

    # Sigma confidence: base confidence modulated by DFT
    sigma_confidence = signals.purity_confidence * testability.testability_score

    # Specification level: how much of sigma is covered by existing assertions
    spec_level = _compute_spec_level(sigma, signals.assertion_count)

    # Phase detection
    phase = _detect_phase(spec_level)

    return PredictionResult(
        spec_level=spec_level,
        regime=regime,
        sigma=sigma,
        phase=phase,
        sigma_confidence=sigma_confidence,
        testability=testability,
        design_signals=design_signals,
        tpa=tpa,
    )


# ── Decision tree ────────────────────────────────────────────────────


def _decision_tree(
    is_pure: bool,
    semantic_ratio: float,
    weakness_taxonomy: str,
    ast_category_count: int,
    branch_count: int,
    parameter_count: int,
) -> tuple[int, str]:
    """6-path decision tree. Returns (sigma_estimate, regime)."""
    if is_pure:
        if semantic_ratio >= 0.5 and weakness_taxonomy in ("", "HEALTHY"):
            # Path 1: well-specified
            return max(branch_count, 1), "A"
        if semantic_ratio < 0.5:
            # Path 2: known under-specification
            return branch_count + parameter_count + 1, "A"
        # Path 3: pure + weakness (maximum wasted opportunity)
        return branch_count + parameter_count + 2, "A"

    # Impure paths
    if ast_category_count <= 8:
        # Path 4: tractable impure
        return ast_category_count + branch_count, "A"
    if semantic_ratio >= 0.5:
        # Path 5: hard but progressing
        return ast_category_count + branch_count + parameter_count, "A"
    # Path 6: hardest to specify (Regime B candidate)
    sigma = ast_category_count + branch_count + parameter_count + 2
    regime = "B" if sigma > 20 else "A"
    return sigma, regime


# ── AST helpers ──────────────────────────────────────────────────────


def count_ast_categories(func_node: ast.FunctionDef) -> int:
    """Count distinct AST node types in a function body."""
    categories: set[type] = set()
    for node in ast.walk(func_node):
        categories.add(type(node))
    return len(categories)


def count_branches(func_node: ast.FunctionDef) -> int:
    """Count branching constructs (If/For/While/Try/Match)."""
    count = 0
    for node in ast.walk(func_node):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.Match)):
            count += 1
    return count


# ── DFT scoring ──────────────────────────────────────────────────────


def compute_dft_score(func_node: ast.FunctionDef) -> TestabilityProfile:
    """Compute design-for-testability score.

    Starts at 1.0, deducts for statefulness, side effects,
    hidden dependencies, and lack of injection points.
    """
    is_stateful = detect_statefulness(func_node)
    has_side_effects = _detect_side_effects(func_node)
    hidden_deps = _count_hidden_deps(func_node)
    injectable_deps = _count_injectable_deps(func_node)
    param_count = len(func_node.args.args)

    score = 1.0
    if is_stateful:
        score -= 0.3
    if has_side_effects:
        score -= 0.3
    if hidden_deps > 0:
        score -= 0.2
    if injectable_deps == 0 and param_count > 0:
        score -= 0.1
    score = max(score, 0.0)

    return TestabilityProfile(
        testability_score=score,
        is_stateful=is_stateful,
        has_side_effects=has_side_effects,
        injectable_deps=injectable_deps,
        hidden_deps=hidden_deps,
    )


def detect_statefulness(func_node: ast.FunctionDef) -> bool:
    """Detect if function writes to self.*, global, or nonlocal state."""
    for node in ast.walk(func_node):
        # self.attr = ... (store to self)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            return True
        # global x
        if isinstance(node, ast.Global):
            return True
        # nonlocal x
        if isinstance(node, ast.Nonlocal):
            return True
    return False


def _detect_side_effects(func_node: ast.FunctionDef) -> bool:
    """Detect I/O, network, file, or subprocess side effects."""
    io_names = frozenset(
        {
            "print",
            "open",
            "write",
            "read",
            "input",
            "subprocess",
            "os.system",
            "os.popen",
            "requests",
            "urllib",
            "socket",
        }
    )
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name and any(name.startswith(io) for io in io_names):
            return True
    return False


def _count_hidden_deps(func_node: ast.FunctionDef) -> int:
    """Count global/module-level reads not via parameters."""
    param_names = {arg.arg for arg in func_node.args.args}
    hidden = 0
    for node in ast.walk(func_node):
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            continue
        if node.id in param_names or node.id.startswith("_"):
            continue
        if node.id.isupper() or node.id[0].isupper():
            hidden += 1
    return hidden


def _count_injectable_deps(func_node: ast.FunctionDef) -> int:
    """Count parameters that are callable or have default=None."""
    count = 0
    defaults = func_node.args.defaults
    num_non_default = len(func_node.args.args) - len(defaults)

    for i, _arg in enumerate(func_node.args.args):
        default_idx = i - num_non_default
        if 0 <= default_idx < len(defaults):
            default = defaults[default_idx]
            if isinstance(default, ast.Constant) and default.value is None:
                count += 1
    return count


def _call_name(node: ast.Call) -> str | None:
    """Extract the name of a function call."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        return node.func.attr
    return None


# ── Phase detection ──────────────────────────────────────────────────


def _detect_phase(spec_level: float) -> str:
    """Map specification level to dynamics phase."""
    if spec_level < 0.3:
        return "bulk"
    if spec_level < 0.7:
        return "transition"
    if spec_level < 0.95:
        return "tail"
    return "complete"


def _compute_spec_level(sigma: int, assertion_count: int) -> float:
    """Compute specification level as coverage ratio."""
    if sigma <= 0:
        return 1.0 if assertion_count > 0 else 0.0
    return min(assertion_count / sigma, 1.0)
