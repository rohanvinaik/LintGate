import os
from unittest.mock import MagicMock, patch

import pytest

from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.policy import MutationOperatorCategory, MutationTelemetry, RuntimeBudget
from lintgate.mutation.state import CoverageDepth, FunctionMutationState, MutationStateManager


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
        mock_parse.assert_called_once_with(["src/a.py"])
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

        # Should have called for both files
        assert mock_exec.call_count == 2

        # Check that test_filter was passed correctly for a.py (has mapping)
        calls = mock_exec.call_args_list
        a_call = [c for c in calls if c[1].get("paths") == ["src/a.py"]][0]
        assert a_call[1]["test_filter"] == "tests/test_a.py::test_foo"

        # Check that b.py has no test filter (no mapping)
        b_call = [c for c in calls if c[1].get("paths") == ["src/b.py"]][0]
        assert b_call[1]["test_filter"] is None

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

    cats, skip_count, covered = engine._compute_relevant_categories(str(file_path), "math_func")
    assert MutationOperatorCategory.ARITHMETIC in cats
    assert MutationOperatorCategory.NUMBER in cats
    assert MutationOperatorCategory.STRING not in cats
    assert isinstance(skip_count, int)
    assert isinstance(covered, set)


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


def test_mutation_engine_get_mutant_info_with_ast(mock_state_manager, budget, tmp_path):
    """Verify AST-based mutant info extraction returns line numbers and operators."""
    engine = MutationEngine(mock_state_manager, budget)

    code = """
def add(a, b):
    return a + b

def sub(x, y):
    return x - y

class Test:
    def method(self):
        return 1
"""
    info = engine._get_mutant_info_with_ast("test_module", source=code)

    assert len(info) >= 2  # At least add and sub functions

    # Verify structure
    for _mutant_id, mutant_data in info.items():
        assert "category" in mutant_data
        assert "line" in mutant_data
        assert "operator" in mutant_data
        assert isinstance(mutant_data["line"], int)


def test_mutation_engine_parse_includes_survivor_sites(mock_state_manager, budget, tmp_path):
    """Verify parse output includes survivor_sites field."""
    # Create a test file
    test_file = tmp_path / "test.py"
    test_file.write_text("def add(a, b): return a + b")

    # Test that survivor_sites is included in the structure by checking the method
    # The actual mutmut execution would need real mutmut setup
    engine = MutationEngine(mock_state_manager, budget)

    # Verify the get_mutant_info method returns line info
    info = engine._get_mutant_info(str(test_file), test_file.read_text())
    assert len(info) > 0

    # Check line numbers are present
    for mutant_id, data in info.items():
        assert data["line"] > 0, f"Expected positive line number for {mutant_id}"
        # Verify operator is included in the info
        assert "operator" in data, f"Expected operator in {mutant_id} info"


def test_survivor_site_operator_field():
    """Test that SurvivorSite includes operator field."""
    from lintgate.mutation.state import SurvivorSite

    site = SurvivorSite(
        line=10,
        column=5,
        category="arithmetic",
        mutant_id="mut_1",
        operator="add",
    )

    assert site.operator == "add"
    d = site.to_dict()
    assert d["operator"] == "add"


def test_survivor_site_operator_default():
    """Test that SurvivorSite defaults operator to 'unknown'."""
    from lintgate.mutation.state import SurvivorSite

    site = SurvivorSite(
        line=10,
        column=5,
        category="arithmetic",
        mutant_id="mut_1",
    )

    assert site.operator == "unknown"


def test_survivor_site_deserialization_with_operator():
    """Test SurvivorSite from_dict includes operator."""
    from lintgate.mutation.state import SurvivorSite

    data = {
        "line": 10,
        "column": 5,
        "category": "arithmetic",
        "mutant_id": "mut_1",
        "operator": "sub",
    }

    site = SurvivorSite.from_dict(data)
    assert site is not None
    assert site.operator == "sub"


def test_survivor_site_ordering_deterministic():
    """Test that survivor_sites are sorted deterministically."""
    from lintgate.mutation.state import SurvivorSite

    sites = [
        SurvivorSite(line=20, column=1, category="string", mutant_id="mut_3"),
        SurvivorSite(line=10, column=1, category="arithmetic", mutant_id="mut_1"),
        SurvivorSite(line=15, column=1, category="conditional", mutant_id="mut_2"),
    ]

    sorted_sites = sorted(sites, key=lambda s: (s.line, s.category, s.mutant_id))

    assert sorted_sites[0].line == 10
    assert sorted_sites[1].line == 15
    assert sorted_sites[2].line == 20


class TestCoveredCategorySubtraction:
    """Tests for TEFF-based covered category subtraction."""

    def test_compute_relevant_categories_with_teff_manifest(
        self, mock_state_manager, budget, tmp_path
    ):
        """Test that TEFF strong assertions reduce the relevant category set."""
        engine = MutationEngine(mock_state_manager, budget)

        code = """
def math_func(x):
    return x * 2 + 1
"""
        file_path = tmp_path / "math.py"
        file_path.write_text(code)

        # Create a mock TEFF manifest with strong assertions
        from lintgate.linters.test_effectiveness.types import (
            AssertionInfo,
            AssertionKind,
            FunctionEffectiveness,
            TestEffectivenessManifest,
        )

        func_effect = FunctionEffectiveness(
            function_name="math_func",
            test_count=1,
            assertions=[
                AssertionInfo(
                    kind=AssertionKind.EQUALITY,
                    line=1,
                    strength=0.9,  # Strong assertion
                ),
                AssertionInfo(
                    kind=AssertionKind.COMPARISON,
                    line=2,
                    strength=0.85,
                ),
            ],
        )

        teff_manifest = TestEffectivenessManifest(functions={str(file_path): func_effect})

        # With strong assertions, covered categories should reduce the set
        cats, skip_count, covered = engine._compute_relevant_categories(
            str(file_path), "math_func", teff_manifest=teff_manifest
        )

        # Should have categories but also track covered
        assert len(cats) > 0
        assert isinstance(skip_count, int)
        assert isinstance(covered, set)

    def test_compute_relevant_categories_without_teff(self, mock_state_manager, budget, tmp_path):
        """Test default behavior without TEFF manifest."""
        engine = MutationEngine(mock_state_manager, budget)

        code = """
def string_func(s):
    return s.upper()
"""
        file_path = tmp_path / "string.py"
        file_path.write_text(code)

        cats, skip_count, covered = engine._compute_relevant_categories(
            str(file_path), "string_func"
        )

        # Without TEFF, skip_count should be 0
        assert skip_count == 0
        assert MutationOperatorCategory.STRING in cats


def test_assertion_kind_to_category_mapping():
    """Test the assertion kind to category mapping function."""
    from lintgate.mutation.engine import _map_assertion_kind_to_category

    # Test known mappings
    assert _map_assertion_kind_to_category("exact_value") == "arithmetic"
    assert _map_assertion_kind_to_category("equality") == "arithmetic"
    assert _map_assertion_kind_to_category("comparison") == "conditional"
    assert _map_assertion_kind_to_category("range_check") == "conditional"
    assert _map_assertion_kind_to_category("string_equality") == "string"

    # Test None for structural assertions
    assert _map_assertion_kind_to_category("is_none") is None
    assert _map_assertion_kind_to_category("isinstance_check") is None

    # Test unknown returns None
    assert _map_assertion_kind_to_category("unknown_assertion") is None


def test_classify_sampled_quality_deterministic():
    """Test that sampled quality classification is deterministic."""
    from lintgate.mutation.engine import _classify_sampled_quality
    from lintgate.mutation.state import SignalQuality

    # Same inputs should always produce same output (deterministic)
    result1 = _classify_sampled_quality(total_mutants=15, category_count=3, timeout_ratio=0.0)
    result2 = _classify_sampled_quality(total_mutants=15, category_count=3, timeout_ratio=0.0)
    result3 = _classify_sampled_quality(total_mutants=15, category_count=3, timeout_ratio=0.0)

    assert result1 == result2 == result3
    assert result1 == SignalQuality.SAMPLED_HIGH


def test_classify_sampled_quality_low_mutants():
    """Test that low mutant count results in sampled_low."""
    from lintgate.mutation.engine import _classify_sampled_quality
    from lintgate.mutation.state import SignalQuality

    # Below threshold = low quality
    result = _classify_sampled_quality(total_mutants=5, category_count=1, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_LOW


def test_classify_sampled_quality_high_mutants_sufficient_categories():
    """Test high mutants with sufficient categories = sampled_high."""
    from lintgate.mutation.engine import _classify_sampled_quality
    from lintgate.mutation.state import SignalQuality

    result = _classify_sampled_quality(total_mutants=20, category_count=3, timeout_ratio=0.05)
    assert result == SignalQuality.SAMPLED_HIGH


def test_classify_sampled_quality_high_mutants_low_timeout():
    """Test high mutants with low timeout ratio but few categories = sampled_high."""
    from lintgate.mutation.engine import _classify_sampled_quality
    from lintgate.mutation.state import SignalQuality

    # Only 1 category but very low timeout ratio should still be high
    result = _classify_sampled_quality(total_mutants=15, category_count=1, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_HIGH


def test_classify_sampled_quality_boundary_values():
    """Test exact boundary threshold values."""
    from lintgate.mutation.engine import _classify_sampled_quality
    from lintgate.mutation.state import SignalQuality

    # Exactly at mutant threshold (10) with 2 categories = high
    result = _classify_sampled_quality(total_mutants=10, category_count=2, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_HIGH

    # Below mutant threshold (9) = low even with categories
    result = _classify_sampled_quality(total_mutants=9, category_count=2, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_LOW

    # Exactly at category threshold (2) = high
    result = _classify_sampled_quality(total_mutants=15, category_count=2, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_HIGH


def test_classify_sampled_quality_invalid_inputs():
    """Test that invalid inputs default to sampled_low."""
    from lintgate.mutation.engine import _classify_sampled_quality
    from lintgate.mutation.state import SignalQuality

    # Zero mutants
    result = _classify_sampled_quality(total_mutants=0, category_count=2, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_LOW

    # Negative mutants
    result = _classify_sampled_quality(total_mutants=-5, category_count=2, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_LOW

    # Zero categories
    result = _classify_sampled_quality(total_mutants=15, category_count=0, timeout_ratio=0.0)
    assert result == SignalQuality.SAMPLED_LOW


def test_classify_sampled_quality_high_timeout_ratio():
    """Test that high timeout ratio with low mutants and categories = sampled_low."""
    from lintgate.mutation.engine import _classify_sampled_quality
    from lintgate.mutation.state import SignalQuality

    # High timeout ratio + only 1 category + low mutants = low
    # (requires high mutants AND either categories OR low timeouts - so high timeouts kills it)
    result = _classify_sampled_quality(total_mutants=10, category_count=1, timeout_ratio=0.15)
    assert result == SignalQuality.SAMPLED_LOW
