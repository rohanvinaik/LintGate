import json

from lintgate.mutation.state import (
    CoverageDepth,
    FunctionMutationState,
    MutationStateManager,
)


def test_migration_v1_to_v2(tmp_path):
    storage_path = tmp_path / "mutation_state.json"

    # v1 schema: mapping of f_id -> state_dict
    v1_data = {
        "logic.py::add": {
            "function_name": "add",
            "file_path": "logic.py",
            "code_hash": "abc",
            "test_hash": "def",
            "depth": "sampled",
            "confidence": "low",
            "last_run_ts": 1000.0,
            "source": "mutmut",
            "killed": 5,
            "survived": 2,
            "timeout": 0,
            "total": 7,
            "killed_by_assertion": 3,
            "killed_by_crash": 2
        }
    }
    storage_path.write_text(json.dumps(v1_data))

    # Load with manager
    manager = MutationStateManager(storage_path)

    # Verify data is loaded
    assert "logic.py::add" in manager.state
    state = manager.state["logic.py::add"]
    assert state.function_name == "add"
    assert state.depth == CoverageDepth.SAMPLED
    assert state.survived == 2

    # Verify migration auto-saved to v2
    raw = json.loads(storage_path.read_text())
    assert raw["schema_version"] == 2
    assert "states" in raw
    assert "logic.py::add" in raw["states"]
    assert raw["states"]["logic.py::add"]["survived"] == 2

def test_load_v2_directly(tmp_path):
    storage_path = tmp_path / "mutation_state_v2.json"

    v2_data = {
        "schema_version": 2,
        "last_updated": 2000.0,
        "states": {
            "math.py::mul": {
                "function_name": "mul",
                "file_path": "math.py",
                "code_hash": "ghi",
                "test_hash": "jkl",
                "depth": "profiled",
                "confidence": "high",
                "last_run_ts": 1500.0,
                "source": "manual",
                "killed": 10,
                "survived": 0,
                "timeout": 0,
                "total": 10,
                "killed_by_assertion": 8,
                "killed_by_crash": 2,
                "survived_by_category": {"arithmetic": 0}
            }
        }
    }
    storage_path.write_text(json.dumps(v2_data))

    manager = MutationStateManager(storage_path)
    assert "math.py::mul" in manager.state
    state = manager.state["math.py::mul"]
    assert state.depth == CoverageDepth.PROFILED
    assert manager.last_updated == 2000.0

def test_save_persists_v2(tmp_path):
    storage_path = tmp_path / "new_state.json"
    manager = MutationStateManager(storage_path)

    new_state = FunctionMutationState(
        function_name="div",
        file_path="math.py",
        code_hash="mno",
        test_hash="pqr",
        depth=CoverageDepth.SAMPLED,
        total=5,
        survived=1,
        survived_by_category={"division": 1}
    )
    manager.update_state(new_state)
    manager.save()

    raw = json.loads(storage_path.read_text())
    assert raw["schema_version"] == 2
    assert raw["states"]["math.py::div"]["survived_by_category"] == {"division": 1}
