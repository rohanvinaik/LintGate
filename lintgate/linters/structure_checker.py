"""Structure and clean-code checker — AST-based architectural enforcement.

Tier 2 — runs on logic and structural changes. Pure Python stdlib,
no external dependencies. This is the "clean code conscience" of LintGate.

Checks modeled after research across three codebases:
- ARC_AGI_3's pylint design checks (R0913 max-args, R0902 max-attributes,
  R0914 max-locals, R0915 max-statements) — reimplemented as lightweight AST
- TailChasingFixer's responsibility diffusion detection — adapted as
  god-class/god-function heuristics
- Cognitive complexity (distinct from cyclomatic) — measures understanding
  difficulty through nesting depth, boolean chains, and recursion
- File-level growth monitoring — catches the gradual bloat that LLM agents
  tend to produce when they keep appending to the same file

Philosophy: These are the checks that matter to someone who values
intentional architectural design over "does it technically run?" The
traditional CS linting world focuses on syntax errors and type mismatches;
this module focuses on structural coherence and readability — the things
that make code *maintainable* by humans (or future LLM sessions).

Thresholds are strictness-aware: pipeline-critical paths get tighter limits.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter
from .cognitive_complexity import (
    compute_cognitive_complexity,
    compute_max_nesting,
    count_statements,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

# ─── Default thresholds by strictness ────────────────────────────────────

_DEFAULTS: dict[str, dict[str, int]] = {
    "relaxed": {
        "max_function_args": 8,
        "max_function_locals": 20,
        "max_function_statements": 60,
        "max_function_returns": 8,
        "max_class_attributes": 15,
        "max_class_methods": 20,
        "max_class_parents": 4,
        "max_nesting_depth": 6,
        "max_file_lines": 600,
        "max_file_classes": 6,
        "max_file_functions": 20,
        "cognitive_complexity_threshold": 25,
    },
    "normal": {
        "max_function_args": 6,
        "max_function_locals": 15,
        "max_function_statements": 50,
        "max_function_returns": 6,
        "max_class_attributes": 10,
        "max_class_methods": 15,
        "max_class_parents": 3,
        "max_nesting_depth": 5,
        "max_file_lines": 400,
        "max_file_classes": 4,
        "max_file_functions": 15,
        "cognitive_complexity_threshold": 15,
    },
    "strict": {
        "max_function_args": 4,
        "max_function_locals": 10,
        "max_function_statements": 30,
        "max_function_returns": 4,
        "max_class_attributes": 7,
        "max_class_methods": 10,
        "max_class_parents": 2,
        "max_nesting_depth": 4,
        "max_file_lines": 300,
        "max_file_classes": 3,
        "max_file_functions": 10,
        "cognitive_complexity_threshold": 10,
    },
}


class StructureChecker(BaseLinter):
    """AST-based structure and clean-code checker.

    Catches:
    - God functions (too many args, locals, statements, returns)
    - God classes (too many attributes, methods, parents)
    - Deep nesting (understanding difficulty)
    - Cognitive complexity (beyond cyclomatic — measures human readability)
    - File bloat (too many lines, classes, functions per file)

    All checks are pure AST — no imports, no execution, ~2ms per file.
    """

    name = "structure_checker"
    tier = 2
    timeout_ms = 3000
    required_tool = None  # Pure AST, always available

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run all structure checks on specified files."""
        thresholds = _get_thresholds(ctx)

        for filepath in ctx.files:
            yield from self._check_file(filepath, thresholds)

    def _check_file(
        self,
        filepath: str,
        thresholds: dict[str, int],
    ) -> Iterable[LintIssue]:
        """Run all checks on a single file."""
        try:
            with open(filepath) as f:
                source = f.read()
        except OSError:
            return

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return  # Syntax errors caught by ruff

        lines = source.splitlines()

        yield from _check_file_size(filepath, lines, thresholds)
        yield from _check_file_structure(filepath, tree, thresholds)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from _check_function(filepath, node, thresholds)
            elif isinstance(node, ast.ClassDef):
                yield from _check_class(filepath, node, thresholds)


# ─── File-level checks ──────────────────────────────────────────────────


def _check_file_size(
    filepath: str,
    lines: list[str],
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check file-level size limits."""
    max_lines = thresholds["max_file_lines"]
    line_count = len(lines)

    if line_count > max_lines:
        severity = "blocking" if line_count > max_lines * 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="file-too-long",
            message=(
                f"File has {line_count} lines (limit: {max_lines}). "
                f"Consider splitting into focused modules."
            ),
            file=filepath,
            severity=severity,
            confidence=1.0,
            evidence={"lines": line_count, "threshold": max_lines},
            suggestions=[
                "Extract related functions into a separate module",
                "Group by responsibility: data, logic, I/O",
            ],
        )


def _check_file_structure(
    filepath: str,
    tree: ast.Module,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check file-level structural limits (classes, functions count)."""
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    max_classes = thresholds["max_file_classes"]
    if len(classes) > max_classes:
        yield LintIssue(
            linter="structure",
            kind="too-many-classes",
            message=(
                f"File has {len(classes)} classes (limit: {max_classes}). "
                f"This suggests mixed responsibilities."
            ),
            file=filepath,
            severity="warning",
            confidence=0.9,
            evidence={"count": len(classes), "threshold": max_classes},
            suggestions=[
                "Each class should represent one clear concept",
                "Split into one module per class (or per related group)",
            ],
        )

    max_functions = thresholds["max_file_functions"]
    if len(functions) > max_functions:
        yield LintIssue(
            linter="structure",
            kind="too-many-functions",
            message=(
                f"File has {len(functions)} top-level functions (limit: {max_functions}). "
                f"Consider grouping into classes or modules."
            ),
            file=filepath,
            severity="informational",
            confidence=0.85,
            evidence={"count": len(functions), "threshold": max_functions},
            suggestions=[
                "Group related functions into a class or separate module",
            ],
        )


# ─── Function-level checks ──────────────────────────────────────────────


def _check_function(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check a single function for structural issues."""
    name = node.name

    # Skip dunder methods — they have legitimate reasons for many args/complexity
    if name.startswith("__") and name.endswith("__"):
        return

    yield from _check_function_args(filepath, node, name, thresholds)
    yield from _check_function_locals(filepath, node, name, thresholds)
    yield from _check_function_statements(filepath, node, name, thresholds)
    yield from _check_function_returns(filepath, node, name, thresholds)
    yield from _check_nesting_depth(filepath, node, name, thresholds)
    yield from _check_cognitive_complexity(filepath, node, name, thresholds)


def _check_function_args(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check function argument count (pylint R0913)."""
    args = node.args
    # Count all positional args, excluding 'self' and 'cls'
    all_args = args.posonlyargs + args.args + args.kwonlyargs
    arg_names = [a.arg for a in all_args if a.arg not in ("self", "cls")]
    count = len(arg_names)

    max_args = thresholds["max_function_args"]
    if count > max_args:
        severity = "blocking" if count > max_args + 4 else "warning"
        yield LintIssue(
            linter="structure",
            kind="too-many-args",
            message=(
                f"Function '{name}' has {count} arguments (limit: {max_args}). "
                f"Consider using a config dataclass or **kwargs."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"count": count, "threshold": max_args, "args": arg_names},
            suggestions=[
                "Group related args into a dataclass or TypedDict",
                "Use builder pattern for complex construction",
            ],
        )


def _check_function_locals(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check local variable count in function (pylint R0914)."""
    local_names = _count_local_names(node)

    count = len(local_names)
    max_locals = thresholds["max_function_locals"]

    if count > max_locals:
        yield LintIssue(
            linter="structure",
            kind="too-many-locals",
            message=(
                f"Function '{name}' has {count} local variables (limit: {max_locals}). "
                f"This suggests the function is doing too many things."
            ),
            file=filepath,
            line=node.lineno,
            severity="warning",
            confidence=0.9,  # AST counting has edge cases
            evidence={"count": count, "threshold": max_locals},
            suggestions=[
                "Extract helper functions for distinct logical steps",
                "Consider whether some variables track the same concept",
            ],
        )


def _check_function_statements(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check statement count in function (pylint R0915)."""
    count = count_statements(node)
    max_stmts = thresholds["max_function_statements"]

    if count > max_stmts:
        severity = "blocking" if count > max_stmts * 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="too-many-statements",
            message=(
                f"Function '{name}' has {count} statements (limit: {max_stmts}). "
                f"Functions should do one thing well."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"count": count, "threshold": max_stmts},
            suggestions=[
                "Extract logical blocks into named helper functions",
                "Each function should have one clear purpose",
            ],
        )


def _check_function_returns(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check return statement count (too many exit points)."""
    returns = sum(1 for n in ast.walk(node) if isinstance(n, ast.Return))
    max_returns = thresholds["max_function_returns"]

    if returns > max_returns:
        yield LintIssue(
            linter="structure",
            kind="too-many-returns",
            message=(
                f"Function '{name}' has {returns} return statements (limit: {max_returns}). "
                f"Multiple exit points make control flow harder to follow."
            ),
            file=filepath,
            line=node.lineno,
            severity="informational",
            confidence=0.85,  # Early returns can be fine — Pythonic guard clauses
            evidence={"count": returns, "threshold": max_returns},
            suggestions=[
                "Consider restructuring to reduce exit points",
                "Guard clauses at the top are fine — mid-function returns are the issue",
            ],
        )


def _check_nesting_depth(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check maximum nesting depth (the 'arrowhead anti-pattern')."""
    max_depth = compute_max_nesting(node)
    threshold = thresholds["max_nesting_depth"]

    if max_depth > threshold:
        severity = "blocking" if max_depth > threshold + 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="deep-nesting",
            message=(
                f"Function '{name}' has nesting depth {max_depth} (limit: {threshold}). "
                f"Deeply nested code is hard to understand and maintain."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"depth": max_depth, "threshold": threshold},
            suggestions=[
                "Use early returns (guard clauses) to flatten nesting",
                "Extract nested blocks into named helper functions",
                "Consider whether the logic can be simplified",
            ],
        )


def _check_cognitive_complexity(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check cognitive complexity — measures understanding difficulty.

    Cognitive complexity differs from cyclomatic complexity:
    - CC counts linearly independent paths (testing metric)
    - CogC measures how hard code is to *understand* (readability metric)

    CogC adds penalties for:
    - Nesting (deeper = harder to hold in working memory)
    - Breaks in linear flow (else, elif, catch, finally)
    - Boolean operator sequences (a and b or c and d)
    - Recursion (self-reference adds mental overhead)
    """
    cogc = compute_cognitive_complexity(node)
    threshold = thresholds["cognitive_complexity_threshold"]

    if cogc > threshold:
        severity = "blocking" if cogc > threshold * 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="cognitive-complexity",
            message=(
                f"Function '{name}' has cognitive complexity {cogc} "
                f"(limit: {threshold}). This function is too hard to understand."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"cognitive_complexity": cogc, "threshold": threshold},
            suggestions=[
                "Cognitive complexity measures understanding difficulty, not path count",
                "Reduce nesting, simplify boolean expressions, extract helper functions",
                "Each function should be understandable in one mental pass",
            ],
        )


# ─── Class-level checks ─────────────────────────────────────────────────


def _check_class(
    filepath: str,
    node: ast.ClassDef,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check a single class for structural issues."""
    name = node.name

    yield from _check_class_attributes(filepath, node, name, thresholds)
    yield from _check_class_methods(filepath, node, name, thresholds)
    yield from _check_class_parents(filepath, node, name, thresholds)


def _check_class_attributes(
    filepath: str,
    node: ast.ClassDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check instance attribute count (pylint R0902 — god class detector)."""
    attrs = _collect_class_attributes(node)

    count = len(attrs)
    max_attrs = thresholds["max_class_attributes"]

    if count > max_attrs:
        severity = "warning" if count <= max_attrs + 5 else "blocking"
        yield LintIssue(
            linter="structure",
            kind="too-many-attributes",
            message=(
                f"Class '{name}' has {count} attributes (limit: {max_attrs}). "
                f"This suggests the class has too many responsibilities."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=0.9,
            evidence={"count": count, "threshold": max_attrs},
            suggestions=[
                "Split into smaller classes, each with one responsibility",
                "Group related attributes into a nested dataclass",
                "Consider composition over inheritance",
            ],
        )


def _check_class_methods(
    filepath: str,
    node: ast.ClassDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check method count (pylint R0904 — god class indicator)."""
    methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # Don't count dunder methods — they're protocol implementations, not bloat
    non_dunder = [m for m in methods if not (m.name.startswith("__") and m.name.endswith("__"))]

    count = len(non_dunder)
    max_methods = thresholds["max_class_methods"]

    if count > max_methods:
        yield LintIssue(
            linter="structure",
            kind="too-many-methods",
            message=(
                f"Class '{name}' has {count} non-dunder methods (limit: {max_methods}). "
                f"Consider splitting responsibilities."
            ),
            file=filepath,
            line=node.lineno,
            severity="warning",
            confidence=0.9,
            evidence={"count": count, "threshold": max_methods},
            suggestions=[
                "Extract method groups into mixin classes or helper objects",
                "Each class should have one clear reason to change",
            ],
        )


def _check_class_parents(
    filepath: str,
    node: ast.ClassDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check inheritance depth (pylint R0901 — deep hierarchy smell)."""
    count = len(node.bases)
    max_parents = thresholds["max_class_parents"]

    if count > max_parents:
        yield LintIssue(
            linter="structure",
            kind="too-many-parents",
            message=(
                f"Class '{name}' inherits from {count} classes (limit: {max_parents}). "
                f"Prefer composition over deep inheritance."
            ),
            file=filepath,
            line=node.lineno,
            severity="informational",
            confidence=0.8,  # Mixin patterns can justify multiple bases
            evidence={"count": count, "threshold": max_parents},
            suggestions=[
                "Prefer composition over inheritance",
                "Consider using Protocol or ABC for interfaces",
            ],
        )


# ─── AST helpers ─────────────────────────────────────────────────────────


def _collect_class_attributes(node: ast.ClassDef) -> set[str]:
    """Collect all attribute names (instance + class-level) from a class."""
    attrs: set[str] = set()
    # Instance attributes (self.x = ...) via AST walk
    for child in ast.walk(node):
        self_attr = _get_self_attr_name(child)
        if self_attr:
            attrs.add(self_attr)
    # Class-level assignments
    for stmt in node.body:
        class_attr = _get_class_level_attr(stmt)
        if class_attr:
            attrs.add(class_attr)
    return attrs


def _get_self_attr_name(node: ast.AST) -> str | None:
    """Extract attribute name from self.x = ... or self.x: type = ..."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Attribute) and _is_self_attribute(target):
                return target.attr
    elif (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Attribute)
        and _is_self_attribute(node.target)
    ):
        return node.target.attr
    return None


def _is_self_attribute(node: ast.AST) -> bool:
    """Check if a node is self.something."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _get_class_level_attr(stmt: ast.stmt) -> str | None:
    """Extract attribute name from a class-level assignment."""
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                return target.id
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return stmt.target.id
    return None


def _count_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Count local variable names in a function via AST walk."""
    local_names: set[str] = set()
    for child in ast.walk(node):
        _collect_locals_from_node(child, local_names)
    return local_names


def _collect_locals_from_node(child: ast.AST, local_names: set[str]) -> None:
    """Extract local variable names from a single AST node."""
    if isinstance(child, ast.Assign):
        for target in child.targets:
            _collect_names(target, local_names)
    elif (
        isinstance(child, ast.AnnAssign)
        and child.target
        or isinstance(child, (ast.AugAssign, ast.For, ast.AsyncFor))
    ):
        _collect_names(child.target, local_names)
    elif isinstance(child, ast.With):
        for item in child.items:
            if item.optional_vars:
                _collect_names(item.optional_vars, local_names)


def _collect_names(target: ast.AST, names: set[str]) -> None:
    """Collect variable names from an assignment target."""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, ast.Tuple):
        for elt in target.elts:
            _collect_names(elt, names)
    elif isinstance(target, ast.Starred):
        _collect_names(target.value, names)


def _get_thresholds(ctx: LinterContext) -> dict[str, int]:
    """Get thresholds for the current strictness, with config overrides."""
    defaults = _DEFAULTS.get(ctx.strictness, _DEFAULTS["normal"])
    thresholds = dict(defaults)

    # Allow per-project config to override individual thresholds
    for key in thresholds:
        if key in ctx.config:
            thresholds[key] = ctx.config[key]

    return thresholds
