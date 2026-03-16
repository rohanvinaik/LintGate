"""AST triviality pre-filter — detect low-value functions before mutation profiling.

Identifies functions that are structurally trivial (accessors, simple
serializers, forwarders, property wrappers) and unlikely to benefit from
mutation testing. These functions produce equivalent or low-value mutants
that waste profiling budget.

Three categories:
  - TRIVIAL_ACCESSOR: single return of self.attr or argument
  - TRIVIAL_SERIALIZER: dict/tuple literal return using only self attributes
  - TRIVIAL_FORWARDER: delegates entirely to a single other function call

Usage in the mutation pipeline:
  Before profiling, call classify_triviality(node) to decide whether to
  skip mutation generation entirely or tag surviving mutants as low-value.
"""

from __future__ import annotations

import ast
from enum import Enum
from typing import Any


class TrivialityClass(str, Enum):
    """Classification of function triviality."""

    NONTRIVIAL = "nontrivial"
    TRIVIAL_ACCESSOR = "trivial_accessor"
    TRIVIAL_SERIALIZER = "trivial_serializer"
    TRIVIAL_FORWARDER = "trivial_forwarder"
    TRIVIAL_PROPERTY = "trivial_property"
    TRIVIAL_IDENTITY = "trivial_identity"


# Function name patterns that strongly suggest boilerplate
_SERIALIZATION_NAMES = frozenset(
    {
        "to_dict",
        "from_dict",
        "to_json",
        "from_json",
        "serialize",
        "deserialize",
        "as_dict",
        "to_tuple",
        "to_list",
        "from_list",
        "to_str",
        "as_tuple",
    }
)


def classify_triviality(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    function_name: str = "",
) -> TrivialityClass:
    """Classify whether a function is structurally trivial.

    Args:
        node: The function AST node.
        function_name: Optional qualified or bare function name.
            Used for name-based heuristics (serializer detection).

    Returns:
        TrivialityClass — NONTRIVIAL means profiling is worthwhile.
    """
    bare_name = function_name.rsplit(".", 1)[-1] if function_name else node.name
    body = _effective_body(node)

    if not body:
        # Empty function (pass only) or just docstring
        return TrivialityClass.TRIVIAL_IDENTITY

    # Single-statement bodies are the strongest triviality signals
    if len(body) == 1:
        stmt = body[0]

        # Check for property-style: @property def x(self): return self._x
        if _is_property_getter(node, stmt):
            return TrivialityClass.TRIVIAL_PROPERTY

        # Check for simple accessor: return self.attr or return arg
        if _is_simple_accessor(stmt, node):
            return TrivialityClass.TRIVIAL_ACCESSOR

        # Check for identity/passthrough: return arg
        if _is_identity_return(stmt, node):
            return TrivialityClass.TRIVIAL_IDENTITY

        # Check for pure forwarder: return other_func(...)
        if _is_forwarder(stmt, node):
            return TrivialityClass.TRIVIAL_FORWARDER

    # Multi-statement: check for dict-building serializer pattern
    if bare_name in _SERIALIZATION_NAMES and _is_dict_serializer(body, node):
        return TrivialityClass.TRIVIAL_SERIALIZER

    # Single return of a dict/tuple literal with only self.attr values
    if len(body) == 1 and _is_literal_return(body[0]):
        return TrivialityClass.TRIVIAL_SERIALIZER

    return TrivialityClass.NONTRIVIAL


def is_trivial(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    function_name: str = "",
) -> bool:
    """Convenience: return True if the function is structurally trivial."""
    return classify_triviality(node, function_name=function_name) != TrivialityClass.NONTRIVIAL


# ── Body extraction ───────────────────────────────────────────────


def _effective_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """Return the function body with docstrings and pass stripped."""
    stmts: list[ast.stmt] = []
    for i, stmt in enumerate(node.body):
        # Skip leading docstring
        if (
            i == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue
        # Skip bare pass
        if isinstance(stmt, ast.Pass):
            continue
        stmts.append(stmt)
    return stmts


# ── Pattern detectors ─────────────────────────────────────────────


def _is_property_getter(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    stmt: ast.stmt,
) -> bool:
    """Check if this is @property def x(self): return self._x."""
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    # Must have @property decorator
    has_property = any(
        (isinstance(d, ast.Name) and d.id == "property")
        or (isinstance(d, ast.Attribute) and d.attr == "property")
        for d in func.decorator_list
    )
    if not has_property:
        return False
    # Return value must be self.attr
    return _is_self_attr(stmt.value)


def _is_simple_accessor(
    stmt: ast.stmt,
    _func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Check: return self.attr or return self.attr.subattr."""
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    val = stmt.value
    return _is_self_attr(val) or (isinstance(val, ast.Attribute) and _is_self_attr(val.value))


def _is_identity_return(
    stmt: ast.stmt,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Check: def f(x): return x — identity function."""
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    if not isinstance(stmt.value, ast.Name):
        return False
    # The returned name must be a parameter
    param_names = _param_names(func)
    return stmt.value.id in param_names


def _is_forwarder(
    stmt: ast.stmt,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Check: return other_func(*args) — pure delegation.

    The call must use only the function's own parameters (not computed values).
    """
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    if not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    # All arguments must be simple names that are parameters or self
    params = _param_names(func)
    for arg in call.args:
        if isinstance(arg, ast.Starred):
            arg = arg.value
        if isinstance(arg, ast.Name):
            if arg.id not in params and arg.id != "self" and arg.id != "cls":
                return False
        elif _is_self_attr(arg):
            continue  # self.x is fine for delegation
        else:
            return False
    for kw in call.keywords:
        if kw.arg is None:
            # **kwargs unpacking
            if isinstance(kw.value, ast.Name) and kw.value.id in params:
                continue
            return False
        if isinstance(kw.value, ast.Name):
            if kw.value.id not in params and kw.value.id != "self":
                return False
        elif _is_self_attr(kw.value):
            continue
        else:
            return False
    return True


def _is_dict_serializer(
    body: list[ast.stmt],
    _func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Check if body builds and returns a dict from self attributes.

    Matches patterns like:
      d = {"key": self.attr, ...}
      return d
    or:
      return {"key": self.attr, ...}
    """
    # Pattern 1: single return of dict literal
    if len(body) == 1:
        return _is_literal_return(body[0])

    # Pattern 2: assign dict → optional mutations → return
    # Must end with return
    if not isinstance(body[-1], ast.Return):
        return False

    # Check that all assignments are dict-building
    for stmt in body[:-1]:
        if isinstance(stmt, ast.Assign):
            # d = {..} or d["key"] = self.attr
            if isinstance(stmt.value, ast.Dict):
                if not _dict_values_are_self_attrs(stmt.value):
                    return False
            elif not _is_self_attr(stmt.value):
                return False
        elif isinstance(stmt, ast.Expr):
            # Allow d.update(...) etc.
            continue
        elif isinstance(stmt, ast.If):
            # Conditional dict additions are common in serializers
            continue
        else:
            return False

    return True


def _is_literal_return(stmt: ast.stmt) -> bool:
    """Check if stmt is `return {literal_dict}` or `return (tuple_of_self_attrs)`."""
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    val = stmt.value
    if isinstance(val, ast.Dict):
        return _dict_values_are_self_attrs(val)
    if isinstance(val, ast.Tuple):
        return all(_is_self_attr(elt) or _is_constant(elt) for elt in val.elts)
    return False


def _dict_values_are_self_attrs(node: ast.Dict) -> bool:
    """Check if all dict values are self.attr or constants."""
    for val in node.values:
        if val is None:
            continue  # ** unpacking
        if not (_is_self_attr(val) or _is_constant(val)):
            return False
    return True


def _is_self_attr(node: Any) -> bool:
    """Check if node is self.something."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _is_constant(node: Any) -> bool:
    """Check if node is a literal constant."""
    return isinstance(node, ast.Constant)


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Get all parameter names (excluding self/cls)."""
    names: set[str] = set()
    for arg in func.args.args:
        if arg.arg not in ("self", "cls"):
            names.add(arg.arg)
    for arg in func.args.posonlyargs:
        if arg.arg not in ("self", "cls"):
            names.add(arg.arg)
    for arg in func.args.kwonlyargs:
        names.add(arg.arg)
    if func.args.vararg:
        names.add(func.args.vararg.arg)
    if func.args.kwarg:
        names.add(func.args.kwarg.arg)
    return names
