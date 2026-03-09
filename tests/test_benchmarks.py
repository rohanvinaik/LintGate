"""Performance benchmarks.

Measures wall-clock time of core operations: lint pipeline, report
formatting, purity analysis, property classification, and manifest
building. Regressions beyond 200% trigger a CI alert.

Requires: pip install pytest-benchmark
Run locally: pytest tests/test_benchmarks.py --benchmark-only
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(
    reason="benchmarks — run with: pytest tests/test_benchmarks.py --benchmark-only"
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CODE = textwrap.dedent("""\
    import os
    from pathlib import Path

    def pure_add(a: int, b: int) -> int:
        return a + b

    def pure_multiply(x: int, y: int) -> int:
        return x * y

    def impure_read(path: str) -> str:
        return Path(path).read_text()

    def loop_example(items: list[int]) -> list[int]:
        result = []
        for item in items:
            if item > 0:
                result.append(item * 2)
        return result

    class Calculator:
        def add(self, a, b):
            return a + b

        def subtract(self, a, b):
            return a - b
""")


@pytest.fixture
def sample_tree():
    return ast.parse(SAMPLE_CODE)


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_CODE)
    return f


@pytest.fixture
def project_python_files():
    """Collect actual lintgate source files for real-world benchmarks."""
    root = Path(__file__).resolve().parent.parent / "lintgate"
    return sorted(str(f) for f in root.rglob("*.py") if f.stat().st_size > 0)


# ---------------------------------------------------------------------------
# Lint pipeline benchmarks
# ---------------------------------------------------------------------------


def test_bench_lint_single_file(benchmark, sample_file, tmp_path):
    """Lint a single Python file through the ruff linter.

    Target: <500ms for a single file.
    """
    from lintgate.linters.ruff_linter import RuffLinter
    from lintgate.types import LinterContext

    linter = RuffLinter()
    ctx = LinterContext(files=[str(sample_file)], project_root=str(tmp_path))
    benchmark(lambda: list(linter.run(ctx)))


def test_bench_format_report(benchmark):
    """Format a lint report with typical issue counts."""
    from lintgate.agent_reporter import format_report
    from lintgate.types import AggregatedResult, LintIssue

    issues = [
        LintIssue(
            linter="ruff",
            kind=f"E{i}",
            message=f"issue {i}",
            file="/tmp/test.py",
            line=i,
            severity="warning",
        )
        for i in range(1, 21)
    ]
    result = AggregatedResult(
        warnings=issues,
        metrics={
            "total_issues": 20,
            "blocking_count": 0,
            "warning_count": 20,
            "linters_skipped": 0,
            "linters_errored": 0,
            "fixable_count": 5,
        },
        tier_used="tier_2_logic",
        tier_reason="Logic change",
        files_linted=["/tmp/test.py"],
    )
    benchmark(format_report, result)


# ---------------------------------------------------------------------------
# Algebraic analysis benchmarks
# ---------------------------------------------------------------------------


def test_bench_purity_analysis(benchmark, sample_tree):
    """Detect pure vs impure functions via AST analysis."""
    from lintgate.linters.performance_checks.purity import analyze_purity

    benchmark(analyze_purity, sample_tree)


def test_bench_property_classification(benchmark, sample_tree):
    """Classify algebraic properties (commutative, associative, etc.)."""
    from lintgate.linters.performance_checks.properties import classify_properties
    from lintgate.linters.performance_checks.purity import analyze_purity

    purity = analyze_purity(sample_tree)
    # Find a pure function node + its purity result
    pure_pairs = [
        (node, purity[node.name])
        for node in ast.walk(sample_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in purity
        and purity[node.name].is_pure
    ]
    assert pure_pairs, "Need at least one pure function for benchmark"
    func_node, purity_result = pure_pairs[0]
    benchmark(classify_properties, func_node, purity_result)


def test_bench_manifest_build(benchmark, tmp_path, sample_file):
    """Build the full algebraic property manifest for a project."""
    from lintgate.linters.performance_checks.manifest import build_manifest

    benchmark(build_manifest, str(tmp_path), [str(sample_file)])
