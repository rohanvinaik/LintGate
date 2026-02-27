"""Mutation channel — integrates mutation testing survivors and coverage into ControlPlane."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.policy import MutationTelemetry, RuntimeBudget
from lintgate.mutation.state import CoverageDepth, MutationStateManager
from lintgate.state import MUTATION_CACHE_DIR
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.mutation.state import FunctionMutationState

import contextlib

from lintgate.mutation.policy import CalibratedPolicy


class MutationChannel:
    """Supervision channel for mutation testing and specification quality."""

    name = "mutation"
    timeout_ms = 15000  # Mutation runs can be slow
    blocking_capable = True

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run when Python files are present."""
        return bool(event.project_root)

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute mutation analysis."""
        start = time.perf_counter()

        state_path = os.path.join(MUTATION_CACHE_DIR, "state.json")
        state_manager = MutationStateManager(state_path)
        with contextlib.suppress(OSError, ValueError):
            state_manager.load()

        budget = RuntimeBudget()
        MutationEngine(state_manager, budget)
        MutationTelemetry("channel_run")

        # 1. Background loading of existing state
        # For changed files, we might want to run sampling
        relevant_files = event.files_changed or []
        if not relevant_files:
            # Fallback to discover all
            from lintgate.channels.performance_channel import _discover_python_files

            relevant_files = _discover_python_files(event.project_root)

        # 2. Heuristic: queue sampling if state is stale or missing for changed files
        any_stale = False
        for f in event.files_changed or []:
            func_id = f  # Use file path as a rough function_id for staleness check
            if state_manager.requires_run(func_id, "unknown", "unknown", CoverageDepth.SAMPLED):
                any_stale = True
                break

        if any_stale and len(relevant_files) <= 5:  # Limit auto-sampling to small PRs
            from lintgate.mutation.automation import global_orchestrator

            for f in relevant_files:
                global_orchestrator.enqueue(f)

        # 3. Analyze state and build findings
        findings: list[LintIssue] = []

        all_states = state_manager.state
        policy = CalibratedPolicy()
        for _key, state in all_states.items():
            # Only report on relevant files if provided
            if relevant_files and state.file_path not in relevant_files:
                continue

            findings.extend(self._analyze_state(state, all_states, policy))

        elapsed_ms = (time.perf_counter() - start) * 1000

        metrics = {
            "functions_profiled": len(all_states),
            "vulnerable_functions": sum(1 for s in all_states.values() if s.survival_rate > 0.3),
            "avg_survival": sum(s.survival_rate for s in all_states.values())
            / max(len(all_states), 1),
        }

        status: Literal["pass", "fail"] = (
            "fail" if any(f.severity == "blocking" for f in findings) else "pass"
        )
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            severity = "informational"
            if any(f.severity == "blocking" for f in findings):
                severity = "blocking"
            elif any(f.severity == "warning" for f in findings):
                severity = "warning"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            metrics=metrics,
            duration_ms=elapsed_ms,
        )

    def _analyze_state(
        self, state: FunctionMutationState, all_states: dict, policy: CalibratedPolicy
    ) -> list[LintIssue]:
        """Generate findings for a specific function state."""
        issues = []
        warning_thresh, blocking_thresh = policy.get_thresholds(state, all_states)
        confidence_score = policy.get_confidence(state)

        # MUT001/002: Survivors found
        if state.survived > 0:
            rate = state.survival_rate
            severity: Literal["blocking", "warning", "informational"] = "informational"
            kind = "MUT001"

            if rate > blocking_thresh and state.depth == CoverageDepth.PROFILED:
                severity = "blocking"
                kind = "MUT002"
            elif rate > warning_thresh:
                severity = "warning"

            issues.append(
                LintIssue(
                    linter=self.name,
                    kind=kind,
                    message=(
                        f"High mutation survival rate ({rate:.1%}) in '{state.function_name}'. "
                        f"{state.survived} of {state.total} mutants survived."
                    ),
                    file=state.file_path,
                    severity=severity,
                    confidence=confidence_score,
                    evidence={
                        "survived": state.survived,
                        "total": state.total,
                        "survival_rate": rate,
                        "depth": state.depth.value,
                        "killed_by_assertion": state.killed_by_assertion,
                        "killed_by_crash": state.killed_by_crash,
                        "calibrated_thresholds": {
                            "warning": warning_thresh,
                            "blocking": blocking_thresh,
                        },
                    },
                    suggestions=[
                        "Add assertions that verify the return value or state change more strictly.",
                        "If this is a pure function, use property-based testing (Hypothesis/icontract).",
                    ],
                )
            )

        # MUT003: Insufficient coverage depth
        if state.depth == CoverageDepth.SAMPLED and state.survival_rate > 0.2:
            issues.append(
                LintIssue(
                    linter=self.name,
                    kind="MUT003",
                    message=f"Function '{state.function_name}' has survivors in sampling and requires full profiling.",
                    file=state.file_path,
                    severity="informational",
                    confidence=0.9,
                    evidence={"depth": state.depth.value},
                    suggestions=["Run mutation_run_full tool on this file."],
                )
            )

        # MUTCH007: Decomposition Candidate (High Entanglement)
        surviving_cats = [c for c, count in state.survived_by_category.items() if count > 0]
        if state.survival_rate >= 0.50 and len(surviving_cats) >= 3:
            issues.append(
                LintIssue(
                    linter=self.name,
                    kind="MUTCH007",
                    message=f"High multi-category mutation survival ({state.survival_rate:.1%}) indicates '{state.function_name}' is highly entangled and does too much.",
                    file=state.file_path,
                    severity="blocking",
                    confidence=0.9,
                    evidence={"survived_categories": surviving_cats},
                    suggestions=[
                        "Use the `mutation_decompose` tool to identify split candidates.",
                        "Use the `mutation_prescribe` tool for specific refactoring intents.",
                        "Use the `mutation_run_full` tool to get a precise breakdown of surviving mutants.",
                    ],
                )
            )

        return issues
