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
        ledger = build_specification_ledger(
            prop_manifest,
            teff_manifest,
            event.project_root,
            py_files=py_files,
            test_files=test_files,
            call_graph=call_graph,
        )
        save_cached_ledger(SPEC_CACHE_DIR, project_hash, ledger)

        # Composition analysis reuses the same call graph
        comp_result = None
        if call_graph is not None:
            comp_result = analyze_composition(call_graph, ledger)

        findings = _emit_findings(ledger, event.project_root)
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


def _emit_findings(ledger: SpecificationLedger, project_root: str) -> list[LintIssue]:
    findings: list[LintIssue] = []
    count = {"spec001": 0, "spec006": 0, "spec009": 0, "spec010": 0}

    for _func_key, fs in ledger.functions.items():
        _check_spec001(fs, findings, count)
        _check_spec006(fs, findings, count)
        _check_spec009(fs, findings, count)
        _check_spec010(fs, findings, count)
        _check_spec012(fs, findings)
        _check_spec013(fs, findings)

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
