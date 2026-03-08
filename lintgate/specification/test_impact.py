"""Test-impact mapping — skip irrelevant tests during mutation evaluation.

Phase 1 (static): Parse test file imports and AST, map test functions
to source functions they reference. Reuses the same heuristic as
ledger.py:_build_test_coverage_map but returns structured pairs.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class TestImpactMap:
    """Mapping from source functions to their covering test functions."""

    function_to_tests: dict[str, list[TestReference]] = field(default_factory=dict)
    total_test_functions: int = 0
    total_source_functions_covered: int = 0

    def tests_for(self, func_name: str) -> list[TestReference]:
        """Get test references for a function name."""
        return self.function_to_tests.get(func_name, [])

    def to_dict(self) -> dict:
        return {
            "total_test_functions": self.total_test_functions,
            "total_source_functions_covered": self.total_source_functions_covered,
            "mappings": {k: [t.to_dict() for t in v] for k, v in self.function_to_tests.items()},
        }


@dataclass
class TestReference:
    """A reference from a test function to a source function."""

    test_file: str
    test_function: str

    def to_dict(self) -> dict:
        return {"test_file": self.test_file, "test_function": self.test_function}


def build_test_impact_map(test_files: list[str]) -> TestImpactMap:
    """Build a test impact map from test file ASTs.

    Scans test functions for calls to source functions and builds
    a mapping from source function name → list of (test_file, test_func).
    """
    impact = TestImpactMap()
    total_tests = 0

    for test_file in test_files:
        tests_in_file = _scan_test_file(test_file)
        total_tests += len(tests_in_file)

        for test_func_name, called_functions in tests_in_file.items():
            for called in called_functions:
                ref = TestReference(test_file=test_file, test_function=test_func_name)
                impact.function_to_tests.setdefault(called, []).append(ref)

    impact.total_test_functions = total_tests
    impact.total_source_functions_covered = len(impact.function_to_tests)
    return impact


def _scan_test_file(filepath: str) -> dict[str, list[str]]:
    """Scan a test file, return {test_func_name: [called_function_names]}."""
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (OSError, SyntaxError):
        return {}

    result: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            calls = _extract_calls(node)
            if calls:
                result[node.name] = calls
    return result


def _extract_calls(test_func: ast.FunctionDef) -> list[str]:
    """Extract function call names from a test function body."""
    names: list[str] = []
    for node in ast.walk(test_func):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name and not name.startswith("test_"):
            names.append(name)
    return names


def _call_name(node: ast.Call) -> str | None:
    """Extract call name from a Call node.

    For attribute calls (obj.method), returns the bare method name.
    The TestImpactMap.tests_for lookup uses bare names, so callers
    should also look up by bare name for consistency.
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None
