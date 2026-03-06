"""Mutmut v3 name demangling, matching, and AST characteristic extraction.

Pure utility functions shared by the mutation engine and result parsers.
Extracted from engine.py to reduce file length and improve cohesion.
"""

from __future__ import annotations

import ast
import os
from typing import Any


def extract_func_name_from_mutant_id(mutant_id: str) -> str | None:
    """Extract the simple function name from a mutmut mutant ID.

    Mutant IDs follow the format ``{module}.{base}__mutmut_{n}`` where
    ``base`` is one of:
    - ``x_funcname`` for module-level functions
    - ``x\\u01c1ClassName\\u01c1method_name`` for class methods

    Returns the simple function/method name (matching AST ``node.name``),
    or ``None`` if the ID cannot be parsed.

    Examples::

        >>> extract_func_name_from_mutant_id("mod.sub.x_compute__mutmut_3")
        'compute'
        >>> extract_func_name_from_mutant_id("mod.sub.x\\u01c1Cls\\u01c1run__mutmut_1")
        'run'
        >>> extract_func_name_from_mutant_id("mod.sub.x__private__mutmut_2")
        '_private'
    """
    # Strip __mutmut_N suffix
    mutmut_idx = mutant_id.find("__mutmut_")
    if mutmut_idx == -1:
        return None

    prefix = mutant_id[:mutmut_idx]

    class_sep = "\u01c1"  # mutmut's CLASS_NAME_SEPARATOR

    # Class method: look for .xǁ pattern
    class_marker = ".x" + class_sep
    class_idx = prefix.rfind(class_marker)
    if class_idx != -1:
        remainder = prefix[class_idx + 2 :]  # skip '.x'
        parts = [p for p in remainder.split(class_sep) if p]
        # parts = ['ClassName', 'method_name'] — return the method name
        return parts[-1] if parts else None

    # Module-level: look for .x_ pattern
    func_marker = ".x_"
    func_idx = prefix.rfind(func_marker)
    if func_idx != -1:
        return prefix[func_idx + 3 :]  # skip '.x_' → 'funcname'

    return None


def tally_status(
    entry: dict[str, Any],
    status: str,
    name_part: str,
    mutant_category_map: dict[str, str],
) -> None:
    """Increment the appropriate counter in *entry* based on *status*."""
    if status == "killed":
        entry["killed"] += 1
    elif status == "survived":
        entry["survived"] += 1
        cat = mutant_category_map.get(name_part)
        if cat:
            entry["survived_by_category"][cat] = entry["survived_by_category"].get(cat, 0) + 1
    elif status == "timeout":
        entry["timeout"] += 1


def match_path(file_path: str, paths: list[str]) -> str | None:
    """Map a demangled *file_path* back to an entry in *paths*.

    Returns the absolute matched path, or None when *paths* is non-empty and
    no match is found.  When *paths* is empty the demangled path is returned
    as-is.
    """
    if not paths:
        return file_path

    for p in paths:
        abs_p = os.path.abspath(p)
        norm_file = os.path.normpath(file_path)
        # Use endswith check to handle absolute vs relative or missing leading slash
        if abs_p.endswith(norm_file):
            return abs_p
        # src-layout: demangled path lacks src/ prefix that the requested path has
        if abs_p.endswith(os.path.normpath(os.path.join("src", file_path))):
            return abs_p

    return None


def demangle_mutmut_name(mangled: str) -> tuple[str, str]:
    """Convert mutmut v3 mangled name to (file_path, function_name).

    mutmut v3 mangles as:
      Module-level: module.path.x_funcname
      Class method: module.path.x\u01c1ClassName\u01c1method_name

    The separator is '.x' followed by either '_' (module-level) or
    '\u01c1' (class method, U+01C1 Latin Letter Lateral Click).

    Examples:
        'lintgate.mutation.ci_stats.x_compute_badge_color'
            -> ('lintgate/mutation/ci_stats.py', 'compute_badge_color')
        'lintgate.mutation.ci_stats.x__enrich_function_names'
            -> ('lintgate/mutation/ci_stats.py', '_enrich_function_names')
        'lintgate.linters.purity.x\u01c1_PureFunctionVisitor\u01c1visit_Call'
            -> ('lintgate/linters/purity.py', '_PureFunctionVisitor.visit_Call')
    """
    class_sep = "\u01c1"  # mutmut's CLASS_NAME_SEPARATOR

    # Class method format: .xǁClassǁmethod
    class_idx = mangled.rfind(".x" + class_sep)
    if class_idx != -1:
        module_dotted = mangled[:class_idx]
        remainder = mangled[class_idx + 2 :]  # skip '.x'
        # remainder is like 'ǁClassǁmethod' — split on separator
        parts = remainder.split(class_sep)
        # parts = ['', 'ClassName', 'method_name']
        parts = [p for p in parts if p]
        func_name = ".".join(parts) if len(parts) >= 2 else (parts[0] if parts else "")
        file_path = module_dotted.replace(".", "/") + ".py"
        return (file_path, func_name)

    # Module-level format: .x_funcname
    idx = mangled.rfind(".x_")
    if idx == -1:
        return ("", "")
    module_dotted = mangled[:idx]
    func_name = mangled[idx + 3 :]  # skip '.x_'
    file_path = module_dotted.replace(".", "/") + ".py"
    return (file_path, func_name)


def is_mutant_relevant(
    mutant_id: str,
    cat: str,
    per_function_categories: dict[str, set] | None,
    relevant_categories: set,
) -> bool:
    """Decide whether a single mutant should be included in the run.

    Phase 4 per-function filtering is tried first; falls back to file-level.
    Returns True if the mutant's category is relevant.
    """
    if per_function_categories is not None:
        func_name = extract_func_name_from_mutant_id(mutant_id)
        if func_name and func_name in per_function_categories:
            return cat in per_function_categories[func_name]
    return cat in relevant_categories


class FunctionCharacteristicVisitor(ast.NodeVisitor):
    """AST visitor to extract structural characteristics for mutation policy."""

    def __init__(self):
        self.branch_count = 0
        self.has_strings = False
        self.has_numbers = False

    def _count_branch(self, node: ast.AST) -> None:
        self.branch_count += 1
        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        self._count_branch(node)

    def visit_While(self, node: ast.While):
        self._count_branch(node)

    def visit_For(self, node: ast.For):
        self._count_branch(node)

    def visit_BoolOp(self, node: ast.BoolOp):
        # and/or also represent branching/logic complexity
        self._count_branch(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            self.has_strings = True
        elif isinstance(node.value, (int, float)):
            self.has_numbers = True

    def visit_Str(self, node: ast.Str):  # Support Python < 3.8
        self.has_strings = True

    def visit_Num(self, node: ast.Num):  # Support Python < 3.8
        self.has_numbers = True
