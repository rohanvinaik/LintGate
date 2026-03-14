"""Convergence orchestrator for iterative specification improvement.

Decides which auto_generate_unit targets to iterate on, when to stop,
and tracks specification trajectory across iterations. Non-destructive:
all decisions are advisory — execution happens through the existing
test_rebuild_* pipeline.

Stop criteria:
    - threshold_reached: spec_level AND kill_rate meet targets
    - phase_complete: function reached complete phase
    - stall_detected: delta < min_delta for stall_limit consecutive iterations
    - stall_in_tail: stall while in tail phase → suggest decomposition
    - max_iterations: per-function iteration limit reached
    - budget_exhausted: total elapsed exceeds budget
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IterationRecord:
    """Metrics from one iteration of test improvement."""

    iteration: int
    spec_level: float
    kill_rate: float
    delta_spec: float = 0.0
    delta_kill: float = 0.0
    phase: str = ""
    timestamp_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "spec_level": self.spec_level,
            "kill_rate": self.kill_rate,
            "delta_spec": self.delta_spec,
            "delta_kill": self.delta_kill,
            "phase": self.phase,
            "timestamp_ms": self.timestamp_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IterationRecord:
        return cls(
            iteration=int(data.get("iteration", 0)),
            spec_level=float(data.get("spec_level", 0.0)),
            kill_rate=float(data.get("kill_rate", 0.0)),
            delta_spec=float(data.get("delta_spec", 0.0)),
            delta_kill=float(data.get("delta_kill", 0.0)),
            phase=str(data.get("phase", "")),
            timestamp_ms=float(data.get("timestamp_ms", 0.0)),
        )


@dataclass
class TargetState:
    """Current convergence state for one function."""

    function_key: str
    source_file: str = ""
    target_test_file: str = ""
    spec_level: float = 0.0
    kill_rate: float = 0.0
    phase: str = "bulk"
    convergence_rate: float = 0.0
    trajectory: list[IterationRecord] = field(default_factory=list)
    status: str = "eligible"  # eligible | converged | halted | decompose
    halt_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_key": self.function_key,
            "source_file": self.source_file,
            "target_test_file": self.target_test_file,
            "spec_level": self.spec_level,
            "kill_rate": self.kill_rate,
            "phase": self.phase,
            "convergence_rate": self.convergence_rate,
            "trajectory": [r.to_dict() for r in self.trajectory],
            "status": self.status,
            "halt_reason": self.halt_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetState:
        trajectory = [IterationRecord.from_dict(r) for r in data.get("trajectory", [])]
        return cls(
            function_key=str(data.get("function_key", "")),
            source_file=str(data.get("source_file", "")),
            target_test_file=str(data.get("target_test_file", "")),
            spec_level=float(data.get("spec_level", 0.0)),
            kill_rate=float(data.get("kill_rate", 0.0)),
            phase=str(data.get("phase", "bulk")),
            convergence_rate=float(data.get("convergence_rate", 0.0)),
            trajectory=trajectory,
            status=str(data.get("status", "eligible")),
            halt_reason=str(data.get("halt_reason", "")),
        )


@dataclass
class ConvergenceConfig:
    """Thresholds and budgets for convergence."""

    target_spec_level: float = 0.80
    target_kill_rate: float = 0.70
    max_iterations: int = 5
    min_delta: float = 0.01
    stall_limit: int = 2
    budget_ms: float = 30_000


@dataclass
class ConvergenceDecision:
    """Decision for one target in the current iteration."""

    function_key: str
    action: str  # "iterate" | "halt" | "converged" | "decompose"
    priority: int  # lower = higher priority
    reason: str = ""


@dataclass
class OrchestratorState:
    """Overall convergence state across all targets."""

    targets: dict[str, TargetState] = field(default_factory=dict)
    iteration_count: int = 0
    start_ms: float = 0.0
    elapsed_ms: float = 0.0

    @property
    def converged_count(self) -> int:
        return sum(1 for t in self.targets.values() if t.status == "converged")

    @property
    def halted_count(self) -> int:
        return sum(1 for t in self.targets.values() if t.status == "halted")

    @property
    def eligible_count(self) -> int:
        return sum(1 for t in self.targets.values() if t.status == "eligible")

    @property
    def ready_to_apply(self) -> bool:
        return self.eligible_count == 0 and self.converged_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": {k: v.to_dict() for k, v in self.targets.items()},
            "iteration_count": self.iteration_count,
            "start_ms": self.start_ms,
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrchestratorState:
        targets = {k: TargetState.from_dict(v) for k, v in data.get("targets", {}).items()}
        return cls(
            targets=targets,
            iteration_count=int(data.get("iteration_count", 0)),
            start_ms=float(data.get("start_ms", 0.0)),
            elapsed_ms=float(data.get("elapsed_ms", 0.0)),
        )


# ── Initialization ────────────────────────────────────────────────


def init_state(plan: Any) -> OrchestratorState:
    """Initialize orchestrator state from a test rebuild manifest.

    Extracts auto_generate_unit targets from the plan.
    """
    from lintgate.specification.test_regeneration_strategy import Strategy

    targets: dict[str, TargetState] = {}
    for func in plan.functions:
        if func.strategy == Strategy.AUTO_GENERATE_UNIT:
            targets[func.evidence.function_key] = TargetState(
                function_key=func.evidence.function_key,
                source_file=func.evidence.source_file,
                target_test_file=func.target_test_file,
            )

    return OrchestratorState(
        targets=targets,
        start_ms=time.monotonic() * 1000,
    )


def init_from_targets(target_dicts: list[dict[str, str]]) -> OrchestratorState:
    """Initialize from a list of target dicts (for testing/flexibility).

    Each dict should have at minimum ``function_key``.
    """
    targets: dict[str, TargetState] = {}
    for t in target_dicts:
        fk = t["function_key"]
        targets[fk] = TargetState(
            function_key=fk,
            source_file=t.get("source_file", ""),
            target_test_file=t.get("target_test_file", ""),
        )
    return OrchestratorState(
        targets=targets,
        start_ms=time.monotonic() * 1000,
    )


# ── State updates ─────────────────────────────────────────────────


def update_target(
    state: OrchestratorState,
    function_key: str,
    spec_level: float,
    kill_rate: float,
    phase: str = "",
    convergence_rate: float = 0.0,
) -> None:
    """Record metrics for a target after an iteration (mutates state)."""
    target = state.targets.get(function_key)
    if target is None:
        return

    record = IterationRecord(
        iteration=len(target.trajectory) + 1,
        spec_level=spec_level,
        kill_rate=kill_rate,
        delta_spec=spec_level - target.spec_level,
        delta_kill=kill_rate - target.kill_rate,
        phase=phase or target.phase,
        timestamp_ms=time.monotonic() * 1000,
    )
    target.trajectory.append(record)
    target.spec_level = spec_level
    target.kill_rate = kill_rate
    if phase:
        target.phase = phase
    if convergence_rate > 0:
        target.convergence_rate = convergence_rate

    state.iteration_count += 1
    state.elapsed_ms = (time.monotonic() * 1000) - state.start_ms


# ── Decision engine ───────────────────────────────────────────────

_PHASE_PRIORITY = {"bulk": 1, "transition": 2, "tail": 3, "complete": 4}


def decide(
    state: OrchestratorState,
    config: ConvergenceConfig | None = None,
) -> list[ConvergenceDecision]:
    """Decide what to do with each eligible target.

    Returns prioritized decisions: iterate, halt, converge, or decompose.
    Targets already converged/halted are excluded.
    """
    cfg = config or ConvergenceConfig()
    decisions: list[ConvergenceDecision] = []

    budget_left = cfg.budget_ms - state.elapsed_ms

    for fk, target in state.targets.items():
        if target.status != "eligible":
            continue

        action, reason = _check_halt(target, cfg, budget_left)
        priority = _compute_priority(target)

        if action == "converged":
            target.status = "converged"
            target.halt_reason = reason
        elif action in ("halt", "decompose"):
            target.status = action if action == "decompose" else "halted"
            target.halt_reason = reason

        decisions.append(
            ConvergenceDecision(
                function_key=fk,
                action=action,
                priority=priority,
                reason=reason,
            )
        )

    decisions.sort(key=lambda d: d.priority)
    return decisions


def _check_halt(
    target: TargetState,
    config: ConvergenceConfig,
    budget_remaining_ms: float,
) -> tuple[str, str]:
    """Check if a target should stop iterating. Returns (action, reason)."""
    # 1. Threshold reached
    if (
        target.spec_level >= config.target_spec_level
        and target.kill_rate >= config.target_kill_rate
    ):
        return "converged", "threshold_reached"

    # 2. Phase is complete
    if target.phase == "complete":
        return "converged", "phase_complete"

    # 3. Max iterations
    if len(target.trajectory) >= config.max_iterations:
        return "halt", "max_iterations"

    # 4. Budget exhaustion
    if budget_remaining_ms <= 0:
        return "halt", "budget_exhausted"

    # 5. Stall detection
    if len(target.trajectory) >= config.stall_limit:
        recent = target.trajectory[-config.stall_limit :]
        if all(
            abs(r.delta_spec) < config.min_delta and abs(r.delta_kill) < config.min_delta
            for r in recent
        ):
            if target.phase == "tail":
                return "decompose", "stall_in_tail"
            return "halt", "stall_detected"

    return "iterate", ""


def _compute_priority(target: TargetState) -> int:
    """Compute iteration priority (lower = higher priority).

    Bulk phase functions get highest priority (most improvement per iteration).
    Within same phase, lower spec_level gets higher priority (more room to grow).
    """
    phase_base = _PHASE_PRIORITY.get(target.phase, 2) * 100
    spec_offset = int(target.spec_level * 99)
    return phase_base + spec_offset


# ── Reporting ─────────────────────────────────────────────────────


def summarize(state: OrchestratorState) -> dict[str, Any]:
    """Summarize current convergence state for reporting."""
    target_summaries = []
    for fk, t in state.targets.items():
        s: dict[str, Any] = {
            "function_key": fk,
            "status": t.status,
            "spec_level": round(t.spec_level, 3),
            "kill_rate": round(t.kill_rate, 3),
            "phase": t.phase,
            "iterations": len(t.trajectory),
        }
        if t.halt_reason:
            s["halt_reason"] = t.halt_reason
        if t.trajectory:
            last = t.trajectory[-1]
            s["last_delta_spec"] = round(last.delta_spec, 4)
            s["last_delta_kill"] = round(last.delta_kill, 4)
        target_summaries.append(s)

    return {
        "total_targets": len(state.targets),
        "converged": state.converged_count,
        "halted": state.halted_count,
        "eligible": state.eligible_count,
        "iterations": state.iteration_count,
        "elapsed_ms": round(state.elapsed_ms, 1),
        "ready_to_apply": state.ready_to_apply,
        "targets": target_summaries,
    }
