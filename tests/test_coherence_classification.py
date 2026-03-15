"""Tests for lintgate.controlplane.coherence.classification."""

from __future__ import annotations

from lintgate.controlplane.coherence.classification import (
    classify_coupled_failure,
    classify_isolated_failure,
    classify_systemic_failure,
)
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue


def _make_result(channel: str, findings: list[LintIssue] | None = None) -> ChannelResult:
    return ChannelResult(
        channel=channel,
        status="fail",
        severity="warning",
        findings=findings or [],
    )


def _issue(file: str = "a.py", kind: str = "E001", severity: str = "warning") -> LintIssue:
    return LintIssue(linter="test", kind=kind, message="msg", file=file, severity=severity)


# ── classify_isolated_failure ────────────────────────────────────────


class TestClassifyIsolatedFailure:
    def test_high_confidence_with_two_passing(self):
        failed = [_make_result("lint", [_issue()])]
        passed = [
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="git", status="pass"),
        ]
        result = classify_isolated_failure(
            failed, passed, [], loud=["lint"], silent=["tests", "git"]
        )
        assert result.state == "isolated"
        # conf = min(1.0, 0.7 + 0.1 * 2) = 0.9
        assert result.confidence == 0.9
        assert "lint" in result.summary
        assert "tests, git" in result.summary

    def test_high_confidence_with_three_passing(self):
        failed = [_make_result("lint", [_issue()])]
        passed = [
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="git", status="pass"),
            ChannelResult(channel="deps", status="pass"),
        ]
        result = classify_isolated_failure(
            failed, passed, [], loud=["lint"], silent=["tests", "git", "deps"]
        )
        # conf = min(1.0, 0.7 + 0.1 * 3) = 1.0
        assert result.confidence == 1.0
        assert result.state == "isolated"

    def test_low_confidence_with_one_passing(self):
        failed = [_make_result("lint", [_issue()])]
        passed = [ChannelResult(channel="tests", status="pass")]
        result = classify_isolated_failure(failed, passed, [], loud=["lint"], silent=["tests"])
        assert result.state == "isolated"
        # conf = 0.5 + 0.1 * 1 = 0.6
        assert result.confidence == 0.6
        assert "confidence is limited" in result.summary

    def test_low_confidence_no_passes(self):
        failed = [_make_result("lint", [_issue()])]
        result = classify_isolated_failure(failed, [], [], loud=["lint"], silent=[])
        assert result.state == "isolated"
        assert result.confidence == 0.3
        assert "No channels passed" in result.summary

    def test_demoted_notes_carried_through(self):
        failed = [_make_result("lint", [_issue()])]
        passed = [
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="git", status="pass"),
        ]
        notes = ["demoted: behavior"]
        result = classify_isolated_failure(
            failed, passed, notes, loud=["lint"], silent=["tests", "git"]
        )
        assert "demoted: behavior" in result.classification_notes


# ── classify_systemic_failure ────────────────────────────────────────


class TestClassifySystemicFailure:
    def test_returns_none_below_threshold(self):
        failed = [
            _make_result("lint", [_issue()]),
            _make_result("tests", [_issue()]),
        ]
        result = classify_systemic_failure(
            failed,
            loud=["lint", "tests"],
            silent=[],
            demoted_notes=[],
            severity_weighted=False,
        )
        # 2 failures < 3.0 and no cross-domain → None
        assert result is None

    def test_three_failures_is_systemic(self):
        failed = [
            _make_result("lint", [_issue()]),
            _make_result("tests", [_issue()]),
            _make_result("git", [_issue()]),
        ]
        result = classify_systemic_failure(
            failed,
            loud=["lint", "tests", "git"],
            silent=[],
            demoted_notes=[],
            severity_weighted=False,
        )
        assert result is not None
        assert result.state == "systemic"
        assert result.confidence == 0.9

    def test_cross_domain_two_channels(self):
        # deps (infra) + lint (code) = cross-domain
        failed = [
            _make_result("deps", [_issue()]),
            _make_result("lint", [_issue()]),
        ]
        result = classify_systemic_failure(
            failed,
            loud=["deps", "lint"],
            silent=[],
            demoted_notes=[],
            severity_weighted=False,
        )
        assert result is not None
        assert result.state == "systemic"
        assert result.confidence == 0.7
        assert any("cross-domain" in n for n in result.classification_notes)


# ── classify_coupled_failure ─────────────────────────────────────────


class TestClassifyCoupledFailure:
    def test_shared_files_two_channels(self):
        failed = [
            _make_result("lint", [_issue("shared.py")]),
            _make_result("tests", [_issue("shared.py")]),
        ]
        result = classify_coupled_failure(
            failed, loud=["lint", "tests"], silent=["git"], demoted_notes=[]
        )
        assert result.state == "coupled"
        assert result.confidence == 0.85
        assert "shared.py" in result.summary

    def test_no_shared_files_two_channels(self):
        failed = [
            _make_result("lint", [_issue("a.py")]),
            _make_result("tests", [_issue("b.py")]),
        ]
        result = classify_coupled_failure(
            failed, loud=["lint", "tests"], silent=[], demoted_notes=[]
        )
        assert result.state == "coupled"
        assert result.confidence == 0.7
        assert any("no shared files" in n for n in result.classification_notes)

    def test_shared_files_three_channels(self):
        failed = [
            _make_result("lint", [_issue("shared.py"), _issue("other.py")]),
            _make_result("tests", [_issue("shared.py")]),
            _make_result("git", [_issue("shared.py")]),
        ]
        result = classify_coupled_failure(
            failed, loud=["lint", "tests", "git"], silent=[], demoted_notes=[]
        )
        assert result.state == "coupled"
        assert result.confidence == 0.85
        assert "shared.py" in result.summary
        # 3 loud channels → ordered action
        assert "then resolve" in result.recommended_action
