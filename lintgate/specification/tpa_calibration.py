"""Test Point Analysis — structural complexity metric for sigma calibration.

Weights different AST elements by their test-generation cost.
Provides a second opinion on the decision-tree sigma estimate.
"""

from __future__ import annotations

import ast
import math

from .types import TPAResult

_TPA_WEIGHT_2 = (ast.If, ast.While, ast.For, ast.Try, ast.ExceptHandler)
_TPA_WEIGHT_1 = (ast.Raise,)


def compute_tpa_points(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count weighted TPA points from AST structural elements."""
    points = 0.0
    for node in ast.walk(func_node):
        points += _node_tpa_weight(node)
    points += len(func_node.args.args)
    return int(points)


def _node_tpa_weight(node: ast.AST) -> float:
    """Return TPA weight for a single AST node."""
    if isinstance(node, _TPA_WEIGHT_2):
        return 2.0
    if isinstance(node, ast.Match):
        return float(len(getattr(node, "cases", [])))
    if isinstance(node, ast.Return) and _is_non_constant_return(node):
        return 1.0
    if isinstance(node, _TPA_WEIGHT_1):
        return 1.0
    if isinstance(node, ast.Assert):
        return 0.5
    return 0.0


def _is_non_constant_return(node: ast.Return) -> bool:
    return node.value is not None and not isinstance(node.value, ast.Constant)


def calibrate_sigma(decision_tree_sigma: int, tpa_points: int) -> TPAResult:
    """Calibrate decision-tree sigma against TPA estimate.

    Returns TPAResult with calibrated sigma and confidence in the agreement.
    """
    tpa_sigma = math.ceil(tpa_points * 0.8) if tpa_points > 0 else 0
    calibrated = (
        round((decision_tree_sigma + tpa_sigma) / 2) if (decision_tree_sigma + tpa_sigma) > 0 else 0
    )

    denom = max(decision_tree_sigma, tpa_sigma, 1)
    tpa_confidence = 1.0 - abs(decision_tree_sigma - tpa_sigma) / denom

    return TPAResult(
        tpa_points=tpa_points,
        tpa_sigma=calibrated,
        tpa_confidence=max(tpa_confidence, 0.0),
    )
