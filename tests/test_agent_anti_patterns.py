"""Tests for agent anti-pattern detection (#191).

Covers:
- detect_mass_delegation: warns on 3+ Agent/Task spawns in short window
- detect_redundant_planning: warns on EnterPlanMode after controlplane_run
- Hook expansion: Agent/EnterPlanMode events recorded to behavior compass
"""

from __future__ import annotations

import time

from lintgate.channels.behavior_detection import (
    detect_mass_delegation,
    detect_redundant_planning,
)
from lintgate.channels.behavior_scoring import IntentBiasScorer, SignalCoordinator
from lintgate.controlplane.behavior_compass import (
    BehaviorCompass,
    record_tool_event,
)
from lintgate.controlplane.behavior_types import CoverageMetrics
from lintgate.controlplane.command_normalization import resolve_intent

# ── Helpers ──────────────────────────────────────────────────────────


def _make_coord(compass: BehaviorCompass) -> SignalCoordinator:
    return SignalCoordinator(compass, {"signal_cooldown": 0, "escalation_threshold": 100})


def _make_scorer(compass: BehaviorCompass) -> IntentBiasScorer:
    return IntentBiasScorer(compass, {})


def _make_compass_with_events(events: list[dict]) -> BehaviorCompass:
    """Build a BehaviorCompass with pre-populated action_history."""
    compass = BehaviorCompass(coverage=CoverageMetrics())
    compass.action_history = list(events)
    compass.intent_history = [e.get("intent", "unknown") for e in events]
    return compass


def _agent_event(description: str = "fix complexity", ts: float = 0.0) -> dict:
    return {
        "tool": "Agent",
        "ts": ts,
        "sig": description,
        "exit": None,
        "err": "",
        "intent": "meta",
    }


def _task_event(description: str = "refactor module", ts: float = 0.0) -> dict:
    return {
        "tool": "Task",
        "ts": ts,
        "sig": description,
        "exit": None,
        "err": "",
        "intent": "meta",
    }


def _cp_run_event(ts: float = 0.0) -> dict:
    return {
        "tool": "controlplane_run",
        "ts": ts,
        "sig": "",
        "exit": None,
        "err": "",
        "intent": "meta",
    }


def _plan_mode_event(ts: float = 0.0) -> dict:
    return {
        "tool": "EnterPlanMode",
        "ts": ts,
        "sig": "",
        "exit": None,
        "err": "",
        "intent": "meta",
    }


def _bash_event(sig: str = "pytest:tests", ts: float = 0.0) -> dict:
    return {
        "tool": "Bash",
        "ts": ts,
        "sig": sig,
        "exit": 0,
        "err": "",
        "intent": "execute",
    }


# ── detect_mass_delegation ───────────────────────────────────────────


class TestDetectMassDelegation:
    def test_no_delegation_no_finding(self):
        compass = _make_compass_with_events([_bash_event(ts=100)])
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 0

    def test_below_threshold_no_finding(self):
        now = time.time()
        compass = _make_compass_with_events(
            [
                _agent_event("fix complexity", ts=now - 60),
                _agent_event("extract method", ts=now - 30),
                _bash_event(ts=now),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 0

    def test_three_agents_fires(self):
        now = time.time()
        compass = _make_compass_with_events(
            [
                _agent_event("fix complexity", ts=now - 120),
                _agent_event("extract method", ts=now - 60),
                _agent_event("split module", ts=now),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 1
        assert coord.findings[0].kind == "mass_delegation"
        assert "3 sub-agent spawns" in coord.findings[0].message

    def test_mixed_agent_task_fires(self):
        now = time.time()
        compass = _make_compass_with_events(
            [
                _agent_event("fix complexity", ts=now - 120),
                _task_event("refactor module", ts=now - 60),
                _agent_event("simplify logic", ts=now),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 1

    def test_outside_window_no_finding(self):
        now = time.time()
        compass = _make_compass_with_events(
            [
                _agent_event("fix complexity", ts=now - 3600),  # 60 min ago
                _agent_event("extract method", ts=now - 3000),
                _agent_event("split module", ts=now),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        # Default window is 10 min, first event is outside
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 0

    def test_refactoring_keywords_noted(self):
        now = time.time()
        compass = _make_compass_with_events(
            [
                _agent_event("fix complexity", ts=now - 120),
                _agent_event("refactor auth", ts=now - 60),
                _agent_event("extract helper", ts=now),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 1
        assert "refactoring-related" in coord.findings[0].message

    def test_non_refactoring_still_fires(self):
        now = time.time()
        compass = _make_compass_with_events(
            [
                _agent_event("research api", ts=now - 120),
                _agent_event("search docs", ts=now - 60),
                _agent_event("check status", ts=now),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 1
        # No refactoring keywords mentioned
        assert "refactoring-related" not in coord.findings[0].message

    def test_custom_threshold(self):
        now = time.time()
        compass = _make_compass_with_events(
            [
                _agent_event("fix a", ts=now - 120),
                _agent_event("fix b", ts=now - 60),
                _agent_event("fix c", ts=now - 30),
                _agent_event("fix d", ts=now),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {"mass_delegation_count": 5}, coord, scorer)
        assert len(coord.findings) == 0  # 4 < 5

    def test_empty_history_no_crash(self):
        compass = _make_compass_with_events([])
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_mass_delegation(compass, {}, coord, scorer)
        assert len(coord.findings) == 0


# ── detect_redundant_planning ────────────────────────────────────────


class TestDetectRedundantPlanning:
    def test_no_cp_run_no_finding(self):
        compass = _make_compass_with_events(
            [
                _plan_mode_event(ts=100),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_redundant_planning(compass, {}, coord, scorer)
        assert len(coord.findings) == 0

    def test_cp_run_then_plan_fires(self):
        compass = _make_compass_with_events(
            [
                _cp_run_event(ts=100),
                _bash_event(ts=200),
                _plan_mode_event(ts=300),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_redundant_planning(compass, {}, coord, scorer)
        assert len(coord.findings) == 1
        assert coord.findings[0].kind == "redundant_planning"
        assert "ControlPlane findings are your plan" in coord.findings[0].message

    def test_plan_before_cp_run_no_finding(self):
        compass = _make_compass_with_events(
            [
                _plan_mode_event(ts=100),
                _cp_run_event(ts=200),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_redundant_planning(compass, {}, coord, scorer)
        assert len(coord.findings) == 0

    def test_no_plan_mode_no_finding(self):
        compass = _make_compass_with_events(
            [
                _cp_run_event(ts=100),
                _bash_event(ts=200),
            ]
        )
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_redundant_planning(compass, {}, coord, scorer)
        assert len(coord.findings) == 0

    def test_empty_history_no_crash(self):
        compass = _make_compass_with_events([])
        coord, scorer = _make_coord(compass), _make_scorer(compass)
        detect_redundant_planning(compass, {}, coord, scorer)
        assert len(coord.findings) == 0


# ── record_tool_event integration ────────────────────────────────────


class TestRecordToolEventExpansion:
    def test_agent_event_records_description(self):
        compass = BehaviorCompass(coverage=CoverageMetrics())
        record_tool_event(compass, "Agent", {"description": "fix complexity"}, "")
        assert len(compass.action_history) == 1
        assert compass.action_history[0]["tool"] == "Agent"
        assert compass.action_history[0]["sig"] == "fix complexity"
        assert compass.action_history[0]["intent"] == "meta"

    def test_task_event_records_description(self):
        compass = BehaviorCompass(coverage=CoverageMetrics())
        record_tool_event(compass, "Task", {"description": "refactor module"}, "")
        assert len(compass.action_history) == 1
        assert compass.action_history[0]["tool"] == "Task"
        assert compass.action_history[0]["sig"] == "refactor module"

    def test_enter_plan_mode_records(self):
        compass = BehaviorCompass(coverage=CoverageMetrics())
        record_tool_event(compass, "EnterPlanMode", {}, "")
        assert len(compass.action_history) == 1
        assert compass.action_history[0]["tool"] == "EnterPlanMode"
        assert compass.action_history[0]["intent"] == "meta"


# ── Intent resolution ────────────────────────────────────────────────


class TestIntentResolution:
    def test_agent_resolves_to_meta(self):
        assert resolve_intent("Agent", "") == "meta"

    def test_enter_plan_mode_resolves_to_meta(self):
        assert resolve_intent("EnterPlanMode", "") == "meta"
