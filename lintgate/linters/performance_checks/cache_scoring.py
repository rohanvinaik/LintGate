"""ROI-ranked cacheability scoring for pure functions.

Scores pure functions by cache ROI based on compute weight, call hotness,
and repeatability heuristics. Impure functions receive a SKIP band.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._helpers import get_name

if TYPE_CHECKING:
    from .algebra_types import PurityResult

# AST node types that represent meaningful computation
_COMPUTE_NODE_TYPES = (
    ast.BinOp,
    ast.Call,
    ast.BoolOp,
    ast.Compare,
    ast.Subscript,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)

# Annotation names that suggest highly repeatable (finite-domain) arguments
_REPEATABLE_ANNOTATIONS = {"Literal", "Enum", "bool", "int"}

_COMPUTE_CAP = 20
_FAN_IN_CAP = 10


@dataclass(frozen=True)
class CacheScore:
    """Aggregate cache ROI score for a single function."""

    score: float  # 0.0-1.0 aggregate cache ROI score
    band: str  # "HIGH" (>0.7), "MEDIUM" (0.3-0.7), "LOW" (<0.3), "SKIP" (impure)
    factors: dict[str, float]  # Individual factor scores

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "band": self.band,
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
        }


def _count_compute_nodes(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count AST nodes that represent meaningful computation within a function."""
    count = 0
    for node in ast.walk(func_node):
        if isinstance(node, _COMPUTE_NODE_TYPES):
            count += 1
    return count


def _compute_weight(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> float:
    """Score 0.0-1.0 based on AST complexity. Simple functions score low."""
    count = _count_compute_nodes(func_node)
    return min(count / _COMPUTE_CAP, 1.0)


def _call_hotness(call_graph: dict[str, Any] | None) -> float:
    """Score 0.0-1.0 based on fan-in from call graph. Default 0.5 if no data."""
    if call_graph is None:
        return 0.5
    fan_in = call_graph.get("fan_in", 0)
    return min(fan_in / _FAN_IN_CAP, 1.0)  # type: ignore[no-any-return]


def _get_annotation_name(annotation: ast.expr) -> str | None:
    """Extract a simple name from a type annotation node."""
    name = get_name(annotation)
    if name is not None:
        return name
    # Handle subscripted generics like Literal[...] — extract the base name
    if isinstance(annotation, ast.Subscript):
        return get_name(annotation.value)
    return None


def _repeatability(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> float:
    """Score 0.0-1.0 heuristic from parameter types.

    Fewer parameters = more repeatable. Constant/enum-like args
    (Literal, Enum, bool, int annotations) boost the score.
    """
    args = func_node.args
    all_args = list(args.args) + list(args.kwonlyargs)
    if hasattr(args, "posonlyargs"):
        all_args.extend(args.posonlyargs)

    param_count = len(all_args)
    if args.vararg:
        param_count += 1
    if args.kwarg:
        param_count += 1

    # Fewer params → higher base score. 0 params = 1.0, 5+ params = 0.0
    base = 1.0 if param_count == 0 else max(1.0 - param_count / 5.0, 0.0)

    # Boost for repeatable-type annotations
    repeatable_count = 0
    for arg in all_args:
        if arg.annotation is not None:
            ann_name = _get_annotation_name(arg.annotation)
            if ann_name is not None and ann_name in _REPEATABLE_ANNOTATIONS:
                repeatable_count += 1

    annotation_boost = 0.3 * (repeatable_count / param_count) if param_count > 0 else 0.0

    return min(base + annotation_boost, 1.0)


def _band_from_score(score: float) -> str:
    """Classify score into HIGH / MEDIUM / LOW band."""
    if score > 0.7:
        return "HIGH"
    if score > 0.3:
        return "MEDIUM"
    return "LOW"


def compute_cache_score(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    purity_result: PurityResult,
    call_graph: dict[str, Any] | None = None,
) -> CacheScore:
    """Compute ROI-ranked cacheability score for a single function.

    Args:
        func_node: The AST node of the function.
        purity_result: The purity analysis result for this function.
        call_graph: Optional dict with ``fan_in`` key indicating call-site count.

    Returns:
        A CacheScore. Impure functions receive band ``SKIP`` with score 0.0.
    """
    if not purity_result.is_pure:
        return CacheScore(
            score=0.0,
            band="SKIP",
            factors={"compute_weight": 0.0, "call_hotness": 0.0, "repeatability": 0.0},
        )

    cw = _compute_weight(func_node)
    ch = _call_hotness(call_graph)
    rp = _repeatability(func_node)

    score = 0.4 * cw + 0.35 * ch + 0.25 * rp
    band = _band_from_score(score)

    return CacheScore(
        score=score,
        band=band,
        factors={"compute_weight": cw, "call_hotness": ch, "repeatability": rp},
    )


def score_all_cacheable(
    tree: ast.AST,
    purity_results: dict[str, PurityResult],
    call_graph: dict[str, dict[str, Any]] | None = None,
) -> dict[str, CacheScore]:
    """Score all functions in an AST for cacheability.

    Walks the AST for FunctionDef/AsyncFunctionDef nodes, matches each to
    its purity result by qualified name, and computes a CacheScore.

    Args:
        tree: Parsed AST module.
        purity_results: Mapping of qualified function names to PurityResult.
        call_graph: Optional mapping of function names to call-graph dicts
            (each with a ``fan_in`` key).

    Returns:
        Mapping of qualified function names to CacheScores.
    """
    scores: dict[str, CacheScore] = {}

    class _Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._handle(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._handle(node)

        def _handle(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            qualname = node.name
            if self.class_stack:
                qualname = f"{'.'.join(self.class_stack)}.{node.name}"

            if qualname in purity_results:
                cg = call_graph.get(qualname) if call_graph else None
                scores[qualname] = compute_cache_score(node, purity_results[qualname], cg)

    _Collector().visit(tree)
    return scores
