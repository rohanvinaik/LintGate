"""Lint channel — wraps the entire existing LintGate pipeline.

This is the bridge between the existing lint pipeline and the new
ControlPlane mesh. It calls the exact same functions in the exact
same order:
  classify_change → select_tier → build_registry → run_linters →
  aggregate_results → update_issue_memory → update_pattern_bank

The result is converted from AggregatedResult → ChannelResult.

Design principle: ZERO changes to existing pipeline modules. This channel
is a pure wrapper — if the channel is removed, nothing else changes.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import TYPE_CHECKING, Any, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintTier

if TYPE_CHECKING:
    from lintgate.types import AggregatedResult


class LintChannel:
    """Supervision channel that wraps the existing LintGate lint pipeline.

    Implements the Channel protocol by calling the existing pipeline
    functions in order and converting the result to a ChannelResult.

    This is the highest-risk component of the ControlPlane: it must
    produce identical results to the direct pipeline. See test_lint_parity.py.
    """

    name = "lint"
    timeout_ms = 8000
    blocking_capable = True

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run on any event with a non-none risk classification.

        Mirrors the quick-exit logic in hook_posttooluse.py:
        - risk_level == "none" → skip
        - No classification → skip
        """
        if event.surface == "mcp":
            return True
        classification = event.change_classification
        if classification is None:
            return False
        return classification.risk_level != "none"

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute the full lint pipeline and convert to ChannelResult.

        Calls the same functions as hook_posttooluse.main() phases 2-4:
        1. select_tier(classification, project_config)
        2. build_registry(project_config)
        3. run_linters(tier, project_config, registry, timeout)
        4. aggregate_results(linter_results, project_config)
        5. update_issue_memory(cwd, all_issues) [side-effect]
        6. update_pattern_bank(cwd, all_issues) [side-effect]
        7. Convert AggregatedResult → ChannelResult
        """
        start = time.perf_counter()

        classification = event.change_classification
        if classification is None:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "no_classification"},
            )

        # Build a ProjectConfig from event data
        project_config = self._build_project_config(event)

        # Phase 2: Select lint tier
        from lintgate.tier_selector import select_tier

        tier = select_tier(classification, project_config)
        if tier.skip:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "tier_skip", "tier": tier.name},
            )
        tier = _apply_mcp_strictness_override(event, tier)

        # Phase 3: Build registry and run linters
        from lintgate.registry import build_registry

        registry = build_registry(project_config)

        timeout_budget_ms = _compute_dynamic_timeout_ms(config, self.name, tier.files)
        remaining_ms = timeout_budget_ms - int((time.perf_counter() - start) * 1000)
        remaining_ms = max(remaining_ms, 2000)

        from lintgate.lint_runner import run_linters

        linter_results = run_linters(tier, project_config, registry, timeout_ms=remaining_ms)

        # Phase 4: Aggregate results
        from lintgate.results_aggregator import aggregate_results

        aggregated = aggregate_results(
            linter_results,
            project_config,
            tier_name=tier.name,
            tier_reason=tier.reason,
        )

        # Side effects: issue memory + pattern bank
        all_issues = [
            *aggregated.blocking,
            *aggregated.warnings,
            *aggregated.informational,
        ]

        recurrence = {
            "repeated_issue_count": 0,
            "unique_signatures_tracked": 0,
            "top_repeated": [],
        }
        with contextlib.suppress(Exception):
            from lintgate.state import update_issue_memory

            recurrence = update_issue_memory(event.project_root, all_issues)

        pattern_report: dict[str, Any] = {"alerted_patterns": [], "top_categories": []}
        with contextlib.suppress(Exception):
            from lintgate.pattern_bank import update_pattern_bank

            pattern_report = update_pattern_bank(event.project_root, all_issues)

        # Convert to ChannelResult
        elapsed_ms = (time.perf_counter() - start) * 1000
        return self._to_channel_result(
            aggregated,
            recurrence,
            pattern_report,
            elapsed_ms,
            tier.strictness,
            timeout_budget_ms,
        )

    def execute_pure(
        self,
        event: SupervisionEvent,
        config: ControlPlaneConfig,
    ) -> tuple[ChannelResult, AggregatedResult]:
        """Execute the lint pipeline and return BOTH ChannelResult and AggregatedResult.

        Used by parity tests to compare the raw AggregatedResult against
        the direct pipeline output. Not used in production.
        """
        start = time.perf_counter()

        classification = event.change_classification
        if classification is None:
            return (
                ChannelResult(
                    channel=self.name,
                    status="skip",
                    severity="none",
                    metrics={"reason": "no_classification"},
                ),
                _empty_aggregated(),
            )

        project_config = self._build_project_config(event)

        from lintgate.tier_selector import select_tier

        tier = select_tier(classification, project_config)
        if tier.skip:
            return (
                ChannelResult(
                    channel=self.name,
                    status="skip",
                    severity="none",
                    metrics={"reason": "tier_skip", "tier": tier.name},
                ),
                _empty_aggregated(),
            )
        tier = _apply_mcp_strictness_override(event, tier)

        from lintgate.registry import build_registry

        registry = build_registry(project_config)

        remaining_ms = self.timeout_ms - int((time.perf_counter() - start) * 1000)
        remaining_ms = max(remaining_ms, 2000)

        from lintgate.lint_runner import run_linters

        linter_results = run_linters(tier, project_config, registry, timeout_ms=remaining_ms)

        from lintgate.results_aggregator import aggregate_results

        aggregated = aggregate_results(
            linter_results,
            project_config,
            tier_name=tier.name,
            tier_reason=tier.reason,
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        # No side effects in pure mode
        recurrence = {
            "repeated_issue_count": 0,
            "unique_signatures_tracked": 0,
            "top_repeated": [],
        }
        pattern_report: dict[str, Any] = {"alerted_patterns": [], "top_categories": []}

        channel_result = self._to_channel_result(
            aggregated,
            recurrence,
            pattern_report,
            elapsed_ms,
            tier.strictness,
            self.timeout_ms,
        )
        return channel_result, aggregated

    def _to_channel_result(
        self,
        aggregated: AggregatedResult,
        recurrence: dict[str, Any],
        pattern_report: dict[str, Any],
        duration_ms: float,
        tier_strictness: str,
        timeout_budget_ms: int,
    ) -> ChannelResult:
        """Convert AggregatedResult → ChannelResult.

        Mapping rules:
        - status: "fail" if any blocking or warning issues, "pass" otherwise
        - severity: "blocking" if blocking issues, "warning" if warnings, "none" otherwise
        - findings: all issues merged (blocking + warnings + informational)
        - metrics: carries forward aggregated.metrics plus recurrence/pattern data
        """
        all_findings = [
            *aggregated.blocking,
            *aggregated.warnings,
            *aggregated.informational,
        ]

        status: Literal["pass", "fail"]
        severity: Literal["blocking", "warning", "informational", "none"]
        if aggregated.blocking:
            status = "fail"
            severity = "blocking"
        elif aggregated.warnings:
            status = "fail"
            severity = "warning"
        else:
            status = "pass"
            severity = "none"

        repairs = _build_lint_repairs(all_findings, aggregated)

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=all_findings,
            repairs=repairs,
            metrics={
                **aggregated.metrics,
                "recurrence": recurrence,
                "pattern_alerts": pattern_report.get("alerted_patterns", []),
                "tier_used": aggregated.tier_used,
                "tier_reason": aggregated.tier_reason,
                "tier_strictness": tier_strictness,
                "linter_statuses": aggregated.linter_statuses,
                "files_linted": aggregated.files_linted,
                "lint_timeout_ms": timeout_budget_ms,
            },
            duration_ms=duration_ms,
        )

    def _build_project_config(self, event: SupervisionEvent) -> Any:
        """Build a ProjectConfig from event data.

        Tries to load from the project's lintgate.yaml first,
        falls back to a minimal config.
        """
        try:
            from lintgate.config import load_config

            return load_config(event.project_root)
        except Exception:
            from lintgate.types import ProjectConfig

            return ProjectConfig(project_root=event.project_root)


def _build_lint_repairs(
    findings: list,
    aggregated: AggregatedResult,
) -> list[RepairAction]:
    """Build repair actions for fixable lint findings.

    Emits command-type repairs for:
    - ruff format (formatting violations)
    - ruff import sorting (I001 violations)
    - ruff --fix (other safe auto-fixable violations)
    """
    repairs: list[RepairAction] = []
    has_fixable = any(getattr(f, "fixable", False) for f in findings)
    has_format = any(getattr(f, "kind", "") in ("E1", "W1") for f in findings)
    has_isort = any(getattr(f, "kind", "") == "I001" for f in findings)

    # Also check metrics for fixable count
    fixable_count = aggregated.metrics.get("fixable_count", 0)
    if not has_fixable and fixable_count > 0:
        has_fixable = True

    if has_fixable:
        repairs.append(
            RepairAction(
                channel="lint",
                kind="command",
                summary=f"Auto-fix {fixable_count} safe lint issue{'s' if fixable_count != 1 else ''}",
                payload={"command": "ruff check --fix ."},
                safe=True,
            )
        )

    if has_isort:
        repairs.append(
            RepairAction(
                channel="lint",
                kind="command",
                summary="Sort imports with ruff",
                payload={"command": "ruff check --select I --fix ."},
                safe=True,
            )
        )

    if has_format:
        repairs.append(
            RepairAction(
                channel="lint",
                kind="command",
                summary="Auto-format with ruff",
                payload={"command": "ruff format ."},
                safe=True,
            )
        )

    return repairs


def _empty_aggregated() -> AggregatedResult:
    """Create an empty AggregatedResult for skip cases."""
    from lintgate.types import AggregatedResult

    return AggregatedResult()


def _estimate_scope_chars(
    files: list[str],
    max_files: int = 200,
    max_chars: int = 2_000_000,
) -> int:
    """Estimate lint workload by summing scoped file byte sizes."""
    total = 0
    for filepath in files[:max_files]:
        try:
            total += max(0, os.path.getsize(filepath))
        except OSError:
            continue
        if total >= max_chars:
            return max_chars
    return total


def _compute_dynamic_timeout_ms(
    config: ControlPlaneConfig,
    channel_name: str,
    files: list[str],
) -> int:
    """Adaptive timeout using scoped content size.

    Formula:
      timeout = clamp(base + 2.5*scope_kchars + 15*file_count, 2000, 0.9*latency_budget)
    """
    base = max(2000, int(config.channel_timeout(channel_name)))
    scope_chars = _estimate_scope_chars(files)
    dynamic_add = int((scope_chars / 1000.0) * 2.5) + (15 * min(len(files), 200))
    dynamic = base + dynamic_add
    cap = max(base, int(config.latency_budget_ms * 0.9))
    return max(2000, min(dynamic, cap))


def _apply_mcp_strictness_override(event: SupervisionEvent, tier: LintTier) -> LintTier:
    """Apply explicit strictness for MCP-triggered runs when requested."""
    if event.surface != "mcp":
        return tier

    requested = event.raw_input.get("strictness")
    if requested not in {"relaxed", "normal", "strict"}:
        return tier

    if requested == tier.strictness:
        return tier

    return LintTier(
        name=tier.name,
        linters=tier.linters,
        files=tier.files,
        reason=tier.reason,
        strictness=requested,
        skip=tier.skip,
    )
