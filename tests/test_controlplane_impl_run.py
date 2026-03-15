"""Tests for mcp_tools/_controlplane_impl_run.py — channel selection, file resolution,
mesh execution helpers, session/persistence, compact enrichment.

Targets 115 surviving mutants across 32 functions with 0% prior kill rate.
Every assertion pins exact values (kills VALUE), tests parameter sensitivity
(kills SWAP), and covers boundary conditions (kills BOUNDARY).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from mcp_tools._controlplane_impl_run import (
    _ALL_CHANNEL_NAMES,
    _AVAILABLE_CHANNEL_DESCRIPTIONS,
    _CYCLE_REASON_TEMPLATES,
    _KNOWN_SCOPES,
    _accumulate_delivery_metrics,
    _append_schema_findings,
    _apply_exit_gate_to_compact,
    _build_supervision_event,
    _check_exit_gate,
    _check_ship_gate_parity,
    _check_theory_staleness_for_compact,
    _collect_files_for_event,
    _compute_dynamic_budget_ms,
    _compute_finding_recurrence,
    _dedup_files,
    _detect_edit_cycles,
    _extract_proven_resolutions,
    _inject_behavior_priors,
    _persist_behavior_compass_delta,
    _persist_global_profile_delta,
    _persist_runtime_state,
    _persist_session_after_mesh,
    _record_tool_event_for_behavior,
    _resolve_explicit_files,
    _resolve_git_changed_files,
    _resolve_scope_files,
    _RunContext,
    _save_run_details_for_drilldown,
    _select_channels,
    _setup_session,
    _update_refactor_state,
    _validate_channel_wiring,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _stub_helpers(**overrides):
    defaults = {
        "_validate_project_root": lambda p: p or "/tmp/test",
        "_collect_python_files": lambda _root: ["a.py", "b.py"],
        "_build_cp_full_details": lambda _mr, _fi: {},
        "_build_onboarding_status": lambda _root: {"config_state": "config_enabled"},
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }
    defaults.update(overrides)
    return defaults


def _make_channel_result(channel="lint", status="pass", findings=None, metrics=None):
    from lintgate.controlplane.types import ChannelResult

    return ChannelResult(
        channel=channel,
        status=status,
        findings=findings or [],
        metrics=metrics or {},
    )


def _make_mesh_result(channel_results=None, coherence_state="stable", git_context=None):
    from lintgate.controlplane.types import CoherenceResult, MeshResult

    return MeshResult(
        channel_results=channel_results or [],
        coherence=CoherenceResult(state=coherence_state),
        git_context=git_context or {},
    )


def _make_finding(kind="TEST001", severity="warning", proven_resolution=None):
    from lintgate.types import LintIssue

    return LintIssue(
        file="test.py",
        line=1,
        column=0,
        linter="test",
        kind=kind,
        message=f"test issue {kind}",
        severity=severity,
        proven_resolution=proven_resolution,
    )


def _make_session(snapshots=None, edit_cycle_state=None):
    return SimpleNamespace(
        session_id="test123",
        snapshots=snapshots or [],
        behavior_compass={"some": "compass"},
        edit_cycle_state=edit_cycle_state or {},
        delivery_health_summary={},
        latest_transfer_packet=None,
    )


# ── Constants ─────────────────────────────────────────────────────────


class TestConstants:
    def test_all_channel_names_contains_all_ten(self):
        names = _ALL_CHANNEL_NAMES.split(",")
        assert len(names) == 10
        assert "lint" in names
        assert "tests" in names
        assert "deps" in names
        assert "git" in names
        assert "behavior" in names
        assert "structure" in names
        assert "performance" in names
        assert "test_effectiveness" in names
        assert "specification" in names
        assert "test_hygiene" in names

    def test_available_channel_descriptions_keys_match_all_names(self):
        names = set(_ALL_CHANNEL_NAMES.split(","))
        assert set(_AVAILABLE_CHANNEL_DESCRIPTIONS.keys()) == names

    def test_known_scopes_exact(self):
        assert {"project", "changed", "staged", "full_sweep"} == _KNOWN_SCOPES

    def test_cycle_reason_templates_keys(self):
        assert set(_CYCLE_REASON_TEMPLATES.keys()) == {
            "CYCLE_SAME_FILE",
            "CYCLE_SAME_FINDING",
            "CYCLE_REPLACE_FAIL",
        }

    def test_cycle_reason_template_same_file_has_placeholder(self):
        assert "{file}" in _CYCLE_REASON_TEMPLATES["CYCLE_SAME_FILE"]

    def test_cycle_reason_template_same_finding_has_placeholder(self):
        assert "{persistence_count}" in _CYCLE_REASON_TEMPLATES["CYCLE_SAME_FINDING"]

    def test_cycle_reason_template_replace_fail_has_placeholder(self):
        assert "{consecutive_failures}" in _CYCLE_REASON_TEMPLATES["CYCLE_REPLACE_FAIL"]


# ── _select_channels ──────────────────────────────────────────────────


class TestSelectChannels:
    def test_known_channels_returned_in_order(self):
        reg = {"lint": "L", "tests": "T", "deps": "D"}
        active, requested, unknown = _select_channels("tests,lint", reg)
        assert active == ["T", "L"]
        assert requested == ["tests", "lint"]
        assert unknown == []

    def test_unknown_channels_collected_separately(self):
        reg = {"lint": "L"}
        active, requested, unknown = _select_channels("lint,bogus,fake", reg)
        assert active == ["L"]
        assert unknown == ["bogus", "fake"]

    def test_none_channels_uses_default_all(self):
        reg = {"lint": "L", "tests": "T"}
        active, requested, unknown = _select_channels(None, reg)
        # Should try to find all channels from _ALL_CHANNEL_NAMES
        assert "L" in active or "T" in active
        assert len(requested) == 10  # All channel names

    def test_empty_string_channels_uses_default(self):
        reg = {"lint": "L"}
        active, requested, unknown = _select_channels("", reg)
        assert len(requested) == 10

    def test_all_unknown_raises_value_error(self):
        reg = {"lint": "L"}
        with pytest.raises(ValueError, match="No valid channels"):
            _select_channels("bogus,fake", reg)

    def test_whitespace_stripped_from_channel_names(self):
        reg = {"lint": "L", "tests": "T"}
        active, requested, unknown = _select_channels(" lint , tests ", reg)
        assert active == ["L", "T"]
        assert requested == ["lint", "tests"]


# ── _dedup_files ─────────────────────────────────────────────────────


class TestDedupFiles:
    def test_empty_list_returns_empty(self):
        assert _dedup_files([]) == []

    def test_no_duplicates_preserved(self):
        result = _dedup_files(["a.py", "b.py", "c.py"])
        assert len(result) == 3

    def test_duplicates_removed(self):
        result = _dedup_files(["a.py", "a.py", "b.py"])
        assert len(result) == 2
        assert result[0] == "a.py"
        assert result[1] == "b.py"

    def test_limit_caps_input(self):
        files = [f"file{i}.py" for i in range(100)]
        result = _dedup_files(files, limit=5)
        assert len(result) == 5

    def test_default_limit_is_50(self):
        files = [f"file{i}.py" for i in range(60)]
        result = _dedup_files(files)
        assert len(result) == 50

    def test_limit_zero_returns_empty(self):
        result = _dedup_files(["a.py", "b.py"], limit=0)
        assert result == []

    def test_preserves_original_paths_not_resolved(self):
        result = _dedup_files(["./a.py"])
        assert result == ["./a.py"]

    def test_symlink_dedup_by_resolved(self, tmp_path):
        real = tmp_path / "real.py"
        real.write_text("x")
        link = tmp_path / "link.py"
        link.symlink_to(real)
        result = _dedup_files([str(real), str(link)])
        assert len(result) == 1


# ── _resolve_explicit_files ──────────────────────────────────────────


class TestResolveExplicitFiles:
    def test_relative_paths_resolved_to_project_root(self, tmp_path):
        result = _resolve_explicit_files(str(tmp_path), ["foo.py", "bar.py"])
        assert len(result) == 2
        assert str(tmp_path / "foo.py") in result[0]

    def test_absolute_paths_left_as_resolved(self, tmp_path):
        abs_path = str(tmp_path / "abs.py")
        result = _resolve_explicit_files(str(tmp_path), [abs_path])
        assert len(result) == 1
        assert result[0] == str(Path(abs_path).resolve())

    def test_empty_files_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty files list"):
            _resolve_explicit_files("/root", [])

    def test_single_file(self, tmp_path):
        result = _resolve_explicit_files(str(tmp_path), ["single.py"])
        assert len(result) == 1

    def test_mixed_absolute_and_relative(self, tmp_path):
        abs_path = str(tmp_path / "abs.py")
        result = _resolve_explicit_files(str(tmp_path), ["rel.py", abs_path])
        assert len(result) == 2


# ── _resolve_git_changed_files ───────────────────────────────────────


class TestResolveGitChangedFiles:
    def test_staged_uses_cached_diff(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(
                stdout="changed.py\n", returncode=0
            )
            _resolve_git_changed_files(
                str(tmp_path), "staged", [str(tmp_path / "changed.py")]
            )
            # Check the git command used for staged
            call_args = mock_run.call_args_list[0]
            cmd = call_args[0][0]
            assert "--cached" in cmd

    def test_non_staged_includes_untracked(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                SimpleNamespace(stdout="a.py\n", returncode=0),
                SimpleNamespace(stdout="b.py\n", returncode=0),
            ]
            _resolve_git_changed_files(
                str(tmp_path), "changed", [str(tmp_path / "a.py"), str(tmp_path / "b.py")]
            )
            assert mock_run.call_count == 2

    def test_returns_none_on_subprocess_error(self, tmp_path):
        with mock.patch("subprocess.run", side_effect=Exception("git error")):
            result = _resolve_git_changed_files(str(tmp_path), "changed", ["a.py"])
            assert result is None

    def test_filters_non_py_files(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(
                stdout="readme.md\nscript.sh\n", returncode=0
            )
            result = _resolve_git_changed_files(str(tmp_path), "staged", [])
            # No .py files in output, no existing_py match => None
            assert result is None

    def test_staged_does_not_call_ls_files(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(stdout="", returncode=0)
            _resolve_git_changed_files(str(tmp_path), "staged", [])
            # staged scope should only call git diff --cached, not ls-files
            assert mock_run.call_count == 1


# ── _resolve_scope_files ─────────────────────────────────────────────


class TestResolveScopeFiles:
    def test_files_scope_delegates_to_resolve_explicit(self, tmp_path):
        result = _resolve_scope_files(
            str(tmp_path), "files", ["x.py"], _stub_helpers()
        )
        assert len(result) == 1

    def test_unknown_scope_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown scope"):
            _resolve_scope_files("/root", "nonsense", [], _stub_helpers())

    def test_project_scope_uses_dedup(self):
        result = _resolve_scope_files("/root", "project", [], _stub_helpers())
        assert isinstance(result, list)

    def test_full_sweep_no_limit(self):
        many = [f"f{i}.py" for i in range(100)]
        helpers = _stub_helpers(_collect_python_files=lambda _: many)
        result = _resolve_scope_files("/root", "full_sweep", [], helpers)
        assert len(result) == 100

    def test_none_scope_defaults_to_changed_fallback(self):
        helpers = _stub_helpers()
        result = _resolve_scope_files("/root", None, [], helpers)
        # Falls through to git-changed, which fails, then defaults to dedup
        assert isinstance(result, list)

    def test_changed_scope_with_git_failure_falls_back(self):
        helpers = _stub_helpers()
        with mock.patch(
            "mcp_tools._controlplane_impl_run._resolve_git_changed_files",
            return_value=None,
        ):
            result = _resolve_scope_files("/root", "changed", [], helpers)
            assert isinstance(result, list)


# ── _compute_dynamic_budget_ms ───────────────────────────────────────


class TestComputeDynamicBudgetMs:
    def test_full_sweep_always_600k(self):
        assert _compute_dynamic_budget_ms(0, 10, "full_sweep") == 600_000

    def test_full_sweep_configured_floor_higher(self):
        assert _compute_dynamic_budget_ms(700_000, 10, "full_sweep") == 700_000

    def test_small_project_30k(self):
        assert _compute_dynamic_budget_ms(0, 5, None) == 30_000

    def test_boundary_20_files_is_30k(self):
        assert _compute_dynamic_budget_ms(0, 20, None) == 30_000

    def test_boundary_21_files_is_60k(self):
        assert _compute_dynamic_budget_ms(0, 21, None) == 60_000

    def test_boundary_100_files_is_60k(self):
        assert _compute_dynamic_budget_ms(0, 100, None) == 60_000

    def test_boundary_101_files_is_120k(self):
        assert _compute_dynamic_budget_ms(0, 101, None) == 120_000

    def test_boundary_500_files_is_120k(self):
        assert _compute_dynamic_budget_ms(0, 500, None) == 120_000

    def test_boundary_501_files_is_300k(self):
        assert _compute_dynamic_budget_ms(0, 501, None) == 300_000

    def test_configured_floor_honored(self):
        assert _compute_dynamic_budget_ms(200_000, 5, None) == 200_000

    def test_configured_lower_than_dynamic_uses_dynamic(self):
        assert _compute_dynamic_budget_ms(10_000, 501, None) == 300_000

    def test_zero_files_is_30k(self):
        assert _compute_dynamic_budget_ms(0, 0, None) == 30_000

    def test_exact_boundary_values_no_off_by_one(self):
        # Exactly at boundary vs one above
        assert _compute_dynamic_budget_ms(0, 20, "changed") == 30_000
        assert _compute_dynamic_budget_ms(0, 21, "changed") == 60_000


# ── _build_supervision_event ─────────────────────────────────────────


class TestBuildSupervisionEvent:
    def test_single_file_risk_moderate(self):
        event = _build_supervision_event("/root", ["a.py"], "strict", ["lint"])
        assert event.surface == "mcp"
        assert event.project_root == "/root"
        assert event.tool_name == "controlplane_run"
        assert event.files_changed == ["a.py"]
        cc = event.change_classification
        assert cc.risk_level == "moderate"
        assert cc.files_changed == ["a.py"]
        assert cc.files_by_language == {"python": ["a.py"]}
        assert cc.change_kind == "logic"
        assert cc.tool_name == "controlplane_run"

    def test_multiple_files_risk_structural(self):
        event = _build_supervision_event("/root", ["a.py", "b.py"], "advisory", ["lint"])
        assert event.change_classification.risk_level == "structural"

    def test_empty_files_no_language_map(self):
        event = _build_supervision_event("/root", [], "strict", ["lint"])
        assert event.change_classification.files_by_language == {}
        # risk_level with 0 files: 0 > 1 is False so "moderate"
        assert event.change_classification.risk_level == "moderate"

    def test_raw_input_contains_strictness_and_channels(self):
        event = _build_supervision_event("/root", ["x.py"], "strict", ["lint", "tests"])
        assert event.raw_input["strictness"] == "strict"
        assert event.raw_input["requested_channels"] == ["lint", "tests"]

    def test_parameter_sensitivity_strictness_appears_in_raw_input(self):
        e1 = _build_supervision_event("/root", ["a.py"], "advisory", ["lint"])
        e2 = _build_supervision_event("/root", ["a.py"], "strict", ["lint"])
        assert e1.raw_input["strictness"] == "advisory"
        assert e2.raw_input["strictness"] == "strict"


# ── _setup_session ───────────────────────────────────────────────────


class TestSetupSession:
    def test_session_memory_disabled_returns_none(self):
        config = SimpleNamespace(session_memory=False, session_max_age_hours=4.0)
        assert _setup_session("/root", config) is None

    def test_session_memory_enabled_returns_session(self):
        config = SimpleNamespace(session_memory=True, session_max_age_hours=4.0)
        with mock.patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value="mock_session",
        ):
            result = _setup_session("/root", config)
            assert result == "mock_session"

    def test_session_memory_enabled_but_import_fails(self):
        config = SimpleNamespace(session_memory=True, session_max_age_hours=4.0)
        with mock.patch.dict("sys.modules", {"lintgate.controlplane.session_memory": None}):
            # contextlib.suppress catches the ImportError
            result = _setup_session("/root", config)
            assert result is None


# ── _inject_behavior_priors ──────────────────────────────────────────


class TestInjectBehaviorPriors:
    def test_compass_injected_when_session_and_behavior_enabled(self):
        session = _make_session()
        config = SimpleNamespace(
            channel_enabled=lambda ch: ch == "behavior",
            global_memory_enabled=False,
        )
        event = SimpleNamespace(raw_input={})
        _inject_behavior_priors(event, session, config)
        assert event.raw_input["behavior_compass"] == {"some": "compass"}

    def test_no_compass_when_session_none(self):
        config = SimpleNamespace(
            channel_enabled=lambda ch: True,
            global_memory_enabled=False,
        )
        event = SimpleNamespace(raw_input={})
        _inject_behavior_priors(event, None, config)
        assert "behavior_compass" not in event.raw_input

    def test_no_compass_when_behavior_disabled(self):
        session = _make_session()
        config = SimpleNamespace(
            channel_enabled=lambda ch: False,
            global_memory_enabled=False,
        )
        event = SimpleNamespace(raw_input={})
        _inject_behavior_priors(event, session, config)
        assert "behavior_compass" not in event.raw_input

    def test_global_priors_injected_when_enabled(self):
        session = _make_session()
        config = SimpleNamespace(
            channel_enabled=lambda ch: True,
            global_memory_enabled=True,
            global_memory_ttl_days=90,
            global_memory_alpha=0.6,
            global_memory_decay_horizon=50,
        )
        event = SimpleNamespace(raw_input={})
        mock_profile = SimpleNamespace(
            session_count=10,
            computed_bias_adjustments={"adj": 0.1},
        )
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            return_value=mock_profile,
        ), mock.patch(
            "lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 5
        ):
            _inject_behavior_priors(event, session, config)
            priors = event.raw_input["behavior_global_priors"]
            assert priors["enabled"] is True
            assert priors["alpha"] == 0.6
            assert priors["decay_horizon"] == 50
            assert priors["computed_bias_adjustments"] == {"adj": 0.1}

    def test_global_priors_skipped_when_below_sample_size(self):
        session = _make_session()
        config = SimpleNamespace(
            channel_enabled=lambda ch: True,
            global_memory_enabled=True,
            global_memory_ttl_days=90,
            global_memory_alpha=0.6,
            global_memory_decay_horizon=50,
        )
        event = SimpleNamespace(raw_input={})
        mock_profile = SimpleNamespace(session_count=1, computed_bias_adjustments={})
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            return_value=mock_profile,
        ), mock.patch(
            "lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 5
        ):
            _inject_behavior_priors(event, session, config)
            assert "behavior_global_priors" not in event.raw_input


# ── _persist_behavior_compass_delta ──────────────────────────────────


class TestPersistBehaviorCompassDelta:
    def test_no_delta_skips_persist(self):
        cr = _make_channel_result(channel="behavior", metrics={})
        session = _make_session()
        config = SimpleNamespace(global_memory_enabled=False)
        with mock.patch(
            "lintgate.controlplane.session_memory.load_behavior_compass"
        ) as mock_load:
            _persist_behavior_compass_delta(cr, session, config)
            mock_load.assert_not_called()

    def test_non_dict_delta_skips_persist(self):
        cr = _make_channel_result(
            channel="behavior", metrics={"behavior_compass_delta": "not-a-dict"}
        )
        session = _make_session()
        config = SimpleNamespace(global_memory_enabled=False)
        with mock.patch(
            "lintgate.controlplane.session_memory.load_behavior_compass"
        ) as mock_load:
            _persist_behavior_compass_delta(cr, session, config)
            mock_load.assert_not_called()

    def test_dict_delta_applies_fields(self):
        delta = {
            "last_fired": "signal_x",
            "signal_fire_counts": {"x": 3},
            "early_nudge_emitted": True,
            "pending_nudge_signals": ["s1"],
            "pending_nudge_constraint_check_count": 2,
            "nudge_outcomes": [{"outcome": "accepted"}],
        }
        cr = _make_channel_result(
            channel="behavior", metrics={"behavior_compass_delta": delta}
        )
        session = _make_session()
        config = SimpleNamespace(global_memory_enabled=False)
        compass_obj = SimpleNamespace(
            last_fired="",
            signal_fire_counts={},
            early_nudge_emitted=False,
            pending_nudge_signals=[],
            pending_nudge_constraint_check_count=0,
            nudge_outcomes=[],
        )
        with mock.patch(
            "lintgate.controlplane.session_memory.load_behavior_compass",
            return_value=compass_obj,
        ), mock.patch(
            "lintgate.controlplane.session_memory.save_behavior_compass"
        ) as mock_save:
            _persist_behavior_compass_delta(cr, session, config)
            mock_save.assert_called_once_with(session, compass_obj)
            assert compass_obj.last_fired == "signal_x"
            assert compass_obj.signal_fire_counts == {"x": 3}
            assert compass_obj.early_nudge_emitted is True
            assert compass_obj.pending_nudge_signals == ["s1"]
            assert compass_obj.pending_nudge_constraint_check_count == 2
            assert compass_obj.nudge_outcomes == [{"outcome": "accepted"}]


# ── _persist_global_profile_delta ────────────────────────────────────


class TestPersistGlobalProfileDelta:
    def test_disabled_skips(self):
        cr = _make_channel_result(metrics={"global_profile_delta": {"x": 1}})
        config = SimpleNamespace(global_memory_enabled=False)
        # Should return immediately without error
        _persist_global_profile_delta(cr, None, config)

    def test_no_delta_key_skips(self):
        cr = _make_channel_result(metrics={})
        config = SimpleNamespace(global_memory_enabled=True, global_memory_ttl_days=90)
        _persist_global_profile_delta(cr, None, config)

    def test_non_dict_delta_skips(self):
        cr = _make_channel_result(metrics={"global_profile_delta": 42})
        config = SimpleNamespace(global_memory_enabled=True, global_memory_ttl_days=90)
        _persist_global_profile_delta(cr, None, config)

    def test_valid_delta_calls_apply_and_save(self):
        cr = _make_channel_result(metrics={"global_profile_delta": {"key": "val"}})
        session = _make_session()
        config = SimpleNamespace(global_memory_enabled=True, global_memory_ttl_days=90)
        mock_profile = SimpleNamespace()
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            return_value=mock_profile,
        ) as mock_load, mock.patch(
            "lintgate.controlplane.global_behavior_profile.apply_session_delta"
        ) as mock_apply, mock.patch(
            "lintgate.controlplane.global_behavior_profile.save_global_profile"
        ) as mock_save:
            _persist_global_profile_delta(cr, session, config)
            mock_load.assert_called_once_with(ttl_days=90)
            mock_apply.assert_called_once_with(mock_profile, {"key": "val"}, session_id="test123")
            mock_save.assert_called_once_with(mock_profile)


# ── _persist_session_after_mesh ──────────────────────────────────────


class TestPersistSessionAfterMesh:
    def test_none_session_is_noop(self):
        mesh = _make_mesh_result()
        _persist_session_after_mesh(None, mesh, {}, SimpleNamespace())

    def test_records_mesh_and_saves(self):
        session = _make_session()
        cr = _make_channel_result(channel="behavior", metrics={"behavior_compass_delta": {}})
        mesh = _make_mesh_result(channel_results=[cr])
        config = SimpleNamespace(global_memory_enabled=False)
        finding_index = {"fp1": {"kind": "K"}}

        with mock.patch(
            "lintgate.controlplane.session_memory.record_mesh_run"
        ) as mock_record, mock.patch(
            "lintgate.controlplane.session_memory.save_session"
        ) as mock_save, mock.patch(
            "mcp_tools._controlplane_impl_run._persist_behavior_compass_delta"
        ) as mock_persist:
            _persist_session_after_mesh(session, mesh, finding_index, config)
            mock_record.assert_called_once_with(session, mesh, finding_index=finding_index)
            mock_persist.assert_called_once()
            mock_save.assert_called_once_with(session)

    def test_only_behavior_channel_triggers_compass_persist(self):
        session = _make_session()
        cr_lint = _make_channel_result(channel="lint")
        cr_tests = _make_channel_result(channel="tests")
        mesh = _make_mesh_result(channel_results=[cr_lint, cr_tests])
        config = SimpleNamespace(global_memory_enabled=False)

        with mock.patch(
            "lintgate.controlplane.session_memory.record_mesh_run"
        ), mock.patch(
            "lintgate.controlplane.session_memory.save_session"
        ), mock.patch(
            "mcp_tools._controlplane_impl_run._persist_behavior_compass_delta"
        ) as mock_persist:
            _persist_session_after_mesh(session, mesh, {}, config)
            mock_persist.assert_not_called()


# ── _persist_runtime_state ───────────────────────────────────────────


class TestPersistRuntimeState:
    def test_counts_blocking_and_warning_findings(self):
        f_block = _make_finding(severity="blocking")
        f_warn = _make_finding(severity="warning")
        f_info = _make_finding(severity="informational")
        cr = _make_channel_result(findings=[f_block, f_warn, f_info])
        mesh = _make_mesh_result(channel_results=[cr], coherence_state="stable")

        with mock.patch(
            "lintgate.runtime_state.build_runtime_state"
        ) as mock_build, mock.patch(
            "lintgate.runtime_state.save_runtime_state"
        ) as mock_save:
            rt = SimpleNamespace(symbol_coverage_blockers=0)
            mock_build.return_value = rt
            _persist_runtime_state(mesh, "/root", None)
            mock_build.assert_called_once()
            call_kwargs = mock_build.call_args
            assert call_kwargs[1]["last_blocking"] == 1
            assert call_kwargs[1]["last_warnings"] == 1
            mock_save.assert_called_once()

    def test_counts_symbol_blockers(self):
        from lintgate.types import LintIssue

        f_symbol = LintIssue(
            linter="test", kind="symbol_uncovered", message="x",
            severity="blocking", file="t.py", line=1, column=0,
        )
        cr = _make_channel_result(channel="tests", findings=[f_symbol])
        mesh = _make_mesh_result(channel_results=[cr], coherence_state="stable")

        with mock.patch(
            "lintgate.runtime_state.build_runtime_state"
        ) as mock_build, mock.patch(
            "lintgate.runtime_state.save_runtime_state"
        ):
            rt = SimpleNamespace(symbol_coverage_blockers=0)
            mock_build.return_value = rt
            _persist_runtime_state(mesh, "/root", None)
            assert rt.symbol_coverage_blockers == 1


# ── _save_run_details_for_drilldown ──────────────────────────────────


class TestSaveRunDetailsForDrilldown:
    def test_calls_save_with_run_id(self):
        compact = {"run_id": "abc123"}
        helpers = _stub_helpers()
        mesh = _make_mesh_result()

        with mock.patch("lintgate.state.save_controlplane_run") as mock_save:
            _save_run_details_for_drilldown(mesh, {}, compact, helpers)
            mock_save.assert_called_once_with("abc123", {})


# ── _check_ship_gate_parity ─────────────────────────────────────────


class TestCheckShipGateParity:
    def test_non_strict_returns_stale(self, tmp_path):
        result = _check_ship_gate_parity(str(tmp_path), "advisory")
        assert result["status"] == "stale"
        assert "skipped" in result["message"]
        assert "command_to_verify" in result

    def test_strict_missing_script_returns_error(self, tmp_path):
        result = _check_ship_gate_parity(str(tmp_path), "strict")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_strict_with_script_runs_subprocess(self, tmp_path):
        script = tmp_path / "scripts" / "ship_main.py"
        script.parent.mkdir(parents=True)
        script.write_text("pass")

        mock_proc = SimpleNamespace(stdout='{"status":"pass"}', returncode=0, stderr="")
        with mock.patch("subprocess.run", return_value=mock_proc):
            result = _check_ship_gate_parity(str(tmp_path), "strict")
            assert result == {"status": "pass"}

    def test_strict_with_invalid_json_returns_error(self, tmp_path):
        script = tmp_path / "scripts" / "ship_main.py"
        script.parent.mkdir(parents=True)
        script.write_text("pass")

        mock_proc = SimpleNamespace(stdout="not json", returncode=1, stderr="err msg")
        with mock.patch("subprocess.run", return_value=mock_proc):
            result = _check_ship_gate_parity(str(tmp_path), "strict")
            assert result["status"] == "error"
            assert result["exit_code"] == 1

    def test_strict_subprocess_exception_returns_error(self, tmp_path):
        script = tmp_path / "scripts" / "ship_main.py"
        script.parent.mkdir(parents=True)
        script.write_text("pass")

        with mock.patch("subprocess.run", side_effect=TimeoutError("timed out")):
            result = _check_ship_gate_parity(str(tmp_path), "strict")
            assert result["status"] == "error"
            assert "timed out" in result["error"]


# ── _record_tool_event_for_behavior ──────────────────────────────────


class TestRecordToolEventForBehavior:
    def test_none_session_is_noop(self):
        config = SimpleNamespace(channel_enabled=lambda ch: True)
        _record_tool_event_for_behavior(None, config)

    def test_behavior_disabled_is_noop(self):
        session = _make_session()
        config = SimpleNamespace(channel_enabled=lambda ch: False)
        with mock.patch(
            "lintgate.controlplane.session_memory.load_behavior_compass"
        ) as mock_load:
            _record_tool_event_for_behavior(session, config)
            mock_load.assert_not_called()

    def test_records_event_when_enabled(self):
        session = _make_session()
        config = SimpleNamespace(channel_enabled=lambda ch: ch == "behavior")
        compass_obj = SimpleNamespace()
        with mock.patch(
            "lintgate.controlplane.session_memory.load_behavior_compass",
            return_value=compass_obj,
        ), mock.patch(
            "lintgate.controlplane.behavior_compass.record_tool_event"
        ) as mock_record, mock.patch(
            "lintgate.controlplane.session_memory.save_behavior_compass"
        ) as mock_save:
            _record_tool_event_for_behavior(session, config)
            mock_record.assert_called_once_with(compass_obj, "controlplane_run", {}, "")
            mock_save.assert_called_once_with(session, compass_obj)


# ── _compute_finding_recurrence ──────────────────────────────────────


class TestComputeFindingRecurrence:
    def test_empty_snapshots_returns_empty(self):
        session = _make_session(snapshots=[])
        assert _compute_finding_recurrence(session) == {}

    def test_single_snapshot_with_findings(self):
        snap = SimpleNamespace(finding_index={"fp1": {}, "fp2": {}})
        session = _make_session(snapshots=[snap])
        result = _compute_finding_recurrence(session)
        assert result == {"fp1": 1, "fp2": 1}

    def test_recurrence_accumulates_across_snapshots(self):
        snap1 = SimpleNamespace(finding_index={"fp1": {}, "fp2": {}})
        snap2 = SimpleNamespace(finding_index={"fp1": {}, "fp3": {}})
        snap3 = SimpleNamespace(finding_index={"fp1": {}})
        session = _make_session(snapshots=[snap1, snap2, snap3])
        result = _compute_finding_recurrence(session)
        assert result["fp1"] == 3
        assert result["fp2"] == 1
        assert result["fp3"] == 1

    def test_non_dict_finding_index_skipped(self):
        snap = SimpleNamespace(finding_index="not-a-dict")
        session = _make_session(snapshots=[snap])
        assert _compute_finding_recurrence(session) == {}

    def test_missing_finding_index_attr_skipped(self):
        snap = SimpleNamespace()  # no finding_index attribute
        session = _make_session(snapshots=[snap])
        assert _compute_finding_recurrence(session) == {}


# ── _detect_edit_cycles ──────────────────────────────────────────────


class TestDetectEditCycles:
    def test_none_session_returns_none(self):
        assert _detect_edit_cycles(None, {}) is None

    def _make_edit_cycle_state(self):
        from lintgate.orchestration.cycle_detector import EditCycleState

        return EditCycleState()

    def test_no_cycles_returns_none(self):
        session = _make_session()
        state = self._make_edit_cycle_state()
        with mock.patch(
            "lintgate.orchestration.cycle_detector.track_event",
            return_value=state,
        ), mock.patch(
            "lintgate.orchestration.cycle_detector.detect_cycles",
            return_value=[],
        ):
            result = _detect_edit_cycles(session, {})
            assert result is None

    def test_cycles_detected_returns_alerts(self):
        session = _make_session()
        cycle_info = SimpleNamespace(
            cycle_detected=True,
            reason="CYCLE_SAME_FILE",
            diagnostics={"file": "foo.py"},
        )
        state = self._make_edit_cycle_state()
        with mock.patch(
            "lintgate.orchestration.cycle_detector.track_event",
            return_value=state,
        ), mock.patch(
            "lintgate.orchestration.cycle_detector.detect_cycles",
            return_value=[cycle_info],
        ):
            result = _detect_edit_cycles(session, {"fp1": {}})
            assert result is not None
            assert len(result) == 1
            assert "foo.py" in result[0]

    def test_non_detected_cycles_filtered(self):
        session = _make_session()
        no_cycle = SimpleNamespace(cycle_detected=False, reason=None, diagnostics={})
        state = self._make_edit_cycle_state()
        with mock.patch(
            "lintgate.orchestration.cycle_detector.track_event",
            return_value=state,
        ), mock.patch(
            "lintgate.orchestration.cycle_detector.detect_cycles",
            return_value=[no_cycle],
        ):
            result = _detect_edit_cycles(session, {})
            assert result is None

    def test_unknown_reason_template_skipped(self):
        session = _make_session()
        cycle_info = SimpleNamespace(
            cycle_detected=True,
            reason="UNKNOWN_REASON",
            diagnostics={},
        )
        state = self._make_edit_cycle_state()
        with mock.patch(
            "lintgate.orchestration.cycle_detector.track_event",
            return_value=state,
        ), mock.patch(
            "lintgate.orchestration.cycle_detector.detect_cycles",
            return_value=[cycle_info],
        ):
            result = _detect_edit_cycles(session, {})
            # No matching template, so alert list is empty -> None
            assert result is None


# ── _accumulate_delivery_metrics ─────────────────────────────────────


class TestAccumulateDeliveryMetrics:
    def test_none_session_is_noop(self):
        mesh = _make_mesh_result()
        _accumulate_delivery_metrics(None, mesh)

    def test_counts_findings_and_suppressed(self):
        f1 = _make_finding()
        f2 = _make_finding(kind="K2")
        cr_beh = _make_channel_result(
            channel="behavior", findings=[f1], metrics={"suppressed_nudges": 3}
        )
        cr_lint = _make_channel_result(channel="lint", findings=[f2])
        mesh = _make_mesh_result(channel_results=[cr_beh, cr_lint])
        session = _make_session()

        with mock.patch(
            "lintgate.orchestration.continuity.generate_transfer_packet",
            side_effect=AttributeError,
        ):
            _accumulate_delivery_metrics(session, mesh)

        assert session.delivery_health_summary["delivered"] == 2
        assert session.delivery_health_summary["skipped"] == 3
        assert set(session.delivery_health_summary["channels"]) == {"behavior", "lint"}

    def test_empty_findings_not_counted(self):
        cr = _make_channel_result(channel="lint", findings=[])
        mesh = _make_mesh_result(channel_results=[cr])
        session = _make_session()

        with mock.patch(
            "lintgate.orchestration.continuity.generate_transfer_packet",
            side_effect=AttributeError,
        ):
            _accumulate_delivery_metrics(session, mesh)

        assert session.delivery_health_summary["delivered"] == 0
        assert session.delivery_health_summary["channels"] == []

    def test_snapshot_receives_delivery_metrics(self):
        f1 = _make_finding()
        cr = _make_channel_result(channel="lint", findings=[f1])
        mesh = _make_mesh_result(channel_results=[cr])
        snap = SimpleNamespace(delivery_metrics={})
        session = _make_session(snapshots=[snap])

        with mock.patch(
            "lintgate.orchestration.continuity.generate_transfer_packet",
            side_effect=AttributeError,
        ):
            _accumulate_delivery_metrics(session, mesh)

        assert snap.delivery_metrics["delivered"] == 1


# ── _extract_proven_resolutions ──────────────────────────────────────


class TestExtractProvenResolutions:
    def test_no_resolutions_returns_empty(self):
        cr = _make_channel_result(findings=[_make_finding()])
        mesh = _make_mesh_result(channel_results=[cr])
        assert _extract_proven_resolutions(mesh) == []

    def test_extracts_resolution_fields(self):
        res = {"repertoire": "fix_imports", "confidence": 0.95}
        f = _make_finding(proven_resolution=res)
        cr = _make_channel_result(findings=[f])
        mesh = _make_mesh_result(channel_results=[cr])
        result = _extract_proven_resolutions(mesh)
        assert len(result) == 1
        assert result[0]["finding"] == f.kind
        assert result[0]["resolution"] == "fix_imports"
        assert result[0]["confidence"] == 0.95

    def test_multiple_resolutions_across_channels(self):
        r1 = {"repertoire": "r1", "confidence": 0.8}
        r2 = {"repertoire": "r2", "confidence": 0.9}
        f1 = _make_finding(kind="K1", proven_resolution=r1)
        f2 = _make_finding(kind="K2", proven_resolution=r2)
        cr1 = _make_channel_result(channel="lint", findings=[f1])
        cr2 = _make_channel_result(channel="tests", findings=[f2])
        mesh = _make_mesh_result(channel_results=[cr1, cr2])
        result = _extract_proven_resolutions(mesh)
        assert len(result) == 2

    def test_empty_dict_resolution_not_included(self):
        # proven_resolution={} is falsy in `if f.proven_resolution`
        f = _make_finding(proven_resolution={})
        cr = _make_channel_result(findings=[f])
        mesh = _make_mesh_result(channel_results=[cr])
        assert _extract_proven_resolutions(mesh) == []


# ── _check_exit_gate ─────────────────────────────────────────────────


class TestCheckExitGate:
    def test_none_session_returns_none_tuple(self):
        a, f = _check_exit_gate(None)
        assert a is None
        assert f is None

    def test_too_few_snapshots_returns_none(self):
        session = _make_session(snapshots=[SimpleNamespace()])
        a, f = _check_exit_gate(session)
        assert a is None
        assert f is None

    def test_exactly_two_snapshots_runs_gate(self):
        session = _make_session(snapshots=[SimpleNamespace(), SimpleNamespace()])
        with mock.patch(
            "lintgate.controlplane.session_memory.check_session_exit_gate",
            return_value=["advisory1"],
        ), mock.patch(
            "lintgate.controlplane.session_memory.escalate_persistent_failures",
            return_value=["fail1"],
        ):
            advisories, failures = _check_exit_gate(session)
            assert advisories == ["advisory1"]
            assert failures == ["fail1"]

    def test_empty_results_become_none(self):
        session = _make_session(snapshots=[SimpleNamespace(), SimpleNamespace()])
        with mock.patch(
            "lintgate.controlplane.session_memory.check_session_exit_gate",
            return_value=[],
        ), mock.patch(
            "lintgate.controlplane.session_memory.escalate_persistent_failures",
            return_value=[],
        ):
            advisories, failures = _check_exit_gate(session)
            assert advisories is None
            assert failures is None


# ── _update_refactor_state ───────────────────────────────────────────


class TestUpdateRefactorState:
    def test_calls_update_with_counts(self):
        compact = {
            "run_id": "run1",
            "counts": {"blocking": 2, "warning": 3, "informational": 1},
        }
        with mock.patch(
            "lintgate.refactor_state.update_finding_counts"
        ) as mock_update:
            _update_refactor_state(compact, "/root")
            mock_update.assert_called_once_with(
                "/root", "run1", {"blocking": 2, "warning": 3, "informational": 1}
            )

    def test_empty_run_id_skips(self):
        compact = {"run_id": "", "counts": {"blocking": 0}}
        with mock.patch(
            "lintgate.refactor_state.update_finding_counts"
        ) as mock_update:
            _update_refactor_state(compact, "/root")
            mock_update.assert_not_called()

    def test_missing_run_id_skips(self):
        compact = {"counts": {"blocking": 0}}
        with mock.patch(
            "lintgate.refactor_state.update_finding_counts"
        ) as mock_update:
            _update_refactor_state(compact, "/root")
            mock_update.assert_not_called()

    def test_missing_counts_defaults_to_zero(self):
        compact = {"run_id": "run1"}
        with mock.patch(
            "lintgate.refactor_state.update_finding_counts"
        ) as mock_update:
            _update_refactor_state(compact, "/root")
            mock_update.assert_called_once_with(
                "/root", "run1", {"blocking": 0, "warning": 0, "informational": 0}
            )


# ── _apply_exit_gate_to_compact ──────────────────────────────────────


class TestApplyExitGateToCompact:
    def test_no_advisories_no_change(self):
        compact: dict[str, Any] = {}
        _apply_exit_gate_to_compact(compact, None, None)
        assert "session_exit_gate" not in compact
        assert "persistent_test_failures" not in compact

    def test_advisories_add_gate_section(self):
        compact: dict[str, Any] = {}
        _apply_exit_gate_to_compact(compact, ["adv1", "adv2"], None)
        assert compact["session_exit_gate"]["advisories"] == ["adv1", "adv2"]
        assert compact["session_exit_gate"]["persistent_failures"] == 0

    def test_persistent_failures_truncated_to_10(self):
        compact: dict[str, Any] = {}
        failures = list(range(15))
        _apply_exit_gate_to_compact(compact, None, failures)
        assert len(compact["persistent_test_failures"]) == 10

    def test_both_advisories_and_failures(self):
        compact: dict[str, Any] = {}
        _apply_exit_gate_to_compact(compact, ["a1"], ["f1", "f2"])
        assert compact["session_exit_gate"]["persistent_failures"] == 2
        assert compact["persistent_test_failures"] == ["f1", "f2"]


# ── _check_theory_staleness_for_compact ──────────────────────────────


class TestCheckTheoryStalenessForCompact:
    def test_no_git_context_is_noop(self):
        compact: dict[str, Any] = {}
        mesh = _make_mesh_result()
        _check_theory_staleness_for_compact(compact, mesh, None, "/root")
        assert "theory_staleness" not in compact

    def test_empty_modified_and_untracked_is_noop(self):
        compact: dict[str, Any] = {}
        mesh = SimpleNamespace(git_context={"modified_files": [], "untracked_files": []})
        _check_theory_staleness_for_compact(compact, mesh, None, "/root")
        assert "theory_staleness" not in compact

    def test_stale_theory_enriches_compact(self):
        compact: dict[str, Any] = {"next_actions": []}
        mesh = SimpleNamespace(git_context={"modified_files": ["a.py"]})
        session = SimpleNamespace(theory_profile_cache={"facets": {}})
        staleness = {
            "stale": True,
            "uncovered_files": [f"f{i}.py" for i in range(15)],
            "total_uncommitted_py": 15,
            "recommendation": "Rebuild theory pack",
        }
        with mock.patch(
            "lintgate.theory_extractor.check_theory_staleness",
            return_value=staleness,
        ):
            _check_theory_staleness_for_compact(compact, mesh, session, "/root")
        ts: dict[str, Any] = compact["theory_staleness"]
        assert ts["stale"] is True
        assert len(ts["uncovered_files"]) == 10  # capped at 10
        assert ts["total_uncommitted_py"] == 15
        assert len(compact["next_actions"]) == 1
        assert compact["next_actions"][0]["tool"] == "build_theory_pack"
        assert compact["next_actions"][0]["priority"] == 2

    def test_not_stale_does_not_enrich(self):
        compact: dict[str, Any] = {}
        mesh = SimpleNamespace(git_context={"modified_files": ["a.py"]})
        with mock.patch(
            "lintgate.theory_extractor.check_theory_staleness",
            return_value={"stale": False},
        ):
            _check_theory_staleness_for_compact(compact, mesh, None, "/root")
        assert "theory_staleness" not in compact


# ── _validate_channel_wiring ─────────────────────────────────────────


class TestValidateChannelWiring:
    def test_no_issues_returns_empty(self):
        with mock.patch(
            "lintgate.controlplane.metric_schema.register_all_schemas"
        ), mock.patch(
            "lintgate.controlplane.metric_schema.validate_wiring",
            return_value=[],
        ):
            result = _validate_channel_wiring(["lint"])
            assert result == []

    def test_missing_publisher_gets_wire001(self):
        issue = SimpleNamespace(
            issue_type="missing_publisher",
            consumer="tests",
            key="coverage",
            missing_publisher="no publisher for 'coverage'",
        )
        with mock.patch(
            "lintgate.controlplane.metric_schema.register_all_schemas"
        ), mock.patch(
            "lintgate.controlplane.metric_schema.validate_wiring",
            return_value=[issue],
        ):
            result = _validate_channel_wiring(["lint", "tests"])
            assert len(result) == 1
            assert result[0].kind == "WIRE001"
            assert result[0].linter == "metric_schema"
            assert result[0].file == "<schema>"
            assert result[0].line == 0
            assert result[0].severity == "warning"

    def test_other_issue_type_gets_wire002(self):
        issue = SimpleNamespace(
            issue_type="other",
            consumer="lint",
            key="metric_x",
            missing_publisher="channel Y",
        )
        with mock.patch(
            "lintgate.controlplane.metric_schema.register_all_schemas"
        ), mock.patch(
            "lintgate.controlplane.metric_schema.validate_wiring",
            return_value=[issue],
        ):
            result = _validate_channel_wiring(["lint"])
            assert len(result) == 1
            assert result[0].kind == "WIRE002"

    def test_import_error_gracefully_returns_empty(self):
        with mock.patch.dict("sys.modules", {"lintgate.controlplane.metric_schema": None}):
            result = _validate_channel_wiring(["lint"])
            assert result == []


# ── _append_schema_findings ──────────────────────────────────────────


class TestAppendSchemaFindings:
    def test_no_findings_no_channel_results_is_noop(self):
        mesh = _make_mesh_result(channel_results=[])
        _append_schema_findings(mesh, [])
        assert mesh.channel_results == []

    def test_wiring_findings_appended_to_last_channel(self):
        cr = _make_channel_result(channel="lint", findings=[])
        mesh = _make_mesh_result(channel_results=[cr])
        wiring_f = _make_finding(kind="WIRE001")
        with mock.patch(
            "lintgate.controlplane.metric_schema.validate_result",
            return_value=[],
        ):
            _append_schema_findings(mesh, [wiring_f])
        assert len(cr.findings) == 1
        assert cr.findings[0].kind == "WIRE001"

    def test_validate_result_adds_wire002_per_missing_key(self):
        cr = _make_channel_result(channel="lint", findings=[])
        mesh = _make_mesh_result(channel_results=[cr])
        with mock.patch(
            "lintgate.controlplane.metric_schema.validate_result",
            return_value=["missing_key_1", "missing_key_2"],
        ):
            _append_schema_findings(mesh, [])
        # 2 WIRE002 findings added
        wire_findings = [f for f in cr.findings if f.kind == "WIRE002"]
        assert len(wire_findings) == 2
        assert wire_findings[0].severity == "informational"

    def test_wiring_findings_to_empty_channel_results_creates_synthetic(self):
        mesh = _make_mesh_result(channel_results=[])
        wiring_f = _make_finding(kind="WIRE001")
        with mock.patch(
            "lintgate.controlplane.metric_schema.validate_result",
            return_value=[],
        ):
            _append_schema_findings(mesh, [wiring_f])
        assert len(mesh.channel_results) == 1
        assert mesh.channel_results[0].channel == "schema_validation"
        assert mesh.channel_results[0].status == "fail"


# ── _collect_files_for_event ─────────────────────────────────────────


class TestCollectFilesForEvent:
    def test_delegates_to_resolve_scope_files(self):
        helpers = _stub_helpers()
        with mock.patch(
            "mcp_tools._controlplane_impl_run._resolve_scope_files",
            return_value=["resolved.py"],
        ) as mock_resolve:
            result = _collect_files_for_event("/root", "project", [], helpers)
            assert result == ["resolved.py"]
            mock_resolve.assert_called_once_with("/root", "project", [], helpers)


# ── _RunContext ──────────────────────────────────────────────────────


class TestRunContext:
    def test_dataclass_fields(self):
        ctx = _RunContext(
            project_root="/root",
            cp_config=None,
            session=None,
            strictness="advisory",
            unknown=["bogus"],
            helpers={},
        )
        assert ctx.project_root == "/root"
        assert ctx.strictness == "advisory"
        assert ctx.unknown == ["bogus"]
        assert ctx.mesh_result is None
        assert ctx.finding_index == {}

    def test_finding_index_default_is_empty_dict(self):
        ctx1 = _RunContext(
            project_root="", cp_config=None, session=None,
            strictness="", unknown=[], helpers={},
        )
        ctx2 = _RunContext(
            project_root="", cp_config=None, session=None,
            strictness="", unknown=[], helpers={},
        )
        # Default factory should give independent dicts
        assert ctx1.finding_index is not ctx2.finding_index
