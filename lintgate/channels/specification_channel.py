"""Specification channel — symbolic specification complexity analysis for ControlPlane.

OPT-IN channel (not in default channel list). Consumes PropertyManifest
and TestEffectivenessManifest from the prepass to build a specification
ledger and emit SPEC findings. No subprocess spawning, no test execution.

Finding codes:
- SPEC001: Under-specified: sigma > assertion_count
- SPEC006: Pure + under-specified (holographic: perf x teff x spec)
- SPEC009: Optimization hint gated by spec_level
- SPEC010: Risk-critical under-specification: P0 + spec_level < 0.5
- SPEC012: Low DFT causing low sigma confidence: testability < 0.4
- SPEC005: High coupling surface + high mutation survival (decomposition candidate)
- SPEC013: Stop criteria satisfied
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.specification.composition import CompositionResult
    from lintgate.specification.types import FunctionSpecification, SpecificationLedger

_TOP_N_FINDINGS = 5


def _load_project_mutation_cache(project_root: str) -> dict[str, dict] | None:
    """Load all mutation cache entries for the project.

    Returns function_key → mutation result dict, or None if no data.
    """
    import json
    from pathlib import Path

    cache_dir = Path(project_root) / ".lintgate" / "mutation"
    if not cache_dir.exists():
        return None

    cache: dict[str, dict] = {}
    for cache_file in cache_dir.glob("*.json"):
        if cache_file.name == "scheduler_state.json":
            continue
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        func_key = data.get("function_key", "")
        if func_key:
            cache[func_key] = data

    return cache if cache else None


class SpecificationChannel:
    """Supervision channel for specification complexity analysis.

    Advisory only — specification findings are informational/warning.
    """

    name = "specification"
    timeout_ms = 10000
    blocking_capable = False

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        return bool(event.project_root)

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        start = time.perf_counter()

        prop_manifest = event.context.get("property_manifest")
        teff_manifest = event.context.get("test_effectiveness_manifest")

        if prop_manifest is None or teff_manifest is None:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "missing upstream manifests"},
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        import hashlib

        from lintgate.specification.call_graph import build_cross_module_call_graph
        from lintgate.specification.composition import analyze_composition
        from lintgate.specification.ledger import (
            build_specification_ledger,
            load_cached_ledger,
            save_cached_ledger,
        )
        from lintgate.state import SPEC_CACHE_DIR

        py_files = event.context.get("python_files", [])
        test_files = [f for f in py_files if _is_test_file(f)]
        source_files = [f for f in py_files if not _is_test_file(f)]

        # Build call graph first so fan-in/fan-out flows into the ledger
        call_graph = None
        if source_files:
            call_graph = build_cross_module_call_graph(source_files, event.project_root)

        project_hash = hashlib.sha256(event.project_root.encode()).hexdigest()[:16]
        prior_ledger = load_cached_ledger(SPEC_CACHE_DIR, project_hash)

        # Load mutation cache for ground-truth spec_level override
        mutation_cache = _load_project_mutation_cache(event.project_root)

        ledger = build_specification_ledger(
            prop_manifest,
            teff_manifest,
            event.project_root,
            py_files=py_files,
            test_files=test_files,
            call_graph=call_graph,
            prior_ledger=prior_ledger,
            mutation_cache=mutation_cache,
        )
        save_cached_ledger(SPEC_CACHE_DIR, project_hash, ledger)

        # Persist slim graph projection for prescriptive retrospective compose
        try:
            from lintgate.specification.prescriptive.projection import (
                build_projection_from_ledger,
                save_projection,
            )

            ledger_flat = {k: v.to_dict() for k, v in ledger.functions.items()}
            cg_flat = None
            if call_graph is not None and hasattr(call_graph, "calls"):
                cg_flat = {k: list(v) for k, v in call_graph.calls.items()}
            projections = build_projection_from_ledger(ledger_flat, cg_flat)
            if projections:
                save_projection(event.project_root, projections)
        except Exception:
            pass

        # Composition analysis reuses the same call graph
        comp_result = None
        if call_graph is not None:
            comp_result = analyze_composition(call_graph, ledger)

        findings = _emit_findings(ledger, event.project_root, mutation_cache)
        elapsed_ms = (time.perf_counter() - start) * 1000

        metrics = _build_metrics(ledger, comp_result)

        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            if any(f.severity == "warning" for f in findings):
                severity = "warning"
            else:
                severity = "informational"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            metrics=metrics,
            duration_ms=elapsed_ms,
        )


def _emit_findings(
    ledger: SpecificationLedger,
    project_root: str,
    mutation_cache: dict[str, dict] | None = None,
) -> list[LintIssue]:
    findings: list[LintIssue] = []
    count = {"spec001": 0, "spec005": 0, "spec006": 0, "spec009": 0, "spec010": 0}

    for _func_key, fs in ledger.functions.items():
        _check_spec001(fs, findings, count)
        _check_spec005(fs, findings, count, mutation_cache)
        _check_spec006(fs, findings, count)
        _check_spec009(fs, findings, count)
        _check_spec010(fs, findings, count)
        _check_spec012(fs, findings)
        _check_spec013(fs, findings)
        _check_pspec001(fs, findings, project_root)
        _check_pspec002(fs, findings, project_root)
        _check_pspec003(fs, findings, project_root)

    return findings


def _check_spec001(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    count: dict[str, int],
) -> None:
    sigma = fs.core.estimated_sigma
    assertions = fs.traceability.assertion_count
    if sigma <= assertions:
        return
    count["spec001"] += 1
    if count["spec001"] > _TOP_N_FINDINGS:
        return
    findings.append(
        LintIssue(
            linter="specification",
            kind="SPEC001",
            message=(
                f"Under-specified: '{fs.function_key}' has sigma={sigma} "
                f"but only {assertions} assertions"
            ),
            file=fs.source_file or fs.function_key,
            severity="warning",
            confidence=0.75,
            evidence={"sigma": sigma, "assertions": assertions, "phase": fs.core.phase},
        )
    )


def _check_spec005(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    count: dict[str, int],
    mutation_cache: dict[str, dict] | None = None,
) -> None:
    """SPEC005: High coupling surface + high mutation survival → decomposition candidate."""
    coupling = fs.traceability.coupling_surface
    if coupling < 3:
        return

    # Check mutation survival — need multiple surviving categories
    surviving_categories: list[str] = []
    if mutation_cache:
        mut_state = mutation_cache.get(fs.function_key)
        if mut_state:
            for cat in mut_state.get("per_category", []):
                if cat.get("survived", 0) > 0:
                    surviving_categories.append(cat.get("category", ""))

    if len(surviving_categories) < 2:
        return

    count["spec005"] += 1
    if count["spec005"] > _TOP_N_FINDINGS:
        return

    findings.append(
        LintIssue(
            linter="specification",
            kind="SPEC005",
            message=(
                f"Decomposition candidate: '{fs.function_key}' has "
                f"coupling_surface={coupling} (test files) and "
                f"{len(surviving_categories)} surviving mutation categories "
                f"({', '.join(surviving_categories)}). "
                f"Specification effort is likely multiplicative."
            ),
            file=fs.source_file or fs.function_key,
            severity="warning",
            confidence=0.80,
            evidence={
                "coupling_surface": coupling,
                "surviving_categories": surviving_categories,
                "spec_level": fs.core.specification_level,
                "sigma": fs.core.estimated_sigma,
            },
        )
    )


def _check_spec006(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    count: dict[str, int],
) -> None:
    if not fs.core.is_pure:
        return
    if fs.core.specification_level >= 0.5:
        return
    count["spec006"] += 1
    if count["spec006"] > _TOP_N_FINDINGS:
        return
    findings.append(
        LintIssue(
            linter="specification",
            kind="SPEC006",
            message=(
                f"Pure function '{fs.function_key}' is under-specified "
                f"(spec_level={fs.core.specification_level:.2f}). "
                f"Wasted optimization opportunity."
            ),
            file=fs.source_file or fs.function_key,
            severity="warning",
            confidence=0.85,
            evidence={
                "spec_level": fs.core.specification_level,
                "is_pure": True,
                "contributing_channels": ["performance", "test_effectiveness", "specification"],
            },
        )
    )


def _check_spec009(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    count: dict[str, int],
) -> None:
    if not fs.optimization_hints:
        return
    from lintgate.specification.optimization_gate import GATE_THRESHOLDS

    for hint in fs.optimization_hints:
        threshold = GATE_THRESHOLDS.get(hint, 0.0)
        if threshold <= 0 or fs.core.specification_level >= threshold:
            continue
        count["spec009"] += 1
        if count["spec009"] > _TOP_N_FINDINGS:
            return
        delta = threshold - fs.core.specification_level
        findings.append(
            LintIssue(
                linter="specification",
                kind="SPEC009",
                message=(
                    f"Optimization hint '{hint}' on '{fs.function_key}' "
                    f"requires spec_level >= {threshold}, "
                    f"current = {fs.core.specification_level:.2f} (delta: {delta:.2f})"
                ),
                file=fs.source_file or fs.function_key,
                severity="warning",
                confidence=0.80,
                evidence={"hint": hint, "threshold": threshold, "delta": delta},
            )
        )
        return


def _check_spec010(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    count: dict[str, int],
) -> None:
    if fs.risk.priority_band != "P0":
        return
    if fs.core.specification_level >= 0.5:
        return
    count["spec010"] += 1
    if count["spec010"] > _TOP_N_FINDINGS:
        return
    findings.append(
        LintIssue(
            linter="specification",
            kind="SPEC010",
            message=(
                f"Risk-critical under-specification: P0 function '{fs.function_key}' "
                f"has spec_level={fs.core.specification_level:.2f}"
            ),
            file=fs.source_file or fs.function_key,
            severity="warning",
            confidence=0.85,
            evidence={
                "risk_score": fs.risk.risk_score,
                "priority_band": "P0",
                "spec_level": fs.core.specification_level,
            },
        )
    )


def _check_spec012(
    fs: FunctionSpecification,
    findings: list[LintIssue],
) -> None:
    if fs.testability.testability_score >= 0.4:
        return
    findings.append(
        LintIssue(
            linter="specification",
            kind="SPEC012",
            message=(
                f"Low DFT score ({fs.testability.testability_score:.2f}) for "
                f"'{fs.function_key}' — sigma confidence is reduced"
            ),
            file=fs.source_file or fs.function_key,
            severity="informational",
            confidence=0.70,
            evidence={"testability_score": fs.testability.testability_score},
        )
    )


def _check_spec013(
    fs: FunctionSpecification,
    findings: list[LintIssue],
) -> None:
    if not fs.stop_criteria_met:
        return
    findings.append(
        LintIssue(
            linter="specification",
            kind="SPEC013",
            message=(
                f"Stop criteria satisfied for '{fs.function_key}' — "
                f"spec_level meets all optimization hint thresholds"
            ),
            file=fs.source_file or fs.function_key,
            severity="informational",
            confidence=0.90,
            evidence={"spec_level": fs.core.specification_level, "hints": fs.optimization_hints},
        )
    )


def _check_pspec001(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    project_root: str,
) -> None:
    """PSPEC001: Function violates prescriptive spec invariants (AST check)."""
    from lintgate.specification.prescriptive.spec import load_spec

    spec = load_spec(project_root, fs.function_key)
    if spec is None or not spec.invariants:
        return

    # Need source file to run AST checker
    source_file = fs.source_file
    if not source_file:
        return

    import os

    if not os.path.isabs(source_file):
        source_file = os.path.join(project_root, source_file)
    if not os.path.isfile(source_file):
        return

    try:
        with open(source_file, encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return

    from lintgate.specification.prescriptive.ast_checker import check_invariants_against_ast

    func_name = fs.function_key.split("::")[-1] if "::" in fs.function_key else fs.function_key
    results = check_invariants_against_ast(source, func_name, spec.invariants)

    for r in results:
        if r.status == "fail":
            findings.append(
                LintIssue(
                    linter="specification",
                    kind="PSPEC001",
                    message=(
                        f"Invariant violation: '{fs.function_key}' fails "
                        f"'{r.invariant_name}' — {r.reason}"
                    ),
                    file=fs.source_file or fs.function_key,
                    severity="warning",
                    confidence=0.80,
                    evidence={
                        "invariant": r.invariant_name,
                        "reason": r.reason,
                        "spec_id": spec.spec_id,
                    },
                )
            )


def _check_pspec002(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    project_root: str,
    threshold: float = 2.0,
) -> None:
    """PSPEC002: Prescriptive σ diverges >threshold× from retrospective σ."""
    from lintgate.specification.prescriptive.sigma import compute_convergence_signal
    from lintgate.specification.prescriptive.spec import load_spec

    spec = load_spec(project_root, fs.function_key)
    if spec is None:
        return

    retro_sigma = fs.core.estimated_sigma
    signal = compute_convergence_signal(spec.prescriptive_sigma, retro_sigma)
    if signal["assessment"] in ("under_specified", "over_specified"):
        ratio = signal["ratio"]
        if ratio > threshold or (ratio > 0 and ratio < 1.0 / threshold):
            findings.append(
                LintIssue(
                    linter="specification",
                    kind="PSPEC002",
                    message=(
                        f"Prescriptive σ divergence for '{fs.function_key}': "
                        f"prescriptive={spec.prescriptive_sigma}, "
                        f"retrospective={retro_sigma} (ratio={ratio:.2f})"
                    ),
                    file=fs.source_file or fs.function_key,
                    severity="warning",
                    confidence=0.75,
                    evidence={
                        "prescriptive_sigma": spec.prescriptive_sigma,
                        "retrospective_sigma": retro_sigma,
                        "ratio": ratio,
                        "assessment": signal["assessment"],
                    },
                )
            )


def _check_pspec003(
    fs: FunctionSpecification,
    findings: list[LintIssue],
    project_root: str,
) -> None:
    """PSPEC003: Function written without prescriptive spec (advisory)."""
    from lintgate.specification.prescriptive.spec import load_spec_index

    index = load_spec_index(project_root)
    if not index:
        return  # No specs at all — don't spam

    if fs.function_key not in index:
        findings.append(
            LintIssue(
                linter="specification",
                kind="PSPEC003",
                message=f"Function '{fs.function_key}' has no prescriptive spec",
                file=fs.source_file or fs.function_key,
                severity="informational",
                confidence=0.60,
            )
        )


def _is_test_file(filepath: str) -> bool:
    """Check if a file path looks like a test file."""
    import os

    base = os.path.basename(filepath)
    return base.startswith("test_") or base.endswith("_test.py")


def _build_metrics(
    ledger: SpecificationLedger, comp_result: CompositionResult | None = None
) -> dict:
    spec_func_list = {}
    for key, fs in ledger.functions.items():
        spec_func_list[key] = {
            "spec_level": round(fs.core.specification_level, 3),
            "regime": fs.core.regime,
            "sigma": fs.core.estimated_sigma,
            "phase": fs.core.phase,
            "is_pure": fs.core.is_pure,
            "risk_score": round(fs.risk.risk_score, 3),
            "priority_band": fs.risk.priority_band,
            "testability_score": round(fs.testability.testability_score, 3),
            "optimization_hints": fs.optimization_hints,
            "stop_criteria_met": fs.stop_criteria_met,
            "coupling_surface": fs.traceability.coupling_surface,
        }

    return {
        "specification_coverage": round(ledger.specification_coverage, 3),
        "regime_distribution": ledger.regime_distribution,
        "pure_underspecified_count": sum(
            1
            for fs in ledger.functions.values()
            if fs.core.is_pure and fs.core.specification_level < 0.5
        ),
        "total_estimated_sigma": ledger.total_sigma,
        "risk_distribution": ledger.risk_distribution,
        "mean_testability": round(ledger.mean_testability, 3),
        "stop_criteria_met_count": ledger.stop_criteria_met_count,
        "sheaf_obstruction": comp_result.total_gamma if comp_result is not None else None,
        "specification_function_list": spec_func_list,
        "composition_gaps": comp_result.to_dict().get("composition_gaps")
        if comp_result is not None
        else None,
    }
