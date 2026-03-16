"""Prescriptive spec tests for _has_actionable_findings.

Target: coherence::_has_actionable_findings
8 behavioral claims pinning every severity path.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

from lintgate.controlplane.coherence import _has_actionable_findings
from lintgate.controlplane.types import ChannelResult

_Severity = Literal["blocking", "warning", "informational", "none"]


def _finding(severity: str = "informational") -> SimpleNamespace:
    return SimpleNamespace(severity=severity)


def _result(
    findings: list | None = None,
    severity: _Severity = "none",
) -> ChannelResult:
    r = ChannelResult(channel="test", status="fail", severity=severity)
    r.findings = findings or []
    return r


class TestBlockingFinding:
    def test_blocking_finding_returns_true(self):
        assert _has_actionable_findings(_result([_finding("blocking")])) is True

    def test_blocking_among_informational(self):
        assert (
            _has_actionable_findings(_result([_finding("informational"), _finding("blocking")]))
            is True
        )


class TestWarningFinding:
    def test_warning_finding_returns_true(self):
        assert _has_actionable_findings(_result([_finding("warning")])) is True

    def test_warning_among_informational(self):
        assert (
            _has_actionable_findings(_result([_finding("informational"), _finding("warning")]))
            is True
        )


class TestInformationalOnly:
    def test_all_informational_no_channel_severity(self):
        assert (
            _has_actionable_findings(
                _result(
                    [_finding("informational"), _finding("informational")], severity="informational"
                )
            )
            is False
        )

    def test_all_informational_with_none_severity(self):
        assert (
            _has_actionable_findings(_result([_finding("informational")], severity="none")) is False
        )


class TestChannelSeverityFallback:
    def test_channel_blocking_no_blocking_findings(self):
        """Channel-level severity=blocking with only informational findings → True."""
        assert (
            _has_actionable_findings(_result([_finding("informational")], severity="blocking"))
            is True
        )

    def test_channel_warning_no_warning_findings(self):
        """Channel-level severity=warning with only informational findings → True."""
        assert (
            _has_actionable_findings(_result([_finding("informational")], severity="warning"))
            is True
        )

    def test_channel_informational_no_actionable_findings(self):
        """Channel-level severity=informational → False."""
        assert (
            _has_actionable_findings(_result([_finding("informational")], severity="informational"))
            is False
        )


class TestEmptyFindings:
    def test_empty_findings_informational_severity(self):
        assert _has_actionable_findings(_result([], severity="informational")) is False

    def test_empty_findings_none_severity(self):
        assert _has_actionable_findings(_result([], severity="none")) is False

    def test_empty_findings_blocking_severity(self):
        """Empty findings but channel-level blocking → True (fallback)."""
        assert _has_actionable_findings(_result([], severity="blocking")) is True

    def test_empty_findings_warning_severity(self):
        assert _has_actionable_findings(_result([], severity="warning")) is True


class TestReturnType:
    def test_returns_bool(self):
        result = _has_actionable_findings(_result())
        assert isinstance(result, bool)
