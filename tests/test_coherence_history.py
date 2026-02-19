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
                findings=[LintIssue(linter=name, kind="E001", severity="warning", message="issue")],
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
            snapshots=[{"state": "coupled", "loud": ["lint", "tests"], "silent": ["deps"]}],
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
                {"state": "stable", "loud": [], "silent": ["lint", "tests"]},  # lint was silent
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
