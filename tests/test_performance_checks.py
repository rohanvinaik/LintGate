"""Per-check unit tests for performance_checks/ sub-modules.

Each PERF00x check gets dedicated test cases covering:
- Positive detection (anti-pattern present → issue raised)
- Negative/skip cases (false-positive guardrails working)
- Edge cases specific to each check

Tests use inline source strings parsed via ast.parse(), not real files,
to keep fixtures self-contained and fast.
"""

from __future__ import annotations

import ast
import tempfile
import textwrap
from pathlib import Path

from lintgate.linters.performance_checks._helpers import (
    attach_parents,
    find_loop_bodies,
    get_constant_index,
    get_name,
    has_import,
)
from lintgate.linters.performance_checks.perf001_quadratic_membership import (
    check_quadratic_membership,
)
from lintgate.linters.performance_checks.perf002_recompile import (
    check_recompile_in_function,
)
from lintgate.linters.performance_checks.perf003_sorted_first_last import (
    check_sorted_first_last,
)
from lintgate.linters.performance_checks.perf004_string_concat import (
    check_string_concat_in_loop,
)
from lintgate.linters.performance_checks.perf005_unnecessary_list_wrap import (
    check_unnecessary_list_wrap,
)
from lintgate.linters.performance_checks.perf006_dict_keys import (
    check_dict_keys_iteration,
)
from lintgate.linters.performance_checks.perf007_numerical_loop import (
    check_numerical_loop,
)
from lintgate.linters.performance_checks.perf008_sequential_io import (
    check_sequential_io_in_loop,
)
from lintgate.linters.performance_checks.perf009_multi_pass import check_multi_pass
from lintgate.linters.performance_checks.perf010_unnecessary_materialization import (
    check_unnecessary_materialization,
)


def _parse_and_check(source: str, check_fn):
    """Parse source, attach parents, and run a check function."""
    source = textwrap.dedent(source)
    tree = ast.parse(source, filename="<test>")
    attach_parents(tree)
    return list(check_fn(tree, "<test>"))


# ─── Shared helpers tests ────────────────────────────────────────────


class TestHelpers:
    def test_get_constant_index_positive(self):
        assert get_constant_index(ast.Constant(value=0)) == 0
        assert get_constant_index(ast.Constant(value=5)) == 5

    def test_get_constant_index_negative(self):
        node = ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=1))
        assert get_constant_index(node) == -1

    def test_get_constant_index_none(self):
        assert get_constant_index(ast.Name(id="x")) is None

    def test_find_loop_bodies(self):
        tree = ast.parse("for x in range(10):\n    pass\nwhile True:\n    break")
        loops = find_loop_bodies(tree)
        assert len(loops) == 2

    def test_has_import_true(self):
        tree = ast.parse("import numpy\nimport os")
        assert has_import(tree, "numpy") is True

    def test_has_import_false(self):
        tree = ast.parse("import os")
        assert has_import(tree, "numpy") is False

    def test_has_import_from(self):
        tree = ast.parse("from numpy import array")
        assert has_import(tree, "numpy") is True

    def test_get_name_simple(self):
        assert get_name(ast.Name(id="foo")) == "foo"

    def test_get_name_attribute(self):
        node = ast.Attribute(value=ast.Name(id="foo"), attr="bar")
        assert get_name(node) == "foo.bar"


# ─── PERF001: Quadratic membership ──────────────────────────────────


class TestPERF001:
    def test_detects_list_in_loop(self):
        issues = _parse_and_check(
            """
            items = [1, 2, 3, 4, 5, 6]
            for x in range(100):
                if x in items:
                    pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF001"

    def test_skips_set_target(self):
        issues = _parse_and_check(
            """
            items = set([1, 2, 3])
            for x in range(100):
                if x in items:
                    pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 0

    def test_skips_dict_target(self):
        issues = _parse_and_check(
            """
            items = {"a": 1, "b": 2}
            for x in range(100):
                if x in items:
                    pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 0

    def test_skips_small_constant_list(self):
        issues = _parse_and_check(
            """
            items = [1, 2, 3]
            for x in range(100):
                if x in items:
                    pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 0

    def test_skips_mutated_container(self):
        issues = _parse_and_check(
            """
            items = [1, 2, 3, 4, 5, 6]
            for x in range(100):
                if x in items:
                    items.append(x)
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 0

    def test_skips_non_name_target(self):
        """Inline expressions like `x in [1,2,3]` are not flagged."""
        issues = _parse_and_check(
            """
            for x in range(100):
                if x in [1, 2, 3]:
                    pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 0

    def test_skips_range_target(self):
        issues = _parse_and_check(
            """
            items = range(1000)
            for x in range(100):
                if x in items:
                    pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 0

    def test_untyped_param_low_confidence(self):
        """Untyped function parameter → confidence 0.25."""
        issues = _parse_and_check(
            """
            def process(items):
                for x in range(100):
                    if x in items:
                        pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 1
        assert issues[0].confidence == 0.25
        assert "untyped parameter" in issues[0].message
        assert issues[0].evidence["param_type"] == "untyped"

    def test_no_param_medium_confidence(self):
        """Module-level variable (not a param) → confidence 0.50."""
        issues = _parse_and_check(
            """
            items = [1, 2, 3, 4, 5, 6]
            for x in range(100):
                if x in items:
                    pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 1
        assert issues[0].confidence == 0.50
        assert issues[0].evidence["param_type"] is None

    def test_typed_slow_full_confidence(self):
        """list-typed param → confidence 0.60."""
        issues = _parse_and_check(
            """
            def process(items: list):
                for x in range(100):
                    if x in items:
                        pass
        """,
            check_quadratic_membership,
        )
        assert len(issues) == 1
        assert issues[0].confidence == 0.60
        assert issues[0].evidence["param_type"] == "typed_slow"


# ─── PERF002: re.compile inside function ─────────────────────────────


class TestPERF002:
    def test_detects_compile_in_function(self):
        issues = _parse_and_check(
            """
            import re
            def process(text):
                pattern = re.compile(r"\\d+")
                return pattern.findall(text)
        """,
            check_recompile_in_function,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF002"

    def test_skips_module_level_compile(self):
        issues = _parse_and_check(
            """
            import re
            PATTERN = re.compile(r"\\d+")
            def process(text):
                return PATTERN.findall(text)
        """,
            check_recompile_in_function,
        )
        assert len(issues) == 0

    def test_skips_cached_function(self):
        issues = _parse_and_check(
            """
            import re
            from functools import lru_cache
            @lru_cache
            def get_pattern():
                return re.compile(r"\\d+")
        """,
            check_recompile_in_function,
        )
        assert len(issues) == 0

    def test_skips_non_constant_pattern(self):
        issues = _parse_and_check(
            """
            import re
            def process(pattern_str):
                return re.compile(pattern_str)
        """,
            check_recompile_in_function,
        )
        assert len(issues) == 0


# ─── PERF003: sorted()[0] / sorted()[-1] ─────────────────────────────


class TestPERF003:
    def test_detects_sorted_first(self):
        issues = _parse_and_check(
            """
            x = sorted(items)[0]
        """,
            check_sorted_first_last,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF003"
        assert "min" in issues[0].message

    def test_detects_sorted_last(self):
        issues = _parse_and_check(
            """
            x = sorted(items)[-1]
        """,
            check_sorted_first_last,
        )
        assert len(issues) == 1
        assert "max" in issues[0].message

    def test_skips_sorted_middle(self):
        issues = _parse_and_check(
            """
            x = sorted(items)[3]
        """,
            check_sorted_first_last,
        )
        assert len(issues) == 0

    def test_skips_sorted_no_index(self):
        issues = _parse_and_check(
            """
            x = sorted(items)
        """,
            check_sorted_first_last,
        )
        assert len(issues) == 0


# ─── PERF004: String concat in loop ──────────────────────────────────


class TestPERF004:
    def test_detects_string_concat(self):
        issues = _parse_and_check(
            """
            result = ""
            for item in items:
                result += " "
                x = 1
                y = 2
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF004"

    def test_detects_fstring_concat(self):
        issues = _parse_and_check(
            """
            result = ""
            for item in items:
                result += f"{item}"
                x = 1
                y = 2
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) == 1

    def test_skips_small_loop(self):
        """Loops with 1-2 statements are fine."""
        issues = _parse_and_check(
            """
            result = ""
            for item in items:
                result += " "
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) == 0

    def test_skips_numeric_augassign(self):
        issues = _parse_and_check(
            """
            total = 0
            for item in items:
                total += 1
                x = 1
                y = 2
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) == 0

    def test_per_iteration_reset_low_confidence(self):
        """Accumulator consumed/reset in same iteration → confidence 0.40."""
        issues = _parse_and_check(
            """
            for item in items:
                msg = ""
                msg += f"prefix: "
                msg += f"{item}"
                log(msg)
                msg = ""
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) >= 1
        for issue in issues:
            assert issue.confidence == 0.40

    def test_per_iteration_reset_before_concat(self):
        """Per-iteration building: variable reset inside loop before += → confidence 0.40."""
        issues = _parse_and_check(
            """
            for item in items:
                msg = ""
                msg += f"Processing {item}"
                x = 1
                y = 2
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) >= 1
        for issue in issues:
            assert issue.confidence == 0.40

    def test_per_iteration_reset_string_literal(self):
        """Per-iteration building with string literal concat → confidence 0.40."""
        issues = _parse_and_check(
            """
            for item in items:
                line = "prefix: "
                line += " suffix"
                x = 1
                y = 2
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) >= 1
        for issue in issues:
            assert issue.confidence == 0.40

    def test_flags_cross_iteration_accumulation(self):
        """Cross-iteration: variable initialized before loop, accumulates."""
        issues = _parse_and_check(
            """
            result = ""
            for item in items:
                result += f"{item}"
                x = 1
                y = 2
        """,
            check_string_concat_in_loop,
        )
        assert len(issues) == 1
        assert issues[0].confidence == 0.9
        assert issues[0].kind == "PERF004"


# ─── PERF005: Unnecessary list() in for-loop ─────────────────────────


class TestPERF005:
    def test_detects_list_range(self):
        issues = _parse_and_check(
            """
            for x in list(range(10)):
                pass
        """,
            check_unnecessary_list_wrap,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF005"

    def test_detects_list_genexpr(self):
        issues = _parse_and_check(
            """
            for x in list(y for y in items):
                pass
        """,
            check_unnecessary_list_wrap,
        )
        assert len(issues) == 1

    def test_skips_bare_range(self):
        issues = _parse_and_check(
            """
            for x in range(10):
                pass
        """,
            check_unnecessary_list_wrap,
        )
        assert len(issues) == 0

    def test_skips_list_not_in_for(self):
        issues = _parse_and_check(
            """
            items = list(range(10))
        """,
            check_unnecessary_list_wrap,
        )
        assert len(issues) == 0


# ─── PERF006: dict .keys() iteration ─────────────────────────────────


class TestPERF006:
    def test_detects_dict_keys(self):
        issues = _parse_and_check(
            """
            d = {"a": 1}
            for k in d.keys():
                pass
        """,
            check_dict_keys_iteration,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF006"

    def test_skips_bare_dict_iter(self):
        issues = _parse_and_check(
            """
            d = {"a": 1}
            for k in d:
                pass
        """,
            check_dict_keys_iteration,
        )
        assert len(issues) == 0

    def test_skips_values(self):
        issues = _parse_and_check(
            """
            d = {"a": 1}
            for v in d.values():
                pass
        """,
            check_dict_keys_iteration,
        )
        assert len(issues) == 0


# ─── PERF007: Numerical loop ─────────────────────────────────────────


class TestPERF007:
    def test_detects_arithmetic_loop(self):
        issues = _parse_and_check(
            """
            result = [0] * 1000
            for i in range(n):
                result[i] = i * 2 + 1
        """,
            check_numerical_loop,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF007"

    def test_skips_small_range(self):
        issues = _parse_and_check(
            """
            for i in range(10):
                x = i * 2
        """,
            check_numerical_loop,
        )
        assert len(issues) == 0

    def test_skips_numpy_imported(self):
        issues = _parse_and_check(
            """
            import numpy as np
            for i in range(n):
                result[i] = i * 2
        """,
            check_numerical_loop,
        )
        assert len(issues) == 0

    def test_skips_no_arithmetic(self):
        issues = _parse_and_check(
            """
            for i in range(n):
                print(i)
        """,
            check_numerical_loop,
        )
        assert len(issues) == 0


# ─── PERF008: Sequential I/O in loop ─────────────────────────────────


class TestPERF008:
    def test_detects_requests_in_loop(self):
        issues = _parse_and_check(
            """
            for url in urls:
                response = requests.get(url)
        """,
            check_sequential_io_in_loop,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF008"

    def test_detects_open_variable_path_in_loop(self):
        issues = _parse_and_check(
            """
            for path in paths:
                f = open(path)
        """,
            check_sequential_io_in_loop,
        )
        assert len(issues) == 1

    def test_skips_open_constant_path(self):
        issues = _parse_and_check(
            """
            for i in range(10):
                f = open("config.json")
        """,
            check_sequential_io_in_loop,
        )
        assert len(issues) == 0

    def test_skips_non_requests_call(self):
        issues = _parse_and_check(
            """
            for item in items:
                result = process(item)
        """,
            check_sequential_io_in_loop,
        )
        assert len(issues) == 0


# ─── PERF009: Multi-pass iteration ───────────────────────────────────


class TestPERF009:
    def test_detects_multi_pass(self):
        issues = _parse_and_check(
            """
            for x in items:
                process(x)
            for y in items:
                other(y)
        """,
            check_multi_pass,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF009"

    def test_skips_single_pass(self):
        issues = _parse_and_check(
            """
            for x in items:
                process(x)
                other(x)
        """,
            check_multi_pass,
        )
        assert len(issues) == 0

    def test_skips_different_collections(self):
        issues = _parse_and_check(
            """
            for x in items:
                process(x)
            for y in others:
                other(y)
        """,
            check_multi_pass,
        )
        assert len(issues) == 0


# ─── PERF010: Unnecessary materialization ────────────────────────────


class TestPERF010:
    def test_detects_list_wrap_genexpr(self):
        issues = _parse_and_check(
            """
            result = list(x * 2 for x in range(1000))
            for item in result:
                pass
        """,
            check_unnecessary_materialization,
        )
        assert len(issues) == 1
        assert issues[0].kind == "PERF010"

    def test_skips_small_materialization(self):
        issues = _parse_and_check(
            """
            result = list(x * 2 for x in range(5))
        """,
            check_unnecessary_materialization,
        )
        assert len(issues) == 0


# ─── Integration: Full PerformanceChecker pipeline ────────────────────


class TestPerformanceCheckerIntegration:
    """Verify the orchestrator runs all checks through the real pipeline."""

    def test_checker_finds_issues_in_sample(self):
        """Run the full checker on a sample file with known anti-patterns."""
        from lintgate.linters.performance_checker import PerformanceChecker
        from lintgate.types import LinterContext

        source = textwrap.dedent("""\
            import re

            def process(data):
                pattern = re.compile(r"\\d+")
                items = [1, 2, 3, 4, 5, 6]
                for x in data:
                    if x in items:
                        pass
                return sorted(data)[0]
        """)

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            path = f.name

        try:
            checker = PerformanceChecker()
            ctx = LinterContext(
                files=[path],
                project_root=str(Path(path).parent),
                strictness="normal",
                config={},
            )
            issues = list(checker.run(ctx))
            kinds = {i.kind for i in issues}
            # Should find at least PERF001 (quadratic), PERF002 (recompile), PERF003 (sorted)
            assert "PERF001" in kinds
            assert "PERF002" in kinds
            assert "PERF003" in kinds
        finally:
            Path(path).unlink(missing_ok=True)

    def test_disabled_checks_respected(self):
        """Verify disabled_checks config is honored."""
        from lintgate.linters.performance_checker import PerformanceChecker
        from lintgate.types import LinterContext

        source = textwrap.dedent("""\
            x = sorted(data)[0]
        """)

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(source)
            f.flush()
            path = f.name

        try:
            checker = PerformanceChecker()
            ctx = LinterContext(
                files=[path],
                project_root=str(Path(path).parent),
                strictness="normal",
                config={"disabled_checks": ["PERF003"]},
            )
            issues = list(checker.run(ctx))
            assert all(i.kind != "PERF003" for i in issues)
        finally:
            Path(path).unlink(missing_ok=True)
