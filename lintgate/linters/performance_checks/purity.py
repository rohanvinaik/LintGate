"""Pure function detector using two-pass AST analysis."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

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
    # Container mutations
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
    # I/O mutations
    "write",
    "writelines",
    "seek",
    "truncate",
    "flush",
    "close",
}

# Methods that are always side-effectful regardless of target locality.
# Unlike _MUTATING_METHODS which are only flagged on non-local objects,
# these produce external side effects even when called on parameters.
_ALWAYS_IMPURE_METHODS = {
    "execute",
    "executemany",
    "commit",
    "rollback",
}

# Path methods that perform filesystem writes
_PATH_WRITE_METHODS = {
    "mkdir",
    "touch",
    "write_text",
    "write_bytes",
    "unlink",
    "rmdir",
    "rename",
    "replace",
    "symlink_to",
    "hardlink_to",
    "chmod",
}

# Serialization functions that write to file handles (module.func patterns)
_SERIALIZER_WRITE_CALLS = {
    "json.dump",
    "yaml.dump",
    "pickle.dump",
    "csv.writer",
    "toml.dump",
    "marshal.dump",
}

# ML/training operations that mutate model state or perform I/O
_ML_IMPURE_CALLS = {
    "from_pretrained",
    "load_state_dict",
    "save_pretrained",
    "save",
    "backward",
    "step",  # optimizer.step
    "zero_grad",
    "train",  # model.train() changes mode
    "eval",  # model.eval() changes mode
}

# ML namespace prefixes (torch, tensorflow, etc.)
_ML_IMPURE_NAMESPACES = {
    "torch.load",
    "torch.save",
    "torch.cuda",
    "tf.io",
    "tf.data",
    "tf.train",
    "keras.models.load_model",
    "joblib.dump",
    "joblib.load",
}

# Known impure modules/namespaces generally involving I/O or global state
_IMPURE_NAMESPACES = {
    "print",
    "open",
    "input",
    "logging",
    "logger",
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
    "shutil",
    "tempfile",
    "signal",
    "atexit",
    "gc",
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
        defaults.extend([d for d in self.func_node.args.kw_defaults if d is not None])

        for d in defaults:
            if isinstance(d, (ast.List, ast.Dict, ast.Set, ast.Call)):
                self.side_effects.append(
                    SideEffect(
                        "mutable_default",
                        "DefaultArg",
                        self.func_node.lineno,
                        "Function has a mutable default argument which persists state across calls",
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
                    "nonlocal_write",
                    "Nonlocal",
                    node.lineno,
                    f"Writes to nonlocal '{name}'",
                )
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Track assignments to local variables
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.local_names.add(target.id)
            elif isinstance(target, ast.Attribute):
                target_name = get_name(target.value)
                if self.is_method and target_name in ("self", "cls") and self.func_node.name != "__init__":
                    self.side_effects.append(
                        SideEffect(
                            "attribute_mutation",
                            "Assign",
                            node.lineno,
                            f"Mutates instance state: {target_name}.{target.attr}",
                        )
                    )
                elif target_name and target_name not in self.local_names:
                    # Attribute write on external object: obj.x = val
                    self.side_effects.append(
                        SideEffect(
                            "attribute_mutation",
                            "Assign",
                            node.lineno,
                            f"Mutates external object attribute: {target_name}.{target.attr}",
                        )
                    )
            elif isinstance(target, ast.Subscript):
                # obj[key] = val on non-local object
                target_name = get_name(target.value)
                if target_name and target_name not in self.local_names:
                    self.side_effects.append(
                        SideEffect(
                            "mutation",
                            "Assign",
                            node.lineno,
                            f"Subscript write on external object: {target_name}[...]",
                        )
                    )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # x += 1 style — check if target is external
        if isinstance(node.target, ast.Attribute):
            target_name = get_name(node.target.value)
            if self.is_method and target_name in ("self", "cls") and self.func_node.name != "__init__":
                self.side_effects.append(
                    SideEffect(
                        "attribute_mutation",
                        "AugAssign",
                        node.lineno,
                        f"Mutates instance state: {target_name}.{node.target.attr}",
                    )
                )
            elif target_name and target_name not in self.local_names:
                self.side_effects.append(
                    SideEffect(
                        "attribute_mutation",
                        "AugAssign",
                        node.lineno,
                        f"Augmented assign on external: {target_name}.{node.target.attr}",
                    )
                )
        elif isinstance(node.target, ast.Subscript):
            target_name = get_name(node.target.value)
            if target_name and target_name not in self.local_names:
                self.side_effects.append(
                    SideEffect(
                        "mutation",
                        "AugAssign",
                        node.lineno,
                        f"Augmented subscript on external: {target_name}[...]",
                    )
                )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.local_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                # del obj[key] — check if obj is external
                target_name = get_name(target.value)
                if target_name and target_name not in self.local_names:
                    self.side_effects.append(
                        SideEffect(
                            "mutation",
                            "Delete",
                            node.lineno,
                            f"Deletes from external object: {target_name}[...]",
                        )
                    )
            else:
                target_name = get_name(target)
                if target_name and target_name not in self.local_names:
                    self.side_effects.append(
                        SideEffect(
                            "mutation",
                            "Delete",
                            node.lineno,
                            f"Deletes external name: {target_name}",
                        )
                    )
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.side_effects.append(
            SideEffect("generator", "Yield", node.lineno, "Function is a stateful generator")
        )
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.side_effects.append(
            SideEffect(
                "generator",
                "YieldFrom",
                node.lineno,
                "Function is a stateful generator",
            )
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = get_name(node.func)
        if func_name:
            self.called_functions.add(func_name)

            # Direct I/O or known impure namespaces
            if func_name in _IMPURE_NAMESPACES or func_name.split(".")[0] in _IMPURE_NAMESPACES:
                self.side_effects.append(
                    SideEffect(
                        "io_call",
                        "Call",
                        node.lineno,
                        f"Calls impure namespace/function: {func_name}",
                    )
                )

            # Serializer write calls (json.dump, yaml.dump, etc.)
            if func_name in _SERIALIZER_WRITE_CALLS:
                self.side_effects.append(
                    SideEffect(
                        "io_call",
                        "Call",
                        node.lineno,
                        f"Serializer write: {func_name}",
                    )
                )

            # ML impure namespace calls (torch.load, torch.save, etc.)
            if func_name in _ML_IMPURE_NAMESPACES:
                self.side_effects.append(
                    SideEffect(
                        "io_call",
                        "Call",
                        node.lineno,
                        f"ML I/O operation: {func_name}",
                    )
                )

            # Method call mutations (e.g., list.append, dict.update)
            if isinstance(node.func, ast.Attribute):
                method = node.func.attr

                if method in _MUTATING_METHODS:
                    target = get_name(node.func.value)
                    if target and target not in self.local_names:
                        self.side_effects.append(
                            SideEffect(
                                "mutation",
                                "Call",
                                node.lineno,
                                f"Mutates external object via .{method}()",
                            )
                        )

                # Always-impure methods (DB ops) — side-effectful regardless of target
                if method in _ALWAYS_IMPURE_METHODS:
                    self.side_effects.append(
                        SideEffect(
                            "io_call",
                            "Call",
                            node.lineno,
                            f"Database operation via .{method}()",
                        )
                    )

                # Path write methods (path.write_text, path.mkdir, etc.)
                if method in _PATH_WRITE_METHODS:
                    self.side_effects.append(
                        SideEffect(
                            "io_call",
                            "Call",
                            node.lineno,
                            f"Filesystem write via .{method}()",
                        )
                    )

                # ML impure method calls (model.backward, optimizer.step, etc.)
                # Always flagged — these mutate model/optimizer state regardless of locality
                if method in _ML_IMPURE_CALLS:
                    target = get_name(node.func.value) or "<unknown>"
                    self.side_effects.append(
                        SideEffect(
                            "io_call",
                            "Call",
                            node.lineno,
                            f"ML state mutation via {target}.{method}()",
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


def _check_called_impurity(
    called: str,
    functions: dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, Any]],
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> SideEffect | None:
    """Check if a called function introduces impurity. Returns a SideEffect or None."""
    if called in functions:
        called_visitor = functions[called][1]
        if called_visitor.side_effects:
            return SideEffect(
                "impure_call",
                "Call",
                node.lineno,
                f"Calls organically impure function '{called}' in same module",
            )
    elif called not in _KNOWN_PURE_BUILTINS and not called.islower():
        return SideEffect(
            "impure_call",
            "Call",
            node.lineno,
            f"Calls unresolved external function '{called}'",
        )
    return None


def _compute_pure_confidence(
    visitor: _PureFunctionVisitor,
    functions: dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, Any]],
) -> float:
    """Compute evidence-weighted confidence for a pure function.

    Replaces flat 0.8 with bands based on call resolution:
    - 0.95: No external calls at all (fully self-contained)
    - 0.90: All calls resolved to known pure builtins or same-module pure functions
    - 0.80: Some unresolved lowercase calls (convention-based purity assumption)
    - 0.65: Many unresolved lowercase calls (3+), higher uncertainty
    """
    unresolved_count = 0
    for called in visitor.called_functions:
        if called in functions:
            # Same-module call — resolved
            continue
        if called in _KNOWN_PURE_BUILTINS:
            # Known pure builtin — resolved
            continue
        if called.islower():
            # Convention-based assumption (lowercase = likely pure helper)
            unresolved_count += 1
        # Non-lowercase unresolved calls would have been flagged as impure
        # by _check_called_impurity, so they don't reach here

    if not visitor.called_functions:
        return 0.95  # No calls at all — leaf function
    if unresolved_count == 0:
        return 0.90  # All calls fully resolved
    if unresolved_count <= 2:
        return 0.80  # Few unresolved convention-based assumptions
    return 0.65  # Many unresolved calls — higher uncertainty


def _propagate_impurity(
    functions: dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, Any]],
) -> None:
    """Propagate impurity transitively: if A calls impure B, A is impure."""
    changed = True
    while changed:
        changed = False
        for _qualname, (node, visitor) in functions.items():
            if visitor.side_effects:
                continue
            for called in visitor.called_functions:
                effect = _check_called_impurity(called, functions, node)
                if effect is not None:
                    visitor.side_effects.append(effect)
                    changed = True
                    break


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
    _propagate_impurity(functions)

    # 3. Build Result objects
    results: dict[str, PurityResult] = {}
    for qualname, (node, visitor) in functions.items():
        ret_ann = get_name(node.returns) if node.returns else None

        is_pure = len(visitor.side_effects) == 0
        if is_pure:
            confidence = _compute_pure_confidence(visitor, functions)
        else:
            confidence = 1.0  # Impurity is certain

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
