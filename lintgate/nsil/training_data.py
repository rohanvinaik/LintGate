"""NSIL training data schema and deterministic extractors.

Provides TrainingExample schema and extraction from session artifacts
for the training data pipeline.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrainingExample:
    """Represents a training example for NSIL.

    Attributes:
        prompt: The input prompt/context
        completion: The expected completion/response
        reward: Reward signal (0.0-1.0)
        labels: Classification labels
        source: Source artifact identifier
    """

    prompt: str = ""
    completion: str = ""
    reward: float = 0.0
    labels: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""


@dataclass
class ExtractionDiagnostics:
    """Diagnostics from extraction process.

    Tracks counts of successful extractions and skipped records.
    """

    total_records: int = 0
    extracted_count: int = 0
    skipped_invalid: int = 0
    skipped_empty: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total_records": self.total_records,
            "extracted": self.extracted_count,
            "skipped_invalid": self.skipped_invalid,
            "skipped_empty": self.skipped_empty,
        }


def _parse_json_safely(content: str) -> dict[str, Any] | None:
    """Parse JSON safely, returning None on failure."""
    import json

    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_from_controlplane_traces(
    traces: list[str] | list[dict],
) -> tuple[list[TrainingExample], ExtractionDiagnostics]:
    """Extract training examples from ControlPlane traces.

    Deterministic ordering and stable labels. Skips invalid records with diagnostics.

    Args:
        traces: List of trace file paths or trace dicts

    Returns:
        Tuple of (examples, diagnostics)
    """
    diagnostics = ExtractionDiagnostics()
    examples: list[TrainingExample] = []

    # Handle empty input (adversarial requirement)
    if not traces:
        return examples, diagnostics

    diagnostics.total_records = len(traces)

    for trace in traces:
        try:
            # Handle both file paths and dicts
            if isinstance(trace, str):
                path = Path(trace)
                if not path.exists():
                    diagnostics.skipped_empty += 1
                    continue
                content = path.read_text()
                parsed = _parse_json_safely(content)
            elif isinstance(trace, dict):
                parsed = trace
            else:
                diagnostics.skipped_invalid += 1
                continue

            if not parsed:
                diagnostics.skipped_invalid += 1
                continue

            # Extract prompt and completion from trace
            prompt = parsed.get("prompt", "") or parsed.get("event", {}).get("prompt", "")
            completion = parsed.get("completion", "") or parsed.get("response", {}).get(
                "content", ""
            )
            reward = parsed.get("reward", 0.0)
            source = parsed.get("run_id", "") or parsed.get("source", "controlplane")

            # Extract labels (stable, deterministic)
            labels = []
            coherence = parsed.get("coherence", {})
            if isinstance(coherence, dict):
                state = coherence.get("state", "")
                if state:
                    labels.append(f"coherence:{state}")
            channel_results = parsed.get("channel_results", [])
            if isinstance(channel_results, list):
                for cr in channel_results:
                    if isinstance(cr, dict) and cr.get("blocking"):
                        labels.append("blocking:true")

            # Skip if no meaningful content
            if not prompt and not completion:
                diagnostics.skipped_empty += 1
                continue

            example = TrainingExample(
                prompt=str(prompt),
                completion=str(completion),
                reward=float(reward) if reward else 0.0,
                labels=tuple(sorted(set(labels))),  # Deterministic, deduplicated
                source=str(source),
            )
            examples.append(example)
            diagnostics.extracted_count += 1

        except Exception:
            diagnostics.skipped_invalid += 1
            continue

    # Deterministic ordering by source
    examples.sort(key=lambda e: (e.source, e.prompt[:50] if e.prompt else ""))

    return examples, diagnostics


def extract_from_prediction_log(
    log_path: str,
) -> tuple[list[TrainingExample], ExtractionDiagnostics]:
    """Extract training examples from prediction log.

    Args:
        log_path: Path to prediction log file

    Returns:
        Tuple of (examples, diagnostics)
    """
    diagnostics = ExtractionDiagnostics()
    examples: list[TrainingExample] = []

    path = Path(log_path)

    # Empty artifact directory returns empty list (adversarial requirement)
    if not path.exists():
        return examples, diagnostics

    try:
        content = path.read_text()
    except Exception:
        return examples, diagnostics

    diagnostics.total_records = 1

    # Try to parse as JSON lines
    for line in content.strip().split("\n"):
        if not line.strip():
            continue

        parsed = _parse_json_safely(line)
        if not parsed:
            diagnostics.skipped_invalid += 1
            continue

        prompt = parsed.get("input", "") or parsed.get("prompt", "")
        prediction = parsed.get("prediction", "") or parsed.get("output", "")
        correct = parsed.get("correct", None)
        source = parsed.get("model", "prediction")

        if not prompt:
            diagnostics.skipped_empty += 1
            continue

        # Compute reward from correctness
        reward = 1.0 if correct is True else (0.0 if correct is False else 0.5)

        labels = []
        if correct is True:
            labels.append("accuracy:correct")
        elif correct is False:
            labels.append("accuracy:incorrect")

        # Add confidence label if available
        if confidence := parsed.get("confidence"):
            labels.append(f"confidence:{confidence}")

        example = TrainingExample(
            prompt=str(prompt),
            completion=str(prediction),
            reward=reward,
            labels=tuple(sorted(set(labels))),
            source=str(source),
        )
        examples.append(example)
        diagnostics.extracted_count += 1

    # Deterministic ordering
    examples.sort(key=lambda e: e.source)

    return examples, diagnostics


def extract_from_constraint_outcomes(
    outcomes: list[str] | list[dict],
) -> tuple[list[TrainingExample], ExtractionDiagnostics]:
    """Extract training examples from constraint outcomes.

    Args:
        outcomes: List of outcome file paths or outcome dicts

    Returns:
        Tuple of (examples, diagnostics)
    """
    diagnostics = ExtractionDiagnostics()
    examples: list[TrainingExample] = []

    if not outcomes:
        return examples, diagnostics

    diagnostics.total_records = len(outcomes)

    for outcome in outcomes:
        try:
            if isinstance(outcome, str):
                path = Path(outcome)
                if not path.exists():
                    diagnostics.skipped_empty += 1
                    continue
                content = path.read_text()
                parsed = _parse_json_safely(content)
            elif isinstance(outcome, dict):
                parsed = outcome
            else:
                diagnostics.skipped_invalid += 1
                continue

            if not parsed:
                diagnostics.skipped_invalid += 1
                continue

            # Extract constraint info
            constraint = parsed.get("constraint", "")
            violated = parsed.get("violated", False)
            context = parsed.get("context", "")
            repair = parsed.get("repair_suggestion", "")

            if not context:
                diagnostics.skipped_empty += 1
                continue

            # Reward based on constraint satisfaction
            reward = 0.0 if violated else 1.0

            labels = [
                f"constraint:{constraint}",
                "violated:true" if violated else "violated:false",
            ]

            # Add repair outcome
            if repair:
                labels.append("repair:proposed")

            example = TrainingExample(
                prompt=str(context),
                completion=str(repair) if repair else "constraint satisfied",
                reward=reward,
                labels=tuple(sorted(set(labels))),
                source=f"constraint:{constraint[:20]}",
            )
            examples.append(example)
            diagnostics.extracted_count += 1

        except Exception:
            diagnostics.skipped_invalid += 1
            continue

    # Deterministic ordering
    examples.sort(key=lambda e: (e.source, e.prompt[:50] if e.prompt else ""))

    return examples, diagnostics


def extract_from_ship_reports(
    reports: list[str] | list[dict],
) -> tuple[list[TrainingExample], ExtractionDiagnostics]:
    """Extract training examples from ship reports.

    Args:
        reports: List of report file paths or report dicts

    Returns:
        Tuple of (examples, diagnostics)
    """
    diagnostics = ExtractionDiagnostics()
    examples: list[TrainingExample] = []

    if not reports:
        return examples, diagnostics

    diagnostics.total_records = len(reports)

    for report in reports:
        try:
            if isinstance(report, str):
                path = Path(report)
                if not path.exists():
                    diagnostics.skipped_empty += 1
                    continue
                content = path.read_text()
                parsed = _parse_json_safely(content)
            elif isinstance(report, dict):
                parsed = report
            else:
                diagnostics.skipped_invalid += 1
                continue

            if not parsed:
                diagnostics.skipped_invalid += 1
                continue

            # Extract ship result info
            passed = parsed.get("passed", False)
            checks = parsed.get("checks", [])
            issue_count = parsed.get("issue_count", 0)
            source = parsed.get("run_id", "ship")

            # Determine reward based on ship outcome
            reward = 1.0 if passed else 0.0

            labels = []
            if passed:
                labels.append("ship:passed")
            else:
                labels.append("ship:failed")

            # Add check details
            if isinstance(checks, list):
                for check in checks:
                    if isinstance(check, str):
                        labels.append(f"check:{check}")
                    elif isinstance(check, dict):
                        name = check.get("name", "")
                        status = check.get("status", "")
                        if name:
                            labels.append(f"check:{name}:{status}")

            if issue_count > 0:
                labels.append(f"issues:{min(issue_count, 5)}")  # Cap for stability

            example = TrainingExample(
                prompt=f"Ship report for run {source}",
                completion=f"Passed: {passed}, Issues: {issue_count}",
                reward=reward,
                labels=tuple(sorted(set(labels))),
                source=str(source),
            )
            examples.append(example)
            diagnostics.extracted_count += 1

        except Exception:
            diagnostics.skipped_invalid += 1
            continue

    # Deterministic ordering
    examples.sort(key=lambda e: e.source)

    return examples, diagnostics


def extract_training_examples(
    artifact_paths: dict[str, list[str]],
) -> tuple[list[TrainingExample], dict[str, ExtractionDiagnostics]]:
    """Extract training examples from multiple artifact types.

    Args:
        artifact_paths: Dict mapping artifact type to list of paths

    Returns:
        Tuple of (combined examples, diagnostics by type)
    """
    all_examples: dict[str, list[TrainingExample]] = {}
    diagnostics_by_type: dict[str, ExtractionDiagnostics] = {}

    for artifact_type, paths in artifact_paths.items():
        examples: list[TrainingExample]
        diagnostics = ExtractionDiagnostics()

        if artifact_type == "controlplane":
            examples, diagnostics = extract_from_controlplane_traces(paths)
        elif artifact_type == "predictions":
            # Multiple prediction logs - combine
            for p in paths:
                ex, d = extract_from_prediction_log(p)
                examples.extend(ex)
                diagnostics.total_records += d.total_records
                diagnostics.extracted_count += d.extracted_count
                diagnostics.skipped_invalid += d.skipped_invalid
                diagnostics.skipped_empty += d.skipped_empty
        elif artifact_type == "constraints":
            examples, diagnostics = extract_from_constraint_outcomes(paths)
        elif artifact_type == "ship":
            examples, diagnostics = extract_from_ship_reports(paths)
        else:
            diagnostics = ExtractionDiagnostics()
            diagnostics.total_records = len(paths)
            examples = []

        all_examples[artifact_type] = examples
        diagnostics_by_type[artifact_type] = diagnostics

    # Combine all examples
    combined = []
    for examples in all_examples.values():
        combined.extend(examples)

    # Final deterministic sort
    combined.sort(key=lambda e: (e.source, e.prompt[:50] if e.prompt else ""))

    return combined, diagnostics_by_type


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
    compliance_indicators = {"violated", "blocking", "constraint", "lint", "test", "gate"}
    if labels & compliance_indicators:
        return CURRECT_BUCKET

    # Default to compliance for simple cases
    return CURRECT_BUCKET


def order_by_curriculum(
    examples: list[TrainingExample],
) -> list[TrainingExample]:
    """Order examples by curriculum stage.

    Order: compliance -> optimization -> multi_step
    Within each bucket, maintain deterministic order.

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
            e.source,
            e.prompt[:50] if e.prompt else "",
        ),
    )
