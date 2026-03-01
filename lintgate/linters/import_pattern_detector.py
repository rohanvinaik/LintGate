"""Detect optional-import patterns (try/except ImportError) for finding post-processing.

When an import lives inside ``try / except (ImportError, ModuleNotFoundError)``,
findings that reference the imported name should be downgraded from blocking to
informational — the missing module is intentionally handled.

This module is a **post-processing utility**, not a linter.  It is called by
``results_aggregator.py`` to adjust severity of findings from *any* linter
(ruff, bandit, import_checker, …) that touch guarded imports.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

_IMPORT_ERROR_NAMES = frozenset({"ImportError", "ModuleNotFoundError"})


@dataclass
class OptionalImport:
    """An import guarded by try/except ImportError."""

    module: str  # top-level module name
    names: list[str]  # imported names (or [module] for plain import)
    line: int
    fallback_value: str | None  # e.g. "None" if except block sets name = None


@dataclass
class OptionalImportReport:
    """Result of scanning a file for optional-import patterns."""

    optional_imports: list[OptionalImport] = field(default_factory=list)
    guarded_lines: set[int] = field(default_factory=set)
    guarded_names: set[str] = field(default_factory=set)


def detect_optional_imports(source: str) -> OptionalImportReport:
    """Scan *source* for try/except ImportError patterns.

    Returns an ``OptionalImportReport`` with all guarded import lines,
    imported names, and any fallback values found in except bodies.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return OptionalImportReport()

    report = OptionalImportReport()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue

        if not _catches_import_error(node):
            continue

        # Collect imports in the try body
        for stmt in node.body:
            _extract_imports(stmt, node.handlers, report)
            # Also walk nested statements (e.g. import inside if block in try)
            for child in ast.walk(stmt):
                if child is not stmt:
                    _extract_imports(child, node.handlers, report)

    return report


def _catches_import_error(try_node: ast.Try) -> bool:
    """Check if any handler catches ImportError or ModuleNotFoundError."""
    for handler in try_node.handlers:
        if handler.type is None:
            # Bare except — catches everything
            return True
        if (
            isinstance(handler.type, ast.Name)
            and handler.type.id in _IMPORT_ERROR_NAMES
        ):
            return True
        if isinstance(handler.type, ast.Tuple):
            for elt in handler.type.elts:
                if isinstance(elt, ast.Name) and elt.id in _IMPORT_ERROR_NAMES:
                    return True
    return False


def _extract_imports(
    node: ast.AST,
    handlers: list[ast.ExceptHandler],
    report: OptionalImportReport,
) -> None:
    """If *node* is an import statement, record it in *report*."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.asname or alias.name
            fallback = _find_fallback_assignment(name, handlers)
            report.optional_imports.append(
                OptionalImport(
                    module=alias.name,
                    names=[name],
                    line=node.lineno,
                    fallback_value=fallback,
                )
            )
            report.guarded_lines.add(node.lineno)
            report.guarded_names.add(name)

    elif isinstance(node, ast.ImportFrom) and node.module:
        imported_names = []
        for alias in node.names:
            imported_name = alias.asname or alias.name
            imported_names.append(imported_name)
            report.guarded_names.add(imported_name)
            fallback = _find_fallback_assignment(imported_name, handlers)
            if fallback is not None:
                # Record per-name for evidence
                report.optional_imports.append(
                    OptionalImport(
                        module=node.module,
                        names=[imported_name],
                        line=node.lineno,
                        fallback_value=fallback,
                    )
                )
        if not imported_names:
            return
        # Also add the whole module name as guarded
        report.guarded_names.add(node.module.split(".")[0])
        report.guarded_lines.add(node.lineno)


def _find_fallback_assignment(
    name: str, handlers: list[ast.ExceptHandler]
) -> str | None:
    """Check if except body assigns a fallback value to *name*.

    Recognizes patterns like:
        except ImportError:
            name = None
    """
    for handler in handlers:
        for stmt in handler.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        return ast.dump(stmt.value)
    return None


def is_finding_on_guarded_import(
    finding_line: int | None,
    finding_kind: str,
    finding_message: str,
    report: OptionalImportReport,
) -> bool:
    """Check if a lint finding relates to a guarded optional import.

    Returns True if the finding should be downgraded to informational.
    """
    if not report.optional_imports:
        return False

    # Direct line match — finding is on a guarded import line
    if finding_line is not None and finding_line in report.guarded_lines:
        return True

    # Check if finding message references a guarded name
    return any(name in finding_message for name in report.guarded_names)
