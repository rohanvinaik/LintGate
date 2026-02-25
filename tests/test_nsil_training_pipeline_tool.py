"""Tests for NSIL training pipeline tool."""

from lintgate.nsil.training_data import (
    CURRECT_BUCKET,
    MULTI_STEP_BUCKET,
    OPTIMIZATION_BUCKET,
    TrainingExample,
    compute_combined_reward,
    compute_contract_adherence_reward,
    compute_cost_to_green_proxy,
    compute_prediction_accuracy_reward,
    get_curriculum_stage,
    order_by_curriculum,
)
from mcp_tools.nsil_tools import nsil_extract_training_data

# ── Reward function tests ───────────────────────────────────────────────


def test_compute_contract_adherence_all_passed():
    """Test reward when all required checks pass."""
    reward = compute_contract_adherence_reward(
        passed_checks=["lint", "tests", "typecheck"],
        required_checks=["lint", "tests", "typecheck"],
    )
    assert reward == 1.0


def test_compute_contract_adherence_some_missing():
    """Test reward when some checks are missing."""
    reward = compute_contract_adherence_reward(
        passed_checks=["lint"],
        required_checks=["lint", "tests", "typecheck"],
    )
    assert reward < 0
    assert reward >= -1.0


def test_compute_contract_adherence_no_required():
    """Test reward when no required checks."""
    reward = compute_contract_adherence_reward(
        passed_checks=["lint"],
        required_checks=[],
    )
    assert reward == 0.5


def test_compute_cost_to_green_no_violations():
    """Test reward when no initial violations."""
    reward = compute_cost_to_green_proxy(
        initial_violations=0,
        final_violations=0,
        effort_steps=0,
    )
    assert reward == 1.0


def test_compute_cost_to_green_improved():
    """Test reward when improved."""
    reward = compute_cost_to_green_proxy(
        initial_violations=10,
        final_violations=2,
        effort_steps=3,
    )
    assert reward > 0


def test_compute_cost_to_green_worse():
    """Test reward when got worse."""
    reward = compute_cost_to_green_proxy(
        initial_violations=5,
        final_violations=8,
        effort_steps=2,
    )
    assert reward < 0


def test_compute_prediction_accuracy_high():
    """Test reward for high accuracy."""
    reward = compute_prediction_accuracy_reward(10, 9)
    assert reward > 0.5


def test_compute_prediction_accuracy_zero():
    """Test reward for zero accuracy."""
    reward = compute_prediction_accuracy_reward(10, 0)
    assert reward == -1.0


def test_compute_prediction_accuracy_no_predictions():
    """Test reward when no predictions."""
    reward = compute_prediction_accuracy_reward(0, 0)
    assert reward == 0.5


def test_compute_combined_reward():
    """Test combined reward computation."""
    reward = compute_combined_reward(
        contract_passed=["lint"],
        contract_required=["lint", "tests"],
        initial_violations=5,
        final_violations=2,
        effort_steps=3,
    )
    assert -1.0 <= reward <= 1.0


def test_compute_combined_reward_with_custom_weights():
    """Test combined reward with custom weights."""
    reward = compute_combined_reward(
        contract_passed=["lint"],
        contract_required=["lint"],
        initial_violations=5,
        final_violations=2,
        effort_steps=3,
        weights={"contract": 0.8, "cost_to_green": 0.1, "prediction": 0.1},
    )
    assert -1.0 <= reward <= 1.0


def test_reward_bounds():
    """Test all rewards are bounded to [-1.0, 1.0]."""
    for _ in range(100):
        reward = compute_combined_reward(
            contract_passed=["lint", "tests"],
            contract_required=["lint", "tests", "typecheck"],
            initial_violations=10,
            final_violations=5,
            effort_steps=5,
            predictions_made=10,
            predictions_correct=7,
        )
        assert -1.0 <= reward <= 1.0


# ── Curriculum ordering tests ───────────────────────────────────────────


def test_get_curriculum_stage_compliance():
    """Test compliance stage detection."""
    ex = TrainingExample(
        prompt="fix lint errors",
        completion="fixed",
        labels=("violated:true", "blocking:true"),
    )
    stage = get_curriculum_stage(ex)
    assert stage == CURRECT_BUCKET


def test_get_curriculum_stage_optimization():
    """Test optimization stage detection."""
    ex = TrainingExample(
        prompt="optimize performance",
        completion="optimized",
        labels=("optimization", "tradeoff"),
    )
    stage = get_curriculum_stage(ex)
    assert stage == OPTIMIZATION_BUCKET


def test_get_curriculum_stage_multi_step():
    """Test multi-step stage detection."""
    ex = TrainingExample(
        prompt="implement multi-step plan",
        completion="done",
        labels=("planning", "multi_step"),
    )
    stage = get_curriculum_stage(ex)
    assert stage == MULTI_STEP_BUCKET


def test_get_curriculum_stage_default():
    """Test default stage (compliance)."""
    ex = TrainingExample(
        prompt="simple task",
        completion="done",
        labels=(),
    )
    stage = get_curriculum_stage(ex)
    assert stage == CURRECT_BUCKET


def test_order_by_curriculum():
    """Test curriculum ordering."""
    examples = [
        TrainingExample(prompt="multi", completion="c", labels=("multi_step",), source="m"),
        TrainingExample(prompt="comp", completion="c", labels=("violated",), source="c"),
        TrainingExample(prompt="opt", completion="c", labels=("optimization",), source="o"),
    ]
    ordered = order_by_curriculum(examples)

    # Should be ordered: compliance -> optimization -> multi_step
    assert get_curriculum_stage(ordered[0]) == CURRECT_BUCKET
    assert get_curriculum_stage(ordered[1]) == OPTIMIZATION_BUCKET
    assert get_curriculum_stage(ordered[2]) == MULTI_STEP_BUCKET


def test_order_by_curriculum_deterministic():
    """Test curriculum ordering is deterministic."""
    examples = [
        TrainingExample(prompt="b task", completion="c", labels=("violated:true",), source="s2"),
        TrainingExample(prompt="a task", completion="c", labels=("violated:true",), source="s1"),
    ]
    ordered1 = order_by_curriculum(examples.copy())
    ordered2 = order_by_curriculum(examples.copy())

    # Same order for same input
    assert ordered1[0].source == ordered2[0].source
    assert ordered1[1].source == ordered2[1].source


# ── MCP tool tests ─────────────────────────────────────────────────────


def test_nsil_extract_training_data_basic():
    """Test basic extraction."""
    result = nsil_extract_training_data(path=".", format="jsonl", limit=3)

    assert "records" in result
    assert isinstance(result["records"], list)
    assert "curriculum_counts" in result
    assert "diagnostics" in result


def test_nsil_extract_training_data_jsonl_format():
    """Test JSONL format output."""
    result = nsil_extract_training_data(path=".", format="jsonl", limit=3)

    assert "jsonl" in result
    # jsonl should be a string
    assert isinstance(result["jsonl"], str)


def test_nsil_extract_training_data_format_list():
    """Test list format output."""
    result = nsil_extract_training_data(path=".", format="list", limit=3)

    assert "records" in result
    assert isinstance(result["records"], list)


def test_nsil_extract_training_data_limit():
    """Test limit is respected."""
    result = nsil_extract_training_data(path=".", format="list", limit=5)

    assert len(result["records"]) <= 5


def test_nsil_extract_training_data_record_keys():
    """Test record has required keys."""
    result = nsil_extract_training_data(path=".", format="list", limit=3)

    if result["records"]:
        record = result["records"][0]
        assert "prompt" in record
        assert "completion" in record
        assert "reward" in record
        assert "labels" in record
        assert "source" in record
        assert "curriculum_stage" in record


def test_nsil_extract_training_data_curriculum_stage_valid():
    """Test curriculum_stage is valid."""
    result = nsil_extract_training_data(path=".", format="list", limit=10)

    valid_stages = {"compliance", "optimization", "multi_step"}
    for record in result["records"]:
        assert record["curriculum_stage"] in valid_stages


def test_nsil_extract_training_data_reward_bounds():
    """Test reward values are bounded."""
    result = nsil_extract_training_data(path=".", format="list", limit=10)

    for record in result["records"]:
        assert -1.0 <= record["reward"] <= 1.0
