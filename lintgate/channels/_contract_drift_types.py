"""Contract drift detector — data types and small AST helpers.

Extracted from contract_drift_detector.py to keep the main module under
the 400-line limit.  All symbols are re-exported from the parent module.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignatureChange:
    """A detected change in a function's observable contract."""

    module: str  # e.g. "lintgate.controlplane.behavior_scoring"
    function: str  # e.g. "finalize"
    file: str  # absolute path
    line: int = 0
    change_type: str = ""  # "return_arity" | "param_added" | "param_removed"
    old_value: Any = None
    new_value: Any = None


@dataclass
class AffectedTestSite:
    """A test call site that will break due to a signature change."""

    test_file: str
    line: int
    unpacking_arity: int | None = None  # Number of targets in tuple unpack
    call_expression: str = ""  # e.g. "coord.finalize()"


@dataclass
class ContractDriftResult:
    """Result of contract drift analysis for a single function."""

    change: SignatureChange
    affected_sites: list[AffectedTestSite] = field(default_factory=list)
    advisory: str = ""


# ── AST Helpers ──────────────────────────────────────────────────────


def _extract_function_return_arities(tree: ast.AST) -> dict[str, int]:
    """Extract return tuple arities for all functions in an AST.

    Returns dict of function_name -> arity. Only includes functions
    that return tuples (explicit ast.Tuple in return statement).
    Skips functions with no return or scalar returns.
    """
    arities: dict[str, int] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Check return annotation first (most reliable)
        annotation_arity = _arity_from_annotation(node)
        if annotation_arity is not None:
            arities[node.name] = annotation_arity
            continue

        # Fall back to analyzing return statements
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]
        tuple_returns = [r for r in returns if isinstance(r.value, ast.Tuple)]

        if tuple_returns and isinstance(tuple_returns[0].value, ast.Tuple):
            # Use the first tuple return as representative
            arities[node.name] = len(tuple_returns[0].value.elts)

    return arities


def _arity_from_annotation(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int | None:
    """Extract return arity from type annotation like `-> tuple[A, B, C]`."""
    ann = node.returns
    if ann is None:
        return None

    # tuple[A, B, C] -> Subscript(Name("tuple"), Tuple([A, B, C]))
    if not isinstance(ann, ast.Subscript):
        return None

    # Check it's `tuple`
    if (
        isinstance(ann.value, ast.Name)
        and ann.value.id == "tuple"
        and isinstance(ann.slice, ast.Tuple)
    ):
        return len(ann.slice.elts)

    return None


def _extract_function_params(tree: ast.AST) -> dict[str, set[str]]:
    """Extract parameter names for all functions in an AST.

    Returns dict of function_name -> set of param names.
    Excludes 'self' and 'cls'.
    """
    params: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        names: set[str] = set()
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.arg not in ("self", "cls"):
                names.add(arg.arg)
        if node.args.vararg:
            names.add(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            names.add(f"**{node.args.kwarg.arg}")

        params[node.name] = names

    return params


def _find_function_line(tree: ast.AST, func_name: str) -> int:
    """Find the line number of a function definition."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node.lineno
    return 0


def _filepath_to_module(filepath: str) -> str:
    """Convert file path to module path (best-effort)."""
    # Strip .py extension and convert separators
    path = filepath.rstrip(".py") if filepath.endswith(".py") else filepath
    # Use basename parts after common roots
    parts = path.replace(os.sep, "/").split("/")
    # Find the first part that looks like a package
    for i, part in enumerate(parts):
        if part and not part.startswith("."):
            pkg_path = os.sep.join(parts[i:])
            if os.path.isfile(pkg_path + ".py") or os.path.isdir(pkg_path):
                return ".".join(parts[i:])
    return ".".join(parts[-3:]) if len(parts) >= 3 else ".".join(parts)
