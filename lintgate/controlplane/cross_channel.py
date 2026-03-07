"""Cross-channel coherence pass for performance × test-effectiveness × specification.

Runs after channel execution and cross-references signals that only appear
when combining purity analysis with assertion-quality analysis.

Finding codes:
- COH001: Pure function + structural-heavy assertions
- COH002: Pure function + minimal branch-testing assertions
- COH101: Pure + optimization hint + low spec_level (performance × specification)
- COH102: Regime B + high fan-out (specification × structure)
- COH103: High composition gap at module boundary (specification × structure)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.controlplane.types import ChannelResult


def cross_channel_coherence(channel_results: list[ChannelResult]) -> list[LintIssue]:
    """Generate findings from cross-channel signal convergence.

    Extracts data from performance and test_effectiveness channels and checks
    for coherence patterns. Gracefully degrades when either channel is missing
    or errored.
    """
    perf = _find_channel(channel_results, "performance")
    teff = _find_channel(channel_results, "test_effectiveness")

    if perf is None or teff is None:
        return []

    pure_functions = _extract_pure_functions(perf)
    assertion_quality = _extract_assertion_quality(teff)

    findings: list[LintIssue] = []

    for func_name, func_info in pure_functions.items():
        file_path = func_info.get("file", "")
        assertions = assertion_quality.get(file_path, {})

        if not assertions:
            continue

        _check_coh001(func_name, file_path, assertions, findings)
        _check_coh002(func_name, file_path, assertions, findings)

    # Specification cross-channel checks (COH101-103)
    spec = _find_channel(channel_results, "specification")
    if spec is not None:
        _check_coh101_103(spec, pure_functions, findings)

    return findings


def _check_coh001(
    func_name: str,
    file_path: str,
    assertions: dict[str, Any],
    findings: list[LintIssue],
) -> None:
    """COH001: Pure function + structural-heavy assertions."""
    value_ratio = assertions.get("value_checking_ratio", 1.0)
    if value_ratio >= 0.3:
        return

    findings.append(
        LintIssue(
            linter="coherence",
            kind="COH001",
            message=(
                f"Pure function '{func_name}' has mostly structural assertions "
                f"(value-checking ratio: {value_ratio:.0%}). "
                f"Add exact-value assertions."
            ),
            file=file_path or func_name,
            severity="warning",
            confidence=0.8,
            evidence={
                "value_checking_ratio": value_ratio,
                "is_pure": True,
                "contributing_channels": ["performance", "test_effectiveness"],
            },
            suggestions=[
                "Use generate_property_tests to create Hypothesis tests for this pure function.",
                "Add exact-value assertions for representative and edge-case inputs.",
            ],
        )
    )


def _check_coh002(
    func_name: str,
    file_path: str,
    assertions: dict[str, Any],
    findings: list[LintIssue],
) -> None:
    """COH002: Pure function + minimal branch-testing assertions."""
    branch_ratio = assertions.get("branch_testing_ratio", 1.0)
    if branch_ratio >= 0.2:
        return

    findings.append(
        LintIssue(
            linter="coherence",
            kind="COH002",
            message=(
                f"Pure function in '{file_path or func_name}' has minimal "
                f"branch-testing assertions."
            ),
            file=file_path or func_name,
            severity="informational",
            confidence=0.72,
            evidence={
                "branch_testing_ratio": branch_ratio,
                "contributing_channels": ["performance", "test_effectiveness"],
            },
            suggestions=[
                "Add tests that exercise both True and False branches.",
            ],
        )
    )


def _find_channel(results: list[ChannelResult], name: str) -> ChannelResult | None:
    """Find a channel result by name, returning None if missing or errored."""
    for r in results:
        if r.channel == name and r.status not in ("error", "timeout", "skip"):
            return r
    return None


def _extract_pure_functions(perf: ChannelResult | None) -> dict[str, dict[str, Any]]:
    """Extract pure function map from performance channel metrics."""
    if not perf:
        return {}

    metrics = perf.metrics if isinstance(perf.metrics, dict) else {}
    pure_list = metrics.get("pure_function_list", [])

    result: dict[str, dict[str, Any]] = {}
    for entry in pure_list:
        name = entry.get("name", "")
        if name:
            result[name] = entry
    return result


def _check_coh101_103(
    spec: ChannelResult,
    pure_functions: dict[str, dict[str, Any]],
    findings: list[LintIssue],
) -> None:
    """COH101-103: Specification cross-channel coherence checks."""
    spec_metrics = spec.metrics if isinstance(spec.metrics, dict) else {}
    spec_funcs = spec_metrics.get("specification_function_list", {})

    _check_coh101(spec_funcs, findings)

    comp_gaps = spec_metrics.get("composition_gaps")
    if comp_gaps is not None:
        _check_coh102_103(spec_funcs, comp_gaps, findings)


def _check_coh101(
    spec_funcs: dict[str, Any],
    findings: list[LintIssue],
) -> None:
    """COH101: Pure + optimization hint + low spec_level."""
    for func_key, func_info in spec_funcs.items():
        hints = func_info.get("optimization_hints", [])
        spec_level = func_info.get("spec_level", 0.0)
        is_pure = func_info.get("is_pure", False)
        if is_pure and hints and spec_level < 0.5:
            findings.append(
                LintIssue(
                    linter="coherence",
                    kind="COH101",
                    message=(
                        f"Pure function '{func_key}' has optimization hints {hints} "
                        f"but spec_level={spec_level:.2f} (needs specification work)"
                    ),
                    file=func_key,
                    severity="warning",
                    confidence=0.8,
                    evidence={
                        "spec_level": spec_level,
                        "hints": hints,
                        "contributing_channels": ["performance", "specification"],
                    },
                )
            )


def _check_coh102_103(
    spec_funcs: dict[str, Any],
    comp_gaps: dict[str, Any],
    findings: list[LintIssue],
) -> None:
    """COH102: Regime B + high fan-out. COH103: High composition gap."""
    # Derive per-function cross-module fan-out from composition edges
    cross_module_fan_out: dict[str, int] = {}
    for edge_key in comp_gaps:
        parts = edge_key.split("::", 2)
        if len(parts) >= 2:
            caller = parts[0] if len(parts) == 2 else f"{parts[0]}::{parts[1]}"
            cross_module_fan_out[caller] = cross_module_fan_out.get(caller, 0) + 1

    for func_key, func_info in spec_funcs.items():
        regime = func_info.get("regime", "unknown")
        if regime != "B":
            continue
        fan_out = cross_module_fan_out.get(func_key, 0)
        if fan_out >= 5:
            findings.append(
                LintIssue(
                    linter="coherence",
                    kind="COH102",
                    message=(
                        f"Regime B function '{func_key}' has cross-module fan-out={fan_out} "
                        f"— forced decomposition signal"
                    ),
                    file=func_key,
                    severity="warning",
                    confidence=0.75,
                    evidence={
                        "regime": "B",
                        "fan_out": fan_out,
                        "contributing_channels": ["specification", "structure"],
                    },
                )
            )

    for edge_key, gap_info in comp_gaps.items():
        gamma = gap_info.get("gamma", 0.0)
        if gamma > 3.0:
            findings.append(
                LintIssue(
                    linter="coherence",
                    kind="COH103",
                    message=(
                        f"High composition gap (gamma={gamma:.2f}) at module boundary: {edge_key}"
                    ),
                    file=edge_key,
                    severity="warning",
                    confidence=0.7,
                    evidence={
                        "gamma": gamma,
                        "contributing_channels": ["specification", "structure"],
                    },
                )
            )


def _extract_assertion_quality(teff: ChannelResult | None) -> dict[str, dict[str, Any]]:
    """Extract per-file assertion quality from test effectiveness findings."""
    if not teff:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for finding in teff.findings:
        evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
        file_path = finding.file or ""
        if not file_path:
            continue

        quality = result.setdefault(
            file_path,
            {
                "value_checking_ratio": 1.0,
                "branch_testing_ratio": 1.0,
            },
        )

        if "value_ratio" in evidence:
            quality["value_checking_ratio"] = evidence["value_ratio"]
        elif "semantic_ratio" in evidence:
            quality["value_checking_ratio"] = evidence["semantic_ratio"]

        if "branch_ratio" in evidence:
            quality["branch_testing_ratio"] = evidence["branch_ratio"]

    return result
