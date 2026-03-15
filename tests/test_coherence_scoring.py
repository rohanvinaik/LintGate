"""Tests for lintgate.controlplane.coherence.scoring — severity-weighted coherence scoring."""

from __future__ import annotations

from lintgate.controlplane.coherence.scoring import (
    channel_failure_weight,
    channel_finding_summary,
    effective_failure_count,
    find_shared_files,
    finding_severity_counts,
    is_cross_domain_failure,
    ordered_failed_channels,
    top_finding_kind,
)
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue


def _make_issue(
    severity: str = "warning", kind: str = "test-kind", file: str | None = None
) -> LintIssue:
    return LintIssue(
        linter="test",
        kind=kind,
        message="test issue",
        severity=severity,
        file=file,
    )


def _make_result(
    channel: str = "lint",
    status: str = "fail",
    severity: str = "warning",
    findings: list[LintIssue] | None = None,
) -> ChannelResult:
    return ChannelResult(
        channel=channel,
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        findings=findings or [],
    )


# ── finding_severity_counts ──────────────────────────────────────────


class TestFindingSeverityCounts:
    def test_counts_by_severity(self):
        result = _make_result(
            findings=[
                _make_issue("blocking"),
                _make_issue("blocking"),
                _make_issue("warning"),
                _make_issue("informational"),
            ]
        )
        counts = finding_severity_counts(result)
        assert counts == {"blocking": 2, "warning": 1, "informational": 1}

    def test_empty_findings_with_fail_uses_channel_severity(self):
        result = _make_result(status="fail", severity="blocking", findings=[])
        counts = finding_severity_counts(result)
        assert counts == {"blocking": 1, "warning": 0, "informational": 0}

    def test_empty_findings_with_fail_unknown_severity(self):
        result = _make_result(status="fail", severity="custom", findings=[])
        counts = finding_severity_counts(result)
        assert counts == {"blocking": 0, "warning": 1, "informational": 0}

    def test_empty_findings_pass_status(self):
        result = _make_result(status="pass", severity="warning", findings=[])
        counts = finding_severity_counts(result)
        assert counts == {"blocking": 0, "warning": 0, "informational": 0}

    def test_unknown_finding_severity_mapped_to_informational(self):
        result = _make_result(findings=[_make_issue("critical")])
        counts = finding_severity_counts(result)
        assert counts["informational"] == 1


# ── channel_failure_weight ───────────────────────────────────────────


class TestChannelFailureWeight:
    def test_blocking_finding_weight(self):
        result = _make_result(findings=[_make_issue("blocking")])
        w = channel_failure_weight(result)
        assert w == 1.0  # 1 * _BLOCKING_COUNT_WEIGHT

    def test_warning_finding_weight(self):
        result = _make_result(findings=[_make_issue("warning")])
        w = channel_failure_weight(result)
        assert w == 0.35  # 1 * _WARNING_COUNT_WEIGHT

    def test_informational_finding_weight(self):
        # 1 info finding: base = 0.10, but 100% informational > 80% → damped by 0.3
        result = _make_result(findings=[_make_issue("informational")])
        w = channel_failure_weight(result)
        assert abs(w - 0.03) < 0.001  # 0.10 * 0.3 = 0.03

    def test_mostly_informational_damped(self):
        # 9 informational + 1 warning = 90% informational → damped by 0.3
        findings = [_make_issue("informational") for _ in range(9)] + [_make_issue("warning")]
        result = _make_result(findings=findings)
        w = channel_failure_weight(result)
        # base = 9*0.10 + 1*0.35 = 1.25; > 80% info → 1.25 * 0.3 = 0.375
        assert abs(w - 0.375) < 0.001

    def test_capped_at_max(self):
        findings = [_make_issue("blocking") for _ in range(5)]
        result = _make_result(findings=findings)
        w = channel_failure_weight(result)
        # 5 * 1.0 = 5.0, capped at 2.0
        assert w == 2.0

    def test_no_findings_uses_severity_weight(self):
        result = _make_result(status="pass", severity="warning", findings=[])
        w = channel_failure_weight(result)
        assert w == 0.55  # _SEVERITY_WEIGHT["warning"]


# ── effective_failure_count ──────────────────────────────────────────


class TestEffectiveFailureCount:
    def test_single_channel(self):
        results = [_make_result(findings=[_make_issue("blocking")])]
        assert effective_failure_count(results) == 1.0

    def test_with_channel_weights(self):
        results = [_make_result(channel="lint", findings=[_make_issue("blocking")])]
        total = effective_failure_count(results, channel_weights={"lint": 2.0})
        assert total == 2.0  # 1.0 * 2.0

    def test_unweighted_channel_gets_default(self):
        results = [_make_result(channel="custom", findings=[_make_issue("blocking")])]
        total = effective_failure_count(results, channel_weights={"lint": 2.0})
        assert total == 0.5  # 1.0 * 0.5 (default)

    def test_empty_results(self):
        assert effective_failure_count([]) == 0.0


# ── ordered_failed_channels ─────────────────────────────────────────


class TestOrderedFailedChannels:
    def test_orders_by_weight_descending(self):
        results = [
            _make_result(channel="info_ch", findings=[_make_issue("informational")]),
            _make_result(channel="block_ch", findings=[_make_issue("blocking")]),
            _make_result(channel="warn_ch", findings=[_make_issue("warning")]),
        ]
        ordered = ordered_failed_channels(results)
        assert ordered == ["block_ch", "warn_ch", "info_ch"]

    def test_tiebreak_alphabetical(self):
        results = [
            _make_result(channel="beta", findings=[_make_issue("blocking")]),
            _make_result(channel="alpha", findings=[_make_issue("blocking")]),
        ]
        ordered = ordered_failed_channels(results)
        assert ordered == ["alpha", "beta"]


# ── find_shared_files ────────────────────────────────────────────────


class TestFindSharedFiles:
    def test_shared_across_two_channels(self):
        results = [
            _make_result(
                channel="lint", findings=[_make_issue(file="a.py"), _make_issue(file="b.py")]
            ),
            _make_result(
                channel="tests", findings=[_make_issue(file="b.py"), _make_issue(file="c.py")]
            ),
        ]
        shared = find_shared_files(results)
        assert shared == {"b.py"}

    def test_no_shared_files(self):
        results = [
            _make_result(channel="lint", findings=[_make_issue(file="a.py")]),
            _make_result(channel="tests", findings=[_make_issue(file="b.py")]),
        ]
        shared = find_shared_files(results)
        assert shared == set()

    def test_single_channel_returns_empty(self):
        results = [_make_result(findings=[_make_issue(file="a.py")])]
        shared = find_shared_files(results)
        assert shared == set()

    def test_findings_with_no_file_excluded(self):
        results = [
            _make_result(channel="lint", findings=[_make_issue(file=None)]),
            _make_result(channel="tests", findings=[_make_issue(file=None)]),
        ]
        shared = find_shared_files(results)
        assert shared == set()


# ── is_cross_domain_failure ──────────────────────────────────────────


class TestIsCrossDomainFailure:
    def test_infra_and_code_failure(self):
        results = [
            _make_result(channel="deps"),
            _make_result(channel="lint"),
        ]
        assert is_cross_domain_failure(results) is True

    def test_only_code_channels(self):
        results = [
            _make_result(channel="lint"),
            _make_result(channel="tests"),
        ]
        assert is_cross_domain_failure(results) is False

    def test_only_infra_channels(self):
        results = [
            _make_result(channel="deps"),
            _make_result(channel="git"),
        ]
        assert is_cross_domain_failure(results) is False

    def test_with_effective_count_below_threshold(self):
        results = [
            _make_result(channel="git"),
            _make_result(channel="lint"),
        ]
        assert is_cross_domain_failure(results, effective_failure_count=1.0) is False

    def test_with_effective_count_at_threshold(self):
        results = [
            _make_result(channel="deps"),
            _make_result(channel="structure"),
        ]
        assert is_cross_domain_failure(results, effective_failure_count=1.25) is True


# ── top_finding_kind ─────────────────────────────────────────────────


class TestTopFindingKind:
    def test_most_common_kind(self):
        result = _make_result(
            findings=[
                _make_issue(kind="complexity"),
                _make_issue(kind="complexity"),
                _make_issue(kind="import-error"),
            ]
        )
        assert top_finding_kind(result) == "complexity"

    def test_no_findings_returns_empty(self):
        result = _make_result(findings=[])
        assert top_finding_kind(result) == ""

    def test_single_finding(self):
        result = _make_result(findings=[_make_issue(kind="lint-error")])
        assert top_finding_kind(result) == "lint-error"


# ── channel_finding_summary ──────────────────────────────────────────


class TestChannelFindingSummary:
    def test_zero_findings(self):
        result = _make_result(findings=[])
        assert channel_finding_summary(result) == "0 findings"

    def test_single_finding(self):
        result = _make_result(findings=[_make_issue(kind="complexity")])
        summary = channel_finding_summary(result)
        assert summary == "1 finding (top: complexity)"

    def test_multiple_findings(self):
        result = _make_result(
            findings=[
                _make_issue(kind="complexity"),
                _make_issue(kind="import-error"),
                _make_issue(kind="complexity"),
            ]
        )
        summary = channel_finding_summary(result)
        assert summary == "3 findings (top: complexity)"
