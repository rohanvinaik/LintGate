"""Performance channel — algebraic property-based optimization analysis for ControlPlane."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

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
    manifest: PropertyManifest,
    project_root: str,
    call_graph: Any = None,
    mutation_cache: dict[str, dict] | None = None,
) -> list[LintIssue]:
    """Emit ranked optimization findings using cache ROI scoring.

    Uses cache_scoring.py's compute_weight + call_hotness + repeatability
    model to rank cacheable functions by ROI instead of listing arbitrary
    examples. Also surfaces purity tier distribution and mutation gating.
    """
    from lintgate.linters.performance_checks.algebra_types import PurityTier

    findings: list[LintIssue] = []
    parallel_count = 0
    cache_noinval_count = 0

    # ── Ranked cache hotspots via ROI scoring ─────────────────────
    cache_hotspots = _score_cache_hotspots(manifest, project_root, call_graph)

    # Collect all cacheable function names from optimization_potential as fallback
    all_cacheable = [
        name
        for name, hints_list in manifest.optimization_potential
        if "cacheable" in set(hints_list)
    ]

    # Emit PERFCH005 — ranked if ROI data available, count-based fallback otherwise
    if cache_hotspots:
        top = cache_hotspots[:10]
        findings.append(
            LintIssue(
                linter="performance_channel",
                kind="PERFCH005",
                message=(
                    f"Top {len(top)} cache hotspots by ROI "
                    f"(of {len(cache_hotspots)} cacheable): "
                    + ", ".join(f"'{h['function']}' ({h['band']})" for h in top[:5])
                ),
                file=project_root,
                severity="informational",
                confidence=0.75,
                evidence={
                    "code": "PERFCH005",
                    "cacheable_count": len(cache_hotspots),
                    "top_hotspots": top,
                },
                suggestions=[
                    "Use inspect_algebra(path) to see the full manifest.",
                    "Decorate HIGH-band functions with @functools.lru_cache.",
                ],
            )
        )
    elif all_cacheable:
        # Fallback: no ROI data available (no source files parsed), use counts
        top_examples = all_cacheable[:5]
        findings.append(
            LintIssue(
                linter="performance_channel",
                kind="PERFCH005",
                message=(
                    f"{len(all_cacheable)} pure functions are cacheable: "
                    + ", ".join(f"'{f}'" for f in top_examples)
                    + (f" and {len(all_cacheable) - 5} more" if len(all_cacheable) > 5 else "")
                ),
                file=project_root,
                severity="informational",
                confidence=0.7,
                evidence={
                    "code": "PERFCH005",
                    "cacheable_count": len(all_cacheable),
                    "top_examples": top_examples,
                },
                suggestions=[
                    "Use inspect_algebra(path) to see the full manifest.",
                    "Run optimization_landscape(path, mode='static') for ROI-ranked hotspots.",
                ],
            )
        )

    for func_name, hints_list in manifest.optimization_potential:
        source = _resolve_file(manifest, func_name, project_root)
        hints = set(hints_list)

        # PERFCH003 — Parallelization (top-N only)
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
                        suggestions=["Use multiprocessing.Pool.map or thread pools safely."],
                    )
                )

        # PERFCH004 — Cache without invalidation (top-N only)
        if "cache-without-invalidation" in hints:
            cache_noinval_count += 1
            if cache_noinval_count <= _TOP_N_FINDINGS:
                # Mutation gating: check if this function has a mutation baseline
                mutation_gated = _check_mutation_gate(func_name, mutation_cache)
                msg = (
                    f"High-value caching: '{func_name}' is pure and idempotent. "
                    f"Safe to cache without complex invalidation."
                )
                if mutation_gated:
                    msg += " ⚠ No mutation baseline — run mutation_run_sampling first."
                findings.append(
                    LintIssue(
                        linter="performance_channel",
                        kind="PERFCH004",
                        message=msg,
                        file=source,
                        severity="informational",
                        confidence=0.7 if mutation_gated else 0.8,
                        evidence={
                            "code": "PERFCH004",
                            "function": func_name,
                            "mutation_gated": mutation_gated,
                        },
                        suggestions=(
                            [
                                "Run mutation_run_sampling to verify behavioral correctness before caching."
                            ]
                            if mutation_gated
                            else ["Decorate with @functools.lru_cache or @functools.cache"]
                        ),
                    )
                )

    # ── PERFCH006 — Stable-read functions (new purity tier) ───────
    stable_read_funcs = [
        name
        for name, func in manifest.functions.items()
        if func.purity_tier == PurityTier.STABLE_READ
    ]
    if stable_read_funcs:
        findings.append(
            LintIssue(
                linter="performance_channel",
                kind="PERFCH006",
                message=(
                    f"{len(stable_read_funcs)} functions are impure but read-only (stable_read tier). "
                    f"Safe for disk caches, preloading, memoized snapshots: "
                    + ", ".join(f"'{f}'" for f in stable_read_funcs[:5])
                ),
                file=project_root,
                severity="informational",
                confidence=0.7,
                evidence={
                    "code": "PERFCH006",
                    "stable_read_count": len(stable_read_funcs),
                    "top_examples": stable_read_funcs[:10],
                },
                suggestions=[
                    "Consider disk-based caching (shelve, joblib.Memory) for expensive stable-read functions.",
                    "Preload strategies can safely memoize these at startup.",
                ],
            )
        )

    return findings


def _score_cache_hotspots(
    manifest: PropertyManifest,
    project_root: str,
    call_graph: Any = None,
) -> list[dict[str, Any]]:
    """Score all cacheable functions by ROI and return ranked list."""
    import ast

    from lintgate.linters.performance_checks.cache_scoring import compute_cache_score

    hotspots: list[dict[str, Any]] = []

    for func_name, func_props in manifest.functions.items():
        if not func_props.purity.is_pure:
            continue
        if "cacheable" not in func_props.optimization_hints:
            continue

        # We need the AST node for scoring — try to parse the source file
        source_file = func_props.source_file
        if not source_file:
            continue

        try:
            with open(source_file, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (OSError, SyntaxError):
            continue

        # Find the function node
        func_node = None
        bare_name = func_name.split(".")[-1]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == bare_name:
                func_node = node
                break

        if func_node is None:
            continue

        # Get call graph data if available
        cg_data = None
        if call_graph and hasattr(call_graph, "called_by"):
            fan_in = len(call_graph.called_by.get(func_name, set()))
            cg_data = {"fan_in": fan_in}

        score = compute_cache_score(func_node, func_props.purity, cg_data)
        if score.band != "SKIP":
            hotspots.append(
                {
                    "function": func_name,
                    "source_file": source_file,
                    "score": round(score.score, 3),
                    "band": score.band,
                    "factors": {k: round(v, 3) for k, v in score.factors.items()},
                }
            )

    hotspots.sort(key=lambda h: h["score"], reverse=True)
    return hotspots


def _check_mutation_gate(func_name: str, mutation_cache: dict[str, dict] | None) -> bool:
    """Check if a function lacks a mutation baseline (optimization safety gate).

    Returns True if the function has NO mutation profile — meaning the
    optimization recommendation should be flagged as unverified.
    """
    if not mutation_cache:
        return True  # No cache at all — everything is ungated
    return func_name not in mutation_cache


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

        # Use shared manifest from run_mesh() pre-pass if available,
        # otherwise fall back to building our own (non-ControlPlane paths).
        manifest = event.context.get("property_manifest")
        py_files = event.context.get("python_files") or _discover_python_files(project_root)

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

        # 2. Build property manifest for project (only if not shared from pre-pass)
        if manifest is None:
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

        # Load call graph and mutation cache for ranked scoring + gating
        call_graph = None
        mutation_cache = None
        try:
            import os

            from lintgate.specification.call_graph import build_cross_module_call_graph

            source_files = [
                f
                for f in py_files
                if not os.path.basename(f).startswith("test_")
                and not os.path.basename(f).endswith("_test.py")
            ]
            if source_files:
                call_graph = build_cross_module_call_graph(source_files, project_root)
        except Exception:
            pass
        try:
            from lintgate.channels.specification_channel import _load_project_mutation_cache

            mutation_cache = _load_project_mutation_cache(project_root)
        except Exception:
            pass

        findings.extend(
            _analyze_optimization_opportunities(
                manifest,
                project_root,
                call_graph=call_graph,
                mutation_cache=mutation_cache,
            )
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        # #209: Export pure function list for cross-channel coherence
        pure_function_list = [
            {
                "name": name,
                "file": func.source_file,
                "hints": list(func.optimization_hints),
            }
            for name, func in manifest.functions.items()
            if func.purity.is_pure
        ]

        # Purity tier distribution

        tier_dist: dict[str, int] = {"pure": 0, "stable_read": 0, "stateful": 0}
        for func in manifest.functions.values():
            tier_dist[func.purity_tier.value] = tier_dist.get(func.purity_tier.value, 0) + 1

        metrics = {
            "pure_functions": manifest.pure_count,
            "impure_functions": manifest.impure_count,
            "purity_ratio": round(purity_ratio, 3),
            "purity_tiers": tier_dist,
            "properties_detected": {k.value: v for k, v in manifest.property_distribution.items()},
            "optimization_opportunities": len(manifest.optimization_potential),
            "pure_function_list": pure_function_list,
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
