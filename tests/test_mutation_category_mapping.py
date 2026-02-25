import os
from unittest.mock import MagicMock, patch

import pytest

from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.state import MutationStateManager


@pytest.fixture
def engine(tmp_path):
    state_manager = MutationStateManager(tmp_path / "mutation_state.json")
    budget = MagicMock()
    budget.max_workers = 2
    budget.enabled = True
    return MutationEngine(state_manager, budget)


def test_build_mutant_category_map(engine, tmp_path):
    source = """
def add(a, b):
    return a + b

def greet(name):
    return "Hello " + name
"""
    file_path = tmp_path / "logic.py"
    file_path.write_text(source)

    # We need to mock mutmut.node_mutation and mutmut.trampoline_templates if they are not available
    # But since they are needed by the implementation, we'll see if the implementation handles them.
    # If the implementation fails to import, it returns {}

    mapping = engine._build_mutant_category_map(str(file_path), source)

    if not mapping:
        pytest.skip("Category mapping unavailable (missing libcst/mutmut)")

    assert any("x_add__mutmut_1" in k for k in mapping), (
        f"No x_add__mutmut_1 in {list(mapping.keys())}"
    )
    # a + b -> arithmetic
    cat_add = [v for k, v in mapping.items() if "x_add__mutmut_1" in k][0]
    assert cat_add == "arithmetic"

    # "Hello " + name -> string? OR arithmetic (+)?
    # mutmut + operator is arithmetic.
    assert any("x_greet" in k for k in mapping)


def test_parse_mutmut_results_with_categories(engine, tmp_path):
    source = """
def add(a, b):
    return a + b
"""
    file_path = tmp_path / "logic.py"
    file_path.write_text(source)

    # Mock mutmut results output
    # Need to match the mangled name used by _build_mutant_category_map
    mangled_name = os.path.splitext(str(file_path))[0].replace("/", ".")
    if mangled_name.startswith("."):
        mangled_name = mangled_name[1:]
    mangled_name = f"{mangled_name}.x_add__mutmut_1"
    mock_output = f"{mangled_name}: survived\n"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

        # We also need to mock os.path.exists and Path.read_text for _parse_mutmut_results
        # But they will work with our tmp_path

        results = engine._parse_mutmut_results([str(file_path)])

        assert f"{str(file_path)}::add" in results
        state = results[f"{str(file_path)}::add"]
        assert state.survived == 1
        assert state.survived_by_category == {"arithmetic": 1}
