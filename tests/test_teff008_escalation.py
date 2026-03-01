"""Tests for TEFF008 — persistent test failure escalation and session exit gate.

Verifies that test failures persisting from session start to end are
escalated, and the session exit gate surfaces unresolved failures.
"""

from __future__ import annotations

import time

from lintgate.controlplane.session_memory import (
    SessionMemory,
    SessionSnapshot,
    check_session_exit_gate,
    escalate_persistent_failures,
    record_test_failure_classification,
)


def _make_session_with_snapshots(
    initial_findings: dict[str, dict],
    latest_findings: dict[str, dict],
) -> SessionMemory:
    """Build a session with two snapshots containing the given finding indexes."""
    session = SessionMemory(project_root="/tmp/test_project")
    session.snapshots = [
        SessionSnapshot(
            run_id="run_001",
            timestamp=time.time() - 600,
            finding_index=initial_findings,
        ),
        SessionSnapshot(
            run_id="run_002",
            timestamp=time.time(),
            finding_index=latest_findings,
        ),
    ]
    return session


class TestEscalatePersistentFailures:
    def test_persistent_failure_detected(self):
        """A test failure in both snapshots should be escalated."""
        findings = {
            "fp_test_001": {"kind": "test_failure", "message": "test_foo failed"},
        }
        session = _make_session_with_snapshots(findings, findings)

        result = escalate_persistent_failures(session)
        assert len(result) == 1
        assert result[0]["fingerprint"] == "fp_test_001"
        assert result[0]["kind"] == "test_failure"

    def test_resolved_failure_not_escalated(self):
        """A failure present initially but gone in latest should not escalate."""
        initial = {
            "fp_test_001": {"kind": "test_failure", "message": "test_foo failed"},
        }
        latest = {}
        session = _make_session_with_snapshots(initial, latest)

        result = escalate_persistent_failures(session)
        assert len(result) == 0

    def test_new_failure_not_escalated(self):
        """A failure only in latest (not initial) should not escalate."""
        initial = {}
        latest = {
            "fp_test_002": {"kind": "test_failure", "message": "test_bar failed"},
        }
        session = _make_session_with_snapshots(initial, latest)

        result = escalate_persistent_failures(session)
        assert len(result) == 0

    def test_non_test_findings_ignored(self):
        """Non-test findings should not be escalated."""
        findings = {
            "fp_lint_001": {"kind": "ruff_error", "message": "F821 undefined"},
        }
        session = _make_session_with_snapshots(findings, findings)

        result = escalate_persistent_failures(session)
        assert len(result) == 0

    def test_teff009_findings_tracked(self):
        """TEFF009 (stale test) findings should also be tracked for persistence."""
        findings = {
            "fp_stale_001": {"kind": "TEFF009", "message": "stale reference"},
        }
        session = _make_session_with_snapshots(findings, findings)

        result = escalate_persistent_failures(session)
        assert len(result) == 1

    def test_classified_failure_not_escalated(self):
        """A persistent failure that was classified should not be escalated."""
        findings = {
            "fp_test_001": {"kind": "test_failure", "message": "test_foo failed"},
        }
        session = _make_session_with_snapshots(findings, findings)

        # Classify the failure
        record_test_failure_classification(
            session, "fp_test_001", "stale_test", "function was deleted"
        )

        result = escalate_persistent_failures(session)
        assert len(result) == 0

    def test_single_snapshot_returns_empty(self):
        """With only one snapshot, no escalation is possible."""
        session = SessionMemory(project_root="/tmp/test")
        session.snapshots = [
            SessionSnapshot(
                run_id="run_001",
                finding_index={"fp_001": {"kind": "test_failure", "message": "fail"}},
            ),
        ]
        result = escalate_persistent_failures(session)
        assert len(result) == 0

    def test_multiple_persistent_failures(self):
        """Multiple persistent failures should all be escalated."""
        findings = {
            "fp_test_001": {"kind": "test_failure", "message": "test_a failed"},
            "fp_test_002": {"kind": "test_failure", "message": "test_b failed"},
            "fp_test_003": {"kind": "test_failure", "message": "test_c failed"},
        }
        session = _make_session_with_snapshots(findings, findings)

        result = escalate_persistent_failures(session)
        assert len(result) == 3


class TestCheckSessionExitGate:
    def test_advisory_emitted_for_persistent_failures(self):
        """Exit gate should emit advisory when persistent failures exist."""
        findings = {
            "fp_test_001": {"kind": "test_failure", "message": "test_foo failed"},
        }
        session = _make_session_with_snapshots(findings, findings)

        advisories = check_session_exit_gate(session)
        assert len(advisories) == 1
        assert "1 test failure" in advisories[0]
        assert "controlplane_agent_feedback" in advisories[0]

    def test_no_advisory_when_all_resolved(self):
        """Exit gate should be clean when failures are resolved."""
        initial = {"fp_test_001": {"kind": "test_failure", "message": "fail"}}
        latest = {}
        session = _make_session_with_snapshots(initial, latest)

        advisories = check_session_exit_gate(session)
        assert len(advisories) == 0

    def test_no_advisory_when_classified(self):
        """Exit gate should be clean when failures are classified."""
        findings = {
            "fp_test_001": {"kind": "test_failure", "message": "fail"},
        }
        session = _make_session_with_snapshots(findings, findings)
        record_test_failure_classification(
            session, "fp_test_001", "known_regression", "tracked in #123"
        )

        advisories = check_session_exit_gate(session)
        assert len(advisories) == 0

    def test_plural_advisory_message(self):
        """Advisory should use plural when multiple failures persist."""
        findings = {
            "fp_test_001": {"kind": "test_failure", "message": "fail_a"},
            "fp_test_002": {"kind": "test_failure", "message": "fail_b"},
        }
        session = _make_session_with_snapshots(findings, findings)

        advisories = check_session_exit_gate(session)
        assert "2 test failures" in advisories[0]


class TestRecordTestFailureClassification:
    def test_valid_classification_recorded(self):
        session = SessionMemory(project_root="/tmp/test")
        record_test_failure_classification(
            session, "fp_001", "stale_test", "function deleted"
        )
        assert len(session.agent_disagreements) == 1
        entry = session.agent_disagreements[0]
        assert entry["type"] == "test_failure_classification"
        assert entry["fingerprint"] == "fp_001"
        assert entry["classification"] == "stale_test"

    def test_invalid_classification_rejected(self):
        session = SessionMemory(project_root="/tmp/test")
        record_test_failure_classification(session, "fp_001", "invalid_type", "bad")
        assert len(session.agent_disagreements) == 0

    def test_all_valid_classifications(self):
        session = SessionMemory(project_root="/tmp/test")
        for cls in ("stale_test", "known_regression", "flaky", "out_of_scope"):
            record_test_failure_classification(session, f"fp_{cls}", cls, "reason")
        assert len(session.agent_disagreements) == 4
