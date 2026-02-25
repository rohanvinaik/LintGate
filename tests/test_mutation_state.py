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


def test_survivor_site_serialization():
    """Test SurvivorSite serialization and deserialization."""
    from lintgate.mutation.state import SurvivorSite

    site = SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_1")

    # Test to_dict
    d = site.to_dict()
    assert d["line"] == 10
    assert d["column"] == 5
    assert d["category"] == "arithmetic"
    assert d["mutant_id"] == "mut_1"

    # Test from_dict
    restored = SurvivorSite.from_dict(d)
    assert restored.line == 10
    assert restored.column == 5
    assert restored.category == "arithmetic"
    assert restored.mutant_id == "mut_1"


def test_survivor_site_from_dict_invalid():
    """Test that from_dict returns None for invalid data (fail-closed)."""
    from lintgate.mutation.state import SurvivorSite

    # Invalid: missing required fields
    assert SurvivorSite.from_dict({}) is None

    # Invalid: wrong types
    assert SurvivorSite.from_dict({"line": "not an int"}) is None


def test_function_mutation_state_survivor_sites():
    """Test FunctionMutationState with survivor_sites field."""
    from lintgate.mutation.state import SurvivorSite

    site = SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_1")
    state = FunctionMutationState(
        function_name="test_func",
        file_path="test.py",
        code_hash="abc",
        test_hash="def",
        survivor_sites=[site],
    )

    assert len(state.survivor_sites) == 1
    assert state.survivor_sites[0].line == 10

    # Test serialization includes survivor_sites
    d = state.to_dict()
    assert "survivor_sites" in d
    assert len(d["survivor_sites"]) == 1
    assert d["survivor_sites"][0]["line"] == 10


def test_function_mutation_state_survivor_sites_backward_compat():
    """Test backward compatibility: old state loads without survivor_sites."""
    # Create state without survivor_sites field (as old schema would have)
    state_dict = {
        "function_name": "old_func",
        "file_path": "old.py",
        "code_hash": "abc",
        "test_hash": "def",
        "depth": "sampled",
        "confidence": "low",
        "last_run_ts": 1000.0,
        "source": "unknown",
        "killed": 5,
        "survived": 2,
        "timeout": 0,
        "total": 7,
        "killed_by_assertion": 3,
        "killed_by_crash": 2,
        "survived_by_category": {},
        # Note: no survivor_sites field
    }

    state = FunctionMutationState.from_dict(state_dict)
    assert state.survivor_sites == []
    assert len(state.survivor_sites) == 0


def test_function_mutation_state_survivor_sites_malformed():
    """Test fail-closed: malformed survivor_sites entries are dropped."""
    state_dict = {
        "function_name": "test_func",
        "file_path": "test.py",
        "code_hash": "abc",
        "test_hash": "def",
        "depth": "sampled",
        "confidence": "low",
        "survivor_sites": [
            {"line": 10, "column": 5, "category": "arithmetic", "mutant_id": "mut_1"},
            {},  # Invalid: missing required fields
            "not a dict",  # Invalid: not a dict
            {"line": 20, "column": 15, "category": "string", "mutant_id": "mut_2"},
        ],
    }

    state = FunctionMutationState.from_dict(state_dict)
    # Only valid entries should be kept
    assert len(state.survivor_sites) == 2
    assert state.survivor_sites[0].line == 10
    assert state.survivor_sites[1].line == 20


def test_signal_quality_enum_values():
    """Test SignalQuality enum has expected values."""
    from lintgate.mutation.state import SignalQuality

    assert SignalQuality.NONE.value == "none"
    assert SignalQuality.SAMPLED_LOW.value == "sampled_low"
    assert SignalQuality.SAMPLED_HIGH.value == "sampled_high"
    assert SignalQuality.PROFILED.value == "profiled"


def test_signal_quality_from_depth():
    """Test SignalQuality.from_depth mapping."""
    from lintgate.mutation.state import CoverageDepth, SignalQuality

    assert SignalQuality.from_depth(CoverageDepth.NONE) == SignalQuality.NONE
    assert SignalQuality.from_depth(CoverageDepth.SAMPLED) == SignalQuality.SAMPLED_LOW
    assert SignalQuality.from_depth(CoverageDepth.PROFILED) == SignalQuality.PROFILED
    # Unknown depth defaults to NONE
    assert SignalQuality.from_depth(CoverageDepth.NONE) == SignalQuality.NONE


def test_function_mutation_state_signal_quality_included():
    """Test signal_quality is included in to_dict."""
    from lintgate.mutation.state import CoverageDepth, SignalQuality

    state = FunctionMutationState(
        function_name="test_func",
        file_path="test.py",
        code_hash="abc",
        test_hash="def",
        depth=CoverageDepth.PROFILED,
    )
    state.signal_quality = SignalQuality.PROFILED

    d = state.to_dict()
    assert "signal_quality" in d
    assert d["signal_quality"] == "profiled"


def test_function_mutation_state_signal_quality_from_dict():
    """Test signal_quality is loaded from dict with depth fallback."""
    from lintgate.mutation.state import SignalQuality

    # Test with explicit signal_quality
    state_dict = {
        "function_name": "test_func",
        "file_path": "test.py",
        "code_hash": "abc",
        "test_hash": "def",
        "depth": "sampled",
        "signal_quality": "sampled_low",
    }
    state = FunctionMutationState.from_dict(state_dict)
    assert state.signal_quality == SignalQuality.SAMPLED_LOW

    # Test without signal_quality - should derive from depth
    state_dict_no_sq = {
        "function_name": "test_func",
        "file_path": "test.py",
        "code_hash": "abc",
        "test_hash": "def",
        "depth": "profiled",
    }
    state2 = FunctionMutationState.from_dict(state_dict_no_sq)
    assert state2.signal_quality == SignalQuality.PROFILED

    # Test with invalid signal_quality - should derive from depth
    state_dict_invalid = {
        "function_name": "test_func",
        "file_path": "test.py",
        "code_hash": "abc",
        "test_hash": "def",
        "depth": "sampled",
        "signal_quality": "invalid_value",
    }
    state3 = FunctionMutationState.from_dict(state_dict_invalid)
    # Should fall back to depth-derived value
    assert state3.signal_quality == SignalQuality.SAMPLED_LOW
