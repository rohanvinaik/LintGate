"""Cross-channel coherence pass for performance × test-effectiveness.

Runs after channel execution and cross-references signals that only appear
when combining purity analysis with assertion-quality analysis.

Finding codes:
- COH001: Pure function + structural-heavy assertions
- COH002: Pure function + minimal branch-testing assertions
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
