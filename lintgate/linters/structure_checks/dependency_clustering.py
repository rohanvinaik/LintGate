"""B1: Variable dependency clustering — propose function extractions for high-CC functions.

V1 scope: contiguous statement groups only. Proposes extraction when:
- Contiguous statement runs (no interleaving)
- Single-exit segments (no break/continue/return crossing the boundary)
- No writes to outer-scope mutable state
- Small parameter surface (≤ 4 shared variables)
- Residual CC provably lower than original

Does NOT attempt:
- Graph partitioning (Kernighan-Lin, min-cut)
- Control-flow boundary inference across loops
- Side-effect analysis beyond local scope
- Proposals for non-contiguous interleaved blocks
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ...types import Prescription
from ..cognitive_complexity import compute_cognitive_complexity

# ── Statement-level analysis ────────────────────────────────────────────


@dataclass
class _StmtInfo:
    """Analysis of a single top-level statement in a function body."""

    index: int
    stmt: ast.stmt
    reads: frozenset[str]
    writes: frozenset[str]
    has_exit: bool  # return/break/continue inside


def _analyze_statement(index: int, stmt: ast.stmt) -> _StmtInfo:
    """Analyze reads, writes, and exits for a single statement."""
    return _StmtInfo(
        index=index,
        stmt=stmt,
        reads=frozenset(_collect_reads(stmt)),
        writes=frozenset(_collect_writes(stmt)),
        has_exit=_has_exit_statement(stmt),
    )


def _collect_reads(node: ast.AST) -> set[str]:
    """Collect all variable names that are read (loaded) in an AST subtree."""
    reads: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            reads.add(child.id)
    return reads


def _collect_writes(node: ast.AST) -> set[str]:
    """Collect all variable names that are written (stored) in an AST subtree."""
    writes: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            writes.add(child.id)
    return writes


def _has_exit_statement(node: ast.AST) -> bool:
    """Check if return/break/continue exists at THIS scope level.

    Does NOT descend into nested function/class scopes — a return inside
    a nested function is an exit from the nested function, not the parent.
    A FunctionDef/ClassDef node itself is never an exit statement.
    """
    # Defining a function/class doesn't produce an exit at the enclosing scope
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Return, ast.Break, ast.Continue)):
            return True
        # Don't descend into nested function/class scopes
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if _has_exit_statement(child):
            return True
    return False


# ── Block CC computation ────────────────────────────────────────────────


def _compute_block_cc(stmts: list[ast.stmt]) -> int:
    """Compute cognitive complexity of a statement block.

    Wraps statements in a dummy function to reuse the existing CC calculator.
    """
    dummy = ast.FunctionDef(
        name="_dummy",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=stmts,
        decorator_list=[],
        returns=None,
        lineno=0,
        col_offset=0,
    )
    return compute_cognitive_complexity(dummy)


# ── Extraction candidate finder ─────────────────────────────────────────


# Cap on block size to avoid quadratic blowup on very long functions
_MAX_BLOCK_STMTS = 20
# Minimum CC for a block to be worth extracting
_MIN_BLOCK_CC = 3
# Maximum extraction suggestions per function (base; scales with CC)
_MAX_CANDIDATES = 3


def _max_candidates(cc: int) -> int:
    """Scale max extraction candidates with cognitive complexity."""
    if cc > 50:
        return 10
    if cc > 30:
        return 6
    return _MAX_CANDIDATES


def find_extraction_candidates(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: str,
    min_statements: int = 3,
    max_params: int = 4,
    max_outputs: int = 2,
) -> list[Prescription]:
    """Find contiguous statement blocks that can be extracted into helper functions.

    Handles both contiguous-block extraction and nested handler extraction
    (the "bag of handlers" pattern common in MCP/Flask/Click registration functions).

    Args:
        func_node: The function AST node to analyze.
        filepath: Source file path (for Prescription.target).
        min_statements: Minimum statements to consider for extraction.
        max_params: Maximum input variables (becomes function parameters).
        max_outputs: Maximum output variables (becomes return values).

    Returns:
        List of Prescription objects for extractable blocks, sorted by CC reduction.
    """
    body = func_node.body

    # Try nested handler extraction first — if this is a "bag of handlers" function,
    # the handler-level prescriptions are more useful than block-level ones.
    handler_candidates = _find_nested_handler_candidates(func_node, filepath)

    # Contiguous block extraction (original algorithm)
    block_candidates: list[Prescription] = []
    if len(body) > min_statements:
        infos = [_analyze_statement(i, stmt) for i, stmt in enumerate(body)]
        param_names = _get_param_names(func_node)
        n = len(infos)

        for start in range(n):
            end_limit = min(n + 1, start + _MAX_BLOCK_STMTS)
            for end in range(start + min_statements, end_limit):
                candidate = _evaluate_block(
                    infos,
                    start,
                    end,
                    param_names,
                    filepath,
                    func_node,
                    max_params,
                    max_outputs,
                )
                if candidate is not None:
                    block_candidates.append(candidate)

    # Merge: handler candidates take priority, then fill with block candidates
    all_candidates = handler_candidates + block_candidates
    all_candidates.sort(
        key=lambda p: p.expected_delta.get("cc_reduction", 0), reverse=True
    )

    func_cc = compute_cognitive_complexity(func_node)
    return _remove_overlapping(all_candidates, _max_candidates(func_cc))


def _evaluate_block(
    infos: list[_StmtInfo],
    start: int,
    end: int,
    param_names: set[str],
    filepath: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    max_params: int,
    max_outputs: int,
) -> Prescription | None:
    """Evaluate whether statements [start:end] can be extracted."""
    block = infos[start:end]

    # Single-exit: no break/continue/return in the block
    if any(s.has_exit for s in block):
        return None

    # Aggregate reads and writes for the block
    block_reads: set[str] = set()
    block_writes: set[str] = set()
    for s in block:
        block_reads |= s.reads
        block_writes |= s.writes

    # Variables defined before the block (available as inputs)
    pre_defined: set[str] = set(param_names)
    for s in infos[:start]:
        pre_defined |= s.writes

    # Inputs: variables read in block that come from outside (not locally defined first)
    inputs = (block_reads & pre_defined) - block_writes

    # Outputs: variables written in block that are read after the block
    post_reads: set[str] = set()
    for s in infos[end:]:
        post_reads |= s.reads
    outputs = block_writes & post_reads

    # Check parameter surface constraints
    if len(inputs) > max_params:
        return None
    if len(outputs) > max_outputs:
        return None

    # Compute block CC — skip if too low to be worth extracting
    block_stmts = [s.stmt for s in block]
    block_cc = _compute_block_cc(block_stmts)
    if block_cc < _MIN_BLOCK_CC:
        return None

    line_start = block[0].stmt.lineno
    line_end = block[-1].stmt.end_lineno or block[-1].stmt.lineno
    proposed_name = _suggest_name(block, func_node.name)
    confidence = _compute_confidence(block, inputs, outputs, block_cc)

    return Prescription(
        kind="extract_function",
        target=f"{filepath}::{func_node.name}",
        action=f"Extract lines {line_start}-{line_end} into `{proposed_name}()`",
        source="static",
        confidence=confidence,
        lines=(line_start, line_end),
        proposed_name=proposed_name,
        inputs=sorted(inputs),
        outputs=sorted(outputs),
        basis=["variable_clustering", "contiguous_block", "single_exit"],
        expected_delta={"cc_reduction": block_cc},
    )


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_param_names(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Extract parameter names from a function definition."""
    names: set[str] = set()
    args = func_node.args
    for arg in args.posonlyargs + args.args + args.kwonlyargs:
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _suggest_name(block: list[_StmtInfo], parent_name: str) -> str:
    """Suggest a name for the extracted function based on block content."""
    all_writes: set[str] = set()
    for s in block:
        all_writes |= s.writes

    # Pick the most prominent non-underscore variable written
    candidates = sorted(w for w in all_writes if not w.startswith("_"))
    if candidates:
        return f"_compute_{candidates[0]}"
    return f"_{parent_name}_helper"


def _compute_confidence(
    block: list[_StmtInfo],
    inputs: set[str],
    outputs: set[str],
    block_cc: int,
) -> float:
    """Compute confidence for an extraction candidate."""
    conf = 0.50  # Base for contiguous single-exit block

    if len(inputs) <= 2:
        conf += 0.10
    if len(outputs) == 0:
        conf += 0.10  # Void helper — cleanest extraction
    if len(block) >= 5:
        conf += 0.05  # More value in extracting larger blocks
    if block_cc >= 8:
        conf += 0.10  # High-CC block — significant improvement

    return min(conf, 0.85)


def _remove_overlapping(
    candidates: list[Prescription], max_count: int = _MAX_CANDIDATES
) -> list[Prescription]:
    """Remove overlapping extraction candidates, keeping highest CC reduction.

    Batch prescriptions (kind="decompose_register") are kept separately and
    do not participate in overlap filtering — they summarize the individual
    handler candidates and are always included.
    """
    # Separate batch prescriptions from individual candidates
    batch = [c for c in candidates if c.kind == "decompose_register"]
    individual = [c for c in candidates if c.kind != "decompose_register"]

    kept: list[Prescription] = []
    used_lines: set[int] = set()

    for c in individual:
        if c.lines is None:
            continue
        start, end = c.lines
        block_lines = set(range(start, end + 1))
        if block_lines & used_lines:
            continue
        kept.append(c)
        used_lines |= block_lines

    return kept[:max_count] + batch


# ── Nested handler extraction (Fixes 2, 3, 5, 6) ─────────────────────

# Decorator patterns that signal intentional independence
_HANDLER_DECORATORS = frozenset(
    {
        "mcp.tool",
        "app.route",
        "app.get",
        "app.post",
        "app.put",
        "app.delete",
        "router.get",
        "router.post",
        "router.put",
        "router.delete",
        "click.command",
        "click.group",
        "pytest.fixture",
    }
)


def _get_decorator_name(decorator: ast.expr) -> str | None:
    """Extract a readable decorator name from the AST node."""
    if isinstance(decorator, ast.Call):
        return _get_decorator_name(decorator.func)
    if isinstance(decorator, ast.Attribute):
        value = decorator.value
        if isinstance(value, ast.Name):
            return f"{value.id}.{decorator.attr}"
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            return f"{value.value.id}.{value.attr}.{decorator.attr}"
    if isinstance(decorator, ast.Name):
        return decorator.id
    return None


def _is_handler_decorator(decorator: ast.expr) -> bool:
    """Check if a decorator matches known handler patterns."""
    name = _get_decorator_name(decorator)
    if name is None:
        return False
    return name in _HANDLER_DECORATORS


def _analyze_closure(
    nested_func: ast.FunctionDef | ast.AsyncFunctionDef,
    outer_scope_vars: set[str],
) -> tuple[set[str], set[str]]:
    """Analyze closure captures for a nested function.

    Returns:
        (captured_reads, captured_writes):
        - captured_reads: outer-scope variables read → become explicit parameters
        - captured_writes: outer-scope variables written → signal lower extraction confidence
    """
    # Collect all reads and writes in the nested function body (not in deeper nested funcs)
    func_reads: set[str] = set()
    func_writes: set[str] = set()
    func_params = {arg.arg for arg in nested_func.args.args}

    for child in ast.iter_child_nodes(nested_func):
        # Skip the function's own arguments node
        if isinstance(child, ast.arguments):
            continue
        _collect_scope_vars(child, func_reads, func_writes)

    captured_reads = (func_reads & outer_scope_vars) - func_params
    captured_writes = func_writes & outer_scope_vars
    return captured_reads, captured_writes


def _collect_scope_vars(node: ast.AST, reads: set[str], writes: set[str]) -> None:
    """Collect reads/writes at current scope level (no descent into nested funcs)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Name):
            if isinstance(child.ctx, ast.Load):
                reads.add(child.id)
            elif isinstance(child.ctx, (ast.Store, ast.Del)):
                writes.add(child.id)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        _collect_scope_vars(child, reads, writes)


def _is_bag_of_handlers(body: list[ast.stmt]) -> bool:
    """Detect if >50% of body statements are nested function definitions."""
    func_count = sum(
        1 for stmt in body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return len(body) > 0 and func_count > len(body) * 0.5


def _find_nested_handler_candidates(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: str,
) -> list[Prescription]:
    """Detect nested handler functions and generate extraction prescriptions.

    Handles the "bag of handlers" pattern: registration functions whose body
    is primarily decorated nested function definitions (e.g., @mcp.tool(),
    @app.route(), @click.command()).
    """
    body = func_node.body
    if not _is_bag_of_handlers(body):
        return []

    outer_params = _get_param_names(func_node)
    # Variables assigned at the outer scope level (before/between handlers)
    outer_vars: set[str] = set(outer_params)
    for stmt in body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            outer_vars |= _collect_writes(stmt)

    candidates: list[Prescription] = []
    handler_infos: list[dict] = []

    for stmt in body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        handler_cc = compute_cognitive_complexity(stmt)
        captured_reads, captured_writes = _analyze_closure(stmt, outer_vars)

        # Decorator-aware naming and confidence
        has_handler_decorator = any(
            _is_handler_decorator(d) for d in stmt.decorator_list
        )
        decorator_name = None
        if stmt.decorator_list:
            decorator_name = _get_decorator_name(stmt.decorator_list[0])

        proposed_name = f"_impl_{stmt.name}"
        confidence = 0.65  # Base for nested handler extraction

        if has_handler_decorator:
            confidence += 0.15  # Decorators signal intentional independence
        if not captured_writes:
            confidence += 0.05  # No writes to outer scope — clean extraction

        line_start = stmt.lineno
        line_end = stmt.end_lineno or stmt.lineno

        basis = ["nested_handler", "closure_analysis"]
        if has_handler_decorator:
            basis.append("decorator_independence")

        candidates.append(
            Prescription(
                kind="extract_function",
                target=f"{filepath}::{func_node.name}",
                action=f"Extract nested handler `{stmt.name}` to module-level `{proposed_name}()`",
                source="static",
                confidence=min(confidence, 0.85),
                lines=(line_start, line_end),
                proposed_name=proposed_name,
                inputs=sorted(captured_reads),
                outputs=sorted(captured_writes),
                basis=basis,
                expected_delta={"cc_reduction": handler_cc},
            )
        )

        handler_infos.append(
            {
                "name": stmt.name,
                "lines": [line_start, line_end],
                "captured": sorted(captured_reads),
                "decorator": decorator_name,
                "cc": handler_cc,
            }
        )

    # Fix 6: Emit batch "decompose_register" prescription if enough handlers
    if len(handler_infos) >= 2:
        total_cc = sum(h["cc"] for h in handler_infos)
        # Group by decorator type
        decorator_types = {h["decorator"] for h in handler_infos if h["decorator"]}
        action_parts = [f"Extract {len(handler_infos)} nested handlers"]
        if decorator_types:
            action_parts.append(f"({', '.join(sorted(decorator_types))})")
        action_parts.append("to module-level _impl_* functions")

        batch_line_start = min(h["lines"][0] for h in handler_infos)
        batch_line_end = max(h["lines"][1] for h in handler_infos)

        candidates.append(
            Prescription(
                kind="decompose_register",
                target=f"{filepath}::{func_node.name}",
                action=" ".join(action_parts),
                source="static",
                confidence=0.85,
                lines=(batch_line_start, batch_line_end),
                basis=["nested_handler", "register_pattern", "batch_extraction"],
                expected_delta={
                    "cc_reduction": total_cc,
                    "handlers": handler_infos,
                },
            )
        )

    return candidates
