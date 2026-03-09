"""Tests for performance × test-effectiveness cross-channel coherence."""

from __future__ import annotations

from lintgate.controlplane.cross_channel import (
    _extract_assertion_quality,
    _extract_pure_functions,
    _find_channel,
    cross_channel_coherence,
)
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue


def _perf_result(pure_list: list[dict] | None = None) -> ChannelResult:
    return ChannelResult(
        channel="performance",
        status="pass",
        severity="none",
        findings=[],
        metrics={"pure_function_list": pure_list or []},
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


def _teff_finding(
    file: str,
    value_ratio: float,
    branch_ratio: float = 1.0,
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


class TestFindChannel:
    def test_finds_channel(self):
        results = [_perf_result(), _teff_result()]
        assert _find_channel(results, "performance") is not None
        assert _find_channel(results, "test_effectiveness") is not None

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


class TestExtractHelpers:
    def test_extract_pure_functions(self):
        perf = _perf_result([{"name": "add", "file": "logic.py", "hints": ["cacheable"]}])
        extracted = _extract_pure_functions(perf)
        assert extracted["add"]["file"] == "logic.py"

    def test_extract_assertion_quality(self):
        teff = _teff_result([_teff_finding("logic.py", value_ratio=0.1, branch_ratio=0.2)])
        extracted = _extract_assertion_quality(teff)
        assert extracted["logic.py"]["value_checking_ratio"] == 0.1
        assert extracted["logic.py"]["branch_testing_ratio"] == 0.2


class TestCoherenceFindings:
    def test_coh001_emitted_for_structural_heavy_assertions(self):
        perf = _perf_result([{"name": "add", "file": "logic.py", "hints": []}])
        teff = _teff_result([_teff_finding("logic.py", value_ratio=0.05)])

        findings = cross_channel_coherence([perf, teff])
        coh001 = [f for f in findings if f.kind == "COH001"]
        assert len(coh001) == 1
        assert coh001[0].severity == "warning"

    def test_coh002_emitted_for_low_branch_ratio(self):
        perf = _perf_result([{"name": "check", "file": "logic.py", "hints": []}])
        teff = _teff_result([_teff_finding("logic.py", value_ratio=0.8, branch_ratio=0.1)])

        findings = cross_channel_coherence([perf, teff])
        coh002 = [f for f in findings if f.kind == "COH002"]
        assert len(coh002) == 1
        assert coh002[0].severity == "informational"

    def test_no_findings_when_missing_required_channel(self):
        findings = cross_channel_coherence([_perf_result()])
        assert findings == []
