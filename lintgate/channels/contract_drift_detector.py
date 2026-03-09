"""Contract drift detector — preemptive test adaptation for signature changes.

When a function's return arity, parameter list, or type annotation changes,
this module detects affected test call sites that will break.

Finding code: **TEFF010 — Contract drift: test unpacking mismatch**

Advisory only — emits warnings about test sites that need updating after
a function signature change.  Does NOT auto-fix (auto-fix is opt-in future work).
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignatureChange:
    """A detected change in a function's observable contract."""

    module: str  # e.g. "lintgate.controlplane.behavior_scoring"
    function: str  # e.g. "finalize"
    file: str  # absolute path
    line: int = 0
    change_type: str = ""  # "return_arity" | "param_added" | "param_removed"
    old_value: Any = None
    new_value: Any = None


@dataclass
class AffectedTestSite:
    """A test call site that will break due to a signature change."""

    test_file: str
    line: int
    unpacking_arity: int | None = None  # Number of targets in tuple unpack
    call_expression: str = ""  # e.g. "coord.finalize()"


@dataclass
class ContractDriftResult:
    """Result of contract drift analysis for a single function."""

    change: SignatureChange
    affected_sites: list[AffectedTestSite] = field(default_factory=list)
    advisory: str = ""


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


def analyze_contract_drift(
    filepath: str,
    old_source: str,
    new_source: str,
    test_files: list[str],
) -> list[ContractDriftResult]:
    """Full contract drift analysis: detect changes and find affected tests.

    Returns a list of ContractDriftResult, one per detected change.
    """
    results: list[ContractDriftResult] = []

    changes = detect_return_arity_change(filepath, old_source, new_source)
    changes.extend(detect_param_changes(filepath, old_source, new_source))

    for change in changes:
        affected = find_affected_test_sites(change, test_files)
        advisory = _build_advisory(change, affected)
        results.append(
            ContractDriftResult(
                change=change,
                affected_sites=affected,
                advisory=advisory,
            )
        )

    return results


# ── AST Helpers ──────────────────────────────────────────────────────


def _extract_function_return_arities(tree: ast.AST) -> dict[str, int]:
    """Extract return tuple arities for all functions in an AST.

    Returns dict of function_name → arity. Only includes functions
    that return tuples (explicit ast.Tuple in return statement).
    Skips functions with no return or scalar returns.
    """
    arities: dict[str, int] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Check return annotation first (most reliable)
        annotation_arity = _arity_from_annotation(node)
        if annotation_arity is not None:
            arities[node.name] = annotation_arity
            continue

        # Fall back to analyzing return statements
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]
        tuple_returns = [r for r in returns if isinstance(r.value, ast.Tuple)]

        if tuple_returns and isinstance(tuple_returns[0].value, ast.Tuple):
            # Use the first tuple return as representative
            arities[node.name] = len(tuple_returns[0].value.elts)

    return arities


def _arity_from_annotation(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int | None:
    """Extract return arity from type annotation like `-> tuple[A, B, C]`."""
    ann = node.returns
    if ann is None:
        return None

    # tuple[A, B, C] → Subscript(Name("tuple"), Tuple([A, B, C]))
    if not isinstance(ann, ast.Subscript):
        return None

    # Check it's `tuple`
    if (
        isinstance(ann.value, ast.Name)
        and ann.value.id == "tuple"
        and isinstance(ann.slice, ast.Tuple)
    ):
        return len(ann.slice.elts)

    return None


def _extract_function_params(tree: ast.AST) -> dict[str, set[str]]:
    """Extract parameter names for all functions in an AST.

    Returns dict of function_name → set of param names.
    Excludes 'self' and 'cls'.
    """
    params: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        names: set[str] = set()
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.arg not in ("self", "cls"):
                names.add(arg.arg)
        if node.args.vararg:
            names.add(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            names.add(f"**{node.args.kwarg.arg}")

        params[node.name] = names

    return params


def _find_function_line(tree: ast.AST, func_name: str) -> int:
    """Find the line number of a function definition."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node.lineno
    return 0


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


def _filepath_to_module(filepath: str) -> str:
    """Convert file path to module path (best-effort)."""
    # Strip .py extension and convert separators
    path = filepath.rstrip(".py") if filepath.endswith(".py") else filepath
    # Use basename parts after common roots
    parts = path.replace(os.sep, "/").split("/")
    # Find the first part that looks like a package
    for i, part in enumerate(parts):
        if part and not part.startswith("."):
            pkg_path = os.sep.join(parts[i:])
            if os.path.isfile(pkg_path + ".py") or os.path.isdir(pkg_path):
                return ".".join(parts[i:])
    return ".".join(parts[-3:]) if len(parts) >= 3 else ".".join(parts)


def _build_advisory(
    change: SignatureChange,
    affected: list[AffectedTestSite],
) -> str:
    """Build a human-readable advisory string."""
    if not affected:
        return ""

    if change.change_type == "return_arity":
        sites_str = ", ".join(f"{os.path.basename(s.test_file)}:{s.line}" for s in affected[:5])
        suffix = f" and {len(affected) - 5} more" if len(affected) > 5 else ""
        return (
            f"{change.function}() return arity: {change.old_value} → {change.new_value}. "
            f"{len(affected)} test site{'s' if len(affected) != 1 else ''} "
            f"unpack{'s' if len(affected) == 1 else ''} {change.old_value} values: "
            f"{sites_str}{suffix}."
        )

    if change.change_type == "param_added":
        old_set = set(change.old_value) if isinstance(change.old_value, list) else set()
        new_set = set(change.new_value) if isinstance(change.new_value, list) else set()
        added = sorted(new_set - old_set)
        return (
            f"{change.function}() gained parameter{'s' if len(added) != 1 else ''}: "
            f"{', '.join(added)}. "
            f"{len(affected)} test call site{'s' if len(affected) != 1 else ''} may need updating."
        )

    if change.change_type == "param_removed":
        old_set = set(change.old_value) if isinstance(change.old_value, list) else set()
        new_set = set(change.new_value) if isinstance(change.new_value, list) else set()
        removed = sorted(old_set - new_set)
        return (
            f"{change.function}() lost parameter{'s' if len(removed) != 1 else ''}: "
            f"{', '.join(removed)}. "
            f"{len(affected)} test call site{'s' if len(affected) != 1 else ''} may need updating."
        )

    return ""
