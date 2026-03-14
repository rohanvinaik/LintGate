"""Tests for lintgate.testing.fresh_validation."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import patch

import pytest

from lintgate.testing.fresh_validation import (
    _is_trivial_assert,
    count_test_assertions,
    run_fresh_kill_rates,
)

# ── count_test_assertions ─────────────────────────────────────────────


class TestCountTestAssertions:
    def test_counts_real_assertions(self, tmp_path):
        f = tmp_path / "test_a.py"
        f.write_text("def test_x():\n    assert 1 == 1\n    assert 'a' in 'ab'\n")
        assert count_test_assertions([str(f)]) == 2

    def test_excludes_trivial_assert_true(self, tmp_path):
        f = tmp_path / "test_b.py"
        f.write_text("def test_stub():\n    assert True\n")
        assert count_test_assertions([str(f)]) == 0

    def test_syntax_error_skipped(self, tmp_path):
        f = tmp_path / "test_bad.py"
        f.write_text("def (broken:\n")
        assert count_test_assertions([str(f)]) == 0

    def test_missing_file_skipped(self):
        assert count_test_assertions(["/no/such/file.py"]) == 0

    def test_empty_list(self):
        assert count_test_assertions([]) == 0


class TestIsTrivialAssert:
    def test_assert_true_is_trivial(self):
        node = cast("ast.Assert", ast.parse("assert True").body[0])
        assert _is_trivial_assert(node) is True

    def test_assert_false_not_trivial(self):
        node = cast("ast.Assert", ast.parse("assert False").body[0])
        assert _is_trivial_assert(node) is False

    def test_assert_expr_not_trivial(self):
        node = cast("ast.Assert", ast.parse("assert x == 1").body[0])
        assert _is_trivial_assert(node) is False


# ── Helpers for run_fresh_kill_rates ──────────────────────────────────

# Patch targets — late imports inside function body require patching at source
_STRATEGY = "lintgate.specification.test_regeneration_strategy.Strategy"
_LOAD_TESTS = "mcp_tools._mutation_impl._load_all_tests_from_files"
_RESOLVE = "mcp_tools._mutation_impl.resolve_function"
_PURITY = "mcp_tools._mutation_impl.detect_purity"
_SAMPLING = "lintgate.specification.mutation_engine.run_function_sampling"


class _FakeStrategy:
    AUTO_GENERATE_UNIT = "AUTO_GENERATE_UNIT"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass
class _Evidence:
    function_key: str = "mod::func"
    source_file: str = "src/mod.py"


@dataclass
class _FuncEntry:
    strategy: str = "AUTO_GENERATE_UNIT"
    evidence: _Evidence = field(default_factory=_Evidence)
    target_test_file: str = "tests/generated/test_mod.py"


@dataclass
class _Plan:
    functions: list[Any] = field(default_factory=list)


@dataclass
class _SamplingResult:
    survival_rate: float = 0.0
    total_mutants: int = 10
    total_killed: int = 10
    categories_tested: int = 4


# ── run_fresh_kill_rates ──────────────────────────────────────────────


class TestRunFreshKillRates:
    def test_empty_when_no_auto_targets(self):
        plan = _Plan(functions=[_FuncEntry(strategy="MANUAL_REVIEW")])
        with patch(_STRATEGY, _FakeStrategy):
            rates, zero, details = run_fresh_kill_rates(plan, "/project")  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert rates == []
        assert zero == 0
        assert details == []

    def test_no_generated_file(self, tmp_path):
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_missing.py")])
        with patch(_STRATEGY, _FakeStrategy):
            rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert rates == [0.0]
        assert zero == 1
        assert details[0]["status"] == "no_generated_file"

    def test_generated_dir_uses_staged_basename(self, tmp_path):
        staging = tmp_path / "workflow" / "staged"
        staging.mkdir(parents=True)
        (staging / "test_mod.py").write_text("def test_x(): assert 1\n")

        func_node = ast.parse("def func(): pass").body[0]
        result_obj = _SamplingResult(survival_rate=0.25)
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=("/abs/mod.py", func_node, None)),
            patch(_PURITY, return_value=True),
            patch(_SAMPLING, return_value=result_obj),
        ):
            rates, zero, details = run_fresh_kill_rates(
                plan,  # type: ignore[arg-type]
                str(tmp_path),
                generated_dir=str(staging),
            )
        assert rates[0] == pytest.approx(0.75)
        assert zero == 0
        assert details[0]["status"] == "sampled"

    def test_no_test_callables(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("# empty\n")

        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[]),
        ):
            rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert rates == [0.0]
        assert zero == 1
        assert details[0]["status"] == "no_test_callables"

    def test_source_unresolved(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert True\n")

        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=(None, None, "not found")),
        ):
            _rates, _zero, details = run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert len(details) == 1
        assert details[0]["status"] == "source_unresolved"

    def test_sampling_error(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert 1\n")

        func_node = ast.parse("def func(): pass").body[0]
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=("/abs/mod.py", func_node, None)),
            patch(_PURITY, return_value=True),
            patch(_SAMPLING, side_effect=RuntimeError("boom")),
        ):
            rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert rates == [0.0]
        assert zero == 1
        assert details[0]["status"] == "sampling_error"
        assert "boom" in details[0]["error"]

    def test_successful_sampling_pure(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert 1\n")

        func_node = ast.parse("def func(): pass").body[0]
        result_obj = _SamplingResult(survival_rate=0.3)
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=("/abs/mod.py", func_node, None)),
            patch(_PURITY, return_value=True),
            patch(_SAMPLING, return_value=result_obj),
        ):
            rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert len(rates) == 1
        assert rates[0] == pytest.approx(0.7, abs=0.01)
        assert zero == 0
        assert details[0]["status"] == "sampled"
        assert details[0]["is_pure"] is True

    def test_successful_sampling_impure_adds_state(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert 1\n")

        func_node = ast.parse("def func(): pass").body[0]
        result_obj = _SamplingResult(survival_rate=0.0)
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        captured_categories = {}

        def fake_sampling(_node, _key, cats, _tests, _orig, **_kw):
            captured_categories["cats"] = cats
            return result_obj

        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=("/abs/mod.py", func_node, None)),
            patch(_PURITY, return_value=False),
            patch(_SAMPLING, side_effect=fake_sampling),
        ):
            run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        from lintgate.specification.mutation_engine import MutationCategory

        assert MutationCategory.STATE in captured_categories["cats"]

    def test_zero_kill_counted(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert 1\n")

        func_node = ast.parse("def func(): pass").body[0]
        result_obj = _SamplingResult(survival_rate=1.0)  # 100% survive = 0% kill
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=("/abs/mod.py", func_node, None)),
            patch(_PURITY, return_value=True),
            patch(_SAMPLING, return_value=result_obj),
        ):
            rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert rates[0] <= 0.0
        assert zero == 1

    def test_async_function_accepted(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert 1\n")

        func_node = ast.parse("async def func(): pass").body[0]
        result_obj = _SamplingResult(survival_rate=0.5)
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=("/abs/mod.py", func_node, None)),
            patch(_PURITY, return_value=True),
            patch(_SAMPLING, return_value=result_obj),
        ):
            rates, _zero, _details = run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert rates[0] == pytest.approx(0.5)

    def test_non_function_node_raises(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert 1\n")

        class_node = ast.parse("class Foo: pass").body[0]
        plan = _Plan(functions=[_FuncEntry(target_test_file="tests/generated/test_mod.py")])
        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, return_value=("/abs/mod.py", class_node, None)),
            patch(_PURITY, return_value=True),
            pytest.raises(ValueError, match="Expected FunctionDef"),
        ):
            run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest

    def test_func_key_splitting(self, tmp_path):
        """Function key with :: splits correctly."""
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_mod.py").write_text("def test_x(): assert 1\n")

        func_node = ast.parse("def my_func(): pass").body[0]
        result_obj = _SamplingResult(survival_rate=0.5)
        entry = _FuncEntry(target_test_file="tests/generated/test_mod.py")
        entry.evidence = _Evidence(function_key="pkg.mod::my_func", source_file="src/mod.py")
        plan = _Plan(functions=[entry])

        resolve_calls = []

        def fake_resolve(_root, _src, name):
            resolve_calls.append(name)
            return ("/abs/mod.py", func_node, None)

        with (
            patch(_STRATEGY, _FakeStrategy),
            patch(_LOAD_TESTS, return_value=[lambda: None]),
            patch(_RESOLVE, side_effect=fake_resolve),
            patch(_PURITY, return_value=True),
            patch(_SAMPLING, return_value=result_obj),
        ):
            run_fresh_kill_rates(plan, str(tmp_path))  # type: ignore[arg-type]  # _Plan duck-types RebuildManifest
        assert resolve_calls[0] == "my_func"
