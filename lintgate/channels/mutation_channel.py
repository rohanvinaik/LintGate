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

    def execute(
        self, event: SupervisionEvent, config: ControlPlaneConfig
    ) -> ChannelResult:
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
            if state_manager.requires_run(
                func_id, "unknown", "unknown", CoverageDepth.SAMPLED
            ):
                any_stale = True
                break

        if any_stale and len(relevant_files) <= 5:  # Limit auto-sampling to small PRs
            from lintgate.mutation.automation import global_orchestrator

            for f in relevant_files:
                global_orchestrator.enqueue(f)

        # 3. Analyze state and build findings
        findings: list[LintIssue] = []

        # Compute current file hashes for staleness detection (#208)
        file_hashes: dict[str, str] = {}
        for f in relevant_files:
            abs_path = (
                os.path.join(event.project_root, f) if not os.path.isabs(f) else f
            )
            try:
                from lintgate.mutation.state import compute_content_hash

                content = open(abs_path, errors="replace").read()
                file_hashes[f] = compute_content_hash(content)
            except OSError:
                pass

        # Read enforcement mode from channel config
        _ch_config = config.channels.get("mutation")
        _mutation_settings = _ch_config.settings if _ch_config else {}
        enforcement_mode = _mutation_settings.get("enforcement_mode", "audit")

        # Build algebra manifest for MUTCH004 cross-reference (#207)
        manifest_hints: dict[str, tuple[str, ...]] = {}
        try:
            from lintgate.linters.performance_checks.manifest import build_manifest

            manifest = build_manifest(event.project_root, relevant_files, enforcement_mode=enforcement_mode)
            for func_key, func_props in manifest.functions.items():
                if func_props.purity.is_pure and func_props.optimization_hints:
                    manifest_hints[func_key] = func_props.optimization_hints
        except Exception:
            pass  # Graceful degradation if manifest build fails

        all_states = state_manager.state
        policy = CalibratedPolicy()
        for _key, state in all_states.items():
            # Only report on relevant files if provided
            if relevant_files and state.file_path not in relevant_files:
                continue

            current_hash = file_hashes.get(state.file_path)
            findings.extend(
                self._analyze_state(state, all_states, policy, current_hash)
            )

        # MUTCH004: Pure functions with optimization hints + insufficient spec evidence
        findings.extend(self._check_mutch004(all_states, manifest_hints, enforcement_mode))

        elapsed_ms = (time.perf_counter() - start) * 1000

        metrics = {
            "functions_profiled": len(all_states),
            "vulnerable_functions": sum(
                1 for s in all_states.values() if s.survival_rate > 0.3
            ),
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
        self,
        state: FunctionMutationState,
        all_states: dict,
        policy: CalibratedPolicy,
        current_file_hash: str | None = None,
    ) -> list[LintIssue]:
        """Generate findings for a specific function state."""
        issues = []
        warning_thresh, blocking_thresh = policy.get_thresholds(state, all_states)
        confidence_score = policy.get_confidence(state)

        # MUTCH005: Stale mutation data (code_hash mismatch)
        if (
            state.depth != CoverageDepth.NONE
            and current_file_hash
            and state.code_hash
            and state.code_hash != current_file_hash
        ):
            issues.append(
                LintIssue(
                    linter=self.name,
                    kind="MUTCH005",
                    message=(
                        f"Mutation data for '{state.function_name}' is stale "
                        f"(code changed since last run). Re-run mutation_run_sampling."
                    ),
                    file=state.file_path,
                    severity="informational",
                    confidence=0.95,
                    evidence={
                        "stored_hash": state.code_hash[:12],
                        "current_hash": current_file_hash[:12],
                        "is_gateable": False,
                        "depth": state.depth.value,
                    },
                    suggestions=["Run mutation_run_sampling to refresh mutation data."],
                )
            )

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

            # Coverage depth severity cap (#208): sampled findings never exceed informational
            if state.depth == CoverageDepth.SAMPLED:
                severity = "informational"

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
                        "is_gateable": state.is_gateable,
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

        # MUT003: Insufficient coverage depth (existing behavior preserved)
        if state.depth == CoverageDepth.SAMPLED and state.survival_rate > 0.2:
            issues.append(
                LintIssue(
                    linter=self.name,
                    kind="MUT003",
                    message=f"Function '{state.function_name}' has survivors in sampling and requires full profiling.",
                    file=state.file_path,
                    severity="informational",
                    confidence=0.9,
                    evidence={"depth": state.depth.value, "is_gateable": False},
                    suggestions=["Run mutation_run_full tool on this file."],
                )
            )

        # MUTCH006: Sampled-depth advisory signal (#208)
        if state.depth == CoverageDepth.SAMPLED and state.survival_rate > 0.2:
            issues.append(
                LintIssue(
                    linter=self.name,
                    kind="MUTCH006",
                    message=(
                        f"Sampled mutation data for '{state.function_name}' "
                        f"(survival={state.survival_rate:.0%}). "
                        f"Directional signal only — not gateable. "
                        f"Run mutation_run_full for authoritative data."
                    ),
                    file=state.file_path,
                    severity="informational",
                    confidence=0.70,
                    evidence={
                        "depth": "sampled",
                        "is_gateable": False,
                        "survival_rate": state.survival_rate,
                    },
                    suggestions=[
                        "Run mutation_run_full tool on this file for authoritative data."
                    ],
                )
            )

        # MUTCH007: Decomposition Candidate (High Entanglement)
        surviving_cats = [
            c for c, count in state.survived_by_category.items() if count > 0
        ]
        if state.survival_rate >= 0.50 and len(surviving_cats) >= 3:
            issues.append(
                LintIssue(
                    linter=self.name,
                    kind="MUTCH007",
                    message=f"High multi-category mutation survival ({state.survival_rate:.1%}) indicates '{state.function_name}' is highly entangled and does too much.",
                    file=state.file_path,
                    severity="blocking",
                    confidence=0.9,
                    evidence={
                        "survived_categories": surviving_cats,
                        "is_gateable": state.is_gateable,
                        "depth": state.depth.value,
                    },
                    suggestions=[
                        "Use the `mutation_decompose` tool to identify split candidates.",
                        "Use the `mutation_prescribe` tool for specific refactoring intents.",
                        "Use the `mutation_run_full` tool to get a precise breakdown of surviving mutants.",
                    ],
                )
            )

        return issues

    def _check_mutch004(
        self,
        all_states: dict[str, FunctionMutationState],
        manifest_hints: dict[str, tuple[str, ...]],
        enforcement_mode: str = "audit",
    ) -> list[LintIssue]:
        """MUTCH004: Pure function with optimization hints AND insufficient specification.

        Emitted when:
        1. Function is pure (in algebra manifest with optimization hints)
        2. Mutation data exists (no data ≠ bad data)
        3. specification_strength < 0.5 (assertion-kills are less than half of total kills)

        Phase 1 (audit): informational only, original confidence.
        Phase 2 (graduated/strict): severity and confidence modulated by resolve_gate_status.
        """
        from lintgate.mutation.prescriptions import resolve_gate_status

        issues: list[LintIssue] = []
        for func_key, hints in manifest_hints.items():
            state = all_states.get(func_key)
            if not state or state.total == 0:
                continue  # No mutation data — skip (no data ≠ bad data)

            spec_strength = state.specification_strength
            if spec_strength >= 0.5:
                continue  # Sufficient spec evidence

            gate_status, multiplier = resolve_gate_status(spec_strength, enforcement_mode)

            # In audit mode, always informational (Phase 1 preserved)
            # In graduated/strict with warn/fail → warning severity
            severity = "informational"
            if enforcement_mode != "audit" and gate_status != "pass":
                severity = "warning"

            original_confidence = 0.85
            adjusted_confidence = original_confidence * multiplier

            hint_list = ", ".join(hints)
            issues.append(
                LintIssue(
                    linter=self.name,
                    kind="MUTCH004",
                    message=(
                        f"Pure function '{state.function_name}' has optimization hints "
                        f"({hint_list}) but insufficient specification evidence "
                        f"(spec_strength={spec_strength:.0%}). "
                        f"Assertion-kills must exceed 50% of total kills to validate hints."
                    ),
                    file=state.file_path,
                    severity=severity,
                    confidence=adjusted_confidence if enforcement_mode != "audit" else original_confidence,
                    evidence={
                        "optimization_hints": list(hints),
                        "specification_strength": spec_strength,
                        "killed_by_assertion": state.killed_by_assertion,
                        "killed_by_crash": state.killed_by_crash,
                        "survival_rate": state.survival_rate,
                        "depth": state.depth.value,
                        "is_gateable": state.is_gateable,
                        "gate_status": gate_status,
                        "enforcement_mode": enforcement_mode,
                        "original_confidence": original_confidence,
                        "adjusted_confidence": adjusted_confidence,
                    },
                    suggestions=[
                        "Add assertions that verify return values, not just crash-freedom.",
                        "Use mutation_prescribe tool to see specific specification gaps.",
                        "Use mutation_run_full for exhaustive specification analysis.",
                    ],
                )
            )
        return issues
