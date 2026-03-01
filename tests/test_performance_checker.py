"""Tests for performance_checker — 8 AST checks with false-positive guardrails."""

from __future__ import annotations

import textwrap

from lintgate.linters.performance_checker import PerformanceChecker
from lintgate.types import LinterContext, LintIssue


def _lint(
    source: str, tmp_path, *, disabled: list[str] | None = None
) -> list[LintIssue]:
    """Write source to a temp file, lint it, return issues."""
    f = tmp_path / "test_code.py"
    f.write_text(textwrap.dedent(source))
    checker = PerformanceChecker()
    ctx = LinterContext(
        files=[str(f)],
        project_root=str(tmp_path),
        config={"disabled_checks": disabled or []},
    )
    return list(checker.run(ctx))


# ─── PERF001: Quadratic membership ──────────────────────────────────────


class TestPERF001:
    def test_list_membership_in_loop(self, tmp_path):
        issues = _lint(
            """\
            big_list = list(range(1000))
            for item in other:
                if item in big_list:
                    pass
        """,
            tmp_path,
        )
        perf001 = [i for i in issues if i.kind == "PERF001"]
        assert len(perf001) >= 1
        assert "O(n²)" in perf001[0].message
        assert "set" in perf001[0].message

    def test_set_membership_no_flag(self, tmp_path):
        issues = _lint(
            """\
            big_set = set(range(1000))
            for item in other:
                if item in big_set:
                    pass
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF001"]

    def test_range_membership_no_flag(self, tmp_path):
        """Membership on range() is optimized; should not be flagged as list O(n²)."""
        issues = _lint(
            """\
            nums = range(1000)
            for item in other:
                if item in nums:
                    pass
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF001"]

    def test_small_constant_list_no_flag(self, tmp_path):
        issues = _lint(
            """\
            allowed = [1, 2, 3]
            for item in other:
                if item in allowed:
                    pass
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF001"]

    def test_mutated_list_in_loop_no_flag(self, tmp_path):
        """Loop-invariant guard: skip if container is mutated inside loop."""
        issues = _lint(
            """\
            items = list(range(1000))
            for x in source:
                if x in items:
                    items.append(x)
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF001"]

    def test_dict_target_no_flag(self, tmp_path):
        issues = _lint(
            """\
            lookup = dict(a=1, b=2)
            for item in other:
                if item in lookup:
                    pass
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF001"]

    def test_frozenset_target_no_flag(self, tmp_path):
        issues = _lint(
            """\
            valid = frozenset([1, 2, 3])
            for item in other:
                if item in valid:
                    pass
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF001"]

    def test_reassigned_list_no_flag(self, tmp_path):
        """If the list is reassigned inside the loop, skip."""
        issues = _lint(
            """\
            items = list(range(1000))
            for x in source:
                if x in items:
                    items = new_items()
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF001"]


# ─── PERF002: re.compile inside function ─────────────────────────────────


class TestPERF002:
    def test_recompile_in_function(self, tmp_path):
        issues = _lint(
            """\
            import re
            def process(text):
                pattern = re.compile(r"\\d+")
                return pattern.findall(text)
        """,
            tmp_path,
        )
        perf002 = [i for i in issues if i.kind == "PERF002"]
        assert len(perf002) >= 1
        assert "module level" in perf002[0].message

    def test_module_level_compile_no_flag(self, tmp_path):
        issues = _lint(
            """\
            import re
            PATTERN = re.compile(r"\\d+")
            def process(text):
                return PATTERN.findall(text)
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF002"]

    def test_lru_cache_no_flag(self, tmp_path):
        issues = _lint(
            """\
            import re
            from functools import lru_cache
            @lru_cache()
            def get_pattern():
                return re.compile(r"\\d+")
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF002"]

    def test_cache_decorator_no_flag(self, tmp_path):
        issues = _lint(
            """\
            import re
            from functools import cache
            @cache
            def get_pattern():
                return re.compile(r"\\d+")
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF002"]

    def test_variable_pattern_no_flag(self, tmp_path):
        """Non-constant pattern argument should not flag."""
        issues = _lint(
            """\
            import re
            def process(pattern_str):
                pattern = re.compile(pattern_str)
                return pattern
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF002"]


# ─── PERF003: sorted()[0] / sorted()[-1] ────────────────────────────────


class TestPERF003:
    def test_sorted_first(self, tmp_path):
        issues = _lint(
            """\
            result = sorted(data)[0]
        """,
            tmp_path,
        )
        perf003 = [i for i in issues if i.kind == "PERF003"]
        assert len(perf003) == 1
        assert "min" in perf003[0].message

    def test_sorted_last(self, tmp_path):
        issues = _lint(
            """\
            result = sorted(data)[-1]
        """,
            tmp_path,
        )
        perf003 = [i for i in issues if i.kind == "PERF003"]
        assert len(perf003) == 1
        assert "max" in perf003[0].message

    def test_sorted_middle_no_flag(self, tmp_path):
        """sorted(data)[3] should not flag."""
        issues = _lint(
            """\
            result = sorted(data)[3]
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF003"]


# ─── PERF004: String concat in loop ─────────────────────────────────────


class TestPERF004:
    def test_string_concat_in_loop(self, tmp_path):
        issues = _lint(
            """\
            result = ""
            for item in data:
                result += "prefix"
                do_something(result)
                do_more(result)
        """,
            tmp_path,
        )
        perf004 = [i for i in issues if i.kind == "PERF004"]
        assert len(perf004) >= 1
        assert "join" in perf004[0].message

    def test_fstring_concat_in_loop(self, tmp_path):
        issues = _lint(
            """\
            result = ""
            for item in data:
                result += f"value: {item}"
                do_something(result)
                do_more(result)
        """,
            tmp_path,
        )
        perf004 = [i for i in issues if i.kind == "PERF004"]
        assert len(perf004) >= 1

    def test_short_loop_no_flag(self, tmp_path):
        """Loops with 1-2 statements should not flag."""
        issues = _lint(
            """\
            result = ""
            for item in data:
                result += "x"
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF004"]


# ─── PERF005: Unnecessary list wrapping ──────────────────────────────────


class TestPERF005:
    def test_list_range_in_for(self, tmp_path):
        issues = _lint(
            """\
            for i in list(range(10)):
                print(i)
        """,
            tmp_path,
        )
        perf005 = [i for i in issues if i.kind == "PERF005"]
        assert len(perf005) == 1
        assert "range" in perf005[0].message

    def test_list_genexpr_in_for(self, tmp_path):
        issues = _lint(
            """\
            for x in list(i * 2 for i in data):
                print(x)
        """,
            tmp_path,
        )
        perf005 = [i for i in issues if i.kind == "PERF005"]
        assert len(perf005) == 1

    def test_list_for_random_access_no_flag(self, tmp_path):
        """list() not used in for-loop iteration should not flag."""
        issues = _lint(
            """\
            items = list(range(10))
            print(items[5])
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF005"]


# ─── PERF006: Dict keys iteration ───────────────────────────────────────


class TestPERF006:
    def test_dict_keys_iteration(self, tmp_path):
        issues = _lint(
            """\
            for k in my_dict.keys():
                print(k)
        """,
            tmp_path,
        )
        perf006 = [i for i in issues if i.kind == "PERF006"]
        assert len(perf006) == 1
        assert "Redundant" in perf006[0].message

    def test_dict_values_no_flag(self, tmp_path):
        issues = _lint(
            """\
            for v in my_dict.values():
                print(v)
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF006"]

    def test_dict_iteration_no_flag(self, tmp_path):
        """Direct iteration (no .keys()) should not flag."""
        issues = _lint(
            """\
            for k in my_dict:
                print(k)
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF006"]


# ─── PERF007: Numerical loop without vectorization ──────────────────────


class TestPERF007:
    def test_numerical_loop_without_numpy(self, tmp_path):
        issues = _lint(
            """\
            total = 0
            for i in range(1000000):
                total += i * i + 2 * i
        """,
            tmp_path,
        )
        perf007 = [i for i in issues if i.kind == "PERF007"]
        assert len(perf007) >= 1
        assert (
            "vectori" in perf007[0].message.lower()
            or "numpy" in perf007[0].message.lower()
        )

    def test_numpy_imported_no_flag(self, tmp_path):
        issues = _lint(
            """\
            import numpy as np
            total = 0
            for i in range(1000000):
                total += i * i
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF007"]

    def test_small_constant_bound_no_flag(self, tmp_path):
        """Small range bounds (< 100) should not flag."""
        issues = _lint(
            """\
            total = 0
            for i in range(10):
                total += i * 2
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF007"]

    def test_no_arithmetic_on_loop_var_no_flag(self, tmp_path):
        """Loop without arithmetic on the loop variable should not flag."""
        issues = _lint(
            """\
            for i in range(1000000):
                print("hello")
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF007"]

    def test_numba_imported_no_flag(self, tmp_path):
        issues = _lint(
            """\
            import numba
            total = 0
            for i in range(1000000):
                total += i * i
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF007"]

    def test_pandas_imported_no_flag(self, tmp_path):
        issues = _lint(
            """\
            import pandas
            total = 0
            for i in range(1000000):
                total += i * i
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF007"]


# ─── PERF008: Sequential I/O in loop ────────────────────────────────────


class TestPERF008:
    def test_requests_get_in_loop(self, tmp_path):
        issues = _lint(
            """\
            import requests
            for url in urls:
                response = requests.get(url)
        """,
            tmp_path,
        )
        perf008 = [i for i in issues if i.kind == "PERF008"]
        assert len(perf008) >= 1
        assert "requests" in perf008[0].message

    def test_requests_post_in_loop(self, tmp_path):
        issues = _lint(
            """\
            import requests
            for data in payloads:
                requests.post("http://api.example.com", json=data)
        """,
            tmp_path,
        )
        perf008 = [i for i in issues if i.kind == "PERF008"]
        assert len(perf008) >= 1

    def test_variable_open_in_loop(self, tmp_path):
        issues = _lint(
            """\
            for path in file_paths:
                with open(path) as f:
                    data = f.read()
        """,
            tmp_path,
        )
        perf008 = [i for i in issues if i.kind == "PERF008"]
        assert len(perf008) >= 1
        assert "open" in perf008[0].message.lower()

    def test_single_constant_open_no_flag(self, tmp_path):
        """open() with constant path in loop should not flag."""
        issues = _lint(
            """\
            for i in range(10):
                with open("config.json") as f:
                    data = f.read()
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF008"]

    def test_requests_outside_loop_no_flag(self, tmp_path):
        issues = _lint(
            """\
            import requests
            response = requests.get("http://example.com")
        """,
            tmp_path,
        )
        assert not [i for i in issues if i.kind == "PERF008"]


# ─── Graceful degradation ───────────────────────────────────────────────


class TestGracefulDegradation:
    def test_syntax_error_file_graceful(self, tmp_path):
        """SyntaxError → no crash, no issues."""
        f = tmp_path / "bad_syntax.py"
        f.write_text("def foo(\n  if broken\n")
        checker = PerformanceChecker()
        ctx = LinterContext(files=[str(f)], project_root=str(tmp_path))
        issues = list(checker.run(ctx))
        assert issues == []

    def test_decode_error_file_graceful(self, tmp_path):
        """Binary file → no crash, no issues."""
        f = tmp_path / "binary.py"
        f.write_bytes(b"\x80\x81\x82\x83\xff\xfe")
        checker = PerformanceChecker()
        ctx = LinterContext(files=[str(f)], project_root=str(tmp_path))
        issues = list(checker.run(ctx))
        assert issues == []

    def test_nonexistent_file_graceful(self, tmp_path):
        """Nonexistent file → no crash, no issues."""
        checker = PerformanceChecker()
        ctx = LinterContext(
            files=[str(tmp_path / "nope.py")], project_root=str(tmp_path)
        )
        issues = list(checker.run(ctx))
        assert issues == []


# ─── Dedupe ──────────────────────────────────────────────────────────────


class TestDedupe:
    def test_no_duplicate_findings_same_line(self, tmp_path):
        """Same anti-pattern on same line should only emit once."""
        issues = _lint(
            """\
            result = sorted(data)[0]
        """,
            tmp_path,
        )
        perf003 = [i for i in issues if i.kind == "PERF003"]
        assert len(perf003) == 1  # exactly 1, not more


# ─── Config ──────────────────────────────────────────────────────────────


class TestConfig:
    def test_disabled_checks_by_id(self, tmp_path):
        """disabled_checks: ["PERF007"] should suppress PERF007."""
        issues = _lint(
            """\
            total = 0
            for i in range(1000000):
                total += i * i + 2 * i
        """,
            tmp_path,
            disabled=["PERF007"],
        )
        assert not [i for i in issues if i.kind == "PERF007"]

    def test_disabled_multiple(self, tmp_path):
        """Multiple disabled checks."""
        issues = _lint(
            """\
            import re
            def process():
                p = re.compile(r"\\d+")
                result = sorted(data)[0]
        """,
            tmp_path,
            disabled=["PERF002", "PERF003"],
        )
        assert not [i for i in issues if i.kind in ("PERF002", "PERF003")]


# ─── Integration ─────────────────────────────────────────────────────────


class TestIntegration:
    def test_registers_in_registry(self):
        from lintgate.registry import build_registry
        from lintgate.types import ProjectConfig

        registry = build_registry(ProjectConfig())
        assert "performance_checker" in registry

    def test_in_tier2_linter_list(self):
        from lintgate.tier_selector import TIER_2_LINTERS

        assert "performance_checker" in TIER_2_LINTERS

    def test_in_tier3_linter_list(self):
        from lintgate.tier_selector import TIER_3_LINTERS

        assert "performance_checker" in TIER_3_LINTERS

    def test_issue_kind_uses_stable_id(self, tmp_path):
        """All kinds should be PERF001-PERF008, not prose names."""
        issues = _lint(
            """\
            import re
            def process(text):
                pattern = re.compile(r"\\d+")
                result = sorted(data)[0]
                return result
        """,
            tmp_path,
        )
        for issue in issues:
            assert issue.kind.startswith("PERF"), (
                f"Kind should be PERFxxx, got {issue.kind!r}"
            )
            assert issue.kind[4:].isdigit(), (
                f"Kind suffix should be digits: {issue.kind!r}"
            )

    def test_all_issues_have_fix_hints(self, tmp_path):
        """Every finding should have a concrete fix hint in suggestions."""
        issues = _lint(
            """\
            import re
            import requests
            big_list = list(range(1000))
            for item in other:
                if item in big_list:
                    pass
            def process():
                p = re.compile(r"\\d+")
                x = sorted(data)[0]
                result = ""
                for item in data:
                    result += "text"
                    do_one()
                    do_two()
            for i in list(range(10)):
                print(i)
            for k in my_dict.keys():
                print(k)
            for url in urls:
                requests.get(url)
        """,
            tmp_path,
        )
        for issue in issues:
            assert issue.suggestions, (
                f"{issue.kind} at line {issue.line} has no suggestions"
            )

    def test_checker_metadata(self):
        """Verify checker class metadata."""
        checker = PerformanceChecker()
        assert checker.name == "performance_checker"
        assert checker.tier == 2
        assert checker.timeout_ms == 3000
        assert checker.required_tool is None
        assert checker.available()
