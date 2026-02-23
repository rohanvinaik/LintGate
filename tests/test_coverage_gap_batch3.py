"""Coverage gap tests for hook_habit.py and hook_posttooluse.py — batch 3."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# ── Shared helper: patch stack for _update_habit_mode_path_a ─────────

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


# ── hook_habit.py: record_behavior_event line 128 ────────────────────

class TestRecordBehaviorEventHabitEnabled:
    def test_habit_enabled_calls_path_a(self):
        from lintgate.hook_habit import record_behavior_event

        cfg = MagicMock()
        cfg.channel_enabled.return_value = True
        cfg.session_memory = True
        cfg.habit_mode_enabled = True
        cfg.session_max_age_hours = 24
        session = MagicMock()
        session.behavior_compass = {}

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            patch(
                "lintgate.controlplane.session_memory.load_behavior_compass",
                return_value=MagicMock(),
            ),
            patch("lintgate.controlplane.session_memory.save_behavior_compass"),
            patch("lintgate.controlplane.session_memory.save_session"),
            patch("lintgate.controlplane.behavior_compass.record_tool_event"),
            patch("lintgate.hook_habit._update_habit_mode_path_a") as mock_pa,
        ):
            record_behavior_event(cfg, "/tmp", "Edit", {"file_path": "/x.py"}, "ok")
            mock_pa.assert_called_once()


# ── hook_habit.py: _update_habit_mode_path_a uncovered branches ──────

class TestUpdateHabitModePathABranches:
    def _cfg(self, *, auto_detect=True):
        c = MagicMock()
        c.habit_mode_auto_detect = auto_detect
        c.habit_mode_enter_score = 0.7
        c.habit_mode_exit_score = 0.3
        c.habit_mode_sustain_calls = 10
        c.habit_mode_compact_threshold = 0.6
        c.habit_mode_token_api_interval = 50
        return c

    def _session(self, overrides=None):
        s = MagicMock()
        s.behavior_compass = overrides if overrides is not None else {}
        s.to_dict.return_value = {}
        return s

    def _compass(self):
        c = MagicMock()
        c.action_history = [{"sig": "pytest tests/", "tool": "Bash"}]
        c.event_counter = 5
        c.to_dict.return_value = {}
        return c

    def test_bash_tool_with_action_history(self):
        """Lines 201-205: Bash tool with action_history."""
        from lintgate.hook_habit import _update_habit_mode_path_a

        hs = MagicMock(active=False, total_events_in_habit=0)
        tr = MagicMock()
        with _path_a_env(hs, tr) as mocks:
            _update_habit_mode_path_a(
                self._cfg(), self._session(), self._compass(),
                "/tmp", "Bash", {"command": "pytest"}, "1 passed",
            )
            mocks["detect_test_result"].assert_called_once()
            assert mocks["detect_test_result"].call_args[0][2] == "pytest tests/"

    def test_non_dict_overrides_reset(self):
        """Line 210: non-dict overrides replaced with {}."""
        from lintgate.hook_habit import _update_habit_mode_path_a

        hs = MagicMock(active=False, total_events_in_habit=0)
        tr = MagicMock()
        with _path_a_env(hs, tr) as mocks:
            _update_habit_mode_path_a(
                self._cfg(), self._session({"habit_config_overrides": "bad"}),
                self._compass(), "/tmp", "Read", {}, "",
            )
            mocks["update_mode"].assert_called_once()

    def test_context_window_size_override(self):
        """Lines 214-215: context_window_size set on tracker."""
        from lintgate.hook_habit import _update_habit_mode_path_a

        hs = MagicMock(active=False, total_events_in_habit=0)
        tr = MagicMock()
        with _path_a_env(hs, tr):
            _update_habit_mode_path_a(
                self._cfg(),
                self._session({"habit_config_overrides": {"context_window_size": "100000"}}),
                self._compass(), "/tmp", "Read", {}, "",
            )
            assert tr.context_window_size == 100000

    def test_auto_detect_disabled_active_increments(self):
        """Lines 227, 229: auto_detect off + active -> total_events_in_habit += 1."""
        from lintgate.hook_habit import _update_habit_mode_path_a

        hs = MagicMock(active=True, total_events_in_habit=5)
        tr = MagicMock()
        with _path_a_env(hs, tr):
            _update_habit_mode_path_a(
                self._cfg(auto_detect=False), self._session(), self._compass(),
                "/tmp", "Read", {}, "",
            )
            assert hs.total_events_in_habit == 6


# ── hook_habit.py: _apply_path_b_telemetry line 423 ─────────────────

class TestApplyPathBTelemetryFallback:
    def test_returns_signal_fires_on_load_error(self):
        from lintgate.hook_habit import _apply_path_b_telemetry

        fires = {"cmd_fail": 2}
        with patch("lintgate.controlplane.model_profiles.load_profiles", side_effect=RuntimeError):
            result = _apply_path_b_telemetry(100, fires)
        assert result == {"cmd_fail": 2}


# ── hook_posttooluse.py: _parse_hook_input lines 68-69 ──────────────

class TestParseHookInputErrors:
    def test_invalid_json_returns_none(self, monkeypatch):
        from lintgate.hook_posttooluse import _parse_hook_input

        monkeypatch.setattr(sys, "stdin", io.StringIO("not json{{{"))
        assert _parse_hook_input() is None

    def test_eof_returns_none(self, monkeypatch):
        from lintgate.hook_posttooluse import _parse_hook_input

        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert _parse_hook_input() is None


# ── hook_posttooluse.py: _normalize_fields lines 77, 89, 93 ─────────

class TestNormalizeFieldsEdgeCases:
    def test_non_str_tool_name(self):
        from lintgate.hook_posttooluse import _normalize_fields

        name, _, _, _ = _normalize_fields(
            {"tool_name": 123, "tool_input": {}, "tool_output": "ok", "cwd": "/tmp"}
        )
        assert name == ""

    def test_non_str_tool_output(self):
        from lintgate.hook_posttooluse import _normalize_fields

        _, _, out, _ = _normalize_fields(
            {"tool_name": "Bash", "tool_input": {}, "tool_output": 42, "cwd": "/tmp"}
        )
        assert out == "42"

    def test_non_str_cwd_uses_getcwd(self, monkeypatch):
        from lintgate.hook_posttooluse import _normalize_fields

        monkeypatch.setattr("os.getcwd", lambda: "/fallback")
        _, _, _, cwd = _normalize_fields(
            {"tool_name": "Bash", "tool_input": {}, "tool_output": "", "cwd": 999}
        )
        assert cwd == "/fallback"

    def test_empty_cwd_uses_getcwd(self, monkeypatch):
        from lintgate.hook_posttooluse import _normalize_fields

        monkeypatch.setattr("os.getcwd", lambda: "/fallback2")
        _, _, _, cwd = _normalize_fields(
            {"tool_name": "Bash", "tool_input": {}, "tool_output": "", "cwd": ""}
        )
        assert cwd == "/fallback2"


# ── hook_posttooluse.py: _run_legacy_pipeline lines 111-123 ─────────

class TestRunLegacyPipeline:
    def _legacy_patches(self, classification, tier, aggregated, *, dep_warnings=None):
        """Return a list of patch context managers for _run_legacy_pipeline."""
        ps = [
            patch("lintgate.hook_posttooluse.classify_change", return_value=classification),
            patch("lintgate.hook_posttooluse.select_tier", return_value=tier),
            patch("lintgate.hook_posttooluse.build_registry", return_value=MagicMock()),
            patch("lintgate.hook_posttooluse.run_linters", return_value=[]),
            patch("lintgate.hook_posttooluse.aggregate_results", return_value=aggregated),
            patch("lintgate.hook_posttooluse.format_report", return_value={}),
            patch("lintgate.hook_posttooluse.load_last_run", return_value=None),
            patch("lintgate.hook_posttooluse.update_issue_memory"),
            patch("lintgate.hook_posttooluse.save_run"),
            patch("lintgate.hook_posttooluse.log_metric"),
        ]
        if dep_warnings is not None:
            ps.append(
                patch(
                    "lintgate.dependency_health.quick_dependency_check",
                    return_value=dep_warnings,
                )
            )
        return ps

    def test_dependency_change_runs_dep_check(self, monkeypatch, tmp_path):
        from lintgate.hook_posttooluse import _run_legacy_pipeline
        from lintgate.types import AggregatedResult, ChangeClassification, LintTier

        cls = ChangeClassification(
            change_kind="dependency", risk_level="low",
            files_changed=["req.txt"],
        )
        tier = LintTier(name="t1", linters=["ruff"], files=["req.txt"], reason="dep", skip=False)
        agg = AggregatedResult(metrics={"linters_run": 1})
        stdout_buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", stdout_buf)
        config = MagicMock(total_timeout_ms=8000)

        with contextlib.ExitStack() as stack:
            for p in self._legacy_patches(cls, tier, agg, dep_warnings=["Warning: unpinned"]):
                stack.enter_context(p)
            with pytest.raises(SystemExit) as exc:
                _run_legacy_pipeline("Edit", {}, "ok", str(tmp_path), config, time.perf_counter())
        assert exc.value.code == 0
        out = json.loads(stdout_buf.getvalue().strip())
        assert "Dependency Health" in out.get("systemMessage", "")

    def test_non_skip_tier_runs_registry_and_linters(self, monkeypatch, tmp_path):
        from lintgate.hook_posttooluse import _run_legacy_pipeline
        from lintgate.types import AggregatedResult, ChangeClassification, LintTier

        cls = ChangeClassification(
            change_kind="logic", risk_level="moderate",
            files_changed=["f.py"],
        )
        tier = LintTier(name="t2", linters=["ruff"], files=["f.py"], reason="logic", skip=False)
        agg = AggregatedResult(metrics={"linters_run": 1})
        stdout_buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", stdout_buf)
        config = MagicMock(total_timeout_ms=8000)

        with contextlib.ExitStack() as stack:
            mocks = {}
            for p in self._legacy_patches(cls, tier, agg):
                m = stack.enter_context(p)
                if hasattr(p, "attribute"):
                    mocks[p.attribute] = m
            with pytest.raises(SystemExit):
                _run_legacy_pipeline("Edit", {}, "ok", str(tmp_path), config, time.perf_counter())
        mocks["build_registry"].assert_called_once_with(config)
        mocks["run_linters"].assert_called_once()


# ── hook_posttooluse.py: main() lines 191, 195-196, 204-207, 211 ────

class TestMainBranches:
    def _run_main(self, payload, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        with pytest.raises(SystemExit) as exc:
            from lintgate.hook_posttooluse import main
            main()
        return int(exc.value.code), sys.stdout.getvalue().strip()

    def test_non_mutation_tool_exits_clean(self, monkeypatch, tmp_path):
        """Line 191."""
        code, output = self._run_main(
            {"tool_name": "Read", "tool_input": {}, "tool_output": "", "cwd": str(tmp_path)},
            monkeypatch,
        )
        assert code == 0 and output == "{}"

    def test_config_load_failure_uses_fallback(self, monkeypatch, tmp_path):
        """Lines 195-196."""
        code, _ = self._run_main(
            {"tool_name": "Write", "tool_input": {"file_path": "x.py", "content": "x=1"},
             "tool_output": "ok", "cwd": str(tmp_path)},
            monkeypatch,
        )
        assert code == 0

    def test_controlplane_enabled_dispatches(self, monkeypatch, tmp_path):
        """Lines 204-205."""
        mock_cp = MagicMock(enabled=True)
        with (
            patch("lintgate.hook_posttooluse.load_config", return_value=MagicMock()),
            patch("lintgate.config.load_controlplane_config", return_value=mock_cp),
            patch("lintgate.hook_posttooluse._run_controlplane") as mock_rc,
        ):
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
                {"tool_name": "Edit", "tool_input": {}, "tool_output": "ok", "cwd": str(tmp_path)}
            )))
            monkeypatch.setattr(sys, "stdout", io.StringIO())
            from lintgate.hook_posttooluse import main
            main()
            mock_rc.assert_called_once()

    def test_controlplane_load_error_falls_to_legacy(self, monkeypatch, tmp_path):
        """Lines 206-207."""
        with (
            patch("lintgate.hook_posttooluse.load_config", return_value=MagicMock()),
            patch("lintgate.config.load_controlplane_config", side_effect=RuntimeError),
            patch("lintgate.hook_posttooluse._run_legacy_pipeline") as mock_leg,
        ):
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
                {"tool_name": "Edit", "tool_input": {}, "tool_output": "ok", "cwd": str(tmp_path)}
            )))
            monkeypatch.setattr(sys, "stdout", io.StringIO())
            from lintgate.hook_posttooluse import main
            main()
            mock_leg.assert_called_once()

    def test_outer_exception_exits_clean(self, monkeypatch, tmp_path):
        """Line 211."""
        with (
            patch("lintgate.hook_posttooluse.load_config", side_effect=RuntimeError),
            patch("lintgate.hook_posttooluse._fallback_config", side_effect=RuntimeError),
        ):
            code, output = self._run_main(
                {"tool_name": "Edit", "tool_input": {}, "tool_output": "ok", "cwd": str(tmp_path)},
                monkeypatch,
            )
        assert code == 0 and output == "{}"
