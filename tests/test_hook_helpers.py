"""Tests for hook helper functions — targeting uncovered branches/lines."""

from __future__ import annotations

import contextlib
import time
from unittest.mock import MagicMock, patch

from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    ControlPlaneConfig,
    MeshResult,
    QualityGateConfig,
    SupervisionEvent,
)
from lintgate.hooks.pre_tool import _check_quality_gate
from lintgate.runtime_state import RuntimeState

# ── Helpers ──────────────────────────────────────────────────────────


def _make_config(enabled: bool = True, **overrides):
    """Build a ControlPlaneConfig with quality gate enabled."""
    qg_kwargs = {
        "enabled": enabled,
        "staleness_threshold_s": 1800.0,
        "block_push": True,
        "advise_commit": True,
        "check_secrets": True,
    }
    qg_kwargs.update(overrides)
    return ControlPlaneConfig(enabled=True, quality_gate=QualityGateConfig(**qg_kwargs))


def _fresh_state(**overrides) -> RuntimeState:
    """Build a RuntimeState that passes all quality checks."""
    defaults = {
        "timestamp": time.time(),
        "blocking_issues": 0,
        "last_test_status": "",
    }
    defaults.update(overrides)
    return RuntimeState(**defaults)


# ── _run_controlplane (hook_posttooluse.py lines 249-260) ────────────


class TestRunControlplane:
    """Test the _run_controlplane function happy path with all deps mocked.

    _run_controlplane uses local imports inside the function body.
    We must patch at the source module locations, not at hook_posttooluse.
    """

    def test_happy_path_runs_mesh_and_prints(self, tmp_path) -> None:
        """Exercise lines 249-260: imports, classification, mesh run, output."""
        from lintgate.hook_posttooluse import _run_controlplane

        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "foo.py"},
            "tool_output": "ok",
        }
        config = MagicMock()
        cp_config = MagicMock()
        cp_config.channel_enabled.return_value = False
        cwd = str(tmp_path)

        mock_classification = MagicMock()
        mock_classification.risk_level = "medium"
        mock_classification.files_changed = ["foo.py"]
        mock_classification.change_kind = "code"

        mock_mesh = MeshResult(
            event=SupervisionEvent(project_root=cwd),
            channel_results=[
                ChannelResult(channel="lint", status="pass"),
            ],
            coherence=CoherenceResult(state="stable", summary="all clear"),
            duration_ms=42.0,
        )

        mock_session = MagicMock()
        mock_session.behavior_compass = {}

        with (
            patch(
                "lintgate.hooks.posttooluse.classify_change",
                return_value=mock_classification,
            ),
            patch(
                "lintgate.controlplane.runtime.run_mesh",
                return_value=mock_mesh,
            ) as patched_run_mesh,
            patch(
                "lintgate.controlplane.reporter.format_mesh_report",
                return_value={"systemMessage": "all good"},
            ),
            patch(
                "lintgate.hooks.habit.record_behavior_event",
            ),
            patch(
                "lintgate.hooks.habit.record_habit_event_lightweight",
            ),
            patch(
                "lintgate.hooks.controlplane.load_global_priors",
                return_value={},
            ),
            patch(
                "lintgate.hooks.controlplane.setup_session_and_gate",
                return_value=(mock_session, None),
            ),
            patch(
                "lintgate.hooks.controlplane.extract_finding_indexes",
                return_value=({}, {}, 0, None, None),
            ),
            patch(
                "lintgate.hooks.controlplane.post_process_session",
                return_value=[],
            ),
            patch(
                "lintgate.hooks.controlplane.save_run_details",
            ),
            patch(
                "lintgate.hooks.controlplane.accumulate_session_telemetry",
            ),
            patch(
                "lintgate.hooks.controlplane.refresh_runtime_after_run",
            ),
            patch(
                "lintgate.hooks.arbitration.arbitrate_output",
                return_value={"systemMessage": "all good"},
            ),
            patch(
                "lintgate.hooks.posttooluse.log_metric",
            ),
            patch("builtins.print") as mock_print,
            patch("sys.exit") as mock_exit,
        ):
            _run_controlplane(input_data, config, cp_config, cwd, time.perf_counter())

            # Verify mesh was actually called
            patched_run_mesh.assert_called_once()

            # Verify output was printed
            mock_print.assert_called_once()
            mock_exit.assert_called_once_with(0)

    def test_structure_and_behavior_channels_enabled(self, tmp_path) -> None:
        """When cp_config.channel_enabled returns True, both structure and behavior
        channels are appended (lines 294-299)."""
        from lintgate.hook_posttooluse import _run_controlplane

        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "foo.py"},
            "tool_output": "ok",
        }
        config = MagicMock()
        cp_config = MagicMock()
        cp_config.channel_enabled.return_value = True  # Both structure + behavior enabled
        cwd = str(tmp_path)

        mock_classification = MagicMock()
        mock_classification.risk_level = "medium"
        mock_classification.files_changed = ["foo.py"]
        mock_classification.change_kind = "code"

        mock_mesh = MeshResult(
            event=SupervisionEvent(project_root=cwd),
            channel_results=[
                ChannelResult(channel="lint", status="pass"),
            ],
            coherence=CoherenceResult(state="stable", summary="all clear"),
            duration_ms=42.0,
        )

        mock_session = MagicMock()
        mock_session.behavior_compass = {}

        with (
            patch(
                "lintgate.hooks.posttooluse.classify_change",
                return_value=mock_classification,
            ),
            patch(
                "lintgate.controlplane.runtime.run_mesh",
                return_value=mock_mesh,
            ) as patched_run_mesh,
            patch(
                "lintgate.controlplane.reporter.format_mesh_report",
                return_value={"systemMessage": "all good"},
            ),
            patch(
                "lintgate.hooks.habit.record_behavior_event",
            ),
            patch(
                "lintgate.hooks.habit.record_habit_event_lightweight",
            ),
            patch(
                "lintgate.hooks.controlplane.load_global_priors",
                return_value={},
            ),
            patch(
                "lintgate.hooks.controlplane.setup_session_and_gate",
                return_value=(mock_session, None),
            ),
            patch(
                "lintgate.hooks.controlplane.extract_finding_indexes",
                return_value=({}, {}, 0, None, None),
            ),
            patch(
                "lintgate.hooks.controlplane.post_process_session",
                return_value=[],
            ),
            patch(
                "lintgate.hooks.controlplane.save_run_details",
            ),
            patch(
                "lintgate.hooks.controlplane.accumulate_session_telemetry",
            ),
            patch(
                "lintgate.hooks.controlplane.refresh_runtime_after_run",
            ),
            patch(
                "lintgate.hooks.arbitration.arbitrate_output",
                return_value={"systemMessage": "all good"},
            ),
            patch(
                "lintgate.hooks.posttooluse.log_metric",
            ),
            patch(
                "lintgate.channels.structure_channel.StructureChannel",
            ) as mock_struct,
            patch(
                "lintgate.channels.behavior_channel.BehaviorChannel",
            ) as mock_behav,
            patch("builtins.print"),
            patch("sys.exit"),
        ):
            _run_controlplane(input_data, config, cp_config, cwd, time.perf_counter())

            # Verify both channel constructors were called
            mock_struct.assert_called_once()
            mock_behav.assert_called_once()
            # run_mesh should have received 7 channels (5 default + structure + behavior)
            call_args = patched_run_mesh.call_args
            channels_arg = call_args[0][2]  # third positional arg
            assert len(channels_arg) == 7

    def test_telemetry_stripped_and_logged(self, tmp_path) -> None:
        """When format_mesh_report returns a report with _telemetry key,
        it is stripped from the report and passed to log_metric (lines 350-351, 367-368)."""
        from lintgate.hook_posttooluse import _run_controlplane

        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "foo.py"},
            "tool_output": "ok",
        }
        config = MagicMock()
        cp_config = MagicMock()
        cp_config.channel_enabled.return_value = False
        cwd = str(tmp_path)

        mock_classification = MagicMock()
        mock_classification.risk_level = "medium"
        mock_classification.files_changed = ["foo.py"]
        mock_classification.change_kind = "code"

        mock_mesh = MeshResult(
            event=SupervisionEvent(project_root=cwd),
            channel_results=[
                ChannelResult(channel="lint", status="pass"),
            ],
            coherence=CoherenceResult(state="stable", summary="all clear"),
            duration_ms=42.0,
        )

        mock_session = MagicMock()
        mock_session.behavior_compass = {}

        telemetry_data = {"prediction_accuracy": 0.8, "events_tracked": 5}

        with (
            patch(
                "lintgate.hooks.posttooluse.classify_change",
                return_value=mock_classification,
            ),
            patch(
                "lintgate.controlplane.runtime.run_mesh",
                return_value=mock_mesh,
            ),
            patch(
                "lintgate.controlplane.reporter.format_mesh_report",
                return_value={
                    "systemMessage": "all good",
                    "_telemetry": telemetry_data,
                },
            ),
            patch(
                "lintgate.hooks.habit.record_behavior_event",
            ),
            patch(
                "lintgate.hooks.habit.record_habit_event_lightweight",
            ),
            patch(
                "lintgate.hooks.controlplane.load_global_priors",
                return_value={},
            ),
            patch(
                "lintgate.hooks.controlplane.setup_session_and_gate",
                return_value=(mock_session, None),
            ),
            patch(
                "lintgate.hooks.controlplane.extract_finding_indexes",
                return_value=({}, {}, 0, None, None),
            ),
            patch(
                "lintgate.hooks.controlplane.post_process_session",
                return_value=[],
            ),
            patch(
                "lintgate.hooks.controlplane.save_run_details",
            ),
            patch(
                "lintgate.hooks.controlplane.accumulate_session_telemetry",
            ),
            patch(
                "lintgate.hooks.controlplane.refresh_runtime_after_run",
            ),
            patch(
                "lintgate.hooks.arbitration.arbitrate_output",
                return_value={"systemMessage": "all good"},
            ),
            patch(
                "lintgate.hooks.posttooluse.log_metric",
            ) as mock_log_metric,
            patch("builtins.print") as mock_print,
            patch("sys.exit"),
        ):
            _run_controlplane(input_data, config, cp_config, cwd, time.perf_counter())

            # Verify log_metric was called with telemetry data
            mock_log_metric.assert_called_once()
            metric_arg = mock_log_metric.call_args[0][0]
            assert "telemetry" in metric_arg
            assert metric_arg["telemetry"] == telemetry_data

            # Verify _telemetry was stripped from printed output
            import json as json_mod

            printed_str = mock_print.call_args[0][0]
            printed_data = json_mod.loads(printed_str)
            assert "_telemetry" not in printed_data

    def test_session_data_non_dict_coerced(self, tmp_path) -> None:
        """When session.behavior_compass is not a dict, it should be coerced
        to empty dict before calling arbitrate_output (lines 377-378)."""
        from lintgate.hook_posttooluse import _run_controlplane

        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "foo.py"},
            "tool_output": "ok",
        }
        config = MagicMock()
        cp_config = MagicMock()
        cp_config.channel_enabled.return_value = False
        cwd = str(tmp_path)

        mock_classification = MagicMock()
        mock_classification.risk_level = "medium"
        mock_classification.files_changed = ["foo.py"]
        mock_classification.change_kind = "code"

        mock_mesh = MeshResult(
            event=SupervisionEvent(project_root=cwd),
            channel_results=[
                ChannelResult(channel="lint", status="pass"),
            ],
            coherence=CoherenceResult(state="stable", summary="all clear"),
            duration_ms=42.0,
        )

        mock_session = MagicMock()
        mock_session.behavior_compass = "not-a-dict"  # Non-dict value

        with (
            patch(
                "lintgate.hooks.posttooluse.classify_change",
                return_value=mock_classification,
            ),
            patch(
                "lintgate.controlplane.runtime.run_mesh",
                return_value=mock_mesh,
            ),
            patch(
                "lintgate.controlplane.reporter.format_mesh_report",
                return_value={"systemMessage": "all good"},
            ),
            patch(
                "lintgate.hooks.habit.record_behavior_event",
            ),
            patch(
                "lintgate.hooks.habit.record_habit_event_lightweight",
            ),
            patch(
                "lintgate.hooks.controlplane.load_global_priors",
                return_value={},
            ),
            patch(
                "lintgate.hooks.controlplane.setup_session_and_gate",
                return_value=(mock_session, None),
            ),
            patch(
                "lintgate.hooks.controlplane.extract_finding_indexes",
                return_value=({}, {}, 0, None, None),
            ),
            patch(
                "lintgate.hooks.controlplane.post_process_session",
                return_value=[],
            ),
            patch(
                "lintgate.hooks.controlplane.save_run_details",
            ),
            patch(
                "lintgate.hooks.controlplane.accumulate_session_telemetry",
            ),
            patch(
                "lintgate.hooks.controlplane.refresh_runtime_after_run",
            ),
            patch(
                "lintgate.hooks.arbitration.arbitrate_output",
                return_value={"systemMessage": "all good"},
            ) as mock_arbitrate,
            patch(
                "lintgate.hooks.posttooluse.log_metric",
            ),
            patch("builtins.print"),
            patch("sys.exit"),
        ):
            _run_controlplane(input_data, config, cp_config, cwd, time.perf_counter())

            # Verify arbitrate_output was called with empty dict (coerced from non-dict)
            mock_arbitrate.assert_called_once()
            session_data_arg = mock_arbitrate.call_args[0][2]
            assert session_data_arg == {}

    def test_risk_none_exits_clean(self, tmp_path) -> None:
        """When classification.risk_level is 'none', _exit_clean is called."""
        from lintgate.hook_posttooluse import _run_controlplane

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_output": "file.txt",
        }
        config = MagicMock()
        cp_config = MagicMock()
        cp_config.channel_enabled.return_value = False
        cwd = str(tmp_path)

        mock_classification = MagicMock()
        mock_classification.risk_level = "none"

        with (
            patch(
                "lintgate.hooks.posttooluse.classify_change",
                return_value=mock_classification,
            ),
            patch("lintgate.hooks.habit.record_behavior_event"),
            patch("lintgate.hooks.habit.record_habit_event_lightweight"),
            patch("lintgate.hooks.controlplane.load_global_priors", return_value={}),
            patch("builtins.print"),
            patch("sys.exit", side_effect=SystemExit(0)),
            contextlib.suppress(SystemExit),
        ):
            _run_controlplane(input_data, config, cp_config, cwd, time.perf_counter())


# ── _fallback_config (hook_posttooluse.py lines 385-389) ─────────────


class TestFallbackConfig:
    def test_returns_project_config_with_cwd(self, tmp_path) -> None:
        from lintgate.hook_posttooluse import _fallback_config

        result = _fallback_config(str(tmp_path))
        assert result.project_root == str(tmp_path)

    def test_returns_project_config_type(self) -> None:
        from lintgate.hook_posttooluse import _fallback_config
        from lintgate.types import ProjectConfig

        result = _fallback_config("/some/path")
        assert isinstance(result, ProjectConfig)


# ── _check_quality_gate branch 197->207 (pre_tool.py) ───────────────


class TestQualityGateNoFailures:
    """Branch 197->207: all checks pass (no failures list), returns empty result.

    The branch at line 207 is: ``if not failures: return QualityGateResult()``
    after all checks have run but none added to failures.
    This is the 'all clear after full evaluation' path — state exists,
    is fresh, no blocking issues, tests passing, no secrets.
    """

    def test_commit_all_clear_returns_empty(self, tmp_path) -> None:
        """Commit with clean state: staleness OK, no blockers, tests pass."""
        state = _fresh_state()
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch(
                "lintgate.hooks.pre_tool.load_runtime_state",
                return_value=state,
            ),
            patch(
                "lintgate.hooks.pre_tool._check_diff_secrets",
                return_value=[],
            ),
        ):
            result = _check_quality_gate("git commit -m 'clean'", str(tmp_path))

        assert result.should_block is False
        assert result.messages == []

    def test_push_all_clear_returns_empty(self, tmp_path) -> None:
        """Push with clean state exercises the same branch for push action."""
        state = _fresh_state()
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch(
                "lintgate.hooks.pre_tool.load_runtime_state",
                return_value=state,
            ),
            patch(
                "lintgate.hooks.pre_tool._check_diff_secrets",
                return_value=[],
            ),
        ):
            result = _check_quality_gate("git push", str(tmp_path))

        assert result.should_block is False
        assert result.messages == []

    def test_commit_with_pass_test_status(self, tmp_path) -> None:
        """Explicit 'pass' test status should not produce failures."""
        state = _fresh_state(last_test_status="pass")
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(),
            ),
            patch(
                "lintgate.hooks.pre_tool.load_runtime_state",
                return_value=state,
            ),
            patch(
                "lintgate.hooks.pre_tool._check_diff_secrets",
                return_value=[],
            ),
        ):
            result = _check_quality_gate("git commit -m 'ok'", str(tmp_path))

        assert result.should_block is False
        assert result.messages == []

    def test_push_secrets_disabled_all_clear(self, tmp_path) -> None:
        """With check_secrets=False, skip secrets scan — still all clear."""
        state = _fresh_state()
        with (
            patch(
                "lintgate.hooks.pre_tool.load_controlplane_config",
                return_value=_make_config(check_secrets=False),
            ),
            patch(
                "lintgate.hooks.pre_tool.load_runtime_state",
                return_value=state,
            ),
        ):
            result = _check_quality_gate("git push", str(tmp_path))

        assert result.should_block is False
        assert result.messages == []
