"""Performance check sub-modules — one per PERF00x check.

Each module exports a single check function with signature:
    (tree: ast.AST, file_path: str) -> Iterable[LintIssue]

The PerformanceChecker class in performance_checker.py orchestrates them.
"""
