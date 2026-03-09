"""Contract drift detector — detection functions.

Extracted from contract_drift_detector.py to keep the main module under
the 400-line limit.  All symbols are re-exported from the parent module.
"""

from __future__ import annotations

import ast

from ._contract_drift_types import (
    AffectedTestSite,
    SignatureChange,
    _extract_function_params,
    _extract_function_return_arities,
    _filepath_to_module,
    _find_function_line,
)


def detect_return_arity_change(
    filepath: str,
    old_source: str,
    new_source: str,
) -> list[SignatureChange]:
    """Detect functions whose return arity changed between old and new source.

    Compares the return tuple length of each function in old vs new source.
    Only reports changes where both old and new return tuples (not None/scalar).
    """
    changes: list[SignatureChange] = []

    try:
        old_tree = ast.parse(old_source, filename=filepath)
        new_tree = ast.parse(new_source, filename=filepath)
    except SyntaxError:
        return changes

    old_funcs = _extract_function_return_arities(old_tree)
    new_funcs = _extract_function_return_arities(new_tree)

    for func_name, old_arity in old_funcs.items():
        new_arity = new_funcs.get(func_name)
        if new_arity is None or old_arity == new_arity:
            continue
        # Both versions return tuples but with different arity
        changes.append(
            SignatureChange(
                module=_filepath_to_module(filepath),
                function=func_name,
                file=filepath,
                line=_find_function_line(new_tree, func_name),
                change_type="return_arity",
                old_value=old_arity,
                new_value=new_arity,
            )
        )

    return changes


def detect_param_changes(
    filepath: str,
    old_source: str,
    new_source: str,
) -> list[SignatureChange]:
    """Detect functions whose parameter lists changed between old and new source."""
    changes: list[SignatureChange] = []

    try:
        old_tree = ast.parse(old_source, filename=filepath)
        new_tree = ast.parse(new_source, filename=filepath)
    except SyntaxError:
        return changes

    old_funcs = _extract_function_params(old_tree)
    new_funcs = _extract_function_params(new_tree)

    for func_name, old_params in old_funcs.items():
        new_params = new_funcs.get(func_name)
        if new_params is None or old_params == new_params:
            continue

        added = new_params - old_params
        removed = old_params - new_params

        if added:
            changes.append(
                SignatureChange(
                    module=_filepath_to_module(filepath),
                    function=func_name,
                    file=filepath,
                    line=_find_function_line(new_tree, func_name),
                    change_type="param_added",
                    old_value=sorted(old_params),
                    new_value=sorted(new_params),
                )
            )
        if removed:
            changes.append(
                SignatureChange(
                    module=_filepath_to_module(filepath),
                    function=func_name,
                    file=filepath,
                    line=_find_function_line(new_tree, func_name),
                    change_type="param_removed",
                    old_value=sorted(old_params),
                    new_value=sorted(new_params),
                )
            )

    return changes


def find_affected_test_sites(
    change: SignatureChange,
    test_files: list[str],
) -> list[AffectedTestSite]:
    """Find test call sites affected by a signature change.

    For return arity changes: finds tuple unpackings where LHS arity
    matches the old return arity (and thus mismatches the new one).

    For parameter changes: finds call sites that will get TypeError.
    """
    sites: list[AffectedTestSite] = []

    for test_file in test_files:
        try:
            with open(test_file, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=test_file)
        except (OSError, SyntaxError):
            continue

        if change.change_type == "return_arity":
            sites.extend(
                _find_unpack_mismatches(tree, test_file, change.function, change.old_value)
            )
        elif change.change_type in ("param_added", "param_removed"):
            sites.extend(_find_call_sites(tree, test_file, change.function))

    return sites


def _find_unpack_mismatches(
    tree: ast.AST,
    test_file: str,
    func_name: str,
    old_arity: int,
) -> list[AffectedTestSite]:
    """Find tuple unpackings that call func_name with old_arity targets."""
    sites: list[AffectedTestSite] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # Check for tuple unpacking on LHS
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Tuple):
            continue

        # Check RHS is a call to the function
        if not isinstance(node.value, ast.Call):
            continue
        call_name = _get_call_name(node.value)
        if not call_name or not call_name.endswith(func_name):
            continue

        arity = len(target.elts)
        if arity == old_arity:
            sites.append(
                AffectedTestSite(
                    test_file=test_file,
                    line=node.lineno,
                    unpacking_arity=arity,
                    call_expression=call_name,
                )
            )

    return sites


def _find_call_sites(
    tree: ast.AST,
    test_file: str,
    func_name: str,
) -> list[AffectedTestSite]:
    """Find all call sites of a function in a test file."""
    sites: list[AffectedTestSite] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _get_call_name(node)
        if not call_name or not call_name.endswith(func_name):
            continue

        sites.append(
            AffectedTestSite(
                test_file=test_file,
                line=node.lineno,
                call_expression=call_name,
            )
        )

    return sites


def _get_call_name(node: ast.Call) -> str:
    """Extract the function name from a Call node."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = [func.attr]
        current = func.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""
