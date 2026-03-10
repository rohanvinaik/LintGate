"""Detect JIT compilation candidates for pure numeric kernels.

Walks the AST to score pure functions on numeric-kernel suitability:
numeric type annotations, loop density, arithmetic intensity, and low
allocation pressure.  Only pure functions (from a prior purity analysis)
are considered.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from .algebra_types import PurityResult
from ._helpers import get_name

# Type-annotation substrings that indicate numeric data.
_NUMERIC_TYPE_NAMES: set[str] = {
    "int",
    "float",
    "complex",
    "np.ndarray",
    "ndarray",
    "numpy.ndarray",
    "np.float64",
    "np.float32",
    "np.int64",
    "np.int32",
}

_NUMERIC_CONTAINER_PREFIXES: tuple[str, ...] = (
    "list[int]",
    "list[float]",
    "list[complex]",
    "tuple[int",
    "tuple[float",
    "tuple[complex",
    "Sequence[int]",
    "Sequence[float]",
    "Sequence[complex]",
    "Iterable[int]",
    "Iterable[float]",
    "Iterable[complex]",
    "Array",
)

# Constructor calls that allocate non-numeric heap objects.
_ALLOC_CONSTRUCTORS: set[str] = {"dict", "list", "set", "frozenset", "bytearray"}


@dataclass(frozen=True)
class JitCandidate:
    """A function identified as a potential JIT-compilation target."""

    function_name: str
    qualified_name: str
    file: str  # source file (empty string if unknown)
    line: int
    jit_score: float  # 0.0-1.0 aggregate score
    jit_band: str  # "HIGH" (>0.7), "MEDIUM" (0.3-0.7), "LOW" (<0.3)
    factors: dict[str, float]  # Individual factor scores
    recommended_backend: str  # e.g. "numba", "cython", "jax"

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_name": self.function_name,
            "qualified_name": self.qualified_name,
            "file": self.file,
            "line": self.line,
            "jit_score": round(self.jit_score, 3),
            "jit_band": self.jit_band,
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
            "recommended_backend": self.recommended_backend,
        }


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------


def _annotation_text(node: ast.expr | None) -> str:
    """Best-effort unparse of a type-annotation node to a comparable string."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        name = get_name(node)
        return name if name else ""


def _is_numeric_annotation(text: str) -> bool:
    """Return True if *text* looks like a numeric type annotation."""
    if not text:
        return False
    low = text.lower().replace(" ", "")
    if low in {t.lower() for t in _NUMERIC_TYPE_NAMES}:
        return True
    for prefix in _NUMERIC_CONTAINER_PREFIXES:
        if low.startswith(prefix.lower()):
            return True
    return False


def _score_numeric_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> float:
    """Score 0-1 based on how many params/return are numeric-typed."""
    all_args: list[ast.arg] = list(node.args.args)
    all_args.extend(node.args.kwonlyargs)
    if hasattr(node.args, "posonlyargs"):
        all_args.extend(node.args.posonlyargs)  # type: ignore[attr-defined]

    # Filter out 'self' / 'cls'
    all_args = [a for a in all_args if a.arg not in ("self", "cls")]

    annotations: list[str] = [_annotation_text(a.annotation) for a in all_args]
    ret_ann = _annotation_text(node.returns)
    if ret_ann:
        annotations.append(ret_ann)

    if not annotations:
        return 0.0

    annotated = [a for a in annotations if a]
    if not annotated:
        return 0.0

    numeric_count = sum(1 for a in annotated if _is_numeric_annotation(a))
    return numeric_count / len(annotations)


def _count_loops(body: list[ast.stmt]) -> int:
    """Recursively count For/While loops in a function body."""
    count = 0
    for node in ast.walk(ast.Module(body=body, type_ignores=[]))  :
        if isinstance(node, (ast.For, ast.While)):
            count += 1
    return count


def _count_statements(body: list[ast.stmt]) -> int:
    """Count total statements (recursively) in a function body."""
    count = 0
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.stmt):
            count += 1
    return count


def _score_loop_density(node: ast.FunctionDef | ast.AsyncFunctionDef) -> float:
    loop_count = _count_loops(node.body)
    stmt_count = _count_statements(node.body)
    return min(loop_count / max(stmt_count, 1) * 3, 1.0)


def _count_arithmetic(body: list[ast.stmt]) -> tuple[int, int]:
    """Return (arithmetic_op_count, total_op_count) for all expressions."""
    arith_count = 0
    total_ops = 0
    arith_binop_types = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)
    arith_unaryop_types = (ast.USub, ast.UAdd)

    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.BinOp):
            total_ops += 1
            if isinstance(node.op, arith_binop_types):
                arith_count += 1
        elif isinstance(node, ast.UnaryOp):
            total_ops += 1
            if isinstance(node.op, arith_unaryop_types):
                arith_count += 1
        elif isinstance(node, ast.BoolOp):
            total_ops += 1
        elif isinstance(node, ast.Compare):
            total_ops += 1

    return arith_count, total_ops


def _score_arithmetic_intensity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> float:
    arith_count, total_ops = _count_arithmetic(node.body)
    return min(arith_count / max(total_ops, 1), 1.0)


def _count_allocations(body: list[ast.stmt]) -> int:
    """Count heap-allocating patterns: constructor calls, f-strings, class instantiation."""
    count = 0
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Call):
            name = get_name(node.func)
            if name and name in _ALLOC_CONSTRUCTORS:
                count += 1
            # Class instantiation heuristic: capitalized call target
            elif name and name[0].isupper() and name not in _NUMERIC_TYPE_NAMES:
                count += 1
        elif isinstance(node, ast.JoinedStr):
            # f-string allocates a string
            count += 1
    return count


def _score_low_allocation(node: ast.FunctionDef | ast.AsyncFunctionDef) -> float:
    alloc_count = _count_allocations(node.body)
    return max(1.0 - alloc_count * 0.2, 0.0)


def _compute_band(score: float) -> str:
    if score > 0.7:
        return "HIGH"
    if score > 0.3:
        return "MEDIUM"
    return "LOW"


def _recommend_backend(loop_density: float, arith_intensity: float) -> str:
    if loop_density > 0.3:
        return "numba"
    if arith_intensity > 0.5:
        return "jax"
    return "cython"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_jit_candidates(
    tree: ast.AST,
    purity_results: dict[str, PurityResult],
    file_path: str = "",
) -> list[JitCandidate]:
    """Walk *tree* and return scored JIT candidates for pure numeric kernels.

    Parameters
    ----------
    tree:
        Parsed AST module.
    purity_results:
        Mapping of qualified name -> PurityResult (from ``analyze_purity``).
    file_path:
        Optional source-file path attached to each candidate.

    Returns
    -------
    list[JitCandidate]
        Only functions with ``jit_score >= 0.2`` are included.
    """
    candidates: list[JitCandidate] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Build qualified name the same way purity.py does (simple heuristic).
        func_name = node.name
        # Look up purity; skip impure functions.
        purity = purity_results.get(func_name)
        if purity is None:
            # Try matching by qualified_name keys in purity_results.
            for qn, pr in purity_results.items():
                if pr.function_name == func_name and pr.line == node.lineno:
                    purity = pr
                    break
        if purity is None or not purity.is_pure:
            continue

        qualified_name = purity.qualified_name

        numeric_sig = _score_numeric_signature(node)
        loop_density = _score_loop_density(node)
        arith_intensity = _score_arithmetic_intensity(node)
        low_alloc = _score_low_allocation(node)

        jit_score = (
            0.3 * numeric_sig
            + 0.25 * loop_density
            + 0.25 * arith_intensity
            + 0.2 * low_alloc
        )

        if jit_score < 0.2:
            continue

        factors = {
            "numeric_signature": numeric_sig,
            "loop_density": loop_density,
            "arithmetic_intensity": arith_intensity,
            "low_allocation": low_alloc,
        }

        candidates.append(
            JitCandidate(
                function_name=func_name,
                qualified_name=qualified_name,
                file=file_path,
                line=node.lineno,
                jit_score=jit_score,
                jit_band=_compute_band(jit_score),
                factors=factors,
                recommended_backend=_recommend_backend(loop_density, arith_intensity),
            )
        )

    return candidates
