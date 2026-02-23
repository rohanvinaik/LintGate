"""Pure function detector using two-pass AST analysis."""

from __future__ import annotations

import ast

from lintgate.linters.performance_checks._helpers import get_name
from lintgate.linters.performance_checks.algebra_types import (
    PurityResult,
    SideEffect,
)

# Functions known to be pure by definition in Python
_KNOWN_PURE_BUILTINS = {
    "abs",
    "all",
    "any",
    "ascii",
    "bin",
    "bool",
    "callable",
    "chr",
    "complex",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "hash",
    "hex",
    "id",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "oct",
    "ord",
    "pow",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "vars",
    "zip",
}

# Methods/Attributes known to mutate state
_MUTATING_METHODS = {
    "append",
    "extend",
    "insert",
    "remove",
    "pop",
    "clear",
    "sort",
    "reverse",
    "update",
    "setdefault",
    "add",
    "discard",
    "write",
    "writelines",
    "seek",
}

# Known impure modules/namespaces generally involving I/O or global state
_IMPURE_NAMESPACES = {
    "print",
    "open",
    "input",
    "logging",
    "requests",
    "os",
    "sys",
    "time",
    "random",
    "urllib",
    "http",
    "socket",
    "subprocess",
    "threading",
    "multiprocessing",
}


class _PureFunctionVisitor(ast.NodeVisitor):
    """Pass 1: Gather local evidence of impurity (side effects) within a single function."""

    def __init__(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool = False):
        self.func_node = func_node
        self.is_method = is_method
        self.side_effects: list[SideEffect] = []
        self.called_functions: set[str] = set()

        # Track local variables to distinguish local mutations from global ones
        self.local_names: set[str] = {arg.arg for arg in func_node.args.args}
        self.local_names.update(arg.arg for arg in func_node.args.kwonlyargs)
        if hasattr(func_node.args, "posonlyargs"):
            self.local_names.update(arg.arg for arg in func_node.args.posonlyargs)
        if func_node.args.vararg:
            self.local_names.add(func_node.args.vararg.arg)
        if func_node.args.kwarg:
            self.local_names.add(func_node.args.kwarg.arg)

        # Add 'self' or 'cls' explicitly if it's a method
        if self.is_method and self.local_names:
            # the first arg is usually self/cls, but we just track it as a local
            pass

        # Check for mutable defaults
        self._check_mutable_defaults()

    def _check_mutable_defaults(self) -> None:
        """Flag mutable default arguments as persistent side effects."""
        defaults = list(self.func_node.args.defaults)
        defaults.extend(
            [d for d in self.func_node.args.kw_defaults if d is not None]
        )

        for d in defaults:
            if isinstance(d, (ast.List, ast.Dict, ast.Set, ast.Call)):
                self.side_effects.append(
                    SideEffect(
                        "mutable_default",
                        "DefaultArg",
                        self.func_node.lineno,
                        "Function has a mutable default argument which persists state across calls"
                    )
                )

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.side_effects.append(
                SideEffect("global_write", "Global", node.lineno, f"Writes to global '{name}'")
            )
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.side_effects.append(
                SideEffect(
                    "nonlocal_write", "Nonlocal", node.lineno, f"Writes to nonlocal '{name}'"
                )
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Track assignments to local variables
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.local_names.add(target.id)
            elif isinstance(target, ast.Attribute) and self.is_method:
                # E.g. `self.x = 1` -> Mutation if outside __init__
                target_name = get_name(target.value)
                if target_name in ("self", "cls") and self.func_node.name != "__init__":
                    self.side_effects.append(
                        SideEffect(
                            "attribute_mutation",
                            "Assign",
                            node.lineno,
                            f"Mutates instance state: {target_name}.{target.attr}",
                        )
                    )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.local_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.side_effects.append(
            SideEffect("generator", "Yield", node.lineno, "Function is a stateful generator")
        )
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.side_effects.append(
            SideEffect("generator", "YieldFrom", node.lineno, "Function is a stateful generator")
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = get_name(node.func)
        if func_name:
            self.called_functions.add(func_name)

            # Direct I/O or known impure builtins
            if func_name in _IMPURE_NAMESPACES or func_name.split(".")[0] in _IMPURE_NAMESPACES:
                self.side_effects.append(
                    SideEffect(
                        "io_call",
                        "Call",
                        node.lineno,
                        f"Calls impure namespace/function: {func_name}",
                    )
                )

            # Method call mutations (e.g., list.append, dict.update)
            if isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATING_METHODS:
                # We only flag if we're mutating something that isn't cleanly local
                # (A perfectly pure function can create a local list and append to it)
                target = get_name(node.func.value)
                if target and target not in self.local_names:
                    self.side_effects.append(
                        SideEffect(
                            "mutation",
                            "Call",
                            node.lineno,
                            f"Mutates external object var via .{node.func.attr}()",
                        )
                    )

        self.generic_visit(node)


def _get_parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    count = len(node.args.args) + len(node.args.kwonlyargs)
    if hasattr(node.args, "posonlyargs"):
        count += len(node.args.posonlyargs)  # type: ignore
    if node.args.vararg:
        count += 1
    if node.args.kwarg:
        count += 1
    return count


def analyze_purity(tree: ast.AST) -> dict[str, PurityResult]:
    """
    Two-pass pure function detector.

    Returns a mapping of qualified function names to their PurityResult.
    """
    # 1. First pass: Collect all functions and their local side effects.
    functions: dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, _PureFunctionVisitor]] = {}

    class _FunctionCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._handle_func(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._handle_func(node)

        def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            qualname = node.name
            is_method = bool(self.class_stack)
            if is_method:
                qualname = f"{'.'.join(self.class_stack)}.{node.name}"

            visitor = _PureFunctionVisitor(node, is_method=is_method)
            visitor.visit(node)
            functions[qualname] = (node, visitor)
            # Do NOT generic_visit here, because we don't want to process nested functions
            # in the same scope as the parent. Actually, for nested, we should handle them,
            # but simple analysis suffices for top-level/class-level.

    collector = _FunctionCollector()
    collector.visit(tree)

    # 2. Second pass: Transitive impurity propagation
    # If A calls B, and B has side effects, A has side effects.
    changed = True
    while changed:
        changed = False
        for _qualname, (node, visitor) in functions.items():
            if visitor.side_effects:
                # Already impure, skip
                continue

            for called in visitor.called_functions:
                # Simple resolution: if we call something in the same module that is impure
                if called in functions:
                    called_visitor = functions[called][1]
                    if called_visitor.side_effects:
                        visitor.side_effects.append(
                            SideEffect(
                                "impure_call",
                                "Call",
                                node.lineno,
                                f"Calls organically impure function '{called}' in same module",
                            )
                        )
                        changed = True
                        break
                elif (
                    called not in _KNOWN_PURE_BUILTINS and not called.islower()
                ):  # heuristic for Built-Ins
                    # We have a call to an unknown, unresolved function (external module).
                    # We conservatively mark it impure.
                    visitor.side_effects.append(
                        SideEffect(
                            "impure_call",
                            "Call",
                            node.lineno,
                            f"Calls unresolved external function '{called}'",
                        )
                    )
                    changed = True
                    break

    # 3. Build Result objects
    results: dict[str, PurityResult] = {}
    for qualname, (node, visitor) in functions.items():
        ret_ann = get_name(node.returns) if node.returns else None

        is_pure = len(visitor.side_effects) == 0
        confidence = 0.8 if is_pure else 1.0  # Impurity is certain, purity is heuristic

        results[qualname] = PurityResult(
            function_name=node.name,
            qualified_name=qualname,
            line=node.lineno,
            is_pure=is_pure,
            confidence=confidence,
            side_effects=tuple(visitor.side_effects),
            parameter_count=_get_parameter_count(node),
            return_annotation=ret_ann,
        )

    return results
