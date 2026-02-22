"""Tests for hook_controlplane.py helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.hook_controlplane import (
    accumulate_session_telemetry,
    apply_behavior_delta,
    extract_finding_indexes,
    load_global_priors,
    mark_session_telemetry_applied,
    post_process_session,
    record_snapshot_behavior,
    refresh_runtime_after_run,
    resolve_event_model_key,
    run_constraint_proposer,
    save_run_details,
    select_telemetry_profile,
    session_telemetry_updates_used,
    setup_session_and_gate,
)


class TestSessionTelemetryUpdatesUsed:
    def test_none_session(self):
        assert session_telemetry_updates_used(None) == 0

    def test_no_behavior_compass_attr(self):
        s = MagicMock(spec=[])
        assert session_telemetry_updates_used(s) == 0

    def test_bc_not_dict(self):
        s = MagicMock()
        s.behavior_compass = "not-a-dict"
        assert session_telemetry_updates_used(s) == 0

    def test_normal_counter(self):
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": 3}
        assert session_telemetry_updates_used(s) == 3

    def test_missing_key(self):
        s = MagicMock()
        s.behavior_compass = {}
        assert session_telemetry_updates_used(s) == 0

    def test_negative_value(self):
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": -1}
        assert session_telemetry_updates_used(s) == 0

    def test_non_int_value(self):
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": "bad"}
        assert session_telemetry_updates_used(s) == 0


class TestMarkSessionTelemetryApplied:
    def test_none_session(self):
        mark_session_telemetry_applied(None)

    def test_no_attr(self):
        s = MagicMock(spec=[])
        mark_session_telemetry_applied(s)

    def test_bc_not_dict(self):
        s = MagicMock()
        s.behavior_compass = "not-a-dict"
        mark_session_telemetry_applied(s)

    def test_increments(self):
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": 2}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 3

    def test_first_increment(self):
        s = MagicMock()
        s.behavior_compass = {}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 1


class TestResolveEventModelKey:
    def test_model_field(self):
        with patch(
            "lintgate.controlplane.model_profiles.resolve_model_key",
            return_value="anthropic:claude-3",
        ):
            result = resolve_event_model_key({"model": "claude-3"})
        assert result == "anthropic:claude-3"

    def test_no_candidates(self):
        with patch("lintgate.controlplane.model_profiles.resolve_model_key", return_value=None):
            result = resolve_event_model_key({})
        assert result is None

    def test_session_dict_branch(self):
        with patch("lintgate.controlplane.model_profiles.resolve_model_key", return_value="found"):
            result = resolve_event_model_key({"session": {"model": "x"}})
        assert result == "found"

    def test_tool_input_dict_branch(self):
        with patch("lintgate.controlplane.model_profiles.resolve_model_key", return_value="found"):
            result = resolve_event_model_key({"tool_input": {"model": "x"}})
        assert result == "found"

    def test_session_not_dict_skipped(self):
        with patch("lintgate.controlplane.model_profiles.resolve_model_key", return_value=None):
            result = resolve_event_model_key({"session": "not-dict"})
        assert result is None

    def test_metadata_dict(self):
        with patch("lintgate.controlplane.model_profiles.resolve_model_key", return_value="found"):
            result = resolve_event_model_key({"metadata": {"model": "x"}})
        assert result == "found"

    def test_blank_candidates_skipped(self):
        with patch("lintgate.controlplane.model_profiles.resolve_model_key", return_value=None):
            result = resolve_event_model_key({"model": "", "model_id": "  "})
        assert result is None


class TestSelectTelemetryProfile:
    def test_no_model_key(self):
        with patch("lintgate.hook_controlplane.resolve_event_model_key", return_value=None):
            assert select_telemetry_profile(MagicMock(), {}) is None

    def test_profile_missing(self):
        store = MagicMock()
        store.profiles = {}
        with patch("lintgate.hook_controlplane.resolve_event_model_key", return_value="key"):
            assert select_telemetry_profile(store, {}) is None

    def test_profile_not_usable(self):
        profile = MagicMock()
        profile.is_usable.return_value = False
        store = MagicMock()
        store.profiles = {"key": profile}
        with patch("lintgate.hook_controlplane.resolve_event_model_key", return_value="key"):
            assert select_telemetry_profile(store, {}) is None

    def test_profile_usable(self):
        profile = MagicMock()
        profile.is_usable.return_value = True
        store = MagicMock()
        store.profiles = {"key": profile}
        with patch("lintgate.hook_controlplane.resolve_event_model_key", return_value="key"):
            assert select_telemetry_profile(store, {}) is profile


class TestLoadGlobalPriors:
    def test_disabled(self):
        cfg = MagicMock()
        cfg.global_memory_enabled = False
        assert load_global_priors(cfg) is None

    def test_behavior_disabled(self):
        cfg = MagicMock()
        cfg.global_memory_enabled = True
        cfg.channel_enabled = MagicMock(return_value=False)
        assert load_global_priors(cfg) is None

    def test_below_min_sample(self):
        cfg = MagicMock()
        cfg.global_memory_enabled = True
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.global_memory_ttl_days = 90
        fake_gp = MagicMock()
        fake_gp.session_count = 1
        with (
            patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=fake_gp,
            ),
            patch("lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 5),
        ):
            assert load_global_priors(cfg) is None

    def test_sufficient_data(self):
        cfg = MagicMock()
        cfg.global_memory_enabled = True
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.global_memory_ttl_days = 90
        cfg.global_memory_alpha = 0.6
        cfg.global_memory_decay_horizon = 50
        fake_gp = MagicMock()
        fake_gp.session_count = 10
        fake_gp.computed_bias_adjustments = {"sig": 0.1}
        with (
            patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=fake_gp,
            ),
            patch("lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 3),
        ):
            result = load_global_priors(cfg)
        assert result["enabled"] is True

    def test_import_error(self):
        cfg = MagicMock()
        cfg.global_memory_enabled = True
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.global_memory_ttl_days = 90
        with patch(
            "lintgate.controlplane.global_behavior_profile.load_global_profile",
            side_effect=ImportError("boom"),
        ):
            assert load_global_priors(cfg) is None


class TestSetupSessionAndGate:
    def test_session_memory_disabled(self):
        cfg = MagicMock()
        cfg.session_memory = False
        cfg.channel_enabled = MagicMock(return_value=False)
        event = MagicMock()
        event.raw_input = {}
        session, advisory = setup_session_and_gate(cfg, "/tmp", "Edit", event, [], None)
        assert session is None

    def test_session_memory_enabled(self):
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 24
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = False
        fake_session = MagicMock()
        fake_session.behavior_compass = {}
        event = MagicMock()
        event.raw_input = {}
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session", return_value=fake_session
        ):
            session, _ = setup_session_and_gate(cfg, "/tmp", "Edit", event, [], None)
        assert session is fake_session

    def test_global_priors_injected(self):
        cfg = MagicMock()
        cfg.session_memory = False
        cfg.channel_enabled = MagicMock(return_value=False)
        event = MagicMock()
        event.raw_input = {}
        priors = {"enabled": True}
        setup_session_and_gate(cfg, "/tmp", "Edit", event, [], priors)
        assert event.raw_input["behavior_global_priors"] == priors


class TestApplyBehaviorDelta:
    def test_no_delta(self):
        session = MagicMock()
        session.behavior_compass = {}
        cr = MagicMock()
        cr.findings = []
        cr.metrics = {}
        cfg = MagicMock()
        cfg.global_memory_enabled = False
        result = apply_behavior_delta(session, cr, cfg, {})
        assert result == []

    def test_delta_applied(self):
        session = MagicMock()
        session.behavior_compass = {}
        cr = MagicMock()
        cr.findings = []
        cr.metrics = {"behavior_compass_delta": {"last_fired": "sig1"}}
        cfg = MagicMock()
        cfg.global_memory_enabled = False
        fake_bc = MagicMock()
        with (
            patch(
                "lintgate.controlplane.session_memory.load_behavior_compass", return_value=fake_bc
            ),
            patch("lintgate.controlplane.session_memory.save_behavior_compass"),
        ):
            apply_behavior_delta(session, cr, cfg, {})
        assert fake_bc.last_fired == "sig1"

    def test_global_delta(self):
        session = MagicMock()
        session.session_id = "s1"
        session.behavior_compass = {}
        cr = MagicMock()
        cr.findings = []
        cr.metrics = {"global_profile_delta": {"key": "val"}}
        cfg = MagicMock()
        cfg.global_memory_enabled = True
        cfg.global_memory_ttl_days = 90
        with (
            patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=MagicMock(),
            ),
            patch("lintgate.controlplane.global_behavior_profile.apply_session_delta") as apply_fn,
            patch("lintgate.controlplane.global_behavior_profile.save_global_profile"),
        ):
            apply_behavior_delta(session, cr, cfg, {})
        apply_fn.assert_called_once()


class TestRecordSnapshotBehavior:
    def test_non_bash_returns_early(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Edit", {}, "ok")
        assert snapshot.behavior.action_type == "edit"

    def test_bash_exit_code_match(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "Exit code: 0")
        assert snapshot.behavior.exit_code == 0

    def test_bash_error_in_output(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "Error occurred")
        assert snapshot.behavior.exit_code == 1

    def test_bash_clean_output(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "file.txt")
        assert snapshot.behavior.exit_code == 0

    def test_bash_str_input(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", "ls -la", "ok")
        assert snapshot.behavior.action_type == "bash"

    def test_bash_other_input(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", 42, "ok")
        assert snapshot.behavior.action_type == "bash"

    def test_bash_non_str_output(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, 42)
        assert snapshot.behavior.exit_code == 0


class TestRunConstraintProposer:
    def test_lint_channel_with_alerts(self):
        session = MagicMock()
        session.pattern_trend = {}
        session.proposed_constraints = [{"id": "c1"}]
        lint_cr = MagicMock()
        lint_cr.channel = "lint"
        lint_cr.metrics = {"pattern_alerts": [{"kind": "test"}]}
        mesh = MagicMock()
        mesh.channel_results = [lint_cr]
        cfg = MagicMock()
        cfg.constraint_proposal_threshold = 0.5
        with (
            patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
                return_value=[{"id": "p1"}],
            ),
            patch("lintgate.controlplane.constraint_proposer.store_proposals_in_session"),
        ):
            result = run_constraint_proposer(session, mesh, cfg)
        assert result == [{"id": "c1"}]

    def test_no_lint_channel(self):
        session = MagicMock()
        session.pattern_trend = {}
        session.proposed_constraints = []
        other_cr = MagicMock()
        other_cr.channel = "tests"
        mesh = MagicMock()
        mesh.channel_results = [other_cr]
        cfg = MagicMock()
        with (
            patch("lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns"),
            patch("lintgate.controlplane.constraint_proposer.store_proposals_in_session"),
        ):
            result = run_constraint_proposer(session, mesh, cfg)
        assert result == []

    def test_exception_suppressed(self):
        session = MagicMock()
        session.pattern_trend = {}
        session.proposed_constraints = []
        mesh = MagicMock()
        mesh.channel_results = []
        cfg = MagicMock()
        with patch(
            "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
            side_effect=ImportError("boom"),
        ):
            result = run_constraint_proposer(session, mesh, cfg)
        assert result == []


class TestSaveRunDetails:
    def test_empty_index(self):
        save_run_details(MagicMock(), {})

    def test_none_index(self):
        save_run_details(MagicMock(), None)

    def test_saves(self):
        cr = MagicMock()
        cr.channel = "lint"
        cr.status = "pass"
        cr.severity = "warning"
        cr.duration_ms = 10.0
        cr.error_message = None
        cr.findings = []
        cr.repairs = []
        cr.metrics = {}
        mesh = MagicMock()
        mesh.channel_results = [cr]
        mesh.event.event_id = "run1"
        mesh.partial = False
        mesh.incomplete_channels = []
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(mesh, {"lint": [{"msg": "test"}]})
        save_fn.assert_called_once()

    def test_no_event_id(self):
        mesh = MagicMock()
        mesh.channel_results = []
        mesh.event.event_id = ""
        mesh.partial = False
        mesh.incomplete_channels = []
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(mesh, {"k": "v"})
        save_fn.assert_not_called()


class TestExtractFindingIndexes:
    def test_none_session(self):
        prev, base, count = extract_finding_indexes(None)
        assert prev is None and base is None and count == 0

    def test_empty_snapshots(self):
        s = MagicMock()
        s.snapshots = []
        prev, base, count = extract_finding_indexes(s)
        assert count == 0

    def test_with_snapshots(self):
        snap1 = MagicMock()
        snap1.finding_index = {"baseline": True}
        snap2 = MagicMock()
        snap2.finding_index = {"latest": True}
        s = MagicMock()
        s.snapshots = [snap1, snap2]
        prev, base, count = extract_finding_indexes(s)
        assert prev == {"latest": True}
        assert base == {"baseline": True}
        assert count == 2


class TestPostProcessSession:
    def test_session_none(self):
        result = post_process_session(None, MagicMock(), {}, MagicMock(), {}, "E", {}, "")
        assert result == []

    def test_session_no_behavior(self):
        session = MagicMock()
        other_cr = MagicMock()
        other_cr.channel = "lint"
        mesh = MagicMock()
        mesh.channel_results = [other_cr]
        with (
            patch("lintgate.controlplane.session_memory.record_mesh_run", return_value=MagicMock()),
            patch("lintgate.controlplane.session_memory.save_session"),
            patch("lintgate.hook_controlplane.run_constraint_proposer", return_value=[]),
        ):
            result = post_process_session(session, mesh, {}, MagicMock(), {}, "E", {}, "")
        assert result == []


class TestAccumulateSessionTelemetry:
    def test_none_report(self):
        accumulate_session_telemetry(None, MagicMock())

    def test_no_telemetry(self):
        accumulate_session_telemetry({"systemMessage": "ok"}, MagicMock())

    def test_session_none(self):
        accumulate_session_telemetry({"_telemetry": {"a": 1}}, None)

    def test_accumulates(self):
        s = MagicMock()
        s.behavior_compass = {"telemetry_counters": {"a": 5}}
        with patch("lintgate.controlplane.session_memory.save_session"):
            accumulate_session_telemetry({"_telemetry": {"a": 3, "b": 1}}, s)
        counters = s.behavior_compass["telemetry_counters"]
        assert counters["a"] == 8 and counters["b"] == 1

    def test_existing_not_dict(self):
        s = MagicMock()
        s.behavior_compass = {"telemetry_counters": "bad"}
        with patch("lintgate.controlplane.session_memory.save_session"):
            accumulate_session_telemetry({"_telemetry": {"a": 1}}, s)
        assert s.behavior_compass["telemetry_counters"]["a"] == 1

    def test_empty_telemetry(self):
        accumulate_session_telemetry({"_telemetry": {}}, MagicMock())


class TestRefreshRuntimeAfterRun:
    def test_session_present(self):
        with (
            patch("lintgate.hook_runtime_state.refresh_runtime_state_with_session") as fn,
            patch("lintgate.controlplane.session_memory.save_session"),
        ):
            refresh_runtime_after_run("/tmp", MagicMock(), MagicMock(), MagicMock(), "Edit", {})
        fn.assert_called_once()

    def test_session_none_no_habit(self):
        cfg = MagicMock()
        cfg.habit_mode_enabled = False
        cfg.session_memory = True
        with patch("lintgate.hook_runtime_state.refresh_runtime_state_lightweight") as fn:
            refresh_runtime_after_run("/tmp", None, cfg, MagicMock(), "Edit", {})
        fn.assert_called_once()

    def test_session_none_habit_enabled(self):
        cfg = MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        with (
            patch(
                "lintgate.hook_runtime_state.refresh_runtime_state_lightweight",
                return_value={"k": "v"},
            ),
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={"write_scheduler": {"s": 1}},
            ),
            patch(
                "lintgate.habit_mode.load_habit_state_standalone", return_value=(MagicMock(), [])
            ),
            patch("lintgate.habit_mode.save_habit_state_standalone") as save_fn,
        ):
            refresh_runtime_after_run("/tmp", None, cfg, MagicMock(), "Edit", {})
        save_fn.assert_called_once()

    def test_session_none_habit_non_dict_return(self):
        cfg = MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        with (
            patch(
                "lintgate.hook_runtime_state.refresh_runtime_state_lightweight",
                return_value="not-dict",
            ),
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={"write_scheduler": {"s": 1}},
            ),
            patch("lintgate.habit_mode.load_habit_state_standalone") as load_fn,
        ):
            refresh_runtime_after_run("/tmp", None, cfg, MagicMock(), "Edit", {})
        load_fn.assert_not_called()
