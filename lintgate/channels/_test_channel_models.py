"""Shared value objects for the test channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from lintgate.channels._test_types import TestRunResult
    from lintgate.controlplane.types import RepairAction
    from lintgate.types import LintIssue


class CoverageEvaluation(NamedTuple):
    """Result of evaluating coverage context for downstream gates."""

    targets_mode: str = "unknown"
    is_partial_run: bool = False
    coverage_pct: float | None = None
    coverage_ok: bool = True


@dataclass
class TestChannelContext:
    """Context for building test channel results with many metrics."""

    channel_name: str
    start: float
    findings: list[LintIssue]
    repairs: list[RepairAction]
    impacted_tests: list[str]
    test_result: TestRunResult | None
    cov_cfg: dict[str, Any]
    gate_result: Any
    cov_eval: CoverageEvaluation = field(default_factory=CoverageEvaluation)
    bootstrap_needed: bool = False

    @property
    def targets_mode(self) -> str:
        return self.cov_eval.targets_mode

    @property
    def coverage_pct(self) -> float | None:
        return self.cov_eval.coverage_pct

    @property
    def is_partial_run(self) -> bool:
        return self.cov_eval.is_partial_run

    @property
    def coverage_ok(self) -> bool:
        return self.cov_eval.coverage_ok


TestChannelContext.__test__ = False  # type: ignore[attr-defined]
