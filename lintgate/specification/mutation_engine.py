"""AST mutation engine — in-process mutant generation and evaluation.

Implements §6.4 dispatch table: category→AST-transform mapping.
Generates mutants by AST rewriting (no subprocess spawning), evaluates
them by running targeted tests in the same process against a sandboxed
namespace. Respects per-function time budgets.
"""

from __future__ import annotations

import ast
import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class MutationCategory(str, Enum):
    """Semantic mutation category (§6.4 dispatch table)."""

    VALUE = "VALUE"
    SWAP = "SWAP"
    STATE = "STATE"
    BOUNDARY = "BOUNDARY"
    TYPE = "TYPE"


@dataclass
class Mutant:
    """A single AST-level mutation."""

    category: MutationCategory
    original_node: ast.AST
    mutated_node: ast.AST
    description: str
    location: int = 0


@dataclass
class MutantResult:
    """Result of evaluating a single mutant against tests."""

    mutant: Mutant
    killed: bool = False
    killed_by: str | None = None  # "assertion" | "crash" | "timeout"
    test_name: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class CategoryResult:
    """Aggregated results for one mutation category."""

    category: MutationCategory
    total: int = 0
    killed: int = 0
    survived: int = 0
    killed_by_assertion: int = 0
    killed_by_crash: int = 0
    timed_out: int = 0

    @property
    def survival_rate(self) -> float:
        return self.survived / self.total if self.total > 0 else 0.0


@dataclass
class SamplingResult:
    """Result of inline mutation sampling for a function."""

    function_key: str = ""
    categories_tested: int = 0
    total_mutants: int = 0
    total_killed: int = 0
    total_survived: int = 0
    survival_rate: float = 0.0
    coverage_depth: str = "sampled"
    per_category: list[CategoryResult] = field(default_factory=list)
    budget_exhausted: bool = False
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "categories_tested": self.categories_tested,
            "total_mutants": self.total_mutants,
            "total_killed": self.total_killed,
            "total_survived": self.total_survived,
            "survival_rate": round(self.survival_rate, 3),
            "coverage_depth": self.coverage_depth,
            "budget_exhausted": self.budget_exhausted,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "per_category": [
                {
                    "category": cr.category.value,
                    "total": cr.total,
                    "killed": cr.killed,
                    "survived": cr.survived,
                    "survival_rate": round(cr.survival_rate, 3),
                }
                for cr in self.per_category
            ],
        }


@dataclass
class ProfilingResult:
    """Result of exhaustive mutation profiling for a function."""

    function_key: str = ""
    categories_tested: int = 0
    total_mutants: int = 0
    total_killed: int = 0
    total_survived: int = 0
    survival_rate: float = 0.0
    coverage_depth: str = "profiled"
    is_gateable: bool = True
    per_category: list[CategoryResult] = field(default_factory=list)
    kill_matrix: dict[str, list[str]] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "categories_tested": self.categories_tested,
            "total_mutants": self.total_mutants,
            "total_killed": self.total_killed,
            "total_survived": self.total_survived,
            "survival_rate": round(self.survival_rate, 3),
            "coverage_depth": self.coverage_depth,
            "is_gateable": self.is_gateable,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "per_category": [
                {
                    "category": cr.category.value,
                    "total": cr.total,
                    "killed": cr.killed,
                    "survived": cr.survived,
                    "killed_by_assertion": cr.killed_by_assertion,
                    "killed_by_crash": cr.killed_by_crash,
                    "survival_rate": round(cr.survival_rate, 3),
                }
                for cr in self.per_category
            ],
        }


# ── §6.4 Dispatch Table: Category → AST Transform ────────────────


class _BaseMutator(ast.NodeTransformer):
    """Base class for all category mutators — tracks ``applied`` state."""

    def __init__(self, target_index: int = 0):
        self.current = 0
        self.target = target_index
        self.applied = False


class _ValueMutator(_BaseMutator):
    """Replace constants with boundary values."""

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if self.applied:
            return node
        if self.current == self.target:
            self.applied = True
            return self._mutate_constant(node)
        self.current += 1
        return node

    @staticmethod
    def _mutate_constant(node: ast.Constant) -> ast.Constant:
        v = node.value
        if isinstance(v, bool):
            return ast.Constant(value=not v)
        if isinstance(v, int):
            return ast.Constant(value=0 if v != 0 else 1)
        if isinstance(v, float):
            return ast.Constant(value=0.0 if v != 0.0 else 1.0)
        if isinstance(v, str):
            return ast.Constant(value="" if v else "mutated")
        return node


class _BoundaryMutator(_BaseMutator):
    """Off-by-one on comparisons: < → <=, >= → >, etc."""

    _SWAP = {
        ast.Lt: ast.LtE,
        ast.LtE: ast.Lt,
        ast.Gt: ast.GtE,
        ast.GtE: ast.Gt,
    }

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if self.applied:
            return self.generic_visit(node)
        new_ops = []
        for op in node.ops:
            if not self.applied and self.current == self.target:
                swapped = self._SWAP.get(type(op))
                if swapped:
                    new_ops.append(swapped())
                    self.applied = True
                else:
                    new_ops.append(op)
            else:
                new_ops.append(op)
            self.current += 1
        node.ops = new_ops
        return self.generic_visit(node)


class _SwapMutator(_BaseMutator):
    """Transpose two parameters in a function call."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if self.applied or len(node.args) < 2:
            return self.generic_visit(node)
        if self.current == self.target:
            self.applied = True
            node.args = list(node.args)
            node.args[0], node.args[1] = node.args[1], node.args[0]
        self.current += 1
        return self.generic_visit(node)


class _StateMutator(_BaseMutator):
    """Remove self.x = ... assignments or replace return with return None."""

    def __init__(self, target_index: int = 0, mode: str = "remove_assign"):
        super().__init__(target_index)
        self.mode = mode

    def visit_Assign(self, node: ast.Assign) -> ast.AST | None:
        if self.applied or self.mode != "remove_assign":
            return node
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                if self.current == self.target:
                    self.applied = True
                    return ast.Pass()
                self.current += 1
        return node

    def visit_Return(self, node: ast.Return) -> ast.AST:
        if self.applied or self.mode != "return_none":
            return node
        if node.value is not None:
            if self.current == self.target:
                self.applied = True
                return ast.Return(value=ast.Constant(value=None))
            self.current += 1
        return node


class _TypeMutator(_BaseMutator):
    """Replace isinstance(x, T) with True."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if self.applied:
            return self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "isinstance":
            if self.current == self.target:
                self.applied = True
                return ast.Constant(value=True)
            self.current += 1
        return self.generic_visit(node)


# ── Mutant Generation ─────────────────────────────────────────────


def _count_targets(func_node: ast.FunctionDef, category: MutationCategory) -> int:
    """Count how many mutation targets exist for a category in a function."""
    counter = _TARGET_COUNTERS.get(category)
    if counter is None:
        return 0
    return sum(counter(node) for node in ast.walk(func_node))


def _count_value_target(node: ast.AST) -> int:
    return 1 if isinstance(node, ast.Constant) else 0


def _count_boundary_target(node: ast.AST) -> int:
    if not isinstance(node, ast.Compare):
        return 0
    return sum(1 for op in node.ops if type(op) in _BoundaryMutator._SWAP)


def _count_swap_target(node: ast.AST) -> int:
    return 1 if isinstance(node, ast.Call) and len(node.args) >= 2 else 0


def _is_self_assign(target: ast.AST) -> bool:
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


def _count_state_target(node: ast.AST) -> int:
    if isinstance(node, ast.Assign):
        return sum(1 for t in node.targets if _is_self_assign(t))
    if isinstance(node, ast.Return) and node.value is not None:
        return 1
    return 0


def _count_type_target(node: ast.AST) -> int:
    return (
        1
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        else 0
    )


_TARGET_COUNTERS: dict[MutationCategory, Callable[[ast.AST], int]] = {
    MutationCategory.VALUE: _count_value_target,
    MutationCategory.BOUNDARY: _count_boundary_target,
    MutationCategory.SWAP: _count_swap_target,
    MutationCategory.STATE: _count_state_target,
    MutationCategory.TYPE: _count_type_target,
}


def generate_mutants(
    func_node: ast.FunctionDef,
    categories: set[MutationCategory],
    max_per_category: int = 0,
) -> list[Mutant]:
    """Generate mutants for a function across specified categories.

    Args:
        func_node: The function AST node to mutate.
        categories: Set of mutation categories to generate.
        max_per_category: Max mutants per category (0 = unlimited).
    """
    mutants: list[Mutant] = []

    for cat in sorted(categories, key=lambda c: c.value):
        target_count = _count_targets(func_node, cat)
        limit = min(target_count, max_per_category) if max_per_category > 0 else target_count

        for i in range(limit):
            mutated_tree = copy.deepcopy(func_node)
            transformer, desc = _make_transformer(cat, i)
            mutated_node = transformer.visit(mutated_tree)
            ast.fix_missing_locations(mutated_node)

            if transformer.applied:
                mutants.append(
                    Mutant(
                        category=cat,
                        original_node=func_node,
                        mutated_node=mutated_node,
                        description=f"{cat.value}_{i}: {desc}",
                        location=getattr(func_node, "lineno", 0),
                    )
                )

    return mutants


def _make_transformer(category: MutationCategory, index: int) -> tuple[_BaseMutator, str]:
    """Create the appropriate transformer for a category + target index."""
    if category == MutationCategory.VALUE:
        return _ValueMutator(index), "replace constant with boundary value"
    if category == MutationCategory.BOUNDARY:
        return _BoundaryMutator(index), "off-by-one comparison"
    if category == MutationCategory.SWAP:
        return _SwapMutator(index), "transpose call arguments"
    if category == MutationCategory.STATE:
        return _StateMutator(index, "remove_assign"), "remove state assignment"
    if category == MutationCategory.TYPE:
        return _TypeMutator(index), "replace isinstance with True"
    msg = f"Unknown category: {category}"
    raise ValueError(msg)


# ── Mutant Evaluation ─────────────────────────────────────────────


def evaluate_mutant(
    mutant: Mutant,
    test_functions: list[Callable[..., None]],
    original_func: Callable[..., Any],
    timeout_ms: float = 5000,
) -> MutantResult:
    """Evaluate a mutant against test functions.

    Compiles the mutated function, then monkey-patches it into each test's
    module namespace before invoking the test with zero args (standard pytest
    contract). The original function is restored after each test.
    """
    start = time.monotonic()
    import inspect

    # Compile mutated function
    try:
        module_ast = ast.Module(body=[mutant.mutated_node], type_ignores=[])
        ast.fix_missing_locations(module_ast)
        code = compile(module_ast, "<mutant>", "exec")
        namespace: dict[str, Any] = {}
        exec(code, namespace)  # noqa: S102  # nosec B102 — intentional: compiling AST mutants
        func_name = getattr(mutant.mutated_node, "name", None)
        mutated_func = namespace.get(func_name) if func_name else None
        if mutated_func is None:
            return MutantResult(
                mutant=mutant,
                killed=True,
                killed_by="crash",
                elapsed_ms=_elapsed(start),
            )
    except Exception:
        return MutantResult(
            mutant=mutant,
            killed=True,
            killed_by="crash",
            elapsed_ms=_elapsed(start),
        )

    # Run tests against mutated function
    for test_fn in test_functions:
        if _elapsed(start) > timeout_ms:
            return MutantResult(
                mutant=mutant, killed=True, killed_by="timeout", elapsed_ms=_elapsed(start)
            )
        # Strategy: if the test's module has the function under test, monkey-patch
        # the mutant in and call the test zero-arg (standard pytest contract).
        # Otherwise, fall back to passing the mutant as an argument (for inline
        # test callables in unit tests).
        test_module = inspect.getmodule(test_fn)
        patched = False
        saved = None
        if test_module is not None and func_name and hasattr(test_module, func_name):
            saved = getattr(test_module, func_name)
            setattr(test_module, func_name, mutated_func)
            patched = True
        try:
            if patched:
                test_fn()
            else:
                # Fallback: pass mutant as arg (inline test callables)
                try:
                    test_fn(mutated_func)
                except TypeError:
                    # Zero-arg test without module patching — call without args
                    test_fn()
        except AssertionError:
            return MutantResult(
                mutant=mutant,
                killed=True,
                killed_by="assertion",
                test_name=getattr(test_fn, "__name__", "unknown"),
                elapsed_ms=_elapsed(start),
            )
        except Exception:
            return MutantResult(
                mutant=mutant,
                killed=True,
                killed_by="crash",
                test_name=getattr(test_fn, "__name__", "unknown"),
                elapsed_ms=_elapsed(start),
            )
        finally:
            if patched and test_module is not None and saved is not None:
                setattr(test_module, func_name, saved)

    return MutantResult(mutant=mutant, killed=False, elapsed_ms=_elapsed(start))


def _elapsed(start: float) -> float:
    return (time.monotonic() - start) * 1000


# ── Sampling & Profiling ──────────────────────────────────────────


def run_function_sampling(
    func_node: ast.FunctionDef,
    func_key: str,
    categories: set[MutationCategory],
    test_functions: list[Callable[..., None]],
    original_func: Callable[..., Any],
    budget_ms: float = 500,
    max_per_category: int = 3,
) -> SamplingResult:
    """Inline sampling mode — generate ≤max_per_category mutants per category.

    Evaluates within time budget. This is the "active hypothesis testing"
    from §6.2: each sampled mutant tests whether the test suite distinguishes
    a specific behavioral dimension.
    """
    start = time.monotonic()
    mutants = generate_mutants(func_node, categories, max_per_category=max_per_category)

    results_by_cat: dict[MutationCategory, CategoryResult] = {}
    budget_exhausted = False
    all_results: list[MutantResult] = []

    for mutant in mutants:
        if _elapsed(start) > budget_ms:
            budget_exhausted = True
            break

        result = evaluate_mutant(mutant, test_functions, original_func, timeout_ms=budget_ms)
        all_results.append(result)

        cr = results_by_cat.setdefault(mutant.category, CategoryResult(category=mutant.category))
        cr.total += 1
        if result.killed:
            cr.killed += 1
            if result.killed_by == "assertion":
                cr.killed_by_assertion += 1
            elif result.killed_by == "crash":
                cr.killed_by_crash += 1
        else:
            cr.survived += 1

    per_cat = list(results_by_cat.values())
    total = sum(cr.total for cr in per_cat)
    killed = sum(cr.killed for cr in per_cat)
    survived = total - killed

    return SamplingResult(
        function_key=func_key,
        categories_tested=len(per_cat),
        total_mutants=total,
        total_killed=killed,
        total_survived=survived,
        survival_rate=survived / total if total > 0 else 0.0,
        per_category=per_cat,
        budget_exhausted=budget_exhausted,
        elapsed_ms=_elapsed(start),
    )


def run_function_profiling(
    func_node: ast.FunctionDef,
    func_key: str,
    categories: set[MutationCategory],
    test_functions: list[Callable[..., None]],
    original_func: Callable[..., Any],
    per_mutant_timeout_ms: float = 5000,
) -> ProfilingResult:
    """Exhaustive profiling mode — generate all mutants, no time budget.

    Returns full survival profile with kill matrix for convergence analysis.
    Result has coverage_depth="profiled" and is_gateable=True.
    """
    start = time.monotonic()
    mutants = generate_mutants(func_node, categories)

    results_by_cat: dict[MutationCategory, CategoryResult] = {}
    kill_matrix: dict[str, list[str]] = {}

    for mutant in mutants:
        result = evaluate_mutant(
            mutant, test_functions, original_func, timeout_ms=per_mutant_timeout_ms
        )

        cr = results_by_cat.setdefault(mutant.category, CategoryResult(category=mutant.category))
        cr.total += 1
        if result.killed:
            cr.killed += 1
            if result.killed_by == "assertion":
                cr.killed_by_assertion += 1
            elif result.killed_by == "crash":
                cr.killed_by_crash += 1
            elif result.killed_by == "timeout":
                cr.timed_out += 1
            if result.test_name:
                kill_matrix.setdefault(mutant.description, []).append(result.test_name)
        else:
            cr.survived += 1

    per_cat = list(results_by_cat.values())
    total = sum(cr.total for cr in per_cat)
    killed = sum(cr.killed for cr in per_cat)
    survived = total - killed

    return ProfilingResult(
        function_key=func_key,
        categories_tested=len(per_cat),
        total_mutants=total,
        total_killed=killed,
        total_survived=survived,
        survival_rate=survived / total if total > 0 else 0.0,
        per_category=per_cat,
        kill_matrix=kill_matrix,
        elapsed_ms=_elapsed(start),
    )
