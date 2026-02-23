"""Performance channel — algebraic property-based optimization analysis for ControlPlane."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.linters.performance_checks.manifest import build_manifest
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintgate.linters.performance_checks.manifest import PropertyManifest

# Maximum per-function findings to emit for PERFCH003/004 (high-value only).
# Remaining are counted in the summary metric, accessible via drill-down.
_TOP_N_FINDINGS = 5


def _discover_python_files(project_root: str) -> list[str]:
    """Discover Python files (simplified for channel)."""
    from lintgate.channels.structure_channel import _discover_python_files as discover

    return discover(project_root)


def _resolve_file(manifest: PropertyManifest, func_name: str, fallback: str) -> str:
    """Resolve source file for a function, falling back to project root."""
    return manifest.get_source_file(func_name) or fallback


def _analyze_purity_summary(
    manifest: PropertyManifest, total_funcs: int, purity_ratio: float, project_root: str
) -> list[LintIssue]:
    """PERFCH001 — Project-level purity summary (only when ratio is low)."""
    if total_funcs <= 10 or purity_ratio >= 0.2:
        return []
    return [
        LintIssue(
            linter="performance_channel",
            kind="PERFCH001",
            message=(
                f"Low project purity ratio ({purity_ratio:.1%}). "
                f"Only {manifest.pure_count} of {total_funcs} functions are mathematically pure."
            ),
            file=project_root,
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
    ]


def _analyze_optimization_opportunities(
    manifest: PropertyManifest, project_root: str
) -> list[LintIssue]:
    """Emit top-N high-value findings (PERFCH003/004) + a single PERFCH005 summary.

    Instead of emitting one finding per cacheable function (which produces
    thousands of identical-feeling findings), we:
    - Emit individual findings only for the highest-value optimizations
      (parallelizable, cache-without-invalidation) capped at _TOP_N_FINDINGS each.
    - Aggregate all remaining "cacheable" functions into a single PERFCH005
      summary finding with a count and the top examples.
    """
    findings: list[LintIssue] = []
    parallel_count = 0
    cache_noinval_count = 0
    cacheable_funcs: list[str] = []

    for func_name, hints_list in manifest.optimization_potential:
        source = _resolve_file(manifest, func_name, project_root)
        hints = set(hints_list) # Convert to set for O(1) average lookup

        # PERFCH003 — Parallelization / MapReduce (top-N only)
        if "parallelizable" in hints or "map-reduce-compatible" in hints:
            parallel_count += 1
            if parallel_count <= _TOP_N_FINDINGS:
                findings.append(
                    LintIssue(
                        linter="performance_channel",
                        kind="PERFCH003",
                        message=(
                            f"High-value optimization: '{func_name}' is pure and "
                            f"associative/commutative, making it trivially parallelizable."
                        ),
                        file=source,
                        severity="informational",
                        confidence=0.8,
                        evidence={"code": "PERFCH003", "function": func_name, "hints": hints_list},
                        suggestions=[
                            "Use multiprocessing.Pool.map or thread pools safely on this function."
                        ],
                    )
                )

        # PERFCH004 — High-value caching (idempotent, top-N only)
        if "cache-without-invalidation" in hints:
            cache_noinval_count += 1
            if cache_noinval_count <= _TOP_N_FINDINGS:
                findings.append(
                    LintIssue(
                        linter="performance_channel",
                        kind="PERFCH004",
                        message=(
                            f"High-value caching: '{func_name}' is pure and idempotent. "
                            f"It is extremely safe to cache without complex invalidation."
                        ),
                        file=source,
                        severity="informational",
                        confidence=0.8,
                        evidence={"code": "PERFCH004", "function": func_name},
                        suggestions=["Decorate with @functools.lru_cache or @functools.cache"],
                    )
                )
        elif "cacheable" in hints:
            cacheable_funcs.append(func_name)

    # PERFCH005 — Single summary for cacheable functions (not per-function spam)
    if cacheable_funcs:
        top_examples = cacheable_funcs[:5]
        remaining = len(cacheable_funcs) - len(top_examples)
        example_list = ", ".join(f"'{f}'" for f in top_examples)
        suffix = f" and {remaining} more" if remaining > 0 else ""
        findings.append(
            LintIssue(
                linter="performance_channel",
                kind="PERFCH005",
                message=(
                    f"{len(cacheable_funcs)} pure functions are cacheable: "
                    f"{example_list}{suffix}. "
                    f"Use inspect_algebra tool for the full list."
                ),
                file=project_root,
                severity="informational",
                confidence=0.7,
                evidence={
                    "code": "PERFCH005",
                    "cacheable_count": len(cacheable_funcs),
                    "top_examples": top_examples,
                    "parallel_total": parallel_count,
                    "cache_noinval_total": cache_noinval_count,
                },
                suggestions=[
                    "Use inspect_algebra(path) to see the full manifest.",
                    "Decorate hot-path pure functions with @functools.lru_cache.",
                ],
            )
        )

    return findings


def _inject_manifest_into_perf011(manifest: PropertyManifest) -> Callable[[], None]:
    """Inject manifest pure names into PERF011 and return a cleanup callable.

    PERF011 uses a module-global ``_manifest_pure_names`` to detect project-local
    pure functions inside loops.  This helper sets that state from the manifest
    and returns a zero-arg callable that clears it — designed for try/finally.
    """
    from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
        set_manifest_pure_names,
    )

    pure_names = manifest.get_pure_function_names()
    set_manifest_pure_names(pure_names)

    def _cleanup() -> None:
        set_manifest_pure_names(None)

    return _cleanup


def _emit_telemetry(
    manifest: PropertyManifest,
    project_root: str,
    purity_ratio: float,
    findings: list[LintIssue],
    elapsed_ms: float,
    files_count: int,
) -> None:
    """Emit a ``performance_analysis`` telemetry event."""
    from lintgate.state import log_metric

    log_metric(
        {
            "event": "performance_analysis",
            "project": project_root,
            "pure_functions_found": manifest.pure_count,
            "impure_functions_found": manifest.impure_count,
            "purity_ratio": round(purity_ratio, 3),
            "properties_detected": {k.value: v for k, v in manifest.property_distribution.items()},
            "optimization_opportunities": len(manifest.optimization_potential),
            "findings_count": len(findings),
            "blocking_count": sum(1 for f in findings if f.severity == "blocking"),
            "duration_ms": elapsed_ms,
            "files_analyzed": files_count,
        }
    )


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

        # 2b. Inject manifest pure names into PERF011 so tier-2 checks
        # can detect project-local pure functions in loops.
        # IMPORTANT: Use try/finally to guarantee cleanup — stale state
        # from a previous project would cause false positives.
        _clear_perf011 = _inject_manifest_into_perf011(manifest)
        try:
            return self._analyze_and_report(manifest, project_root, py_files, findings, start)
        finally:
            _clear_perf011()

    def _analyze_and_report(
        self,
        manifest: PropertyManifest,
        project_root: str,
        py_files: list[str],
        findings: list[LintIssue],
        start: float,
    ) -> ChannelResult:
        """Run analyses, emit telemetry, and build the channel result."""
        total_funcs = manifest.pure_count + manifest.impure_count
        purity_ratio = manifest.pure_count / max(total_funcs, 1)

        # 3. Run analyses
        findings.extend(_analyze_purity_summary(manifest, total_funcs, purity_ratio, project_root))
        findings.extend(_analyze_optimization_opportunities(manifest, project_root))

        elapsed_ms = (time.perf_counter() - start) * 1000

        metrics = {
            "pure_functions": manifest.pure_count,
            "impure_functions": manifest.impure_count,
            "purity_ratio": round(purity_ratio, 3),
            "properties_detected": {k.value: v for k, v in manifest.property_distribution.items()},
            "optimization_opportunities": len(manifest.optimization_potential),
        }

        _emit_telemetry(manifest, project_root, purity_ratio, findings, elapsed_ms, len(py_files))

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
