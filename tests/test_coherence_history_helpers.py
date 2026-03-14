"""Tests for lintgate.controlplane.coherence.history — helper functions."""

from __future__ import annotations

from lintgate.controlplane.coherence.history import (
    detect_persistent_loud,
    detect_refactoring_tradeoffs,
    detect_resolutions,
    state_severity,
)
from lintgate.controlplane.session_memory import SessionMemory, SessionSnapshot
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue


def _snapshot(
    loud: list[str] | None = None,
    silent: list[str] | None = None,
    finding_index: dict | None = None,
) -> SessionSnapshot:
    return SessionSnapshot(
        loud_channels=loud or [],
        silent_channels=silent or [],
        finding_index=finding_index or {},
    )


def _session(snapshots: list[SessionSnapshot]) -> SessionMemory:
    mem = SessionMemory.__new__(SessionMemory)
    mem.snapshots = snapshots
    mem.session_id = "test"
    mem.project_path = "/tmp"
    mem.project_hash = "abc"
    mem.started_at = 0.0
    mem.last_active = 0.0
    mem.repairs_proposed = {}
    mem.repairs_applied = {}
    mem.session_knowledge = None
    return mem


# ── state_severity ───────────────────────────────────────────────────


class TestStateSeverity:
    def test_known_states(self):
        assert state_severity("stable") == 0
        assert state_severity("isolated") == 1
        assert state_severity("coupled") == 2
        assert state_severity("systemic") == 3
        assert state_severity("degraded") == 4

    def test_unknown_state_returns_zero(self):
        assert state_severity("unknown") == 0
        assert state_severity("") == 0


# ── detect_persistent_loud ───────────────────────────────────────────


class TestDetectPersistentLoud:
    def test_no_snapshots_returns_empty(self):
        session = _session([])
        assert detect_persistent_loud(session, ["lint"]) == []

    def test_one_snapshot_returns_empty(self):
        session = _session([_snapshot(loud=["lint"])])
        assert detect_persistent_loud(session, ["lint"]) == []

    def test_streak_of_three(self):
        session = _session([
            _snapshot(loud=["lint"]),
            _snapshot(loud=["lint"]),
        ])
        result = detect_persistent_loud(session, ["lint"])
        # 2 snapshots + 1 current = 3
        assert result == [("lint", 3)]

    def test_broken_streak(self):
        session = _session([
            _snapshot(loud=["lint"]),
            _snapshot(loud=[]),       # break
            _snapshot(loud=["lint"]),
        ])
        result = detect_persistent_loud(session, ["lint"])
        # Last snapshot has lint, + current = 2 (not >=3)
        assert result == []

    def test_empty_current_loud_returns_empty(self):
        session = _session([_snapshot(loud=["lint"]), _snapshot(loud=["lint"])])
        assert detect_persistent_loud(session, []) == []


# ── detect_resolutions ───────────────────────────────────────────────


class TestDetectResolutions:
    def test_no_snapshots_returns_empty(self):
        session = _session([])
        assert detect_resolutions(session, ["lint"]) == []

    def test_channel_resolved(self):
        session = _session([_snapshot(loud=["lint", "tests"])])
        result = detect_resolutions(session, ["lint", "git"])
        # lint was loud in last snapshot and is now silent
        assert result == ["lint"]

    def test_no_overlap(self):
        session = _session([_snapshot(loud=["tests"])])
        result = detect_resolutions(session, ["lint"])
        assert result == []

    def test_multiple_resolutions_sorted(self):
        session = _session([_snapshot(loud=["tests", "lint", "deps"])])
        result = detect_resolutions(session, ["tests", "lint", "deps"])
        assert result == ["deps", "lint", "tests"]


# ── detect_refactoring_tradeoffs ─────────────────────────────────────


class TestDetectRefactoringTradeoffs:
    def test_no_snapshots_returns_empty(self):
        session = _session([])
        results: list[ChannelResult] = []
        assert detect_refactoring_tradeoffs(results, session) == []

    def test_complexity_to_args_tradeoff(self):
        """Reducing cyclomatic_complexity while increasing too_many_args is a tradeoff."""
        prev_index = {
            "fp1": {"kind": "cyclomatic_complexity", "count": 5},
            "fp2": {"kind": "too_many_args", "count": 1},
        }
        session = _session([_snapshot(finding_index=prev_index)])

        # Current: cc=3 (decreased), args=3 (increased)
        current = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[
                    LintIssue(linter="test", kind="cyclomatic_complexity", message="cc"),
                    LintIssue(linter="test", kind="cyclomatic_complexity", message="cc"),
                    LintIssue(linter="test", kind="cyclomatic_complexity", message="cc"),
                    LintIssue(linter="test", kind="too_many_args", message="args"),
                    LintIssue(linter="test", kind="too_many_args", message="args"),
                    LintIssue(linter="test", kind="too_many_args", message="args"),
                ],
            )
        ]
        tradeoffs = detect_refactoring_tradeoffs(current, session)
        assert len(tradeoffs) == 1
        assert tradeoffs[0]["type"] == "refactor_tradeoff_detected"
        assert tradeoffs[0]["improved"] == "cyclomatic_complexity"
        assert tradeoffs[0]["regressed"] == "too_many_args"
        assert tradeoffs[0]["improved_delta"] == -2   # 3 - 5
        assert tradeoffs[0]["regressed_delta"] == 2   # 3 - 1

    def test_no_tradeoff_when_both_decrease(self):
        prev_index = {
            "fp1": {"kind": "cyclomatic_complexity", "count": 5},
            "fp2": {"kind": "too_many_args", "count": 3},
        }
        session = _session([_snapshot(finding_index=prev_index)])

        current = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[
                    LintIssue(linter="test", kind="cyclomatic_complexity", message="cc"),
                    LintIssue(linter="test", kind="too_many_args", message="args"),
                ],
            )
        ]
        tradeoffs = detect_refactoring_tradeoffs(current, session)
        assert tradeoffs == []

    def test_empty_finding_index_returns_empty(self):
        session = _session([_snapshot(finding_index={})])
        current = [
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[LintIssue(linter="test", kind="too_many_args", message="args")],
            )
        ]
        tradeoffs = detect_refactoring_tradeoffs(current, session)
        assert tradeoffs == []
