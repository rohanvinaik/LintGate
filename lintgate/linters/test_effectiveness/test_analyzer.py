"""Per-function test effectiveness computation.

Combines assertion classification (from assertion_classifier) with
test-to-source mapping (from source_mapper) to compute per-function
effectiveness scores.
"""

from __future__ import annotations

import ast
import os
from typing import Any

from .assertion_classifier import classify_test_file_from_path
from .source_mapper import build_source_function_index, map_tests_to_source
from .types import (
    SEMANTIC_STRENGTH_THRESHOLD,
    STRENGTH_MAP,
    AssertionInfo,
    AssertionKind,
    FunctionEffectiveness,
    MappingDiagnostics,
)


def _discover_test_files(project_root: str, max_files: int | None = None) -> list[str]:
    """Discover test files in the project."""
    from lintgate.discovery import discover_project_files

    all_py = discover_project_files(project_root, limit=max_files)
    # Filter to test files only, excluding nested subprojects (#69)
    test_files: list[str] = []
    root = os.path.abspath(project_root)
    for f in all_py:
        if not os.path.basename(f).startswith("test_"):
            continue
        # (#69) Skip nested subprojects
        rel = os.path.relpath(f, root)
        parts = rel.split(os.sep)[:-1]  # directory parts
        if any(
            os.path.exists(os.path.join(root, *parts[:i + 1], marker))
            for i in range(1, len(parts))
            for marker in ("pyproject.toml", "setup.py")
        ):
            continue
        test_files.append(f)
        if max_files is not None and len(test_files) >= max_files:
            break

    return test_files


def _discover_source_files(
    project_root: str, max_files: int | None = None
) -> list[str]:
    """Discover non-test Python source files."""
    from lintgate.discovery import discover_project_files

    all_py = discover_project_files(project_root, limit=max_files)
    source_files: list[str] = []
    root = os.path.abspath(project_root)
    for f in all_py:
        basename = os.path.basename(f)
        if basename.startswith("test_") or basename.endswith("_test.py"):
            continue
        # Skip "tests" directories
        if os.sep + "tests" + os.sep in f:
            continue
        # (#69) Skip nested subprojects
        rel = os.path.relpath(f, root)
        parts = rel.split(os.sep)[:-1]
        if any(
            os.path.exists(os.path.join(root, *parts[:i + 1], marker))
            for i in range(1, len(parts))
            for marker in ("pyproject.toml", "setup.py")
        ):
            continue
        source_files.append(f)
        if max_files is not None and len(source_files) >= max_files:
            break

    return source_files


def _extract_public_functions(filepath: str) -> list[str]:
    """Extract public function/method names from a source file."""
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (SyntaxError, OSError):
        return []

    names: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._class_stack.append(node.name)
            self.generic_visit(node)
            self._class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._handle(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._handle(node)

        def _handle(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            if node.name.startswith("_") and not node.name.startswith("__"):
                return
            if self._class_stack:
                names.append(f"{self._class_stack[-1]}.{node.name}")
            else:
                names.append(node.name)

    visitor = _Visitor()
    visitor.visit(tree)
    return names


def detect_sentinel_patterns(
    assertions: list[AssertionInfo], func_name: str
) -> tuple[list[AssertionInfo], set[str], set[str]]:
    """Apply sentinel pairing and return-type inference."""
    semantic_roots = set()
    for a in assertions:
        if a.strength >= SEMANTIC_STRENGTH_THRESHOLD:
            semantic_roots.add(a.target_root)

    updated_assertions = []
    sentinel_targets = set()
    for a in assertions:
        new_a = a
        if a.kind == AssertionKind.IS_NOT_NONE:
            sentinel_targets.add(a.target_root)
            if a.target_root in semantic_roots:
                new_a.strength = 0.5
        elif a.kind == AssertionKind.IS_NONE:
            sentinel_targets.add(a.target_root)
        updated_assertions.append(new_a)

    if len(updated_assertions) == 1:
        a = updated_assertions[0]
        if a.kind in (AssertionKind.IS_NONE, AssertionKind.IS_NOT_NONE):
            name_match = any(
                p in func_name.lower() or p in a.target_expression.lower()
                for p in ("check_", "validate_", "detect_")
            )
            if name_match:
                a.kind = AssertionKind.SENTINEL_CHECK
                a.strength = STRENGTH_MAP[AssertionKind.SENTINEL_CHECK]
                a.confidence = "heuristic"

    return updated_assertions, sentinel_targets, semantic_roots


def detect_isolated_sentinels(
    assertions: list[AssertionInfo],
    sentinel_targets: set[str],
    semantic_roots: set[str],
) -> list[dict[str, Any]]:
    """Detect isolated sentinels and generate warnings."""
    warnings: list[dict[str, Any]] = []
    for root in sentinel_targets:
        if (
            root
            and root not in semantic_roots
            and root not in ("True", "False", "None")
        ):
            guard_line = next(
                (
                    a.line
                    for a in assertions
                    if a.kind in (AssertionKind.IS_NOT_NONE, AssertionKind.IS_NONE)
                    and a.target_root == root
                ),
                None,
            )
            msg = f"Anti-pattern: isolated sentinel guard on '{root}'. No semantic value checks found for this target."
            warnings.append(
                {
                    "kind": "isolated_sentinel",
                    "message": msg,
                    "remediation": f"Verify the state of '{root}' after checking existence.",
                    "missing_followup_pattern": {
                        "expected_followup": f"assert {root}.<field> == <value>",
                        "guard_line": guard_line,
                    },
                }
            )
    return warnings


def detect_hasattr_chains(assertions: list[AssertionInfo]) -> list[dict[str, Any]]:
    """Detect hasattr chain anti-patterns."""
    warnings: list[dict[str, Any]] = []
    hasattr_chains: dict[str, list[int]] = {}
    current_target_expr = None
    current_lines = []

    for a in assertions:
        if a.kind == AssertionKind.HASATTR_CHECK:
            if a.target_expression == current_target_expr:
                current_lines.append(a.line)
            else:
                if len(current_lines) >= 3:
                    hasattr_chains[current_target_expr] = current_lines[:]  # type: ignore
                current_target_expr = a.target_expression
                current_lines = [a.line]
        else:
            if len(current_lines) >= 3:
                hasattr_chains[current_target_expr] = current_lines[:]  # type: ignore
            current_target_expr = None
            current_lines = []

    if len(current_lines) >= 3:
        hasattr_chains[current_target_expr] = current_lines[:]  # type: ignore

    for target, lines in hasattr_chains.items():
        warnings.append(
            {
                "kind": "hasattr_chain",
                "message": f"Anti-pattern: chain of {len(lines)} hasattr checks on '{target}' (lines {lines[0]}-{lines[-1]}).",
                "remediation": f"Replace with attribute equality: assert {target}.field == expected",
            }
        )
    return warnings


def analyze_function_effectiveness(
    func_name: str,
    assertions: list[AssertionInfo],
    derivation_methods: list[str] | None = None,
) -> tuple[FunctionEffectiveness, list[dict[str, str]]]:
    """Analyze a single test function's assertions."""
    updated_assertions, sentinel_targets, semantic_roots = detect_sentinel_patterns(
        assertions, func_name
    )

    warnings = detect_isolated_sentinels(
        updated_assertions, sentinel_targets, semantic_roots
    )
    warnings.extend(detect_hasattr_chains(updated_assertions))

    has_isolated_sentinel = any(w["kind"] == "isolated_sentinel" for w in warnings)

    anti_patterns = []
    is_structural_only = all(
        a.kind
        in (
            AssertionKind.HASATTR_CHECK,
            AssertionKind.ISINSTANCE_CHECK,
            AssertionKind.IS_TRUE,
            AssertionKind.IS_NONE,
            AssertionKind.IS_NOT_NONE,
        )
        for a in updated_assertions
    )
    has_hasattr = any(a.kind == AssertionKind.HASATTR_CHECK for a in updated_assertions)
    if is_structural_only and has_hasattr and len(updated_assertions) > 0:
        anti_patterns.append(
            {
                "function": func_name,
                "reason": "Exclusively hasattr/isinstance checks with no value assertions.",
                "remediation": "This test verifies interface existence but not state. Add equality assertions.",
            }
        )

    fe = FunctionEffectiveness(
        function_name=func_name, test_count=1, assertions=updated_assertions
    )
    fe.compute_scores(
        derivation_methods=derivation_methods,
        has_isolated_sentinel=has_isolated_sentinel,
    )
    # Merge sentinel/hasattr warnings with structural anti-patterns into a single list
    all_warnings = warnings + anti_patterns
    return fe, all_warnings


def analyze_effectiveness(
    project_root: str,
    source_files: list[str] | None = None,
    test_files: list[str] | None = None,
    effective_weights: dict[AssertionKind, float] | None = None,
) -> tuple[dict[str, FunctionEffectiveness], MappingDiagnostics]:
    """Analyze test effectiveness for all source functions in a project.

    Args:
        project_root: Project root path.
        source_files: Optional list of source files. Auto-discovered if None.
        test_files: Optional list of test files. Auto-discovered if None.
        effective_weights: Optional calibrated weights to override STRENGTH_MAP.

    Returns:
        Tuple of (Mapping of 'relpath::function' → FunctionEffectiveness, MappingDiagnostics).
    """
    if source_files is None:
        source_files = _discover_source_files(project_root)
    if test_files is None:
        test_files = _discover_test_files(project_root)

    diagnostics = MappingDiagnostics()

    if not source_files or not test_files:
        return {}, diagnostics

    # Build source function index
    source_index = build_source_function_index(source_files)

    # Classify assertions in all test files
    all_test_assertions: dict[str, list[AssertionInfo]] = {}
    for tf in test_files:
        file_assertions = classify_test_file_from_path(tf)
        all_test_assertions.update(file_assertions)

    # Map tests to source functions
    source_to_tests: dict[str, list[str]] = {}
    for tf in test_files:
        file_mapping = map_tests_to_source(
            tf, source_index, project_root, diagnostics=diagnostics
        )
        for src_key, mapped_tests in file_mapping.items():
            source_to_tests.setdefault(src_key, []).extend(mapped_tests)

    # Build effectiveness for each source function
    results: dict[str, FunctionEffectiveness] = {}

    # Get all public functions across all source files
    all_public_functions: list[tuple[str, str]] = []  # (func_name, file_path)
    for sf in source_files:
        for func_name in _extract_public_functions(sf):
            all_public_functions.append((func_name, sf))

    for func_name, filepath in all_public_functions:
        relpath = os.path.relpath(filepath, project_root)
        unique_key = f"{relpath}::{func_name}"

        test_funcs = source_to_tests.get(unique_key, [])
        # Deduplicate
        test_funcs = list(dict.fromkeys(test_funcs))

        # Collect all assertions from mapped tests
        assertions: list[AssertionInfo] = []
        for test_func in test_funcs:
            assertions.extend(all_test_assertions.get(test_func, []))

        # (#86) Apply effective weights if provided
        if effective_weights:
            for a in assertions:
                if a.kind in effective_weights:
                    a.strength = effective_weights[a.kind]

        fe, _ = analyze_function_effectiveness(func_name, assertions)
        fe.test_count = len(test_funcs)
        # Recalculate with correct test count if needed (though compute_scores doesn't use test_count currently)
        results[unique_key] = fe

    return results, diagnostics
