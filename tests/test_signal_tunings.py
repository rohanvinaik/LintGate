"""Tests for lintgate/signal_tunings.py — signal tuning eligibility, matching, and application."""

from __future__ import annotations

import os

from lintgate.signal_tunings import (
    _BLOCKED_CODES,
    _TUNABLE_CODES,
    VALID_ACTIONS,
    _tunings_path,
    apply_tuning,
    build_finding_signature,
    filter_tuned_issues,
    is_tunable,
    match_tuning,
)
from lintgate.types import LintIssue

# ── _tunings_path ──────────────────────────────────────────────────


class TestTuningsPath:
    def test_returns_expected_path(self):
        result = _tunings_path("/project")
        assert result == os.path.join("/project", ".lintgate", "signal_tunings.yaml")

    def test_preserves_trailing_separator(self):
        result = _tunings_path("/project/")
        assert ".lintgate" in result
        assert result.endswith("signal_tunings.yaml")


# ── is_tunable ─────────────────────────────────────────────────────


class TestIsTunable:
    def test_basic_tunable(self):
        assert is_tunable("E501", "informational", 0.5, 3) is True

    def test_blocked_code(self):
        assert is_tunable("F821", "informational", 0.5, 3) is False

    def test_severity_warning_rejected(self):
        assert is_tunable("E501", "warning", 0.5, 3) is False

    def test_severity_blocking_rejected(self):
        assert is_tunable("E501", "blocking", 0.5, 3) is False

    def test_high_confidence_rejected(self):
        assert is_tunable("E501", "informational", 0.8, 3) is False

    def test_confidence_just_below_threshold(self):
        assert is_tunable("E501", "informational", 0.79, 3) is True

    def test_low_recurrence_rejected(self):
        assert is_tunable("E501", "informational", 0.5, 2) is False

    def test_recurrence_at_threshold(self):
        assert is_tunable("E501", "informational", 0.5, 3) is True

    def test_unknown_code_rejected(self):
        assert is_tunable("UNKNOWN999", "informational", 0.5, 5) is False

    def test_policy_disables_tuning(self):
        assert (
            is_tunable("E501", "informational", 0.5, 3, policy={"allow_agent_tuning": False})
            is False
        )

    def test_policy_custom_blocked_code(self):
        assert (
            is_tunable("E501", "informational", 0.5, 3, policy={"blocked_codes": ["E501"]}) is False
        )

    def test_policy_custom_tunable_code(self):
        assert (
            is_tunable(
                "CUSTOM1", "informational", 0.5, 3, policy={"tunable_codes_override": ["CUSTOM1"]}
            )
            is True
        )

    def test_policy_custom_min_recurrence(self):
        assert is_tunable("E501", "informational", 0.5, 4, policy={"min_recurrence": 5}) is False
        assert is_tunable("E501", "informational", 0.5, 5, policy={"min_recurrence": 5}) is True

    def test_all_tunable_codes_accepted(self):
        for code in _TUNABLE_CODES:
            assert is_tunable(code, "informational", 0.5, 3) is True, f"{code} should be tunable"

    def test_all_blocked_codes_rejected(self):
        for code in _BLOCKED_CODES:
            assert is_tunable(code, "informational", 0.5, 3) is False, f"{code} should be blocked"


# ── build_finding_signature ────────────────────────────────────────


class TestBuildFindingSignature:
    def test_basic_signature(self):
        issue = LintIssue(
            linter="ruff", kind="E501", message="line too long", file="/project/foo.py"
        )
        sig = build_finding_signature(issue)
        assert sig == "ruff|E501|foo.py"

    def test_no_file(self):
        issue = LintIssue(linter="ruff", kind="E501", message="msg", file=None)
        sig = build_finding_signature(issue)
        assert sig == "ruff|E501|unknown"

    def test_empty_file(self):
        issue = LintIssue(linter="ruff", kind="E501", message="msg", file="")
        sig = build_finding_signature(issue)
        # empty string is falsy, so falls through to "unknown"
        assert sig == "ruff|E501|unknown"

    def test_nested_path_uses_basename(self):
        issue = LintIssue(linter="lint", kind="C901", message="complex", file="/a/b/c/deep.py")
        sig = build_finding_signature(issue)
        assert sig == "lint|C901|deep.py"

    def test_pipe_in_format(self):
        issue = LintIssue(linter="mypy", kind="F811", message="redef", file="/x.py")
        sig = build_finding_signature(issue)
        assert sig.count("|") == 2


# ── match_tuning ───────────────────────────────────────────────────


class TestMatchTuning:
    def _issue(self, linter="ruff", kind="E501", file="/project/foo.py"):
        return LintIssue(linter=linter, kind=kind, message="msg", file=file)

    def test_match_found(self):
        tunings = [{"signature": "ruff|E501|foo.py", "action": "suppress"}]
        result = match_tuning(self._issue(), tunings)
        assert result is not None
        assert result["action"] == "suppress"  # type: ignore[index]

    def test_no_match(self):
        tunings = [{"signature": "ruff|W291|foo.py", "action": "suppress"}]
        assert match_tuning(self._issue(), tunings) is None

    def test_reset_action_skipped(self):
        tunings = [{"signature": "ruff|E501|foo.py", "action": "reset"}]
        assert match_tuning(self._issue(), tunings) is None

    def test_first_match_wins(self):
        tunings = [
            {"signature": "ruff|E501|foo.py", "action": "suppress"},
            {"signature": "ruff|E501|foo.py", "action": "downgrade"},
        ]
        result = match_tuning(self._issue(), tunings)
        assert result is not None
        assert result["action"] == "suppress"

    def test_empty_tunings(self):
        assert match_tuning(self._issue(), []) is None


# ── apply_tuning ───────────────────────────────────────────────────


class TestApplyTuning:
    def test_invalid_action(self):
        result = apply_tuning("/tmp", "sig", "invalid_action", "reason")
        assert "error" in result

    def test_suppress_creates_entry(self, tmp_path):
        result = apply_tuning(str(tmp_path), "ruff|E501|foo.py", "suppress", "not relevant")
        assert result["applied"] is True
        assert result["action"] == "suppress"

    def test_reset_removes_entry(self, tmp_path):
        apply_tuning(str(tmp_path), "ruff|E501|foo.py", "suppress", "reason")
        result = apply_tuning(str(tmp_path), "ruff|E501|foo.py", "reset", "")
        assert result["removed"] is True

    def test_upsert_updates_existing(self, tmp_path):
        apply_tuning(str(tmp_path), "ruff|E501|foo.py", "suppress", "first")
        result = apply_tuning(str(tmp_path), "ruff|E501|foo.py", "downgrade", "second")
        assert result["applied"] is True
        assert result["action"] == "downgrade"

    def test_tuning_cap_enforced(self, tmp_path):
        for i in range(20):
            apply_tuning(str(tmp_path), f"sig_{i}", "suppress", f"reason_{i}")
        result = apply_tuning(str(tmp_path), "sig_overflow", "suppress", "too many")
        assert "error" in result
        assert "cap" in result["error"].lower()

    def test_valid_actions_constant(self):
        assert {"suppress", "downgrade", "reset"} == VALID_ACTIONS


# ── filter_tuned_issues ───────────────────────────────────────────


class TestFilterTunedIssues:
    def _issue(self, linter="ruff", kind="E501", file="/project/foo.py"):
        return LintIssue(linter=linter, kind=kind, message="msg", file=file)

    def test_no_tunings_passthrough(self, tmp_path):
        issues = [self._issue()]
        filtered, count = filter_tuned_issues(issues, str(tmp_path))
        assert len(filtered) == 1
        assert count == 0

    def test_suppress_removes_issue(self, tmp_path):
        apply_tuning(str(tmp_path), "ruff|E501|foo.py", "suppress", "not needed")
        issues = [self._issue()]
        filtered, count = filter_tuned_issues(issues, str(tmp_path))
        assert len(filtered) == 0
        assert count == 1

    def test_downgrade_keeps_issue_with_tuned_evidence(self, tmp_path):
        apply_tuning(str(tmp_path), "ruff|E501|foo.py", "downgrade", "lower priority")
        issues = [self._issue()]
        filtered, count = filter_tuned_issues(issues, str(tmp_path))
        assert len(filtered) == 1
        assert count == 1
        assert filtered[0].severity == "informational"
        assert filtered[0].evidence.get("tuned") is True

    def test_unmatched_issues_preserved(self, tmp_path):
        apply_tuning(str(tmp_path), "ruff|W291|other.py", "suppress", "reason")
        issues = [self._issue()]
        filtered, count = filter_tuned_issues(issues, str(tmp_path))
        assert len(filtered) == 1
        assert count == 0

    def test_mixed_suppress_and_keep(self, tmp_path):
        apply_tuning(str(tmp_path), "ruff|E501|foo.py", "suppress", "reason")
        issues = [
            self._issue(kind="E501"),
            self._issue(kind="W291"),
        ]
        filtered, count = filter_tuned_issues(issues, str(tmp_path))
        assert len(filtered) == 1
        assert filtered[0].kind == "W291"
        assert count == 1
