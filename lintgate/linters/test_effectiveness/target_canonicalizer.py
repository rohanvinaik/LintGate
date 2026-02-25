"""Shared utility for canonicalizing assertion targets.

Suppports matching `is_not_none` guards against follow-up assertions like
`assert state.field == x` by extracting the root variable or expression.
"""

from __future__ import annotations

import ast


def canonicalize_target(node: ast.expr) -> str:
    """Return the root variable name or a canonical string for any LHS expression.

    Examples:
        Name('x')                       → "x"
        Attribute(Name('x'), 'attr')     → "x"
        Subscript(Name('x'), ...)       → "x"
        Call(Attr(Name('x'), 'm'))       → "x"  # method call on x

    Used to match existence guards against value assertions on the same object.
    """
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        # recurse on the value (e.g. x in x.y.z)
        return canonicalize_target(node.value)

    if isinstance(node, ast.Subscript):
        # recurse on the value (e.g. x in x[0])
        return canonicalize_target(node.value)

    if isinstance(node, ast.Call):
        # If it's a method call: x.method() -> x
        if isinstance(node.func, ast.Attribute):
            return canonicalize_target(node.func.value)
        # If it's a bare call: func() -> return unparsed for exact match
        return ast.unparse(node)

    try:
        return ast.unparse(node)
    except Exception:
        return ""
