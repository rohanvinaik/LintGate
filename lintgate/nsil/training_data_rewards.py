"""Reward computation and curriculum ordering for NSIL training data."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .training_data import TrainingExample

# ── Reward computation functions ─────────────────────────────────────────

CURRECT_BUCKET = "compliance"
OPTIMIZATION_BUCKET = "optimization"
MULTI_STEP_BUCKET = "multi_step"


def compute_contract_adherence_reward(
    passed_checks: list[str],
    required_checks: list[str],
) -> float:
    """Compute reward based on contract adherence.

    Args:
        passed_checks: List of checks that passed
        required_checks: List of required checks

    Returns:
        Reward in [-1.0, 1.0] - higher is better
    """
    if not required_checks:
        return 0.5  # No requirements = neutral

    passed_set = set(passed_checks)
    required_set = set(required_checks)

    # Check for missing required checks
    missing = required_set - passed_set
    if missing:
        # Penalize based on fraction missing
        penalty = len(missing) / len(required_set)
        return max(-1.0, -penalty)

    # All required checks passed
    return 1.0


def compute_cost_to_green_proxy(
    initial_violations: int,
    final_violations: int,
    effort_steps: int,
) -> float:
    """Compute cost-to-green proxy reward.

    Lower effort to fix = higher reward.

    Args:
        initial_violations: Initial violation count
        final_violations: Final violation count after fixes
        effort_steps: Number of fix attempts

    Returns:
        Reward in [-1.0, 1.0] - higher is better (less effort)
    """
    if initial_violations == 0:
        return 1.0  # No violations to start with

    if final_violations >= initial_violations and effort_steps > 0:
        # Got worse or stayed same despite effort
        return -0.5

    # Improvement ratio
    improvement = (initial_violations - final_violations) / initial_violations

    # Adjust by effort efficiency
    efficiency = improvement / effort_steps if effort_steps > 0 else improvement

    return max(-1.0, min(1.0, efficiency))


def compute_prediction_accuracy_reward(
    predictions_made: int,
    predictions_correct: int,
) -> float:
    """Compute prediction accuracy reward.

    Args:
        predictions_made: Total predictions
        predictions_correct: Correct predictions

    Returns:
        Reward in [-1.0, 1.0] - higher is better
    """
    if predictions_made == 0:
        return 0.5  # No predictions = neutral

    accuracy = predictions_correct / predictions_made
    # Map accuracy [0, 1] to reward [-1, 1]
    return (accuracy * 2) - 1


def compute_combined_reward(
    contract_passed: list[str],
    contract_required: list[str],
    initial_violations: int,
    final_violations: int,
    effort_steps: int,
    predictions_made: int = 0,
    predictions_correct: int = 0,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute combined reward from multiple signals.

    Args:
        contract_passed: Passed checks
        contract_required: Required checks
        initial_violations: Initial violations
        final_violations: Final violations
        effort_steps: Fix attempts
        predictions_made: Predictions made
        predictions_correct: Correct predictions
        weights: Optional weights for each component (default: equal)

    Returns:
        Combined reward in [-1.0, 1.0]
    """
    if weights is None:
        weights = {
            "contract": 0.4,
            "cost_to_green": 0.4,
            "prediction": 0.2,
        }

    contract_reward = compute_contract_adherence_reward(contract_passed, contract_required)
    cost_reward = compute_cost_to_green_proxy(initial_violations, final_violations, effort_steps)
    pred_reward = compute_prediction_accuracy_reward(predictions_made, predictions_correct)

    combined = (
        weights.get("contract", 0.4) * contract_reward
        + weights.get("cost_to_green", 0.4) * cost_reward
        + weights.get("prediction", 0.2) * pred_reward
    )

    # Ensure bounded
    return max(-1.0, min(1.0, combined))


# ── Curriculum ordering ─────────────────────────────────────────────────

CATEGORY_FAILURE_RATES = {
    CURRECT_BUCKET: 0.2,
    OPTIMIZATION_BUCKET: 0.5,
    MULTI_STEP_BUCKET: 0.8,
}


def compute_difficulty_score(example: TrainingExample) -> float:
    """Compute a difficulty score for curriculum ordering.

    Formula: (Reward + log1p(CompletionLength)) * CategoryFailureRate
    """
    import math

    stage = get_curriculum_stage(example)
    failure_rate = CATEGORY_FAILURE_RATES.get(stage, 0.5)

    completion_len = len(example.completion)
    # log1p prevents zero and dampens long completions
    len_factor = math.log1p(completion_len)

    return (example.reward + len_factor) * failure_rate


def get_curriculum_stage(example: TrainingExample) -> str:
    """Determine curriculum stage for a training example.

    Buckets:
    - compliance: Basic constraint satisfaction
    - optimization: Multi-step reasoning with tradeoffs
    - multi_step: Complex multi-step planning

    Args:
        example: Training example to categorize

    Returns:
        Curriculum stage string
    """
    labels = set(example.labels)

    # Multi-step indicators
    multi_step_indicators = {"multi_step", "multi-step", "planning", "reasoning"}
    if labels & multi_step_indicators:
        return MULTI_STEP_BUCKET

    # Optimization indicators
    optimization_indicators = {"optimization", "tradeoff", "refactor", "improve"}
    if labels & optimization_indicators:
        return OPTIMIZATION_BUCKET

    # Compliance: basic constraint checking
    compliance_indicators = {
        "violated",
        "blocking",
        "constraint",
        "lint",
        "test",
        "gate",
    }
    if labels & compliance_indicators:
        return CURRECT_BUCKET

    # Default to compliance for simple cases
    return CURRECT_BUCKET


def order_by_curriculum(
    examples: list[TrainingExample],
) -> list[TrainingExample]:
    """Order examples by curriculum stage and difficulty.

    Order: stage (compliance -> optimization -> multi_step)
    Within stage: by difficulty score.

    Args:
        examples: List of training examples

    Returns:
        Curriculum-ordered list
    """
    stage_order = {CURRECT_BUCKET: 0, OPTIMIZATION_BUCKET: 1, MULTI_STEP_BUCKET: 2}

    return sorted(
        examples,
        key=lambda e: (
            stage_order.get(get_curriculum_stage(e), 0),
            compute_difficulty_score(e),
            e.source,
        ),
    )
