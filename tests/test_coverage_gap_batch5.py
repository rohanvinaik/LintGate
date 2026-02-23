"""Coverage gap tests — batch 5.

Covers the final uncovered symbols after batches 1-4:
- reporter.py::format_mesh_report (line 186)
- hook_habit.py::_update_habit_mode_path_a (lines 234, 235, 259)
- hook_posttooluse.py::_run_legacy_pipeline (line 120)
- behavior_tools.py::register (lines 438, 440, 447)
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

# ── Shared: path-a env from batch3 pattern ──────────────────────────────

_PATH_A_PATCHES = [
    "lintgate.habit_mode.update_signals",
    "lintgate.habit_mode.track_active_files",
    "lintgate.token_tracker.estimate_tool_tokens",
    "lintgate.hook_habit.check_habit_api_calibration",
    "lintgate.habit_mode.save_habit_state",
    "lintgate.token_tracker.save_tracker_state",
    "lintgate.hook_runtime_state.refresh_runtime_state_with_session",
    "lintgate.state.load_last_run",
    "lintgate.state.log_feature_usage",
    "lintgate.state.log_metric",
    "lintgate.habit_mode.save_habit_state_standalone",
]


@contextlib.contextmanager
def _path_a_env(habit_state, tracker, *, update_mode_rv=None, compaction=(False, None)):
    """Context manager that patches all Path A dependencies."""
    patches = [patch(p) for p in _PATH_A_PATCHES]
    patches += [
        patch("lintgate.habit_mode.load_habit_state", return_value=habit_state),
        patch("lintgate.token_tracker.load_tracker_state", return_value=tracker),
        patch("lintgate.habit_mode.detect_test_result"),
        patch("lintgate.habit_mode.update_mode", return_value=update_mode_rv),
        patch("lintgate.hook_habit.try_habit_compaction", return_value=compaction),
    ]
    with contextlib.ExitStack() as stack:
        mocks = {p.attribute: stack.enter_context(p) for p in patches}
        yield mocks


def _make_session_and_compass():
    """Build mock session + compass with behavior_compass dict."""
    session = MagicMock()
    session.behavior_compass = {}
    session.to_dict.return_value = {}
    compass = MagicMock()
    compass.event_counter = 10
    compass.action_history = []
    compass.to_dict.return_value = {}
    return session, compass


def _make_habit_state(*, active=False, score=0.5):
    hs = MagicMock()
    hs.active = active
    hs.habit_score = score
    hs.total_events_in_habit = 0
    return hs


def _make_cp_config(*, auto_detect=True):
    cfg = MagicMock()
    cfg.habit_mode_auto_detect = auto_detect
    cfg.habit_mode_enter_score = 0.5
    cfg.habit_mode_exit_score = 0.3
    cfg.habit_mode_sustain_calls = 10
    return cfg


# ── reporter.py::format_mesh_report — quota filtering (line 186) ────────


def test_format_mesh_report_quota_zero_skips() -> None:
    """Line 186: continue when allowed <= 0 for a fingerprint.

    Need TWO findings: one "new" (to make quota_by_fp non-empty) and one
    "still_active" (whose fp is NOT in quota_by_fp → allowed=0 → continue).
    """
    from lintgate.controlplane.reporter import format_mesh_report
    from lintgate.controlplane.reporter_delta import compute_finding_fingerprint
    from lintgate.controlplane.types import ChannelResult, ControlPlaneConfig, MeshResult
    from lintgate.types import LintIssue

    # Finding A: will be "new" (not in prev_index) — gives quota_by_fp an entry
    new_finding = LintIssue(
        file="new.py", line=1, kind="E999", message="new issue",
        severity="warning", linter="ruff",
    )
    # Finding B: will be "still_active" (in prev_index with same severity)
    # Its fp won't be in quota_by_fp → allowed=0 → continue at line 186
    old_finding = LintIssue(
        file="old.py", line=1, kind="E001", message="old issue",
        severity="warning", linter="ruff",
    )
    cr = ChannelResult(
        channel="lint", status="fail", severity="warning",
        findings=[old_finding, new_finding], metrics={}, duration_ms=1,
    )
    mesh = MeshResult(channel_results=[cr], duration_ms=1)

    old_fp = compute_finding_fingerprint(old_finding, "lint")
    prev_index = {old_fp: {"severity": "warning", "count": 1, "channel": "lint"}}

    report = format_mesh_report(mesh, ControlPlaneConfig(), previous_finding_index=prev_index)
    assert isinstance(report, dict)


# ── hook_habit.py — transition logging (lines 234, 235) ─────────────────


def test_path_a_transition_logs_metric() -> None:
    """Lines 234-235: log_metric called when transition is truthy."""
    from lintgate.hook_habit import _update_habit_mode_path_a

    session, compass = _make_session_and_compass()
    hs = _make_habit_state()
    tracker = MagicMock()

    with _path_a_env(hs, tracker, update_mode_rv="entered") as mocks:
        _update_habit_mode_path_a(
            _make_cp_config(), session, compass,
            "/tmp/test", "Bash", "pytest", "ok",
        )
        mocks["log_metric"].assert_called_once()
        call_data = mocks["log_metric"].call_args[0][0]
        assert call_data["event"] == "habit_mode_transition"
        assert call_data["transition"] == "entered"


# ── hook_habit.py — compaction snapshot (line 259) ───────────────────────


def test_path_a_compaction_snapshot_saved() -> None:
    """Line 259: when did_compact and snapshot, saves to session."""
    from lintgate.hook_habit import _update_habit_mode_path_a

    session, compass = _make_session_and_compass()
    hs = _make_habit_state()
    tracker = MagicMock()
    fake_snapshot = {"compacted_at": 10}

    with _path_a_env(hs, tracker, compaction=(True, fake_snapshot)):
        _update_habit_mode_path_a(
            _make_cp_config(auto_detect=False), session, compass,
            "/tmp/test", "Read", "f.py", "ok",
        )
        assert session.behavior_compass["habit_last_snapshot"] == fake_snapshot


# ── hook_posttooluse.py — tier skip (line 120) ──────────────────────────


def test_legacy_pipeline_tier_skip_exits_clean() -> None:
    """Line 120: when tier.skip is True, calls _exit_clean."""
    from lintgate.hook_posttooluse import _run_legacy_pipeline

    mock_config = MagicMock()
    mock_config.total_timeout_ms = 5000

    skip_tier = MagicMock()
    skip_tier.skip = True

    classification = MagicMock()
    classification.risk_level = "moderate"
    classification.change_kind = "logic"

    with (
        patch("lintgate.hook_posttooluse.classify_change", return_value=classification),
        patch("lintgate.hook_posttooluse.select_tier", return_value=skip_tier),
        patch("lintgate.hook_posttooluse._exit_clean", side_effect=SystemExit(0)) as mock_exit,
    ):
        with pytest.raises(SystemExit):
            _run_legacy_pipeline(
                tool_name="Edit", tool_input={}, tool_output="ok",
                cwd="/tmp/test", config=mock_config, start=0.0,
            )
        mock_exit.assert_called_once()


# ── behavior_tools.py — accuracy + outcomes (lines 438, 440, 447) ───────


def test_prediction_register_accuracy_present() -> None:
    """Line 438: when pred_accuracy is not None, accuracy field is set."""
    from lintgate.controlplane.behavior_compass import BehaviorCompass

    compass = BehaviorCompass()
    compass.event_counter = 10
    for i in range(6):
        compass.prediction_log.append({"prediction_id": f"p{i}", "status": "confirmed"})

    session = MagicMock()

    cp_cfg = MagicMock()
    cp_cfg.session_max_age_hours = 4.0

    with (
        patch("lintgate.config.load_controlplane_config", return_value=cp_cfg),
        patch("lintgate.controlplane.session_memory.get_or_create_session", return_value=session),
        patch("lintgate.controlplane.session_memory.load_behavior_compass", return_value=compass),
        patch("lintgate.controlplane.session_memory.save_behavior_compass"),
        patch("lintgate.controlplane.session_memory.save_session"),
        patch(
            "lintgate.controlplane.behavior_compass.normalize_command_sig",
            return_value="pytest:run",
        ),
        patch(
            "lintgate.controlplane.behavior_compass.find_relevant_hypotheses",
            return_value=[],
        ),
        patch(
            "lintgate.controlplane.behavior_compass.compute_prediction_accuracy",
            return_value=0.83,
        ),
        patch("lintgate.state.log_feature_usage"),
    ):
        import json

        from mcp_tools.behavior_tools import register

        mcp = MagicMock()
        tools = {}

        def capture_tool(**kwargs):
            def decorator(func):
                tools[kwargs.get("name", func.__name__)] = func
                return func
            return decorator

        mcp.tool = capture_tool
        helpers = {"_validate_project_root": lambda p: p, "_json_dumps": json.dumps}
        register(mcp, helpers)

        result_str = tools["prediction_register"](
            path="/tmp/test",
            planned_action="bash: pytest tests",
            prediction="tests pass",
            prediction_type="exit_code",
            prediction_value="0",
        )
        result = json.loads(result_str)
        assert result["prediction_tracking"]["accuracy"] == 0.83


def test_prediction_register_accuracy_note_and_outcomes() -> None:
    """Lines 440, 447: accuracy_note when < 5 checked; recent_outcomes present."""
    from lintgate.controlplane.behavior_compass import BehaviorCompass

    compass = BehaviorCompass()
    compass.event_counter = 10
    compass.prediction_log = [
        {"prediction_id": "p1", "status": "confirmed"},
        {"prediction_id": "p2", "status": "falsified"},
    ]

    session = MagicMock()
    cp_cfg = MagicMock()
    cp_cfg.session_max_age_hours = 4.0

    with (
        patch("lintgate.config.load_controlplane_config", return_value=cp_cfg),
        patch("lintgate.controlplane.session_memory.get_or_create_session", return_value=session),
        patch("lintgate.controlplane.session_memory.load_behavior_compass", return_value=compass),
        patch("lintgate.controlplane.session_memory.save_behavior_compass"),
        patch("lintgate.controlplane.session_memory.save_session"),
        patch(
            "lintgate.controlplane.behavior_compass.normalize_command_sig",
            return_value="pytest:run",
        ),
        patch(
            "lintgate.controlplane.behavior_compass.find_relevant_hypotheses",
            return_value=[],
        ),
        patch(
            "lintgate.controlplane.behavior_compass.compute_prediction_accuracy",
            return_value=None,
        ),
        patch("lintgate.state.log_feature_usage"),
    ):
        import json

        from mcp_tools.behavior_tools import register

        mcp = MagicMock()
        tools = {}

        def capture_tool(**kwargs):
            def decorator(func):
                tools[kwargs.get("name", func.__name__)] = func
                return func
            return decorator

        mcp.tool = capture_tool
        helpers = {"_validate_project_root": lambda p: p, "_json_dumps": json.dumps}
        register(mcp, helpers)

        result_str = tools["prediction_register"](
            path="/tmp/test",
            planned_action="bash: pytest tests",
            prediction="tests pass",
            prediction_type="exit_code",
            prediction_value="0",
        )
        result = json.loads(result_str)
        acc = result["prediction_tracking"]
        assert "accuracy_note" in acc
        assert "Need" in acc["accuracy_note"]
        assert "recent_outcomes" in acc
        assert len(acc["recent_outcomes"]) == 2
