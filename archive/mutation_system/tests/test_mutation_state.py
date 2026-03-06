from lintgate.mutation.state import (
    ConfidenceLevel,
    CoverageDepth,
    FunctionMutationState,
    MutationStateManager,
    compute_content_hash,
)


def test_compute_content_hash():
    content = "def foo(): pass"
    hash1 = compute_content_hash(content)
    hash2 = compute_content_hash(content)
    assert hash1 == hash2
    assert hash1 != compute_content_hash(content + "\n")


def test_function_mutation_state_serialization():
    state = FunctionMutationState(
        function_name="foo",
        file_path="src/foo.py",
        code_hash="c_hash",
        test_hash="t_hash",
        depth=CoverageDepth.SAMPLED,
        confidence=ConfidenceLevel.MEDIUM,
        killed=5,
        survived=1,
        total=6,
        killed_by_assertion=4,
        killed_by_crash=1,
    )

    assert state.survival_rate == 1 / 6
    assert state.killed_by_assertion == 4

    d = state.to_dict()
    assert d["function_name"] == "foo"
    assert d["depth"] == "sampled"
    assert d["confidence"] == "medium"
    assert d["killed"] == 5

    restored = FunctionMutationState.from_dict(d)
    assert restored.depth == CoverageDepth.SAMPLED
    assert restored.confidence == ConfidenceLevel.MEDIUM
    assert restored.killed == 5
    assert restored.killed_by_assertion == 4
    assert restored.killed_by_crash == 1


def test_survival_rate_edge_cases():
    # No mutants
    st = FunctionMutationState("f", "p", "h", "t", total=0)
    assert st.survival_rate == 1.0

    # 100% killed
    st = FunctionMutationState("f", "p", "h", "t", total=10, killed=10, survived=0)
    assert st.survival_rate == 0.0

    # partial
    st = FunctionMutationState("f", "p", "h", "t", total=10, killed=7, survived=3)
    assert st.survival_rate == 0.3


def test_mutation_state_manager_lifecycle(tmp_path):
    storage = tmp_path / "mutation_state.json"
    manager = MutationStateManager(storage)

    assert storage.exists() is False
    assert manager.get_state("src/foo.py::foo") is None

    state = FunctionMutationState(
        function_name="foo",
        file_path="src/foo.py",
        code_hash="h1",
        test_hash="t1",
        depth=CoverageDepth.SAMPLED,
    )
    manager.update_state(state)
    manager.save()

    assert storage.exists()

    # Reload
    manager2 = MutationStateManager(storage)
    restored = manager2.get_state("src/foo.py::foo")
    assert restored is not None
    assert restored.code_hash == "h1"


def test_mutation_state_requires_run(tmp_path):
    storage = tmp_path / "state.json"
    manager = MutationStateManager(storage)

    func_id = "test.py::func"

    # 1. No state -> runs required
    assert manager.requires_run(func_id, "c1", "t1", CoverageDepth.SAMPLED) is True

    state = FunctionMutationState(
        function_name="func",
        file_path="test.py",
        code_hash="c1",
        test_hash="t1",
        depth=CoverageDepth.SAMPLED,
    )
    manager.update_state(state)

    # 2. Matching hashes and same target depth -> no run
    assert manager.requires_run(func_id, "c1", "t1", CoverageDepth.SAMPLED) is False

    # 3. Matching hashes but lesser target depth -> no run
    assert manager.requires_run(func_id, "c1", "t1", CoverageDepth.NONE) is False

    # 4. Matching hashes but greater target depth -> runs required
    assert manager.requires_run(func_id, "c1", "t1", CoverageDepth.PROFILED) is True

    # 5. Code hash changed -> runs required regardless of depth
    assert manager.requires_run(func_id, "c2", "t1", CoverageDepth.SAMPLED) is True

    # 6. Test hash changed -> runs required
    assert manager.requires_run(func_id, "c1", "t2", CoverageDepth.SAMPLED) is True
