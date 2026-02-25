"""NSIL evaluation harness for baseline testing.

Provides Tier 0 and Tier 1 evaluation runners with deterministic metrics
and comparison table generation.
"""

from dataclasses import dataclass
from typing import Any

from lintgate.nsil.action_verifier import ActionProposal, verify_action

# ── Metric envelope types ────────────────────────────────────────────────


@dataclass
class EvalMetrics:
    """Standard metric envelope for NSIL evaluations.

    Attributes:
        policy_compliance_rate: Fraction of actions passing policy checks [0.0-1.0]
        latency_ms_per_action: Average latency per action in milliseconds
        task_completion_rate: Fraction of tasks completed successfully [0.0-1.0]
        token_cost: Total token cost (estimated)
        false_positive_repair_rate: Fraction of repairs that were unnecessary [0.0-1.0]
    """

    policy_compliance_rate: float = 0.0
    latency_ms_per_action: float = 0.0
    task_completion_rate: float = 0.0
    token_cost: float = 0.0
    false_positive_repair_rate: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "policy_compliance_rate": self.policy_compliance_rate,
            "latency_ms_per_action": self.latency_ms_per_action,
            "task_completion_rate": self.task_completion_rate,
            "token_cost": self.token_cost,
            "false_positive_repair_rate": self.false_positive_repair_rate,
        }


@dataclass
class EvalTaskResult:
    """Result of a single evaluation task."""

    task_id: str
    passed: bool
    metrics: EvalMetrics
    error_message: str | None = None
    latency_ms: float = 0.0


@dataclass
class EvalDiagnostics:
    """Diagnostics from evaluation run."""

    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    skipped_tasks: int = 0
    missing_fixtures: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total_tasks,
            "passed": self.passed_tasks,
            "failed": self.failed_tasks,
            "skipped": self.skipped_tasks,
            "missing_fixtures": self.missing_fixtures,
        }


# ── Tier evaluation runners ─────────────────────────────────────────────


def run_tier0(
    tasks: list[dict[str, Any]],
    adapter: Any = None,
) -> tuple[list[EvalTaskResult], EvalDiagnostics]:
    """Run Tier 0 evaluation (policy compliance baseline).

    Tests basic policy compliance with deterministic mock responses.
    No internet or paid API calls required.

    Args:
        tasks: List of task definitions
        adapter: Optional adapter interface for testing

    Returns:
        Tuple of (task_results, diagnostics)
    """
    diagnostics = EvalDiagnostics()
    results: list[EvalTaskResult] = []

    diagnostics.total_tasks = len(tasks)

    for task in tasks:
        task_id = task.get("id", "unknown")

        # Check for missing fixtures
        if "fixture" not in task or not task["fixture"]:
            diagnostics.missing_fixtures += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=f"Missing fixture for task {task_id}",
                )
            )
            continue

        try:
            # Run task evaluation
            result = _evaluate_tier0_task(task, adapter)
            results.append(result)

            if result.passed:
                diagnostics.passed_tasks += 1
            else:
                diagnostics.failed_tasks += 1

        except Exception as e:
            diagnostics.failed_tasks += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=str(e),
                )
            )

    return results, diagnostics


def run_tier1(
    tasks: list[dict[str, Any]],
    adapter: Any = None,
) -> tuple[list[EvalTaskResult], EvalDiagnostics]:
    """Run Tier 1 evaluation (task completion baseline).

    Tests task completion with deterministic mock responses.
    No internet or paid API calls required.

    Args:
        tasks: List of task definitions
        adapter: Optional adapter interface for testing

    Returns:
        Tuple of (task_results, diagnostics)
    """
    diagnostics = EvalDiagnostics()
    results: list[EvalTaskResult] = []

    diagnostics.total_tasks = len(tasks)

    for task in tasks:
        task_id = task.get("id", "unknown")

        # Check for missing fixtures
        if "fixture" not in task or not task["fixture"]:
            diagnostics.missing_fixtures += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=f"Missing fixture for task {task_id}",
                )
            )
            continue

        try:
            # Run task evaluation
            result = _evaluate_tier1_task(task, adapter)
            results.append(result)

            if result.passed:
                diagnostics.passed_tasks += 1
            else:
                diagnostics.failed_tasks += 1

        except Exception as e:
            diagnostics.failed_tasks += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=str(e),
                )
            )

    return results, diagnostics


def _evaluate_tier0_task(
    task: dict[str, Any],
    adapter: Any,
) -> EvalTaskResult:
    """Evaluate a single Tier 0 task."""
    import time

    task_id = task.get("id", "unknown")
    start_time = time.perf_counter()

    # Get expected action from fixture
    fixture = task.get("fixture", {})
    expected_action = fixture.get("expected_action", "")

    # Get constraints from task
    constraints = task.get("constraints", [])

    # Simple mock evaluation: check if constraints are mentioned in expected_action
    passed = True
    for constraint in constraints:
        if constraint not in expected_action.lower():
            passed = False
            break

    # Compute metrics
    actions = fixture.get("actions", [])
    violations = fixture.get("violations", 0)

    policy_rate = 1.0 - (violations / max(len(actions), 1))

    metrics = EvalMetrics(
        policy_compliance_rate=policy_rate,
        latency_ms_per_action=100.0,  # Mock value
        task_completion_rate=1.0 if passed else 0.0,
        token_cost=len(expected_action) * 1.3,  # Rough estimate
        false_positive_repair_rate=0.1,  # Mock value
    )

    latency_ms = (time.perf_counter() - start_time) * 1000

    return EvalTaskResult(
        task_id=task_id,
        passed=passed,
        metrics=metrics,
        latency_ms=latency_ms,
    )


def _evaluate_tier1_task(
    task: dict[str, Any],
    adapter: Any,
) -> EvalTaskResult:
    """Evaluate a single Tier 1 task."""
    import time

    task_id = task.get("id", "unknown")
    start_time = time.perf_counter()

    # Get expected outcome from fixture
    fixture = task.get("fixture", {})
    expected_outcome = fixture.get("expected_outcome", "")
    actual_outcome = fixture.get("actual_outcome", "")

    # Simple mock evaluation: check if outcome matches
    passed = expected_outcome == actual_outcome

    # Compute metrics
    steps = fixture.get("steps_taken", 1)
    expected_steps = fixture.get("expected_steps", 1)

    completion_rate = 1.0 if passed else (steps / max(expected_steps, 1))

    metrics = EvalMetrics(
        policy_compliance_rate=0.9,  # Mock value
        latency_ms_per_action=150.0,  # Mock value
        task_completion_rate=completion_rate,
        token_cost=len(actual_outcome) * 1.3,  # Rough estimate
        false_positive_repair_rate=0.05,  # Mock value
    )

    latency_ms = (time.perf_counter() - start_time) * 1000

    return EvalTaskResult(
        task_id=task_id,
        passed=passed,
        metrics=metrics,
        latency_ms=latency_ms,
    )


# ── Tier 2 runner (grammar-constrained) ─────────────────────────────────


def run_tier2(
    tasks: list[dict[str, Any]],
    adapter: Any = None,
    capabilities: dict[str, bool] | None = None,
) -> tuple[list[EvalTaskResult], EvalDiagnostics]:
    """Run Tier 2 evaluation (grammar-constrained generation).

    Tests grammar-constrained generation with the adapter.
    Returns structured unsupported_tier diagnostics if grammar is unavailable.

    Args:
        tasks: List of task definitions
        adapter: Optional adapter with grammar support
        capabilities: Adapter capabilities dict

    Returns:
        Tuple of (task_results, diagnostics)
    """
    import time

    diagnostics = EvalDiagnostics()
    results: list[EvalTaskResult] = []

    # Check for grammar support
    if capabilities and not capabilities.get("supports_grammar_constraints", False):
        diagnostics.failed_tasks = len(tasks)
        for task in tasks:
            task_id = task.get("id", "unknown")
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message="unsupported_tier: grammar constraints not available",
                )
            )
        return results, diagnostics

    diagnostics.total_tasks = len(tasks)

    for task in tasks:
        task_id = task.get("id", "unknown")

        # Check for missing fixtures
        if "fixture" not in task or not task["fixture"]:
            diagnostics.missing_fixtures += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=f"Missing fixture for task {task_id}",
                )
            )
            continue

        try:
            start_time = time.perf_counter()

            # Get grammar constraint from task
            grammar = task.get("grammar", {})
            prompt = task.get("prompt", "")

            # If adapter provided and supported, run real generation
            if adapter and capabilities and capabilities.get("supported"):
                # Apply grammar to adapter
                adapter.apply_grammar_constraint(grammar)

                # Generate response
                response = ""
                for chunk in adapter.get_generation_stream(prompt):
                    response += chunk

                # Check if rejected by grammar
                rejected, reason = adapter.check_rejection(response)
                passed = not rejected

                # Metrics from real run
                grammar_tokens = len(response) // 4  # Estimate
                error_message = reason if rejected else None
            else:
                # Mock evaluation path
                passed = bool(grammar) and bool(prompt)
                grammar.get("applied", False)
                grammar_tokens = grammar.get("token_count", 0)
                error_message = None

            metrics = EvalMetrics(
                policy_compliance_rate=1.0 if passed else 0.5,
                latency_ms_per_action=200.0,
                task_completion_rate=1.0 if passed else 0.0,
                token_cost=grammar_tokens * 1.3,
                false_positive_repair_rate=0.05,
            )

            latency_ms = (time.perf_counter() - start_time) * 1000

            result = EvalTaskResult(
                task_id=task_id,
                passed=passed,
                metrics=metrics,
                latency_ms=latency_ms,
                error_message=error_message,
            )
            results.append(result)

            if result.passed:
                diagnostics.passed_tasks += 1
            else:
                diagnostics.failed_tasks += 1

        except Exception as e:
            diagnostics.failed_tasks += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=str(e),
                )
            )

    return results, diagnostics


# ── Tier 3 runner (propose/verify/repair) ────────────────────────────────


def run_tier3(
    tasks: list[dict[str, Any]],
    adapter: Any = None,
    verify_fn: Any = None,
) -> tuple[list[EvalTaskResult], EvalDiagnostics]:
    """Run Tier 3 evaluation (propose/verify/repair loop).

    Tests the full propose-verify-repair loop with the adapter.
    Returns structured unsupported_tier diagnostics if verification unavailable.

    Args:
        tasks: List of task definitions
        adapter: Optional adapter with action hooks
        verify_fn: Optional verification function

    Returns:
        Tuple of (task_results, diagnostics)
    """
    import time

    diagnostics = EvalDiagnostics()
    results: list[EvalTaskResult] = []

    # Check for action hook support
    if adapter and not getattr(adapter, "register_action_hook", None):
        diagnostics.failed_tasks = len(tasks)
        for task in tasks:
            task_id = task.get("id", "unknown")
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message="unsupported_tier: action hooks not available",
                )
            )
        return results, diagnostics

    diagnostics.total_tasks = len(tasks)

    for task in tasks:
        task_id = task.get("id", "unknown")

        # Check for missing fixtures
        if "fixture" not in task or not task["fixture"]:
            diagnostics.missing_fixtures += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=f"Missing fixture for task {task_id}",
                )
            )
            continue

        try:
            start_time = time.perf_counter()

            # Get repair loop info from task
            fixture = task.get("fixture", {})
            prompt = task.get("prompt", "Propose an action")
            max_iterations = task.get("max_iterations", 3)

            iterations = 0
            violations_count = 0
            passed = False
            error_message = None

            if adapter:
                # Real PVR loop
                current_prompt = prompt
                while iterations < max_iterations:
                    iterations += 1

                    # 1. Propose
                    response = ""
                    for chunk in adapter.get_generation_stream(current_prompt):
                        response += chunk

                    # 2. Parse and Verify
                    # (In real loop, we'd parse response into ActionProposal)
                    # For eval harness, we simulate parsing or use fixture mapping
                    action_type = "bash" if "rm" in response or "ls" in response else "read"
                    target = response.strip()

                    proposal = ActionProposal(
                        action_type=action_type, target=target, content=response
                    )
                    verify_result = verify_action(proposal)

                    if verify_result.approved:
                        passed = True
                        violations_count = 0
                        break
                    else:
                        violations_count = len(verify_result.violations)
                        # 3. Repair (Feedback loop)
                        feedback = f"\nAction rejected: {', '.join(verify_result.violations)}"
                        if verify_result.repairs:
                            feedback += f"\nSuggested repairs: {', '.join(verify_result.repairs)}"
                        current_prompt += feedback

                if not passed:
                    error_message = f"Failed to reach compliance after {iterations} iterations"
            else:
                # Mock path
                iterations = fixture.get("iterations", 1)
                violations_count = fixture.get("remaining_violations", 0)
                passed = iterations < 3 or violations_count == 0

            # Compute metrics
            metrics = EvalMetrics(
                policy_compliance_rate=1.0 - (violations_count / 10.0),  # Normalized
                latency_ms_per_action=300.0 * iterations,
                task_completion_rate=1.0 if passed else 0.0,
                token_cost=iterations * 500.0,
                false_positive_repair_rate=0.1 / max(iterations, 1),
            )

            latency_ms = (time.perf_counter() - start_time) * 1000

            result = EvalTaskResult(
                task_id=task_id,
                passed=passed,
                metrics=metrics,
                latency_ms=latency_ms,
                error_message=error_message,
            )
            results.append(result)

            if result.passed:
                diagnostics.passed_tasks += 1
            else:
                diagnostics.failed_tasks += 1

        except Exception as e:
            diagnostics.failed_tasks += 1
            results.append(
                EvalTaskResult(
                    task_id=task_id,
                    passed=False,
                    metrics=EvalMetrics(),
                    error_message=str(e),
                )
            )

    return results, diagnostics


# ── Tier capability checking ───────────────────────────────────────────


def check_tier_capabilities(
    tier: str,
    adapter: Any,
) -> dict[str, bool]:
    """Check if adapter supports a given tier.

    Args:
        tier: Tier identifier (tier0, tier1, tier2, tier3)
        adapter: Runtime adapter

    Returns:
        Dict with capability flags
    """
    capabilities = {
        "tier0": True,  # Basic - always available
        "tier1": True,  # Task completion - always available
        "tier2": False,
        "tier3": False,
    }

    if tier in ("tier2", "tier3") and adapter and hasattr(adapter, "get_capabilities"):
        caps = adapter.get_capabilities()
        if tier == "tier2":
            capabilities["tier2"] = getattr(caps, "supports_grammar_constraints", False)
        if tier == "tier3":
            capabilities["tier3"] = hasattr(adapter, "register_action_hook")

    return {"tier": tier, "supported": capabilities.get(tier, False)}


# ── Comparison table generator ─────────────────────────────────────────


def render_comparison_table(
    tier0_results: list[EvalTaskResult],
    tier1_results: list[EvalTaskResult] | None = None,
) -> str:
    """Render markdown comparison table with stable column order.

    Args:
        tier0_results: Tier 0 evaluation results
        tier1_results: Optional Tier 1 evaluation results

    Returns:
        Markdown table string
    """
    lines = [
        "# NSIL Evaluation Results",
        "",
        "| Metric | Tier0 | Tier1 |",
        "|--------|-------|-------|",
    ]

    # Aggregate metrics from results
    t0_metrics = _aggregate_metrics(tier0_results)
    t1_metrics = _aggregate_metrics(tier1_results or [])

    # Add metric rows in stable order
    metric_names = [
        ("policy_compliance_rate", "Policy Compliance"),
        ("latency_ms_per_action", "Latency (ms/action)"),
        ("task_completion_rate", "Task Completion"),
        ("token_cost", "Token Cost"),
        ("false_positive_repair_rate", "FP Repair Rate"),
    ]

    for key, label in metric_names:
        t0_val = t0_metrics.get(key, 0.0)
        t1_val = t1_metrics.get(key, 0.0)
        lines.append(f"| {label} | {t0_val:.2f} | {t1_val:.2f} |")

    lines.append("")
    lines.append("## Diagnostics")
    lines.append("")

    t0_diag = _aggregate_diagnostics(tier0_results)
    t1_diag = _aggregate_diagnostics(tier1_results or [])

    lines.append(f"- Tier0: {t0_diag['passed']}/{t0_diag['total']} passed")
    if tier1_results:
        lines.append(f"- Tier1: {t1_diag['passed']}/{t1_diag['total']} passed")

    return "\n".join(lines)


def _aggregate_metrics(results: list[EvalTaskResult]) -> dict[str, float]:
    """Aggregate metrics from results."""
    if not results:
        return {
            "policy_compliance_rate": 0.0,
            "latency_ms_per_action": 0.0,
            "task_completion_rate": 0.0,
            "token_cost": 0.0,
            "false_positive_repair_rate": 0.0,
        }

    total = len(results)
    policy_sum = sum(r.metrics.policy_compliance_rate for r in results)
    latency_sum = sum(r.metrics.latency_ms_per_action for r in results)
    completion_sum = sum(r.metrics.task_completion_rate for r in results)
    token_sum = sum(r.metrics.token_cost for r in results)
    fp_sum = sum(r.metrics.false_positive_repair_rate for r in results)

    return {
        "policy_compliance_rate": policy_sum / total,
        "latency_ms_per_action": latency_sum / total,
        "task_completion_rate": completion_sum / total,
        "token_cost": token_sum,
        "false_positive_repair_rate": fp_sum / total,
    }


def _aggregate_diagnostics(results: list[EvalTaskResult]) -> dict[str, int]:
    """Aggregate diagnostics from results."""
    passed = sum(1 for r in results if r.passed)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
    }


# ── Task fixture helpers ────────────────────────────────────────────────


def load_task_fixtures(fixture_dir: str) -> dict[str, dict[str, Any]]:
    """Load task fixtures from directory.

    Returns dict mapping task_id to fixture data.
    Missing files return structured failure, not crash.

    Args:
        fixture_dir: Directory containing fixture JSON files

    Returns:
        Dict of task_id -> fixture data
    """
    import json
    from pathlib import Path

    fixtures: dict[str, dict[str, Any]] = {}

    path = Path(fixture_dir)
    if not path.exists():
        return fixtures

    for fixture_file in path.glob("*.json"):
        try:
            task_id = fixture_file.stem
            data = json.loads(fixture_file.read_text())
            fixtures[task_id] = data
        except (json.JSONDecodeError, OSError):
            # Skip invalid fixtures
            continue

    return fixtures


def make_tier0_task(
    task_id: str,
    constraints: list[str],
    fixture: dict[str, Any],
) -> dict[str, Any]:
    """Create a Tier 0 task definition.

    Args:
        task_id: Unique task identifier
        constraints: List of policy constraints to test
        fixture: Task fixture data

    Returns:
        Task definition dict
    """
    return {
        "id": task_id,
        "tier": 0,
        "constraints": constraints,
        "fixture": fixture,
    }


def make_tier1_task(
    task_id: str,
    expected_outcome: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    """Create a Tier 1 task definition.

    Args:
        task_id: Unique task identifier
        expected_outcome: Expected task outcome
        fixture: Task fixture data

    Returns:
        Task definition dict
    """
    return {
        "id": task_id,
        "tier": 1,
        "expected_outcome": expected_outcome,
        "fixture": fixture,
    }
