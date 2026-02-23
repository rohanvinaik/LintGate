"""Per-function test effectiveness computation.

Combines assertion classification (from assertion_classifier) with
test-to-source mapping (from source_mapper) to compute per-function
effectiveness scores.
"""

from __future__ import annotations

import ast
import os

from .assertion_classifier import classify_test_file_from_path
from .source_mapper import build_source_function_index, map_tests_to_source
from .types import AssertionInfo, FunctionEffectiveness


def _discover_test_files(project_root: str, max_files: int = 200) -> list[str]:
    """Discover test files in the project."""
    test_files: list[str] = []
    root = os.path.abspath(project_root)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".")
            and d
            not in ("node_modules", "__pycache__", ".venv", "venv", "build", "dist", ".git", ".tox")
        ]
        for f in filenames:
            if f.startswith("test_") and f.endswith(".py"):
                test_files.append(os.path.join(dirpath, f))
                if len(test_files) >= max_files:
                    return test_files

    return test_files


def _discover_source_files(project_root: str, max_files: int = 500) -> list[str]:
    """Discover non-test Python source files."""
    source_files: list[str] = []
    root = os.path.abspath(project_root)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".")
            and d
            not in (
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
                "build",
                "dist",
                ".git",
                ".tox",
                "tests",
            )
        ]
        for f in filenames:
            if f.endswith(".py") and not f.startswith("test_"):
                source_files.append(os.path.join(dirpath, f))
                if len(source_files) >= max_files:
                    return source_files

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


def analyze_effectiveness(
    project_root: str,
    source_files: list[str] | None = None,
    test_files: list[str] | None = None,
) -> dict[str, FunctionEffectiveness]:
    """Analyze test effectiveness for all source functions in a project.

    Args:
        project_root: Project root path.
        source_files: Optional list of source files. Auto-discovered if None.
        test_files: Optional list of test files. Auto-discovered if None.

    Returns:
        Mapping of source function names → FunctionEffectiveness.
    """
    if source_files is None:
        source_files = _discover_source_files(project_root)
    if test_files is None:
        test_files = _discover_test_files(project_root)

    if not source_files or not test_files:
        return {}

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
        file_mapping = map_tests_to_source(tf, source_index)
        for src_func, test_funcs in file_mapping.items():
            source_to_tests.setdefault(src_func, []).extend(test_funcs)

    # Build effectiveness for each source function
    results: dict[str, FunctionEffectiveness] = {}

    # Get all public functions across all source files
    all_public_functions: dict[str, str] = {}  # func_name → file_path
    for sf in source_files:
        for func_name in _extract_public_functions(sf):
            all_public_functions[func_name] = sf

    for func_name, _filepath in all_public_functions.items():
        test_funcs = source_to_tests.get(func_name, [])
        # Deduplicate
        test_funcs = list(dict.fromkeys(test_funcs))

        # Collect all assertions from mapped tests
        assertions: list[AssertionInfo] = []
        for test_func in test_funcs:
            assertions.extend(all_test_assertions.get(test_func, []))

        fe = FunctionEffectiveness(
            function_name=func_name,
            test_count=len(test_funcs),
            assertions=assertions,
        )
        fe.compute_scores()
        results[func_name] = fe

    return results
