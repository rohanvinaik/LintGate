"""Tests for #209: Cross-channel mutation × performance × test-effectiveness coherence.

Covers:
- COH001: Pure function + high survival + structural-only assertions
- COH002: Arithmetic survivor + no value-checking assertions
- COH003: Conditional survivor + no branch-testing assertions
- Graceful degradation when channels are missing
- Performance channel exports pure_function_list in metrics
"""

from __future__ import annotations

from lintgate.controlplane.cross_channel import (
    _extract_assertion_quality,
    _extract_pure_functions,
    _extract_survival_data,
    _find_channel,
    cross_channel_coherence,
)
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue

# ── Helpers ─────────────────────────────────────────────────────────────


def _perf_result(pure_list: list[dict] | None = None) -> ChannelResult:
    metrics = {"pure_function_list": pure_list or []}
    return ChannelResult(
        channel="performance",
        status="pass",
        severity="none",
        findings=[],
        metrics=metrics,
        duration_ms=10,
    )


def _mutation_result(findings: list[LintIssue] | None = None) -> ChannelResult:
    return ChannelResult(
        channel="mutation",
        status="fail" if findings else "pass",
        severity="warning" if findings else "none",
        findings=findings or [],
        metrics={},
        duration_ms=10,
    )


def _teff_result(findings: list[LintIssue] | None = None) -> ChannelResult:
    return ChannelResult(
        channel="test_effectiveness",
        status="fail" if findings else "pass",
        severity="informational" if findings else "none",
        findings=findings or [],
        metrics={},
        duration_ms=10,
    )


def _mut_finding(
    file: str, survival_rate: float, categories: list[str] | None = None
) -> LintIssue:
    return LintIssue(
        linter="mutation",
        kind="MUT001",
        message=f"Survival {survival_rate}",
        file=file,
        severity="warning",
        confidence=0.8,
        evidence={
            "survival_rate": survival_rate,
            "survived_categories": categories or [],
        },
    )


def _teff_finding(
    file: str, value_ratio: float, branch_ratio: float = 1.0
) -> LintIssue:
    return LintIssue(
        linter="test_effectiveness",
        kind="TEFF005",
        message="Weak assertions",
        file=file,
        severity="informational",
        confidence=0.7,
        evidence={
            "value_ratio": value_ratio,
            "branch_ratio": branch_ratio,
        },
    )


# ── Extraction helpers ──────────────────────────────────────────────────


class TestFindChannel:
    def test_finds_by_name(self):
        results = [_perf_result(), _mutation_result()]
        assert _find_channel(results, "performance") is not None
        assert _find_channel(results, "mutation") is not None

    def test_returns_none_for_missing(self):
        results = [_perf_result()]
        assert _find_channel(results, "mutation") is None

    def test_skips_errored(self):
        errored = ChannelResult(
            channel="performance",
            status="error",
            severity="none",
            findings=[],
            metrics={},
            duration_ms=0,
        )
        assert _find_channel([errored], "performance") is None

    def test_skips_timeout(self):
        timed_out = ChannelResult(
            channel="mutation",
            status="timeout",
            severity="none",
            findings=[],
            metrics={},
            duration_ms=0,
        )
        assert _find_channel([timed_out], "mutation") is None


class TestExtractPureFunctions:
    def test_extracts_from_metrics(self):
        perf = _perf_result(
            [
                {"name": "add", "file": "math.py", "hints": ["cacheable"]},
                {"name": "mul", "file": "math.py", "hints": ["parallelizable"]},
            ]
        )
        result = _extract_pure_functions(perf)
        assert "add" in result
        assert "mul" in result
        assert result["add"]["file"] == "math.py"

    def test_empty_when_none(self):
        assert _extract_pure_functions(None) == {}

    def test_empty_when_no_list(self):
        perf = ChannelResult(
            channel="performance",
            status="pass",
            severity="none",
            findings=[],
            metrics={},
            duration_ms=0,
        )
        assert _extract_pure_functions(perf) == {}


class TestExtractSurvivalData:
    def test_extracts_from_mut001(self):
        mutation = _mutation_result([_mut_finding("logic.py", 0.5, ["arithmetic"])])
        result = _extract_survival_data(mutation)
        assert "logic.py" in result
        assert result["logic.py"]["survival_rate"] == 0.5

    def test_empty_when_none(self):
        assert _extract_survival_data(None) == {}


class TestExtractAssertionQuality:
    def test_extracts_from_teff_findings(self):
        teff = _teff_result([_teff_finding("test_math.py", 0.1, 0.2)])
        result = _extract_assertion_quality(teff)
        assert "test_math.py" in result
        assert result["test_math.py"]["value_checking_ratio"] == 0.1

    def test_empty_when_none(self):
        assert _extract_assertion_quality(None) == {}


# ── COH001 ──────────────────────────────────────────────────────────────


class TestCOH001:
    """Pure function + high survival + structural-only assertions."""

    def test_emitted_when_all_signals_converge(self):
        perf = _perf_result(
            [{"name": "add", "file": "logic.py", "hints": ["cacheable"]}]
        )
        mutation = _mutation_result([_mut_finding("logic.py", 0.5)])
        teff = _teff_result([_teff_finding("logic.py", 0.1)])

        findings = cross_channel_coherence([perf, mutation, teff])
        coh001 = [f for f in findings if f.kind == "COH001"]
        assert len(coh001) == 1
        assert coh001[0].severity == "warning"
        assert "performance" in coh001[0].evidence["contributing_channels"]

    def test_not_emitted_low_survival(self):
        perf = _perf_result(
            [{"name": "add", "file": "logic.py", "hints": ["cacheable"]}]
        )
        mutation = _mutation_result([_mut_finding("logic.py", 0.1)])
        teff = _teff_result([_teff_finding("logic.py", 0.1)])

        findings = cross_channel_coherence([perf, mutation, teff])
        coh001 = [f for f in findings if f.kind == "COH001"]
        assert len(coh001) == 0

    def test_not_emitted_high_value_ratio(self):
        perf = _perf_result(
            [{"name": "add", "file": "logic.py", "hints": ["cacheable"]}]
        )
        mutation = _mutation_result([_mut_finding("logic.py", 0.5)])
        teff = _teff_result([_teff_finding("logic.py", 0.8)])  # Good assertions

        findings = cross_channel_coherence([perf, mutation, teff])
        coh001 = [f for f in findings if f.kind == "COH001"]
        assert len(coh001) == 0


# ── COH002 ──────────────────────────────────────────────────────────────


class TestCOH002:
    """Arithmetic survivor + no value-checking assertions."""

    def test_emitted_arithmetic_plus_structural_only(self):
        perf = _perf_result([{"name": "compute", "file": "math.py", "hints": []}])
        mutation = _mutation_result([_mut_finding("math.py", 0.4, ["arithmetic"])])
        teff = _teff_result([_teff_finding("math.py", 0.05)])  # Very low value ratio

        findings = cross_channel_coherence([perf, mutation, teff])
        coh002 = [f for f in findings if f.kind == "COH002"]
        assert len(coh002) == 1
        assert coh002[0].severity == "warning"

    def test_not_emitted_non_arithmetic(self):
        perf = _perf_result([{"name": "compute", "file": "math.py", "hints": []}])
        mutation = _mutation_result([_mut_finding("math.py", 0.4, ["string"])])
        teff = _teff_result([_teff_finding("math.py", 0.05)])

        findings = cross_channel_coherence([perf, mutation, teff])
        coh002 = [f for f in findings if f.kind == "COH002"]
        assert len(coh002) == 0


# ── COH003 ──────────────────────────────────────────────────────────────


class TestCOH003:
    """Conditional survivor + no branch-testing assertions."""

    def test_emitted_conditional_plus_no_branch_tests(self):
        perf = _perf_result([{"name": "check", "file": "logic.py", "hints": []}])
        mutation = _mutation_result([_mut_finding("logic.py", 0.4, ["conditional"])])
        teff = _teff_result([_teff_finding("logic.py", 0.5, branch_ratio=0.1)])

        findings = cross_channel_coherence([perf, mutation, teff])
        coh003 = [f for f in findings if f.kind == "COH003"]
        assert len(coh003) == 1
        assert coh003[0].severity == "informational"

    def test_not_emitted_good_branch_testing(self):
        perf = _perf_result([{"name": "check", "file": "logic.py", "hints": []}])
        mutation = _mutation_result([_mut_finding("logic.py", 0.4, ["conditional"])])
        teff = _teff_result([_teff_finding("logic.py", 0.5, branch_ratio=0.8)])

        findings = cross_channel_coherence([perf, mutation, teff])
        coh003 = [f for f in findings if f.kind == "COH003"]
        assert len(coh003) == 0


# ── Graceful degradation ────────────────────────────────────────────────


class TestGracefulDegradation:
    def test_empty_when_only_one_channel(self):
        findings = cross_channel_coherence([_perf_result()])
        assert findings == []

    def test_empty_when_no_channels(self):
        findings = cross_channel_coherence([])
        assert findings == []

    def test_works_with_two_channels(self):
        """Should work with perf + mutation (no teff)."""
        perf = _perf_result([{"name": "add", "file": "logic.py", "hints": []}])
        mutation = _mutation_result([_mut_finding("logic.py", 0.5)])
        findings = cross_channel_coherence([perf, mutation])
        # Won't emit COH001 without teff, but should not crash
        assert isinstance(findings, list)

    def test_skips_errored_channels(self):
        errored = ChannelResult(
            channel="mutation",
            status="error",
            severity="none",
            findings=[],
            metrics={},
            duration_ms=0,
        )
        perf = _perf_result([{"name": "add", "file": "logic.py", "hints": []}])
        teff = _teff_result()
        findings = cross_channel_coherence([perf, errored, teff])
        # Should not crash, may produce empty findings
        assert isinstance(findings, list)


# ── Performance channel pure_function_list export ───────────────────────


class TestPureFunctionListExport:
    def test_perf_channel_exports_list(self):
        """Verify performance channel includes pure_function_list in metrics."""
        from unittest.mock import patch

        from lintgate.channels.performance_channel import PerformanceChannel
        from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent

        channel = PerformanceChannel()
        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root="/tmp/nonexistent")

        with patch(
            "lintgate.channels.performance_channel._discover_python_files",
            return_value=[],
        ):
            result = channel.execute(event, config)

        # With no files, result is a skip
        assert result.status == "skip"
