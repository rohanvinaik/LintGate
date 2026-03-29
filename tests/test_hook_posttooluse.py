"""Targeted tests for hook_posttooluse model-key helpers."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass, field

import pytest

from lintgate.controlplane.model.profiles import ModelProfile, ModelProfileStore
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
    SupervisionEvent,
)
from lintgate.hook_posttooluse import (
    _can_apply_session_telemetry,
    _fallback_config,
    _mark_session_telemetry_applied,
    _record_habit_event_lightweight,
    _refresh_runtime_state_lightweight,
    _resolve_event_model_key,
    _run_controlplane,
    _select_telemetry_profile,
    _session_telemetry_updates_used,
    main,
)
from lintgate.renderers.dynamic import read_generation_from_file
from lintgate.runtime_state import load_runtime_state
from lintgate.types import ChangeClassification, ProjectConfig


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r



def test_resolve_event_model_key_from_top_level() -> None:
    assert _resolve_event_model_key({"model": "claude-opus-4"}) == "anthropic:claude-opus-4"


def test_resolve_event_model_key_from_metadata() -> None:
    payload = {"metadata": {"model_id": "gpt-4o"}}
    assert _resolve_event_model_key(payload) == "openai:gpt-4o"


def test_select_telemetry_profile_requires_explicit_model_key() -> None:
    store = ModelProfileStore(
        profiles={
            "anthropic:claude-opus-4": ModelProfile(
                model_key="anthropic:claude-opus-4",
                confidence=0.9,
            ),
            "openai:gpt-4o": ModelProfile(
                model_key="openai:gpt-4o",
                confidence=0.9,
            ),
        }
    )

    # No model identifier in payload -> no telemetry profile selected.
    assert _select_telemetry_profile(store, {}) is None


def test_select_telemetry_profile_uses_exact_match() -> None:
    profile = ModelProfile(
        model_key="anthropic:claude-opus-4",
        confidence=0.9,
    )
    store = ModelProfileStore(
        profiles={
            "anthropic:claude-opus-4": profile,
            "openai:gpt-4o": ModelProfile(
                model_key="openai:gpt-4o",
                confidence=0.9,
            ),
        }
    )

    selected = _select_telemetry_profile(store, {"model": "claude-opus-4"})
    assert selected is profile


@dataclass
class _DummySession:
    behavior_compass: dict = field(default_factory=dict)


def test_session_telemetry_counter_defaults_to_zero() -> None:
    session = _DummySession()
    assert _session_telemetry_updates_used(session) == 0
    assert _can_apply_session_telemetry(session) is True


def test_session_telemetry_counter_enforces_cap() -> None:
    session = _DummySession(behavior_compass={"_model_profile_telem_updates": 10})
    assert _session_telemetry_updates_used(session) == 10
    assert _can_apply_session_telemetry(session) is False


def test_mark_session_telemetry_applied_increments_counter() -> None:
    session = _DummySession()
    _mark_session_telemetry_applied(session)
    _mark_session_telemetry_applied(session)
    assert session.behavior_compass["_model_profile_telem_updates"] == 2


def _run_main_payload(payload: dict | list, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    with pytest.raises(SystemExit) as exc:
        main()

    return int(exc.value.code), stdout.getvalue().strip()  # type: ignore[arg-type]  # code is int at runtime


def test_main_exits_clean_on_non_dict_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    code, output = _run_main_payload([], monkeypatch)
    assert code == 0
    assert output == "{}"


def test_main_exits_clean_on_write_with_null_tool_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    code, output = _run_main_payload(
        {
            "tool_name": "Write",
            "tool_input": None,
            "tool_output": "ok",
            "cwd": str(tmp_path),
        },
        monkeypatch,
    )
    assert code == 0
    assert output == "{}"


def test_main_accepts_string_bash_tool_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    code, output = _run_main_payload(
        {
            "tool_name": "Bash",
            "tool_input": "pwd",
            "tool_output": "ok",
            "cwd": str(tmp_path),
        },
        monkeypatch,
    )
    assert code == 0
    assert output == "{}"


def test_main_exits_clean_on_multiedit_with_invalid_edits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    code, output = _run_main_payload(
        {
            "tool_name": "MultiEdit",
            "tool_input": {"edits": ["bad"]},
            "tool_output": "ok",
            "cwd": str(tmp_path),
        },
        monkeypatch,
    )
    assert code == 0
    assert output == "{}"


# ── _fallback_config ──────────────────────────────────────────────────


class TestFallbackConfig:
    """Cover _fallback_config (lines 385-389)."""

    def test_returns_project_config_with_cwd(self, tmp_path) -> None:

        config = _fallback_config(str(tmp_path))
        assert isinstance(config, ProjectConfig)
        assert config.project_root == str(tmp_path)

    def test_returns_fresh_instance_each_call(self, tmp_path) -> None:

        a = _fallback_config(str(tmp_path))
        b = _fallback_config(str(tmp_path))
        assert a is not b


# ── _run_controlplane integration-style test with mocks ──────────────


class TestRunControlplane:
    """Cover _run_controlplane orchestration flow (lines 249-382)."""

    def test_exits_clean_on_risk_none(self, tmp_path, monkeypatch) -> None:
        """risk_level='none' triggers _exit_clean (line 282)."""
        from unittest.mock import MagicMock, patch

        classification = ChangeClassification(
            change_kind="none",
            risk_level="none",
            files_changed=[],
        )

        mock_config = MagicMock()
        mock_cp_config = MagicMock()
        mock_cp_config.channel_enabled.return_value = False

        with (
            patch(
                "lintgate.hooks.posttooluse_controlplane.classify_change",
                return_value=classification,
            ),
            patch("lintgate.hooks.habit.record_behavior_event"),
            patch("lintgate.hooks.habit.record_habit_event_lightweight"),
            patch("lintgate.hooks.controlplane.load_global_priors", return_value={}),
            pytest.raises(SystemExit) as exc,
        ):
            _run_controlplane(
                {"tool_name": "Bash", "tool_input": "ls", "tool_output": "ok"},
                mock_config,
                mock_cp_config,
                str(tmp_path),
                0.0,
            )
        assert exc.value.code == 0

    def test_full_orchestration_path(self, tmp_path, monkeypatch) -> None:
        """Full orchestration path with all heavy deps mocked."""
        import io
        from unittest.mock import MagicMock, patch

        classification = ChangeClassification(
            change_kind="code",
            risk_level="low",
            files_changed=["foo.py"],
        )

        mock_config = MagicMock()
        mock_cp_config = MagicMock()
        mock_cp_config.channel_enabled.return_value = False

        mesh_result = MeshResult(
            event=SupervisionEvent(project_root=str(tmp_path)),
            channel_results=[ChannelResult(channel="lint", status="pass")],
            coherence=CoherenceResult(state="stable", summary="all pass"),
            duration_ms=10.0,
        )

        mock_session = MagicMock()
        mock_session.behavior_compass = {}

        stdout_capture = io.StringIO()
        monkeypatch.setattr(sys, "stdout", stdout_capture)

        with (
            patch(
                "lintgate.hooks.posttooluse_controlplane.classify_change",
                return_value=classification,
            ),
            patch("lintgate.hooks.habit.record_behavior_event"),
            patch("lintgate.hooks.habit.record_habit_event_lightweight"),
            patch("lintgate.hooks.controlplane.load_global_priors", return_value={}),
            patch("lintgate.controlplane.runtime.run_mesh", return_value=mesh_result),
            patch("lintgate.controlplane.reporter.build_finding_index", return_value={}),
            patch(
                "lintgate.hooks.controlplane.extract_finding_indexes",
                return_value=({}, {}, 0, None, None),
            ),
            patch(
                "lintgate.hooks.controlplane.setup_session_and_gate",
                return_value=(mock_session, None),
            ),
            patch("lintgate.hooks.controlplane.post_process_session", return_value=[]),
            patch("lintgate.hooks.controlplane.save_run_details"),
            patch("lintgate.controlplane.reporter.format_mesh_report", return_value={}),
            patch("lintgate.hooks.controlplane.accumulate_session_telemetry"),
            patch("lintgate.hooks.controlplane.refresh_runtime_after_run"),
            patch("lintgate.hooks.arbitration.arbitrate_output", return_value={}),
            patch("lintgate.state.log_metric"),
            pytest.raises(SystemExit) as exc,
        ):
            _run_controlplane(
                {"tool_name": "Edit", "tool_input": {}, "tool_output": "ok"},
                mock_config,
                mock_cp_config,
                str(tmp_path),
                0.0,
            )
        assert exc.value.code == 0
        output = stdout_capture.getvalue().strip()
        parsed = _load_tool_result(output)
        assert isinstance(parsed, dict)

    def test_advisory_prepended_to_report(self, tmp_path, monkeypatch) -> None:
        """When setup_session_and_gate returns an advisory, it is prepended."""
        import io
        from unittest.mock import MagicMock, patch

        classification = ChangeClassification(
            change_kind="code",
            risk_level="low",
            files_changed=["foo.py"],
        )

        mock_config = MagicMock()
        mock_cp_config = MagicMock()
        mock_cp_config.channel_enabled.return_value = False

        mesh_result = MeshResult(
            event=SupervisionEvent(project_root=str(tmp_path)),
            channel_results=[ChannelResult(channel="lint", status="pass")],
            coherence=CoherenceResult(state="stable", summary="all pass"),
            duration_ms=10.0,
        )

        mock_session = MagicMock()
        mock_session.behavior_compass = {}

        advisory_msg = "[SessionGate] Bootstrap required"

        stdout_capture = io.StringIO()
        monkeypatch.setattr(sys, "stdout", stdout_capture)

        with (
            patch(
                "lintgate.hooks.posttooluse_controlplane.classify_change",
                return_value=classification,
            ),
            patch("lintgate.hooks.habit.record_behavior_event"),
            patch("lintgate.hooks.habit.record_habit_event_lightweight"),
            patch("lintgate.hooks.controlplane.load_global_priors", return_value={}),
            patch("lintgate.controlplane.runtime.run_mesh", return_value=mesh_result),
            patch("lintgate.controlplane.reporter.build_finding_index", return_value={}),
            patch(
                "lintgate.hooks.controlplane.extract_finding_indexes",
                return_value=({}, {}, 0, None, None),
            ),
            patch(
                "lintgate.hooks.controlplane.setup_session_and_gate",
                return_value=(mock_session, advisory_msg),
            ),
            patch("lintgate.hooks.controlplane.post_process_session", return_value=[]),
            patch("lintgate.hooks.controlplane.save_run_details"),
            patch(
                "lintgate.controlplane.reporter.format_mesh_report",
                return_value={"systemMessage": "lint report"},
            ),
            patch("lintgate.hooks.controlplane.accumulate_session_telemetry"),
            patch("lintgate.hooks.controlplane.refresh_runtime_after_run"),
            patch(
                "lintgate.hooks.arbitration.arbitrate_output",
                side_effect=lambda r, *a, **kw: r,
            ),
            patch("lintgate.state.log_metric"),
            pytest.raises(SystemExit) as exc,
        ):
            _run_controlplane(
                {"tool_name": "Edit", "tool_input": {}, "tool_output": "ok"},
                mock_config,
                mock_cp_config,
                str(tmp_path),
                0.0,
            )
        assert exc.value.code == 0
        output = stdout_capture.getvalue().strip()
        parsed = _load_tool_result(output)
        assert advisory_msg in parsed["systemMessage"]
        assert "lint report" in parsed["systemMessage"]


@dataclass
class _CpLite:
    habit_mode_enabled: bool = True
    session_memory: bool = False
    habit_mode_auto_detect: bool = True
    habit_mode_enter_score: float = 0.70
    habit_mode_exit_score: float = 0.40
    habit_mode_sustain_calls: int = 5
    habit_mode_token_api_interval: int = 99
    habit_mode_compact_threshold: float = 0.95

    def channel_enabled(self, _name: str) -> bool:
        return False


def test_posttooluse_runtime_state_flows_into_json_dumps(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_server
    from lintgate import habit_mode

    monkeypatch.setattr(habit_mode, "_HABIT_STATE_DIR", tmp_path / ".standalone_habit")

    cp = _CpLite()
    _record_habit_event_lightweight(
        cp,
        str(tmp_path),
        "Edit",
        {
            "file_path": str(tmp_path / "module.py"),
            "old_string": "x = 1",
            "new_string": "x = 2",
        },
        "ok",
    )

    runtime = load_runtime_state(str(tmp_path))
    assert runtime is not None
    assert runtime.active_files

    payload = {"project": str(tmp_path), "status": "ok"}
    rendered = mcp_server._json_dumps(payload, output_mode="compact")
    parsed = _load_tool_result(rendered)
    assert "session_context" in parsed
    assert parsed["session_context"]["gen"] >= 1
    assert parsed["session_context"]["focus"]

    extras = habit_mode.load_standalone_extras(str(tmp_path))
    assert isinstance(extras.get("write_scheduler"), dict)


def test_runtime_write_success_matches_watermark_generation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    events: list[dict] = []

    def capture(event):
        events.append(event)

    monkeypatch.setattr("lintgate.state.log_metric", capture)

    _refresh_runtime_state_lightweight(
        str(tmp_path),
        tool_name="Edit",
        tool_input={"file_path": str(tmp_path / "x.py")},
        trigger="compaction",
    )

    metric = next(
        (e for e in reversed(events) if e.get("event") == "runtime_state_write"),
        None,
    )
    assert metric is not None
    assert metric["success"] == 1

    file_gen = read_generation_from_file(str(tmp_path), ".claude/rules/lg_session.md")
    assert file_gen is not None
    assert file_gen == metric["generation"]


def test_runtime_write_failure_has_no_dynamic_watermark(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []

    def capture(event):
        events.append(event)

    monkeypatch.setattr("lintgate.state.log_metric", capture)

    _refresh_runtime_state_lightweight(
        str(tmp_path),
        tool_name="Edit",
        tool_input={"file_path": str(tmp_path / "x.py")},
        trigger="compaction",
    )

    metric = next(
        (e for e in reversed(events) if e.get("event") == "runtime_state_write"),
        None,
    )
    assert metric is not None
    assert metric["success"] == 0

    file_gen = read_generation_from_file(str(tmp_path), ".claude/rules/lg_session.md")
    assert file_gen is None


def test_runtime_write_metric_includes_lock_contention_fields(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    events: list[dict] = []

    def capture(event):
        events.append(event)

    monkeypatch.setattr("lintgate.state.log_metric", capture)

    _refresh_runtime_state_lightweight(
        str(tmp_path),
        tool_name="Edit",
        tool_input={"file_path": str(tmp_path / "y.py")},
        trigger="compaction",
    )

    metric = next(
        (e for e in reversed(events) if e.get("event") == "runtime_state_write"),
        None,
    )
    assert metric is not None
    assert "lock_contention_count" in metric
    assert "lock_acquired" in metric
    assert isinstance(metric["lock_contention_count"], int)
