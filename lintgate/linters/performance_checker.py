"""Performance anti-pattern checker — AST-based detection of structurally wrong choices.

Tier 2 — catches performance anti-patterns that are mathematically wrong regardless
of context: O(n²) when O(n) is available, re.compile() inside functions,
sorted()[0] instead of min(), etc. These are not style preferences — they are
facts about complexity classes and execution models.

The 8 checks (PERF001–PERF008) cover:
- Quadratic membership (list-in-loop → set)
- Re-compile in function (hoist to module level)
- Sorted first/last (use min/max)
- String concat in loop (use join)
- Unnecessary list wrapping in iteration
- Dict keys iteration (redundant .keys())
- Numerical loop without vectorization
- Sequential I/O in loop body

False-positive guardrails:
- PERF001: Only loop-invariant containers (skip if mutated in loop body)
- PERF001: Skip set/dict/frozenset targets and small constant lists
- PERF002: Skip @lru_cache/@cache decorated functions
- PERF004: Skip when loop body is 1-2 statements
- PERF005: Only when list() feeds directly into for-loop iteration
- PERF007: Skip small constant bounds, require arithmetic on loop vars
- PERF007: Skip when numpy/pandas/numba already imported
- PERF008: Only requests.* calls and variable-path open(), not constant paths
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class PerformanceChecker(BaseLinter):
    """Detect performance anti-patterns via pure AST analysis."""

    name = "performance_checker"
    tier = 2
    timeout_ms = 3000
    required_tool = None  # Pure AST, always available

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        disabled = set(ctx.config.get("disabled_checks", []))
        for file_path in ctx.files:
            yield from _check_file(file_path, disabled)


# ─── Main entry ──────────────────────────────────────────────────────────


def _check_file(file_path: str, disabled: set[str]) -> Iterable[LintIssue]:
    """Parse one file and run all performance checks."""
    try:
        with open(file_path) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return

    # Attach parent references for ancestor lookups
    _attach_parents(tree)

    # Dedupe guard: track (file, line, check_id) to avoid duplicates
    seen: set[tuple[str, int, str]] = set()

    checks: list[tuple[str, object]] = [
        ("PERF001", _check_quadratic_membership),
        ("PERF002", _check_recompile_in_function),
        ("PERF003", _check_sorted_first_last),
        ("PERF004", _check_string_concat_in_loop),
        ("PERF005", _check_unnecessary_list_wrap),
        ("PERF006", _check_dict_keys_iteration),
        ("PERF007", _check_numerical_loop),
        ("PERF008", _check_sequential_io_in_loop),
    ]

    for check_id, check_fn in checks:
        if check_id in disabled:
            continue
        for issue in check_fn(tree, file_path):
            key = (file_path, issue.line, issue.kind)
            if key not in seen:
                seen.add(key)
                yield issue


# ─── PERF001: Quadratic membership ──────────────────────────────────────


def _check_quadratic_membership(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag `x in some_list` inside for-loops when the list is loop-invariant.

    Skips:
    - set/dict/frozenset targets (already O(1))
    - Small constant lists (e.g. [1, 2, 3])
    - Containers mutated inside the loop body
    - Non-Name targets (inline expressions)
    """
    for loop_node, body in _find_loop_bodies(tree):
        # Find all `x in <name>` comparisons inside this loop
        for node in ast.walk(loop_node):
            if not isinstance(node, ast.Compare):
                continue
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                if not isinstance(op, ast.In):
                    continue
                # Only flag Name targets (variables), not inline expressions
                if not isinstance(comparator, ast.Name):
                    continue

                container_name = comparator.id

                # Skip if the container is a known set/dict constructor at
                # assignment site (we can't always know, but skip obvious cases)
                if _is_set_or_dict_name(container_name, tree):
                    continue

                # Skip small constant lists
                if _is_small_constant_list(container_name, tree):
                    continue

                # Skip if container is mutated inside the loop body
                if _is_mutated_in_body(container_name, body):
                    continue

                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF001",
                    message=(
                        f"Membership test `in {container_name}` inside loop is "
                        f"O(n²) for lists. Convert `{container_name}` to a set "
                        f"before the loop for O(n) lookup."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="warning",
                    confidence=0.85,
                    evidence={
                        "container": container_name,
                        "check": "PERF001",
                    },
                    suggestions=[
                        f"Add `{container_name}_set = set({container_name})` before the loop.",
                        f"Then use `x in {container_name}_set` instead.",
                    ],
                )


def _is_set_or_dict_name(name: str, tree: ast.AST) -> bool:
    """Check if a name uses fast membership semantics (O(1)/optimized).

    Includes set()/frozenset()/dict() and range(), plus set/dict literals.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = node.value
                    # set(), frozenset(), dict(), range() calls
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id in ("set", "frozenset", "dict", "range")
                    ):
                        return True
                    # Set literal {1, 2, 3}
                    if isinstance(value, ast.Set):
                        return True
                    # Dict literal {k: v}
                    if isinstance(value, ast.Dict):
                        return True
    return False


def _is_small_constant_list(name: str, tree: ast.AST) -> bool:
    """Check if name is assigned a small constant list (≤ 5 elements)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and isinstance(node.value, ast.List)
                    and len(node.value.elts) <= 5
                    and all(isinstance(e, ast.Constant) for e in node.value.elts)
                ):
                    return True
    return False


def _is_mutated_in_body(name: str, body: list[ast.stmt]) -> bool:
    """Check if a name is mutated inside a loop body.

    Detects: .append(), .extend(), .remove(), .insert(), .pop(), .clear(),
             .add(), .discard(), .update(), del name[...], name[...] = ...,
             name = ... (reassignment), name += ...
    """
    mutation_methods = {
        "append", "extend", "remove", "insert", "pop", "clear",
        "add", "discard", "update", "sort", "reverse",
    }

    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        # Method calls: name.append(...), name.extend(...), etc.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and node.func.attr in mutation_methods
        ):
            return True

        # del name[...] or del name
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == name
                ):
                    return True

        # name[...] = ... (subscript assignment)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True  # Full reassignment
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == name
                ):
                    return True

        # name += ... (augmented assignment)
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return True

    return False


# ─── PERF002: re.compile inside function ─────────────────────────────────


def _check_recompile_in_function(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag re.compile() called inside function bodies.

    Skips:
    - Module-level re.compile (correct usage)
    - @lru_cache/@cache decorated functions (compiled once, cached)
    - Non-constant pattern arguments
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Skip @lru_cache / @cache decorated functions
        if _has_cache_decorator(node):
            continue

        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if not _is_re_compile_call(inner):
                continue
            # Only flag when argument is a constant string
            if not inner.args or not isinstance(inner.args[0], ast.Constant):
                continue
            if not isinstance(inner.args[0].value, str):
                continue

            yield LintIssue(
                linter="performance_checker",
                kind="PERF002",
                message=(
                    f"re.compile() with constant pattern at line {inner.lineno} "
                    f"is inside a function. Hoist to module level to compile once."
                ),
                file=file_path,
                line=inner.lineno,
                severity="warning",
                confidence=0.95,
                evidence={"check": "PERF002"},
                suggestions=[
                    "Move the re.compile() call to module level.",
                    "Assign it to a module-level constant (e.g., _PATTERN = re.compile(...)).",
                ],
            )


def _is_re_compile_call(node: ast.Call) -> bool:
    """Check if a Call node is re.compile(...)."""
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "compile"
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    )


def _has_cache_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Check if a function has @lru_cache, @cache, or @functools.lru_cache."""
    cache_names = {"lru_cache", "cache"}
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id in cache_names:
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr in cache_names:
            return True
        # @lru_cache() with call
        if isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Name) and func.id in cache_names:
                return True
            if isinstance(func, ast.Attribute) and func.attr in cache_names:
                return True
    return False


# ─── PERF003: sorted()[0] or sorted()[-1] ───────────────────────────────


def _check_sorted_first_last(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag sorted(x)[0] → min(x) and sorted(x)[-1] → max(x)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue

        # Check if value is sorted(...)
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "sorted"):
            continue

        # Check if slice is [0] or [-1]
        index_value = _get_constant_index(node.slice)
        if index_value is None:
            continue

        if index_value == 0:
            replacement = "min"
        elif index_value == -1:
            replacement = "max"
        else:
            continue

        yield LintIssue(
            linter="performance_checker",
            kind="PERF003",
            message=(
                f"Use `{replacement}(...)` instead of `sorted(...)[{index_value}]`. "
                f"`{replacement}()` is O(n) vs O(n log n) for sorted()."
            ),
            file=file_path,
            line=node.lineno,
            severity="warning",
            confidence=1.0,
            evidence={"replacement": replacement, "check": "PERF003"},
            suggestions=[
                f"Replace `sorted(...)[{index_value}]` with `{replacement}(...)`.",
            ],
        )


# ─── PERF004: String concat in loop ─────────────────────────────────────


def _check_string_concat_in_loop(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag string += inside for/while loops.

    Skips loops with 1-2 statements (small string building is fine).
    """
    for loop_node, body in _find_loop_bodies(tree):
        # Skip small loops (1-2 statements)
        if len(body) <= 2:
            continue

        for node in ast.walk(loop_node):
            if not isinstance(node, ast.AugAssign):
                continue
            if not isinstance(node.op, ast.Add):
                continue
            # Check if the value side is a string or likely string operation
            # We flag all += in loops with string-like right sides
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF004",
                    message=(
                        "String concatenation with `+=` inside a loop creates a new "
                        "string object each iteration. Collect parts in a list and "
                        "use `''.join(parts)` after the loop."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="warning",
                    confidence=0.9,
                    evidence={"check": "PERF004"},
                    suggestions=[
                        "Collect string parts in a list: `parts.append(piece)`.",
                        "After the loop: `result = ''.join(parts)`.",
                    ],
                )
            # Also flag f-strings and string method calls on right side
            elif isinstance(node.value, ast.JoinedStr):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF004",
                    message=(
                        "String concatenation with `+=` (f-string) inside a loop. "
                        "Collect parts in a list and use `''.join(parts)` after the loop."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="warning",
                    confidence=0.9,
                    evidence={"check": "PERF004"},
                    suggestions=[
                        "Collect string parts in a list: `parts.append(f'...')`.",
                        "After the loop: `result = ''.join(parts)`.",
                    ],
                )


# ─── PERF005: Unnecessary list() wrapping in iteration ──────────────────


def _check_unnecessary_list_wrap(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag list(range(...)) or list(genexpr) when used directly in for-loop iteration.

    Only flags when the list() result is the iterable of a for-loop.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        iter_node = node.iter
        if not isinstance(iter_node, ast.Call):
            continue
        if not (isinstance(iter_node.func, ast.Name) and iter_node.func.id == "list"):
            continue

        # Check what's inside list()
        if not iter_node.args:
            continue

        inner = iter_node.args[0]

        # list(range(...))
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
            if inner.func.id == "range":
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF005",
                    message=(
                        "Unnecessary `list(range(...))` in for-loop. "
                        "`range()` is already iterable — use `for x in range(...)` directly."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=0.8,
                    evidence={"check": "PERF005", "inner": "range"},
                    suggestions=[
                        "Remove the `list()` wrapper: `for x in range(...)` instead.",
                    ],
                )
        # list(generator expression)
        elif isinstance(inner, ast.GeneratorExp):
            yield LintIssue(
                linter="performance_checker",
                kind="PERF005",
                message=(
                    "Unnecessary `list(genexpr)` in for-loop. "
                    "Iterate the generator directly to avoid materializing the list."
                ),
                file=file_path,
                line=node.lineno,
                severity="informational",
                confidence=0.8,
                evidence={"check": "PERF005", "inner": "genexpr"},
                suggestions=[
                    "Replace `for x in list(gen)` with `for x in gen`.",
                ],
            )


# ─── PERF006: dict .keys() iteration ────────────────────────────────────


def _check_dict_keys_iteration(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag `for k in d.keys()` → `for k in d`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        iter_node = node.iter
        # d.keys() call
        if (
            isinstance(iter_node, ast.Call)
            and isinstance(iter_node.func, ast.Attribute)
            and iter_node.func.attr == "keys"
            and not iter_node.args
            and not iter_node.keywords
        ):
            dict_name = _get_name(iter_node.func.value)
            if dict_name:
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF006",
                    message=(
                        f"Redundant `.keys()` in `for k in {dict_name}.keys()`. "
                        f"Use `for k in {dict_name}` — iterating a dict yields keys by default."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=1.0,
                    evidence={"check": "PERF006", "dict_name": dict_name},
                    suggestions=[
                        f"Remove `.keys()`: `for k in {dict_name}`.",
                    ],
                )


# ─── PERF007: Numerical loop without vectorization ──────────────────────


def _check_numerical_loop(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag arithmetic-heavy loops over large ranges without numpy/numba.

    Strict gating:
    - Skip if loop bound is a small constant (< 100)
    - Only flag when body contains arithmetic ops (BinOp) on the loop variable
    - Skip if numpy, pandas, or numba already imported
    """
    # Skip entire file if vectorization libraries are imported
    if _has_import(tree, "numpy") or _has_import(tree, "pandas") or _has_import(tree, "numba"):
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        # Check for range() iteration with non-trivial bound
        iter_node = node.iter
        if not isinstance(iter_node, ast.Call):
            continue
        if not (isinstance(iter_node.func, ast.Name) and iter_node.func.id == "range"):
            continue

        # Check bound — skip small constants
        if _is_small_range_bound(iter_node):
            continue

        # Get loop variable name
        if not isinstance(node.target, ast.Name):
            continue
        loop_var = node.target.id

        # Check if loop body has arithmetic on the loop variable
        if not _has_arithmetic_on_var(node.body, loop_var):
            continue

        yield LintIssue(
            linter="performance_checker",
            kind="PERF007",
            message=(
                "Arithmetic-heavy loop over a large range without vectorization. "
                "Consider numpy/numba for numerical computation — vectorized operations "
                "can be orders of magnitude faster than pure Python loops."
            ),
            file=file_path,
            line=node.lineno,
            severity="informational",
            confidence=0.6,
            evidence={"loop_var": loop_var, "check": "PERF007"},
            suggestions=[
                "If this is numerical work, consider `import numpy as np` with vectorized ops.",
                "For hot loops, `@numba.jit` can provide 10-100x speedup.",
            ],
        )


def _is_small_range_bound(call: ast.Call) -> bool:
    """Check if range() has a small constant bound (< 100)."""
    # range(n) — single arg
    if len(call.args) == 1:
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
            return arg.value < 100
    # range(start, stop) or range(start, stop, step)
    elif len(call.args) >= 2:
        stop = call.args[1]
        if isinstance(stop, ast.Constant) and isinstance(stop.value, int):
            start = call.args[0]
            if isinstance(start, ast.Constant) and isinstance(start.value, int):
                return (stop.value - start.value) < 100
    return False


def _has_arithmetic_on_var(body: list[ast.stmt], var_name: str) -> bool:
    """Check if a loop body contains arithmetic BinOp that references the loop variable."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.FloorDiv, ast.Mod))
            and (_references_var(node.left, var_name) or _references_var(node.right, var_name))
        ):
            return True
    return False


def _references_var(node: ast.AST, var_name: str) -> bool:
    """Check if an expression references a specific variable name."""
    if isinstance(node, ast.Name) and node.id == var_name:
        return True
    # Also check subscript: arr[i]
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Name)
        and node.slice.id == var_name
    )


# ─── PERF008: Sequential I/O in loop ────────────────────────────────────


def _check_sequential_io_in_loop(
    tree: ast.AST, file_path: str
) -> Iterable[LintIssue]:
    """Flag requests.* calls and repeated variable-path open() inside loops.

    Narrow scope:
    - Only requests.get/post/put/delete/head/patch
    - open() only with variable paths (not constant paths)
    - Only inside for/while body
    """
    requests_methods = {"get", "post", "put", "delete", "head", "patch", "request"}

    for loop_node, _body in _find_loop_bodies(tree):
        for node in ast.walk(loop_node):
            if not isinstance(node, ast.Call):
                continue

            # requests.get(...), requests.post(...), etc.
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and node.func.attr in requests_methods
            ):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF008",
                    message=(
                        f"Sequential `requests.{node.func.attr}()` inside a loop. "
                        f"Consider batching requests, using async I/O (aiohttp), "
                        f"or concurrent.futures for parallel execution."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=0.7,
                    evidence={"method": node.func.attr, "check": "PERF008"},
                    suggestions=[
                        "Use `concurrent.futures.ThreadPoolExecutor` for parallel I/O.",
                        "Consider `asyncio` + `aiohttp` for async HTTP requests.",
                        "If the API supports it, use batch endpoints.",
                    ],
                )

            # open() with variable path inside loop
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "open"
                and node.args
                and not isinstance(node.args[0], ast.Constant)
            ):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF008",
                    message=(
                        "Repeated `open()` with variable path inside a loop. "
                        "Consider reading all paths at once or using batch I/O."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=0.7,
                    evidence={"io_type": "open", "check": "PERF008"},
                    suggestions=[
                        "Collect paths and read in batch if possible.",
                        "Consider `concurrent.futures.ThreadPoolExecutor` for parallel reads.",
                    ],
                )


# ─── Shared AST helpers ─────────────────────────────────────────────────


def _get_constant_index(node: ast.AST) -> int | None:
    """Extract a constant integer index from an AST slice.

    Handles both `ast.Constant(value=0)` and `ast.UnaryOp(op=USub(), operand=Constant(value=1))`
    (the latter is how Python represents negative indices like [-1]).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
    ):
        return -node.operand.value
    return None


def _attach_parents(tree: ast.AST) -> None:
    """Attach .parent references to all nodes in the AST."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def _find_loop_bodies(tree: ast.AST) -> list[tuple[ast.AST, list[ast.stmt]]]:
    """Find all for/while loops and return (loop_node, body) pairs."""
    result: list[tuple[ast.AST, list[ast.stmt]]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            result.append((node, node.body))
    return result


def _has_import(tree: ast.AST, module_name: str) -> bool:
    """Check if a module is imported anywhere in the file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_name or alias.name.startswith(module_name + "."):
                    return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == module_name or node.module.startswith(module_name + "."))
        ):
            return True
    return False


def _get_name(node: ast.AST) -> str | None:
    """Extract a simple name from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _get_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None
