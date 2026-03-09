"""NSIL Benchmarking — Performance and Accuracy Metrics."""

import time
from dataclasses import dataclass, field
from typing import Any

from lintgate.nsil.action_verifier import verify_action


@dataclass
class BenchmarkResult:
    """Result of an NSIL benchmark run."""

    name: str
    passed: bool
    latency_ms: float
    violations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_nsil_benchmark(scenarios: list[dict[str, Any]]) -> list[BenchmarkResult]:
    """Run a set of scenarios and return benchmark results."""
    results = []
    for scenario in scenarios:
        start_time = time.perf_counter()
        try:
            res = verify_action(
                scenario["proposal"],
                gate_contract=scenario.get("gate_contract"),
                active_constraints=scenario.get("active_constraints"),
            )
            latency = (time.perf_counter() - start_time) * 1000

            passed = res.approved == scenario["expected_approved"]
            if (
                not scenario["expected_approved"]
                and scenario.get("expected_violation")
                and scenario["expected_violation"] not in res.violation_codes
            ):
                passed = False

            results.append(
                BenchmarkResult(
                    name=scenario["name"],
                    passed=passed,
                    latency_ms=latency,
                    violations=list(res.violation_codes),
                )
            )
        except Exception as e:
            latency = (time.perf_counter() - start_time) * 1000
            results.append(
                BenchmarkResult(
                    name=scenario["name"],
                    passed=False,
                    latency_ms=latency,
                    errors=[str(e)],
                )
            )

    return results
