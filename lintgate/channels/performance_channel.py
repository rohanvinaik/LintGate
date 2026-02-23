"""Performance channel — algebraic property-based optimization analysis for ControlPlane."""

from __future__ import annotations

import contextlib
import time
from typing import Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.linters.performance_checks.manifest import build_manifest
from lintgate.types import LintIssue


def _discover_python_files(project_root: str) -> list[str]:
    """Discover Python files (simplified for channel)."""
    # In a real implementation this would ideally share the AST cache with structure_channel.
    from lintgate.channels.structure_channel import _discover_python_files as discover

    return discover(project_root)


class PerformanceChannel:
    """Supervision channel for codebase performance and algebraic properties.

    Advisory only — performance findings are informational unless
    corroborated by other channels or strictness rules.
    """

    name = "performance"
    timeout_ms = 10000
    blocking_capable = True

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run when Python files are present in the project."""
        return bool(event.project_root)

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute performance analysis using the algebraic properties bridge."""
        start = time.perf_counter()
        findings: list[LintIssue] = []

        project_root = event.project_root
        py_files = _discover_python_files(project_root)

        if not py_files:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "no_python_files"},
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # 1. Check for recent lint run with PERF findings to deduplicate (Phase 3.3)
        # We will stub this for now until we implement Phase 3 cross-tool dedup

        # 2. Build property manifest for project
        manifest = build_manifest(project_root, py_files)

        total_funcs = manifest.pure_count + manifest.impure_count
        purity_ratio = manifest.pure_count / max(total_funcs, 1)

        # 3. Run analyses

        # PERFCH001 — Purity summary
        if total_funcs > 10 and purity_ratio < 0.2:
            findings.append(
                LintIssue(
                    linter="performance_channel",
                    kind="PERFCH001",
                    message=f"Low project purity ratio ({purity_ratio:.1%}). Only {manifest.pure_count} of {total_funcs} functions are mathematically pure.",
                    file=project_root,  # Project-level finding
                    severity="informational",
                    confidence=0.9,
                    evidence={
                        "code": "PERFCH001",
                        "pure_count": manifest.pure_count,
                        "impure_count": manifest.impure_count,
                        "ratio": float(purity_ratio),
                    },
                    suggestions=[
                        "Extract pure domain logic from functions that perform I/O or state mutation",
                        "Consider dependency injection to isolate side-effects",
                    ],
                )
            )

        # Look closely at the top optimization opportunities
        for func_name, hints in manifest.optimization_potential:
            # We only want to report the very highest-value hints at the channel level
            # PERFCH003 — Parallelization / MapReduce
            if "parallelizable" in hints or "map-reduce-compatible" in hints:
                findings.append(
                    LintIssue(
                        linter="performance_channel",
                        kind="PERFCH003",
                        message=f"High-value optimization: '{func_name}' is pure and associative/commutative, making it trivially parallelizable.",
                        file=project_root,  # In a real implementation we would track file origin per function
                        severity="informational",
                        confidence=0.8,
                        evidence={"code": "PERFCH003", "function": func_name, "hints": hints},
                        suggestions=[
                            "Use multiprocessing.Pool.map or thread pools safely on this function."
                        ],
                    )
                )

            # PERFCH004 — High-value caching
            if "cache-without-invalidation" in hints:
                findings.append(
                    LintIssue(
                        linter="performance_channel",
                        kind="PERFCH004",
                        message=f"High-value caching: '{func_name}' is pure and idempotent. It is extremely safe to cache without complex invalidation.",
                        file=project_root,
                        severity="informational",
                        confidence=0.8,
                        evidence={"code": "PERFCH004", "function": func_name},
                        suggestions=["Decorate with @functools.lru_cache or @functools.cache"],
                    )
                )

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Build snapshot metrics
        metrics = {
            "pure_functions": manifest.pure_count,
            "impure_functions": manifest.impure_count,
            "purity_ratio": round(purity_ratio, 3),
            "properties_detected": {k.value: v for k, v in manifest.property_distribution.items()},
            "optimization_opportunities": len(manifest.optimization_potential),
        }

        # Telemetry: Emit metrics from Performance Channel (Phase 6.1)
        from lintgate.state import log_metric

        # We catch exceptions so telemetry doesn't break the channel
        with contextlib.suppress(Exception):
            log_metric(
                {
                    "event": "performance_analysis",
                    "project": project_root,
                    "pure_functions_found": manifest.pure_count,
                    "impure_functions_found": manifest.impure_count,
                    "purity_ratio": round(purity_ratio, 3),
                    "properties_detected": {
                        k.value: v for k, v in manifest.property_distribution.items()
                    },
                    "optimization_opportunities": len(manifest.optimization_potential),
                    "findings_count": len(findings),
                    "blocking_count": sum(1 for f in findings if f.severity == "blocking"),
                    "duration_ms": elapsed_ms,
                    "files_analyzed": len(py_files),
                }
            )

        status: Literal["pass", "fail"] = "fail" if findings else "pass"
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
