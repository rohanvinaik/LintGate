import os
from unittest.mock import MagicMock, patch

import pytest

from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.policy import (
    MutationOperatorCategory,
    MutationTelemetry,
    RuntimeBudget,
)
from lintgate.mutation.state import (
    CoverageDepth,
    FunctionMutationState,
    MutationStateManager,
)


@pytest.fixture
def mock_state_manager():
    manager = MagicMock(spec=MutationStateManager)
    return manager


@pytest.fixture
def budget():
    return RuntimeBudget(max_inline_ms_per_function=100, enabled=True)


def test_mutation_engine_inline_sampling_budget_exhaustion(mock_state_manager, budget):
    """Verify inline sampling halts when time budget is spent."""
    engine = MutationEngine(mock_state_manager, budget)
    telemetry = MutationTelemetry("test")

    # Pre-exhaust the budget
    telemetry.inline_time_ms_spent = 500

    with patch.object(engine, "_execute_mutmut") as mock_exec:
        engine.run_inline_sampling(["src/a.py", "src/b.py"], telemetry)

        # Should not execute because budget is exhausted before the loop starts
        mock_exec.assert_not_called()


def test_mutation_engine_inline_sampling_execution(mock_state_manager, budget):
    """Verify inline sampling triggers execution."""
    engine = MutationEngine(mock_state_manager, budget)
    telemetry = MutationTelemetry("test")

    with (
        patch.object(engine, "_execute_mutmut", return_value=True) as mock_exec,
        patch.object(engine, "_parse_mutmut_results") as mock_parse,
    ):
        mock_parse.return_value = {
            "src/a.py::f": FunctionMutationState("f", "src/a.py", "h1", "t1")
        }
        results = engine.run_inline_sampling(["src/a.py"], telemetry)

        mock_exec.assert_called_once()
        mock_parse.assert_called_once_with(["src/a.py"], project_root=None)
        mock_state_manager.update_state.assert_called_once()
        mock_state_manager.save.assert_called_once()
        assert len(results) == 1
        assert results[0].function_name == "f"
        assert results[0].depth == CoverageDepth.SAMPLED
        assert telemetry.inline_functions_profiled == 1


def test_mutation_engine_background_profiling_test_impact(mock_state_manager, budget):
    """Verify background profiling passes down test impact mappings."""
    engine = MutationEngine(mock_state_manager, budget)
    telemetry = MutationTelemetry("test")

    test_mapping = {"src/a.py": ["tests/test_a.py::test_foo"]}

    with patch.object(engine, "_execute_mutmut", return_value=True) as mock_exec:
        engine.run_background_profiling(["src/a.py", "src/b.py"], test_mapping, telemetry)

        # a.py has a mapping
        mock_exec.assert_any_call(
            paths=["src/a.py"],
            depth=CoverageDepth.PROFILED,
            test_filter="tests/test_a.py::test_foo",
            relevant_categories=None,
            telemetry=telemetry,
        )
        # b.py has no mapping, falls back to None
        mock_exec.assert_any_call(
            paths=["src/b.py"],
            depth=CoverageDepth.PROFILED,
            test_filter=None,
            relevant_categories=None,
            telemetry=telemetry,
        )

        assert telemetry.background_functions_profiled == 2


def test_mutation_engine_parse_mutmut_results(mock_state_manager, budget):
    """Verify parsing logic for mutmut v3 results."""
    engine = MutationEngine(mock_state_manager, budget)

    # mutmut v3 output: module.x_funcname__mutmut_N: status
    mock_output = (
        "src.a.x_f1__mutmut_1: survived\n"
        "src.a.x_f1__mutmut_2: killed\n"
        "src.b.x_f2__mutmut_1: killed\n"
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

        with (
            patch("lintgate.mutation.engine.compute_content_hash", return_value="hash1"),
            patch("os.path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=""),
        ):
            results = engine._parse_mutmut_results(["src/a.py"])

            abs_path = os.path.abspath("src/a.py")

            assert f"{abs_path}::f1" in results
            state = results[f"{abs_path}::f1"]
            assert state.killed == 1
            assert state.survived == 1


def test_mutation_engine_compute_relevant_categories(mock_state_manager, budget, tmp_path):
    """Verify AST-based characteristic extraction."""
    engine = MutationEngine(mock_state_manager, budget)

    code = """
def math_func(x):
    return x * 2 + 1
"""
    file_path = tmp_path / "math.py"
    file_path.write_text(code)

    cats = engine._compute_relevant_categories(str(file_path), "math_func")
    assert MutationOperatorCategory.ARITHMETIC in cats
    assert MutationOperatorCategory.NUMBER in cats
    assert MutationOperatorCategory.STRING not in cats


def test_mutation_engine_inline_sampling_with_filtering(mock_state_manager, budget, tmp_path):
    """Verify filtering is wired into sampling."""
    engine = MutationEngine(mock_state_manager, budget)
    telemetry = MutationTelemetry("test")

    code = "def f(): pass"
    file_path = tmp_path / "f.py"
    file_path.write_text(code)

    with (
        patch.object(engine, "_execute_mutmut", return_value=True) as mock_exec,
        patch.object(engine, "_parse_mutmut_results") as mock_parse,
    ):
        mock_parse.return_value = {}
        engine.run_inline_sampling([str(file_path)], telemetry)

        # Check that relevant_categories was passed to _execute_mutmut
        args, kwargs = mock_exec.call_args
        assert "relevant_categories" in kwargs
        assert isinstance(kwargs["relevant_categories"], set)


# --- src/ layout fixes ---


def test_path_to_mutmut_module_flat_layout():
    """Flat layout paths are unchanged."""
    from lintgate.mutation.state import _path_to_mutmut_module

    assert _path_to_mutmut_module("lintgate/mutation/engine.py") == "lintgate.mutation.engine"


def test_path_to_mutmut_module_src_layout():
    """src/ prefix is stripped to match mutmut v3 naming."""
    from lintgate.mutation.state import _path_to_mutmut_module

    assert _path_to_mutmut_module("src/model_atlas/spreading.py") == "model_atlas.spreading"


def test_path_to_mutmut_module_src_init():
    """__init__ is collapsed after src/ stripping."""
    from lintgate.mutation.state import _path_to_mutmut_module

    assert _path_to_mutmut_module("src/model_atlas/__init__.py") == "model_atlas"


def test_match_path_src_layout():
    """Demangled path without src/ matches requested path with src/."""
    from lintgate.mutation.engine import _match_path

    # _demangle produces 'model_atlas/spreading.py' but the requested path is
    # 'src/model_atlas/spreading.py'. _match_path should find the match.
    result = _match_path("model_atlas/spreading.py", ["src/model_atlas/spreading.py"])
    assert result is not None
    assert result.endswith("src/model_atlas/spreading.py")
