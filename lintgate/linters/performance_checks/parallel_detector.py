"""Detect parallelization opportunities at the call-site level.

Walks the AST to find three patterns where independent pure computations
could be executed in parallel:

- **PARALLEL_MAP**: For-loops or comprehensions applying a pure function to each element.
- **PARALLEL_ASYNC**: Sequential ``await`` calls on independent targets.
- **PARALLEL_BEAM**: Independent if/elif branches computing results via pure calls.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._helpers import get_name

if TYPE_CHECKING:
    from .algebra_types import PurityResult


@dataclass(frozen=True)
class ParallelOpportunity:
    """A detected parallelization opportunity at a specific call site."""

    pattern: str  # "PARALLEL_MAP", "PARALLEL_ASYNC", "PARALLEL_BEAM"
    file: str  # source file (empty string if unknown)
    line: int  # line number
    callee: str  # function being called in the pattern
    confidence: float  # 0.0-1.0
    constraints: list[str]  # preconditions, e.g. "callee must be pure"
    detail: str  # human-readable explanation

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "file": self.file,
            "line": self.line,
            "callee": self.callee,
            "confidence": round(self.confidence, 2),
            "constraints": self.constraints,
            "detail": self.detail,
        }


def _is_pure(name: str, purity_results: dict[str, PurityResult]) -> bool | None:
    """Check if a function is known pure, unknown, or known impure.

    Returns True if pure, False if impure, None if not in results.
    """
    if name in purity_results:
        return purity_results[name].is_pure
    return None


def _get_call_name(node: ast.Call) -> str | None:
    """Extract the function name from a Call node."""
    return get_name(node.func)


# ---------------------------------------------------------------------------
# PARALLEL_MAP: for-loop / comprehension over a collection with pure call
# ---------------------------------------------------------------------------


def _check_for_loop(
    node: ast.For,
    purity_results: dict[str, PurityResult],
    file_path: str,
) -> list[ParallelOpportunity]:
    """Detect for-loops that apply a pure function to each element.

    Pattern:
        for x in collection:
            result = pure_func(x)
    """
    results: list[ParallelOpportunity] = []

    for stmt in node.body:
        # Look for assignments like: result = func(x)
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue

        call = stmt.value
        callee = _get_call_name(call)
        if callee is None:
            continue

        purity = _is_pure(callee, purity_results)
        if purity is False:
            # Known impure — skip
            continue

        confidence = 0.85 if purity is True else 0.5
        constraints = ["callee must be pure"]
        if purity is None:
            constraints.append("purity of callee is unknown")

        results.append(
            ParallelOpportunity(
                pattern="PARALLEL_MAP",
                file=file_path,
                line=node.lineno,
                callee=callee,
                confidence=confidence,
                constraints=constraints,
                detail=(
                    f"For-loop applies '{callee}' to each element of the iterable. "
                    f"Consider replacing with a parallel map."
                ),
            )
        )

    return results


def _check_comprehension(
    node: ast.ListComp | ast.SetComp | ast.GeneratorExp,
    purity_results: dict[str, PurityResult],
    file_path: str,
) -> list[ParallelOpportunity]:
    """Detect comprehensions that apply a pure function to each element.

    Pattern:
        [pure_func(x) for x in items]
    """
    results: list[ParallelOpportunity] = []

    # The element expression must be a Call
    if not isinstance(node.elt, ast.Call):
        return results

    callee = _get_call_name(node.elt)
    if callee is None:
        return results

    purity = _is_pure(callee, purity_results)
    if purity is False:
        return results

    confidence = 0.85 if purity is True else 0.5
    constraints = ["callee must be pure"]
    if purity is None:
        constraints.append("purity of callee is unknown")

    results.append(
        ParallelOpportunity(
            pattern="PARALLEL_MAP",
            file=file_path,
            line=node.lineno,
            callee=callee,
            confidence=confidence,
            constraints=constraints,
            detail=(
                f"Comprehension applies '{callee}' to each element. "
                f"Consider replacing with a parallel map."
            ),
        )
    )

    return results


# ---------------------------------------------------------------------------
# PARALLEL_ASYNC: sequential awaits on independent targets
# ---------------------------------------------------------------------------


def _get_await_target_name(stmt: ast.stmt) -> str | None:
    """Extract the function name from an await-assign statement.

    Pattern: result = await func(...)
    Returns the function name or None.
    """
    if not isinstance(stmt, ast.Assign):
        return None
    if not isinstance(stmt.value, ast.Await):
        return None
    if not isinstance(stmt.value.value, ast.Call):
        return None
    return _get_call_name(stmt.value.value)


def _get_assign_target_names(stmt: ast.Assign) -> set[str]:
    """Extract all assigned variable names from an Assign node."""
    names: set[str] = set()
    for target in stmt.targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    names.add(elt.id)
    return names


def _get_referenced_names(node: ast.AST) -> set[str]:
    """Collect all Name.id references in an AST subtree."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
    return names


def _check_async_func(
    node: ast.AsyncFunctionDef,
    file_path: str,
) -> list[ParallelOpportunity]:
    """Detect sequential independent awaits in an async function.

    Pattern: multiple ``result = await func(...)`` statements where
    the targets are independent (no data dependency between them).
    """
    results: list[ParallelOpportunity] = []

    # Collect all await-assign statements with their metadata
    await_stmts: list[tuple[ast.Assign, str, set[str], set[str]]] = []
    for stmt in node.body:
        callee = _get_await_target_name(stmt)
        if callee is None:
            continue
        if not isinstance(stmt, ast.Assign):  # guaranteed by _get_await_target_name
            continue
        assigned = _get_assign_target_names(stmt)
        # References in the call arguments (not including the callee itself)
        if not isinstance(stmt.value, ast.Await) or not isinstance(stmt.value.value, ast.Call):
            continue
        arg_refs = _get_referenced_names(stmt.value.value)
        # Remove the callee name itself from references
        callee_name = get_name(stmt.value.value.func)
        if callee_name:
            arg_refs.discard(callee_name)
        await_stmts.append((stmt, callee, assigned, arg_refs))

    if len(await_stmts) < 2:
        return results

    # Find groups of independent awaits: an await is dependent if its
    # arguments reference a variable assigned by a prior await in the group.
    assigned_so_far: set[str] = set()
    independent_callees: list[str] = []
    first_line = await_stmts[0][0].lineno

    for _stmt, callee, assigned, arg_refs in await_stmts:
        if arg_refs & assigned_so_far:
            # This await depends on a prior one — not independent
            break
        independent_callees.append(callee)
        assigned_so_far.update(assigned)

    if len(independent_callees) >= 2:
        results.append(
            ParallelOpportunity(
                pattern="PARALLEL_ASYNC",
                file=file_path,
                line=first_line,
                callee=", ".join(independent_callees),
                confidence=0.75,
                constraints=["awaits must be on independent targets"],
                detail=(
                    f"Sequential awaits on [{', '.join(independent_callees)}] "
                    f"appear independent. Consider using asyncio.gather()."
                ),
            )
        )

    return results


# ---------------------------------------------------------------------------
# PARALLEL_BEAM: independent if/elif branches with pure computations
# ---------------------------------------------------------------------------


def _check_if_branches(
    node: ast.If,
    purity_results: dict[str, PurityResult],
    file_path: str,
) -> list[ParallelOpportunity]:
    """Detect independent if/elif branches computing via pure functions.

    Pattern:
        if cond:
            r = f(x)
        elif cond2:
            r = g(x)

    where f and g are pure.
    """
    results: list[ParallelOpportunity] = []

    # Collect all branches: the if body + each elif body
    branches: list[tuple[list[ast.stmt], int]] = [(node.body, node.lineno)]
    current = node
    while current.orelse:
        if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
            elif_node = current.orelse[0]
            branches.append((elif_node.body, elif_node.lineno))
            current = elif_node
        else:
            # Plain else — include it too
            branches.append((current.orelse, current.orelse[0].lineno if current.orelse else current.lineno))
            break

    if len(branches) < 2:
        return results

    # Check each branch for a single assignment via a pure call
    pure_callees: list[str] = []
    for body, _line in branches:
        if len(body) != 1:
            continue
        stmt = body[0]
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        callee = _get_call_name(stmt.value)
        if callee is None:
            continue
        purity = _is_pure(callee, purity_results)
        if purity is True:
            pure_callees.append(callee)

    if len(pure_callees) >= 2:
        results.append(
            ParallelOpportunity(
                pattern="PARALLEL_BEAM",
                file=file_path,
                line=node.lineno,
                callee=", ".join(pure_callees),
                confidence=0.6,
                constraints=["callee must be pure", "branches must be independent"],
                detail=(
                    f"If/elif branches call pure functions [{', '.join(pure_callees)}] "
                    f"independently. These could be evaluated in parallel."
                ),
            )
        )

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def detect_parallel_opportunities(
    tree: ast.AST,
    purity_results: dict[str, PurityResult],
    file_path: str = "",
) -> list[ParallelOpportunity]:
    """Walk the AST and detect parallelization opportunities.

    Args:
        tree: The parsed AST of the source file.
        purity_results: Mapping of qualified function names to PurityResult,
            as returned by ``purity.analyze_purity()``.
        file_path: Source file path for diagnostic output.

    Returns:
        A list of detected ParallelOpportunity instances.
    """
    opportunities: list[ParallelOpportunity] = []

    for node in ast.walk(tree):
        # PARALLEL_MAP: for-loops
        if isinstance(node, ast.For):
            opportunities.extend(_check_for_loop(node, purity_results, file_path))

        # PARALLEL_MAP: comprehensions
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            opportunities.extend(_check_comprehension(node, purity_results, file_path))

        # PARALLEL_ASYNC: async functions with sequential awaits
        elif isinstance(node, ast.AsyncFunctionDef):
            opportunities.extend(_check_async_func(node, file_path))

        # PARALLEL_BEAM: if/elif branches with pure calls
        elif isinstance(node, ast.If):
            opportunities.extend(_check_if_branches(node, purity_results, file_path))

    return opportunities
