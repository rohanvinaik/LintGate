"""Class-level structure checks: attributes, methods, parents."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import collect_class_attributes

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_class(
    filepath: str,
    node: ast.ClassDef,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Run all class-level checks."""
    name = node.name
    yield from check_class_attributes(filepath, node, name, thresholds)
    yield from check_class_methods(filepath, node, name, thresholds)
    yield from check_class_parents(filepath, node, name, thresholds)


_ENUM_BASE_NAMES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})


def _is_enum_subclass(node: ast.ClassDef) -> bool:
    """Check if a class inherits from an Enum type."""
    for base in node.bases:
        # Direct: class Foo(Enum)
        if isinstance(base, ast.Name) and base.id in _ENUM_BASE_NAMES:
            return True
        # Qualified: class Foo(enum.Enum)
        if (
            isinstance(base, ast.Attribute)
            and base.attr in _ENUM_BASE_NAMES
            and isinstance(base.value, ast.Name)
        ):
            return True
    return False


def check_class_attributes(
    filepath: str,
    node: ast.ClassDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check instance attribute count (pylint R0902 — god class detector)."""
    # Enum members are values, not responsibilities — skip the check.
    if _is_enum_subclass(node):
        return

    attrs = collect_class_attributes(node)
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


def check_class_methods(
    filepath: str,
    node: ast.ClassDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check method count (pylint R0904 — god class indicator)."""
    methods = [
        n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    non_dunder = [
        m for m in methods if not (m.name.startswith("__") and m.name.endswith("__"))
    ]

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


def check_class_parents(
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
            confidence=0.8,
            evidence={"count": count, "threshold": max_parents},
            suggestions=[
                "Prefer composition over inheritance",
                "Consider using Protocol or ABC for interfaces",
            ],
        )
