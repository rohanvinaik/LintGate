import json

from lintgate.nsil.state_schema import InferenceStateSnapshot


def test_inference_state_snapshot_creation():
    snapshot = InferenceStateSnapshot()
    assert snapshot.gate_status == "unknown"
    assert snapshot.blocking_findings == []
    assert snapshot.mutation_summary == {}
    assert snapshot.active_constraints == []
    assert snapshot.prediction_accuracy == 0.0
    assert snapshot.risk_level == "unknown"
    assert snapshot.token_count == 0

    custom_snapshot = InferenceStateSnapshot(
        gate_status="fail",
        blocking_findings=["bug_A", "bug_B"],
        mutation_summary={"file1.py": {"mutated": 5}},
        active_constraints=["complexity_limit"],
        prediction_accuracy=0.85,
        risk_level="high",
        token_count=100,
    )
    assert custom_snapshot.gate_status == "fail"
    assert custom_snapshot.blocking_findings == ["bug_A", "bug_B"]
    assert custom_snapshot.mutation_summary == {"file1.py": {"mutated": 5}}
    assert custom_snapshot.active_constraints == ["complexity_limit"]
    assert custom_snapshot.prediction_accuracy == 0.85
    assert custom_snapshot.risk_level == "high"
    assert custom_snapshot.token_count == 100


def test_serialize_compact_json_flat_determinism():
    snapshot1 = InferenceStateSnapshot(
        gate_status="pass",
        blocking_findings=["issue1", "issue2"],
        mutation_summary={"file.py": {"killed": 10}},
        active_constraints=["A", "B"],
        prediction_accuracy=0.9,
        risk_level="low",
    )
    snapshot2 = InferenceStateSnapshot(
        gate_status="pass",
        blocking_findings=["issue1", "issue2"],
        mutation_summary={"file.py": {"killed": 10}},
        active_constraints=["A", "B"],
        prediction_accuracy=0.9,
        risk_level="low",
    )

    serialized1 = snapshot1.serialize_compact(format="json_flat", budget=1000)
    serialized2 = snapshot2.serialize_compact(format="json_flat", budget=1000)
    assert serialized1 == serialized2

    # Test with different order of input but should produce same serialized output
    snapshot3 = InferenceStateSnapshot(
        risk_level="low",
        prediction_accuracy=0.9,
        active_constraints=["B", "A"],  # Different order
        mutation_summary={"file.py": {"killed": 10}},
        blocking_findings=["issue2", "issue1"],  # Different order
        gate_status="pass",
    )
    serialized3 = snapshot3.serialize_compact(format="json_flat", budget=1000)
    assert serialized1 == serialized3  # Should still be deterministic


def test_serialize_compact_kv_pairs_determinism():
    snapshot1 = InferenceStateSnapshot(
        gate_status="pass",
        blocking_findings=["issue1", "issue2"],
        mutation_summary={"file.py": {"killed": 10}},
        active_constraints=["A", "B"],
        prediction_accuracy=0.9,
        risk_level="low",
    )
    snapshot2 = InferenceStateSnapshot(
        gate_status="pass",
        blocking_findings=["issue1", "issue2"],
        mutation_summary={"file.py": {"killed": 10}},
        active_constraints=["A", "B"],
        prediction_accuracy=0.9,
        risk_level="low",
    )

    serialized1 = snapshot1.serialize_compact(format="kv_pairs", budget=1000)
    serialized2 = snapshot2.serialize_compact(format="kv_pairs", budget=1000)
    assert serialized1 == serialized2

    snapshot3 = InferenceStateSnapshot(
        risk_level="low",
        prediction_accuracy=0.9,
        active_constraints=["B", "A"],
        mutation_summary={"file.py": {"killed": 10}},
        blocking_findings=["issue2", "issue1"],
        gate_status="pass",
    )
    serialized3 = snapshot3.serialize_compact(format="kv_pairs", budget=1000)
    assert serialized1 == serialized3


def test_serialize_compact_structured_text_determinism():
    snapshot1 = InferenceStateSnapshot(
        gate_status="pass",
        blocking_findings=["issue1", "issue2"],
        mutation_summary={"file.py": {"killed": 10}},
        active_constraints=["A", "B"],
        prediction_accuracy=0.9,
        risk_level="low",
    )
    snapshot2 = InferenceStateSnapshot(
        gate_status="pass",
        blocking_findings=["issue1", "issue2"],
        mutation_summary={"file.py": {"killed": 10}},
        active_constraints=["A", "B"],
        prediction_accuracy=0.9,
        risk_level="low",
    )

    serialized1 = snapshot1.serialize_compact(format="structured_text", budget=1000)
    serialized2 = snapshot2.serialize_compact(format="structured_text", budget=1000)
    assert serialized1 == serialized2


def test_serialize_compact_budgeting():
    long_list = [f"item_{i}" for i in range(50)]
    long_summary = {f"key_{i}": f"value_{i}" for i in range(20)}

    # Test JSON_FLAT
    snapshot_json = InferenceStateSnapshot(
        blocking_findings=long_list,
        mutation_summary=long_summary,
        active_constraints=long_list,
        gate_status="fail",
        risk_level="high",
        prediction_accuracy=0.5,
    )

    # Budget that should truncate everything but core fields
    budget_json_minimal = snapshot_json.serialize_compact(format="json_flat", budget=100)
    parsed_json_minimal = json.loads(budget_json_minimal)
    assert parsed_json_minimal["gate_status"] == "fail"
    assert parsed_json_minimal["risk_level"] == "high"
    assert "blocking_findings" not in parsed_json_minimal
    assert "mutation_summary" not in parsed_json_minimal
    assert "active_constraints" not in parsed_json_minimal

    # Budget that allows some fields but not all
    budget_json_some = snapshot_json.serialize_compact(format="json_flat", budget=200)
    parsed_json_some = json.loads(budget_json_some)
    assert parsed_json_some["gate_status"] == "fail"
    assert parsed_json_some["risk_level"] == "high"
    # At 200 chars, all list/dict fields should be removed; only core scalars remain
    assert "blocking_findings" not in parsed_json_some
    assert "mutation_summary" not in parsed_json_some
    assert "active_constraints" not in parsed_json_some

    # Test KV_PAIRS
    snapshot_kv = InferenceStateSnapshot(
        blocking_findings=long_list,
        mutation_summary=long_summary,
        active_constraints=long_list,
        gate_status="fail",
        risk_level="high",
        prediction_accuracy=0.5,
    )
    budget_kv_minimal = snapshot_kv.serialize_compact(format="kv_pairs", budget=100)
    assert "gate_status=fail" in budget_kv_minimal
    assert "risk_level=high" in budget_kv_minimal
    assert (
        "blocking_findings" not in budget_kv_minimal or "blocking_findings=[]" in budget_kv_minimal
    )
    assert "mutation_summary" not in budget_kv_minimal or "mutation_summary={}" in budget_kv_minimal
    assert (
        "active_constraints" not in budget_kv_minimal
        or "active_constraints=[]" in budget_kv_minimal
    )


def test_serialize_compact_resilience_extreme_budget():
    """
    Per issue #130 adversarial requirement: under extreme budget (<=120 tokens),
    serialization must still return parseable output (never crash/empty).
    Note: The minimal valid JSON with core fields is ~88 chars, so we test
    that extreme budgets produce valid, parseable output even if they exceed
    the impossible budget constraint.
    """
    snapshot = InferenceStateSnapshot(
        gate_status="fail",
        blocking_findings=[f"bug_{i}" for i in range(100)],
        mutation_summary={f"file{i}.py": {"killed": i} for i in range(50)},
        active_constraints=[f"constraint_{i}" for i in range(100)],
        prediction_accuracy=0.1,
        risk_level="critical",
    )

    # Extreme budget for JSON_FLAT - should still be parseable
    serialized_json = snapshot.serialize_compact(format="json_flat", budget=50)
    # Must be parseable JSON (not crash, not empty)
    assert len(serialized_json) > 0
    parsed_json = json.loads(serialized_json)
    assert "gate_status" in parsed_json
    assert "risk_level" in parsed_json
    # All list/dict fields should be removed under extreme budget
    assert "blocking_findings" not in parsed_json
    assert "mutation_summary" not in parsed_json
    assert "active_constraints" not in parsed_json

    # Extreme budget for KV_PAIRS - should still be parseable
    serialized_kv = snapshot.serialize_compact(format="kv_pairs", budget=50)
    assert len(serialized_kv) > 0
    assert "gate_status=" in serialized_kv
    assert "risk_level=" in serialized_kv

    # Extreme budget for structured_text - should still be parseable
    serialized_text = snapshot.serialize_compact(format="structured_text", budget=50)
    assert len(serialized_text) > 0
    assert "Gate Status:" in serialized_text
    assert "Risk Level:" in serialized_text


def test_from_compact_json_flat():
    original_snapshot = InferenceStateSnapshot(
        gate_status="fail",
        blocking_findings=["bug_A", "bug_B"],
        mutation_summary={"file1.py": {"mutated": 5}},
        active_constraints=["complexity_limit"],
        prediction_accuracy=0.85,
        risk_level="high",
        token_count=100,
    )
    serialized_data = original_snapshot.serialize_compact(format="json_flat", budget=1000)
    deserialized_snapshot = InferenceStateSnapshot.from_compact(serialized_data, format="json_flat")

    assert deserialized_snapshot.gate_status == original_snapshot.gate_status
    assert deserialized_snapshot.blocking_findings == original_snapshot.blocking_findings
    assert deserialized_snapshot.mutation_summary == original_snapshot.mutation_summary
    assert deserialized_snapshot.active_constraints == original_snapshot.active_constraints
    assert deserialized_snapshot.prediction_accuracy == original_snapshot.prediction_accuracy
    assert deserialized_snapshot.risk_level == original_snapshot.risk_level
    # Token count is recalculated on serialization, so it might not match the original if budget affects it
    assert isinstance(deserialized_snapshot.token_count, int)


def test_from_compact_kv_pairs():
    original_snapshot = InferenceStateSnapshot(
        gate_status="pass",
        blocking_findings=["bug_X"],
        mutation_summary={"file2.py": {"killed": 2}},
        active_constraints=["perf_issue"],
        prediction_accuracy=0.99,
        risk_level="low",
        token_count=50,
    )
    serialized_data = original_snapshot.serialize_compact(format="kv_pairs", budget=1000)
    deserialized_snapshot = InferenceStateSnapshot.from_compact(serialized_data, format="kv_pairs")

    assert deserialized_snapshot.gate_status == original_snapshot.gate_status
    assert deserialized_snapshot.blocking_findings == original_snapshot.blocking_findings
    assert deserialized_snapshot.mutation_summary == original_snapshot.mutation_summary
    assert deserialized_snapshot.active_constraints == original_snapshot.active_constraints
    assert deserialized_snapshot.prediction_accuracy == original_snapshot.prediction_accuracy
    assert deserialized_snapshot.risk_level == original_snapshot.risk_level
    assert isinstance(deserialized_snapshot.token_count, int)
