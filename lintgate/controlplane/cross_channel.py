"""Cross-channel coherence pass for mutation × performance × test-effectiveness (#209).

Runs after all channels complete. Cross-references outputs to find convergence
signals invisible to any single channel.

Finding codes:
- COH001: Pure function + high survival + structural-only assertions
- COH002: Arithmetic survivor + no value-checking assertions
- COH003: Conditional survivor + no branch-testing assertions
"""

from __future__ import annotations

from typing import Any

from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue


def cross_channel_coherence(channel_results: list[ChannelResult]) -> list[LintIssue]:
    """Generate findings from cross-channel signal convergence.

    Extracts data from performance, mutation, and test_effectiveness channels,
    then checks for convergence patterns. Gracefully degrades if any channel
    is missing or errored.
    """
    perf = _find_channel(channel_results, "performance")
    mutation = _find_channel(channel_results, "mutation")
    teff = _find_channel(channel_results, "test_effectiveness")

    # Need at least two of the three channels for cross-referencing
    available = sum(1 for c in (perf, mutation, teff) if c is not None)
    if available < 2:
        return []

    pure_functions = _extract_pure_functions(perf)
    survival_data = _extract_survival_data(mutation)
    assertion_quality = _extract_assertion_quality(teff)

    findings: list[LintIssue] = []

    for func_name, func_info in pure_functions.items():
        file_path = func_info.get("file", "")
        survival = survival_data.get(file_path) or survival_data.get(func_name)
        assertions = assertion_quality.get(file_path, {})

        if not survival or not assertions:
            continue

        _check_coh001(func_name, file_path, survival, assertions, findings)
        _check_coh002(func_name, file_path, survival, assertions, findings)
        _check_coh003(func_name, file_path, survival, assertions, findings)

    return findings


def _check_coh001(
    func_name: str,
    file_path: str,
    survival: dict[str, Any],
    assertions: dict[str, Any],
    findings: list[LintIssue],
) -> None:
    """COH001: Pure function + high survival + structural-only assertions."""
    surv_rate = survival.get("survival_rate", 0)
    value_ratio = assertions.get("value_checking_ratio", 1.0)
    if surv_rate <= 0.3 or value_ratio >= 0.3:
        return
    findings.append(
        LintIssue(
            linter="coherence",
            kind="COH001",
            message=(
                f"Pure function '{func_name}' has high mutation survival "
                f"({surv_rate:.0%}) and mostly structural assertions "
                f"(value-checking ratio: {value_ratio:.0%}). "
                f"Add exact-value assertions."
            ),
            file=file_path or func_name,
            severity="warning",
            confidence=0.85,
            evidence={
                "survival_rate": surv_rate,
                "value_checking_ratio": value_ratio,
                "is_pure": True,
                "contributing_channels": [
                    "performance",
                    "mutation",
                    "test_effectiveness",
                ],
            },
            suggestions=[
                "Use generate_property_tests to create Hypothesis tests for this pure function.",
                "Use mutation_prescribe to see specific survivor categories.",
            ],
        )
    )


def _check_coh002(
    func_name: str,
    file_path: str,
    survival: dict[str, Any],
    assertions: dict[str, Any],
    findings: list[LintIssue],
) -> None:
    """COH002: Arithmetic survivor + no value-checking assertions."""
    survivor_cats = survival.get("survived_categories", [])
    value_ratio = assertions.get("value_checking_ratio", 1.0)
    if "arithmetic" not in survivor_cats or value_ratio >= 0.2:
        return
    findings.append(
        LintIssue(
            linter="coherence",
            kind="COH002",
            message=(
                f"Function in '{file_path or func_name}' has arithmetic mutation "
                f"survivors AND no value-checking assertions. "
                f"Tests verify structure but not computed values."
            ),
            file=file_path or func_name,
            severity="warning",
            confidence=0.80,
            evidence={
                "survivor_categories": survivor_cats,
                "value_checking_ratio": value_ratio,
                "contributing_channels": ["mutation", "test_effectiveness"],
            },
            suggestions=[
                "Add assertions with exact expected return values (assert result == 42).",
                "Use generate_property_tests(from_prescriptions=True) for targeted templates.",
            ],
        )
    )


def _check_coh003(
    func_name: str,
    file_path: str,
    survival: dict[str, Any],
    assertions: dict[str, Any],
    findings: list[LintIssue],
) -> None:
    """COH003: Conditional survivor + no branch-testing assertions."""
    survivor_cats = survival.get("survived_categories", [])
    branch_ratio = assertions.get("branch_testing_ratio", 1.0)
    if "conditional" not in survivor_cats or branch_ratio >= 0.2:
        return
    findings.append(
        LintIssue(
            linter="coherence",
            kind="COH003",
            message=(
                f"Function in '{file_path or func_name}' has conditional mutation "
                f"survivors AND minimal branch-testing assertions."
            ),
            file=file_path or func_name,
            severity="informational",
            confidence=0.75,
            evidence={
                "survivor_categories": survivor_cats,
                "branch_testing_ratio": branch_ratio,
                "contributing_channels": ["mutation", "test_effectiveness"],
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


def _extract_survival_data(mutation: ChannelResult | None) -> dict[str, dict[str, Any]]:
    """Extract per-function survival data from mutation channel findings."""
    if not mutation:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for finding in mutation.findings:
        if finding.kind in ("MUT001", "MUT002"):
            evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
            # Key by file path for cross-referencing
            func_key = finding.file or ""
            result[func_key] = {
                "survival_rate": evidence.get("survival_rate", 0),
                "survived_categories": list(evidence.get("survived_categories", [])),
                "depth": evidence.get("depth", ""),
            }
    # Also extract from MUTCH007 which has category data
    for finding in mutation.findings:
        if finding.kind == "MUTCH007":
            evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
            func_key = finding.file or ""
            if func_key not in result:
                result[func_key] = {}
            result[func_key]["survived_categories"] = evidence.get(
                "survived_categories", []
            )
    return result


def _extract_assertion_quality(teff: ChannelResult | None) -> dict[str, dict[str, Any]]:
    """Extract per-file assertion quality from test effectiveness findings."""
    if not teff:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for finding in teff.findings:
        evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
        file_path = finding.file or ""
        if file_path:
            # Build quality metrics from TEFF findings
            quality = result.setdefault(
                file_path,
                {
                    "value_checking_ratio": 1.0,
                    "branch_testing_ratio": 1.0,
                },
            )
            # TEFF findings about assertion quality
            if "value_ratio" in evidence:
                quality["value_checking_ratio"] = evidence["value_ratio"]
            elif "semantic_ratio" in evidence:
                quality["value_checking_ratio"] = evidence["semantic_ratio"]
            if "branch_ratio" in evidence:
                quality["branch_testing_ratio"] = evidence["branch_ratio"]
    return result
