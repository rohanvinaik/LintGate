"""Tests for trajectory-aware coherence diagnosis.

Tests compute_coherence_with_history() — the session-enriched version
that detects regressions, persistent failures, and resolutions.
"""

from __future__ import annotations

from lintgate.controlplane.coherence import (
    compute_coherence,
    compute_coherence_with_history,
)
from lintgate.controlplane.session_memory import SessionMemory, SessionSnapshot
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue

# ── Helpers ──────────────────────────────────────────────────────────


def _make_channel_results(
    loud: list[str] | None = None,
    silent: list[str] | None = None,
) -> list[ChannelResult]:
    """Build channel results with loud (fail) and silent (pass) channels."""
    results = []
    for name in loud or []:
        results.append(
            ChannelResult(
                channel=name,
                status="fail",
                severity="warning",
                findings=[
                    LintIssue(
                        linter=name, kind="E001", severity="warning", message="issue"
                    )
                ],
            )
        )
    for name in silent or []:
        results.append(ChannelResult(channel=name, status="pass"))
    return results


def _make_session_with_snapshots(
    snapshots: list[dict],
    trajectory: list[str] | None = None,
) -> SessionMemory:
    """Build a session with specified snapshot history."""
    session = SessionMemory(project_root="/test")
    for snap_data in snapshots:
        session.snapshots.append(
            SessionSnapshot(
                run_id=snap_data.get("run_id", "r"),
                coherence_state=snap_data.get("state", "stable"),
                loud_channels=snap_data.get("loud", []),
                silent_channels=snap_data.get("silent", []),
            )
        )
    if trajectory:
        session.coherence_trajectory = trajectory
    else:
        session.coherence_trajectory = [s.coherence_state for s in session.snapshots]
    return session


# ── Tests ────────────────────────────────────────────────────────────


class TestNoSession:
    """Without session, should behave identically to compute_coherence."""

    def test_no_session_returns_base(self):
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        base = compute_coherence(results)
        enriched = compute_coherence_with_history(results, session=None)

        assert enriched.state == base.state
        assert enriched.summary == base.summary
        assert enriched.recommended_action == base.recommended_action

    def test_empty_session_returns_base(self):
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        session = SessionMemory(project_root="/test")  # no snapshots
        enriched = compute_coherence_with_history(results, session=session)
        base = compute_coherence(results)

        assert enriched.state == base.state
        assert enriched.summary == base.summary


class TestRegressionDetection:
    """Detect when coherence state worsens from previous run."""

    def test_stable_to_isolated_regression(self):
        session = _make_session_with_snapshots(
            snapshots=[{"state": "stable", "loud": [], "silent": ["lint", "tests"]}],
            trajectory=["stable"],
        )
        # Current run: lint failing → isolated
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        assert enriched.state == "isolated"
        assert "REGRESSION" in enriched.summary
        assert "stable → isolated" in enriched.summary

    def test_isolated_to_systemic_regression(self):
        session = _make_session_with_snapshots(
            snapshots=[{"state": "isolated", "loud": ["lint"], "silent": ["tests"]}],
            trajectory=["isolated"],
        )
        results = _make_channel_results(
            loud=["lint", "tests", "deps"],
            silent=[],
        )
        enriched = compute_coherence_with_history(results, session)

        assert "REGRESSION" in enriched.summary
        assert "isolated → systemic" in enriched.summary

    def test_no_regression_same_state(self):
        session = _make_session_with_snapshots(
            snapshots=[{"state": "isolated", "loud": ["lint"], "silent": ["tests"]}],
            trajectory=["isolated"],
        )
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        assert "REGRESSION" not in enriched.summary

    def test_improvement_no_regression(self):
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "coupled", "loud": ["lint", "tests"], "silent": ["deps"]}
            ],
            trajectory=["coupled"],
        )
        # Now only lint failing → isolated (improvement!)
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        assert "REGRESSION" not in enriched.summary


class TestPersistentDetection:
    """Detect channels that have been loud for 3+ consecutive runs."""

    def test_three_run_persistence(self):
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "isolated", "loud": ["lint"], "silent": ["tests"]},
                {"state": "isolated", "loud": ["lint"], "silent": ["tests"]},
            ],
            trajectory=["isolated", "isolated"],
        )
        # Current run: lint still loud → 3 consecutive
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        assert "PERSISTENT" in enriched.summary
        assert "lint" in enriched.summary
        assert "3 consecutive runs" in enriched.summary
        assert "different approach" in enriched.recommended_action

    def test_two_run_not_persistent(self):
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "isolated", "loud": ["lint"], "silent": ["tests"]},
            ],
            trajectory=["isolated"],
        )
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        assert "PERSISTENT" not in enriched.summary

    def test_interrupted_streak_not_persistent(self):
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "isolated", "loud": ["lint"], "silent": ["tests"]},
                {
                    "state": "stable",
                    "loud": [],
                    "silent": ["lint", "tests"],
                },  # lint was silent
                {"state": "isolated", "loud": ["lint"], "silent": ["tests"]},
            ],
            trajectory=["isolated", "stable", "isolated"],
        )
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        # Only 2 consecutive (last snapshot + current), not 3
        assert "PERSISTENT" not in enriched.summary


class TestResolutionDetection:
    """Detect when previously-loud channels become silent."""

    def test_resolution_detected(self):
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "isolated", "loud": ["lint"], "silent": ["tests"]},
            ],
            trajectory=["isolated"],
        )
        # Current run: lint now passing!
        results = _make_channel_results(loud=[], silent=["lint", "tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        assert "RESOLVED" in enriched.summary
        assert "lint" in enriched.summary

    def test_no_resolution_if_still_loud(self):
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "isolated", "loud": ["lint"], "silent": ["tests"]},
            ],
            trajectory=["isolated"],
        )
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        assert "RESOLVED" not in enriched.summary


class TestCombinedAnnotations:
    """Multiple annotations in a single run."""

    def test_regression_and_persistent(self):
        """Regression + persistent when lint has been failing and state gets worse."""
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "isolated", "loud": ["lint"], "silent": ["tests", "deps"]},
                {"state": "isolated", "loud": ["lint"], "silent": ["tests", "deps"]},
            ],
            trajectory=["isolated", "isolated"],
        )
        # Now tests also fail → coupled, and lint still persists
        results = _make_channel_results(loud=["lint", "tests"], silent=["deps"])
        enriched = compute_coherence_with_history(results, session)

        assert "REGRESSION" in enriched.summary
        assert "PERSISTENT" in enriched.summary
        assert enriched.state in ("coupled", "systemic")

    def test_state_preserved_with_annotations(self):
        """Enrichment never changes the state enum."""
        session = _make_session_with_snapshots(
            snapshots=[
                {"state": "stable", "loud": [], "silent": ["lint", "tests"]},
            ],
            trajectory=["stable"],
        )
        results = _make_channel_results(loud=["lint"], silent=["tests", "deps"])
        enriched = compute_coherence_with_history(results, session)

        # State should be isolated (from base compute), not affected by annotations
        assert enriched.state == "isolated"
        assert enriched.loud_channels == ["lint"]
        assert "tests" in enriched.silent_channels


class TestTradeoffDetection:
    """Detect refactoring tradeoff patterns between runs."""

    def _make_session_with_findings(
        self, finding_index: dict[str, dict]
    ) -> SessionMemory:
        """Build session with one snapshot containing a finding_index."""
        session = SessionMemory(project_root="/test")
        session.snapshots.append(
            SessionSnapshot(
                run_id="prev",
                coherence_state="isolated",
                loud_channels=["lint"],
                silent_channels=["tests"],
                finding_index=finding_index,
            )
        )
        session.coherence_trajectory = ["isolated"]
        return session

    def test_cc_down_args_up_detected(self):
        """CC decrease + args increase → TRADEOFF annotation."""
        # Previous: 3 CC issues, 0 args issues
        prev_index = {
            "fp1": {"kind": "cyclomatic_complexity", "severity": "warning", "count": 1},
            "fp2": {"kind": "cyclomatic_complexity", "severity": "warning", "count": 1},
            "fp3": {"kind": "cyclomatic_complexity", "severity": "warning", "count": 1},
        }
        session = self._make_session_with_findings(prev_index)

        # Current: 1 CC issue, 2 args issues (decomposed functions)
        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    LintIssue(
                        linter="ruff",
                        kind="cyclomatic_complexity",
                        severity="warning",
                        message="CC=12",
                    ),
                    LintIssue(
                        linter="ruff",
                        kind="too_many_args",
                        severity="warning",
                        message="7 args",
                    ),
                    LintIssue(
                        linter="ruff",
                        kind="too_many_args",
                        severity="warning",
                        message="6 args",
                    ),
                ],
            ),
            ChannelResult(channel="tests", status="pass"),
        ]
        enriched = compute_coherence_with_history(results, session)

        assert "TRADEOFF" in enriched.summary
        assert "cyclomatic_complexity" in enriched.summary
        assert "too_many_args" in enriched.summary

    def test_no_tradeoff_when_both_increase(self):
        """Both CC and args increasing is not a tradeoff."""
        prev_index = {
            "fp1": {"kind": "cyclomatic_complexity", "severity": "warning", "count": 1},
        }
        session = self._make_session_with_findings(prev_index)

        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    LintIssue(
                        linter="ruff",
                        kind="cyclomatic_complexity",
                        severity="warning",
                        message="CC=15",
                    ),
                    LintIssue(
                        linter="ruff",
                        kind="cyclomatic_complexity",
                        severity="warning",
                        message="CC=12",
                    ),
                    LintIssue(
                        linter="ruff",
                        kind="too_many_args",
                        severity="warning",
                        message="7 args",
                    ),
                ],
            ),
            ChannelResult(channel="tests", status="pass"),
        ]
        enriched = compute_coherence_with_history(results, session)

        assert "TRADEOFF" not in enriched.summary

    def test_no_tradeoff_without_previous_findings(self):
        """No tradeoff if previous snapshot has empty finding_index."""
        session = self._make_session_with_findings({})

        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    LintIssue(
                        linter="ruff",
                        kind="too_many_args",
                        severity="warning",
                        message="7 args",
                    ),
                ],
            ),
            ChannelResult(channel="tests", status="pass"),
        ]
        enriched = compute_coherence_with_history(results, session)

        assert "TRADEOFF" not in enriched.summary

    def test_file_too_long_vs_too_many_functions(self):
        """file_too_long decrease + too_many_functions increase → TRADEOFF."""
        prev_index = {
            "fp1": {"kind": "file_too_long", "severity": "warning", "count": 2},
        }
        session = self._make_session_with_findings(prev_index)

        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    LintIssue(
                        linter="ruff",
                        kind="file_too_long",
                        severity="warning",
                        message="350 lines",
                    ),
                    LintIssue(
                        linter="ruff",
                        kind="too_many_functions",
                        severity="warning",
                        message="15 functions",
                    ),
                    LintIssue(
                        linter="ruff",
                        kind="too_many_functions",
                        severity="warning",
                        message="12 functions",
                    ),
                ],
            ),
            ChannelResult(channel="tests", status="pass"),
        ]
        enriched = compute_coherence_with_history(results, session)

        assert "TRADEOFF" in enriched.summary
        assert "file_too_long" in enriched.summary
        assert "too_many_functions" in enriched.summary

    def test_tradeoff_does_not_change_severity(self):
        """Tradeoff annotation must not change finding severity or coherence state."""
        prev_index = {
            "fp1": {
                "kind": "cyclomatic_complexity",
                "severity": "blocking",
                "count": 3,
            },
        }
        session = self._make_session_with_findings(prev_index)

        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="blocking",
                findings=[
                    LintIssue(
                        linter="ruff",
                        kind="too_many_args",
                        severity="blocking",
                        message="8 args",
                    ),
                ],
            ),
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="deps", status="pass"),
        ]
        enriched = compute_coherence_with_history(results, session)

        assert "TRADEOFF" in enriched.summary
        # State remains isolated (annotation only, no state change)
        assert enriched.state == "isolated"
        # Severity on the finding itself is unchanged
        assert results[0].findings[0].severity == "blocking"
