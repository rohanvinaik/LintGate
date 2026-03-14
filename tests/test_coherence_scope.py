"""Tests for lintgate.controlplane.coherence.scope."""

from __future__ import annotations

from lintgate.controlplane.coherence.scope import (
    apply_edit_scope,
    classify_edit_scope,
    has_ambient_critical_findings,
)
from lintgate.controlplane.types import ChannelResult, CoherenceResult
from lintgate.types import LintIssue


def _issue(
    file: str = "a.py", kind: str = "E001", severity: str = "warning"
) -> LintIssue:
    return LintIssue(linter="test", kind=kind, message="msg", file=file, severity=severity)


def _result(
    channel: str, findings: list[LintIssue] | None = None, status: str = "fail"
) -> ChannelResult:
    return ChannelResult(
        channel=channel, status=status, severity="warning", findings=findings or []
    )


# ── classify_edit_scope ──────────────────────────────────────────────


class TestClassifyEditScope:
    def test_edit_related_by_absolute_path(self, tmp_path):
        target = tmp_path / "foo.py"
        target.write_text("x = 1")
        findings = [_issue(file=str(target))]
        cr = _result("lint", findings)

        edit_related, ambient, unknown = classify_edit_scope(
            [cr], [str(target)]
        )
        assert edit_related == ["lint"]
        assert ambient == []
        assert unknown == []

    def test_ambient_when_no_overlap(self, tmp_path):
        changed = tmp_path / "changed.py"
        changed.write_text("")
        finding_file = tmp_path / "other.py"
        finding_file.write_text("")
        cr = _result("lint", [_issue(file=str(finding_file))])

        edit_related, ambient, unknown = classify_edit_scope(
            [cr], [str(changed)]
        )
        assert edit_related == []
        assert ambient == ["lint"]

    def test_unknown_scope_no_file_evidence(self):
        cr = _result("lint", [_issue(file="")])
        edit_related, ambient, unknown = classify_edit_scope([cr], ["foo.py"])
        assert unknown == ["lint"]

    def test_unknown_scope_no_findings(self):
        cr = _result("lint", findings=[])
        edit_related, ambient, unknown = classify_edit_scope([cr], ["foo.py"])
        assert unknown == ["lint"]


# ── has_ambient_critical_findings ────────────────────────────────────


class TestHasAmbientCriticalFindings:
    def test_blocking_severity_is_critical(self):
        cr = _result("lint", [_issue(severity="blocking")])
        assert has_ambient_critical_findings([cr], ["lint"]) is True

    def test_security_kind_is_critical(self):
        cr = _result("lint", [_issue(kind="secret_in_code")])
        assert has_ambient_critical_findings([cr], ["lint"]) is True

    def test_non_ambient_channel_ignored(self):
        cr = _result("lint", [_issue(severity="blocking")])
        assert has_ambient_critical_findings([cr], ["tests"]) is False

    def test_normal_warning_not_critical(self):
        cr = _result("lint", [_issue(severity="warning", kind="E001")])
        assert has_ambient_critical_findings([cr], ["lint"]) is False


# ── apply_edit_scope ─────────────────────────────────────────────────


class TestApplyEditScope:
    def test_stable_state_returned_unchanged(self):
        coherence = CoherenceResult(state="stable")
        channel_results = [_result("lint", [_issue()])]
        result = apply_edit_scope(coherence, channel_results, ["a.py"])
        assert result.state == "stable"
        assert result.edit_scoped is False

    def test_all_ambient_downgraded_to_stable(self, tmp_path):
        changed = tmp_path / "changed.py"
        changed.write_text("")
        other = tmp_path / "other.py"
        other.write_text("")

        coherence = CoherenceResult(
            state="isolated",
            loud_channels=["lint"],
            silent_channels=["tests"],
            confidence=0.9,
            classification_notes=[],
        )
        cr = _result("lint", [_issue(file=str(other), severity="warning", kind="E001")])

        result = apply_edit_scope(coherence, [cr], [str(changed)])
        assert result.state == "stable"
        assert result.edit_scoped is True
        assert result.ambient_channels == ["lint"]
        assert result.loud_channels == []

    def test_all_ambient_with_critical_stays_isolated(self, tmp_path):
        changed = tmp_path / "changed.py"
        changed.write_text("")
        other = tmp_path / "other.py"
        other.write_text("")

        coherence = CoherenceResult(
            state="isolated",
            loud_channels=["lint"],
            silent_channels=[],
            confidence=0.9,
            classification_notes=[],
        )
        cr = _result("lint", [_issue(file=str(other), severity="blocking")])

        result = apply_edit_scope(coherence, [cr], [str(changed)])
        assert result.state == "isolated"
        assert result.edit_scoped is True
        assert "blocking/security" in result.classification_notes[-1]

    def test_mixed_ambient_and_edit_keeps_state(self, tmp_path):
        target = tmp_path / "target.py"
        target.write_text("")
        other = tmp_path / "other.py"
        other.write_text("")

        coherence = CoherenceResult(
            state="coupled",
            summary="Two channels fail",
            recommended_action="Fix things",
            loud_channels=["lint", "tests"],
            silent_channels=[],
            confidence=0.85,
            classification_notes=[],
        )
        cr_lint = _result("lint", [_issue(file=str(target))])
        cr_tests = _result("tests", [_issue(file=str(other))])

        result = apply_edit_scope(coherence, [cr_lint, cr_tests], [str(target)])
        assert result.state == "coupled"
        assert result.edit_scoped is True
        assert result.edit_related_channels == ["lint"]
        assert result.ambient_channels == ["tests"]
        assert "pre-existing" in result.recommended_action
