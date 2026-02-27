"""Coverage tests for lintgate/hook_controlplane.py."""

from __future__ import annotations

from unittest import mock

from lintgate.hook_controlplane import (
    accumulate_session_telemetry,
    extract_finding_indexes,
    load_global_priors,
    mark_session_telemetry_applied,
    post_process_session,
    record_snapshot_behavior,
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

    def test_no_behavior_compass(self):
        s = mock.MagicMock(spec=[])
        assert session_telemetry_updates_used(s) == 0

    def test_non_dict_bc(self):
        s = mock.MagicMock()
        s.behavior_compass = "not_a_dict"
        assert session_telemetry_updates_used(s) == 0

    def test_normal_counter(self):
        s = mock.MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": 5}
        assert session_telemetry_updates_used(s) == 5

    def test_negative_returns_zero(self):
        s = mock.MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": -3}
        assert session_telemetry_updates_used(s) == 0


class TestMarkSessionTelemetryApplied:
    def test_none_session(self):
        mark_session_telemetry_applied(None)  # no error

    def test_non_dict_bc(self):
        s = mock.MagicMock()
        s.behavior_compass = "not_a_dict"
        mark_session_telemetry_applied(s)  # no error

    def test_increments_counter(self):
        s = mock.MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": 2}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 3


class TestResolveEventModelKey:
    def test_model_in_top_level(self):
        with mock.patch(
            "lintgate.controlplane.model_profiles.resolve_model_key",
            return_value="anthropic:claude-opus-4",
        ):
            result = resolve_event_model_key({"model": "claude-opus-4"})
        assert result == "anthropic:claude-opus-4"

    def test_model_in_metadata(self):
        with mock.patch(
            "lintgate.controlplane.model_profiles.resolve_model_key",
            side_effect=lambda x: "anthropic:claude-opus-4" if x == "opus" else None,
        ):
            result = resolve_event_model_key({"metadata": {"model": "opus"}})
        assert result == "anthropic:claude-opus-4"

    def test_model_in_session(self):
        with mock.patch(
            "lintgate.controlplane.model_profiles.resolve_model_key",
            side_effect=lambda x: "openai:gpt-4o" if x == "gpt-4o" else None,
        ):
            result = resolve_event_model_key({"session": {"model": "gpt-4o"}})
        assert result == "openai:gpt-4o"

    def test_model_in_tool_input(self):
        with mock.patch(
            "lintgate.controlplane.model_profiles.resolve_model_key",
            side_effect=lambda x: "test:model" if x == "test-m" else None,
        ):
            result = resolve_event_model_key({"tool_input": {"model": "test-m"}})
        assert result == "test:model"

    def test_no_model_found(self):
        with mock.patch(
            "lintgate.controlplane.model_profiles.resolve_model_key",
            return_value=None,
        ):
            result = resolve_event_model_key({})
        assert result is None


class TestSelectTelemetryProfile:
    def test_no_model_key(self):
        store = mock.MagicMock()
        with mock.patch(
            "lintgate.hook_controlplane.resolve_event_model_key",
            return_value=None,
        ):
            result = select_telemetry_profile(store, {})
        assert result is None

    def test_usable_profile(self):
        profile = mock.MagicMock()
        profile.is_usable.return_value = True
        store = mock.MagicMock()
        store.profiles = {"anthropic:claude-opus-4": profile}
        with mock.patch(
            "lintgate.hook_controlplane.resolve_event_model_key",
            return_value="anthropic:claude-opus-4",
        ):
            result = select_telemetry_profile(store, {"model": "opus"})
        assert result is profile

    def test_non_usable_profile(self):
        profile = mock.MagicMock()
        profile.is_usable.return_value = False
        store = mock.MagicMock()
        store.profiles = {"key": profile}
        with mock.patch(
            "lintgate.hook_controlplane.resolve_event_model_key",
            return_value="key",
        ):
            result = select_telemetry_profile(store, {})
        assert result is None


class TestLoadGlobalPriors:
    def test_disabled(self):
        cfg = mock.MagicMock()
        cfg.global_memory_enabled = False
        cfg.channel_enabled.return_value = True
        assert load_global_priors(cfg) is None

    def test_behavior_channel_disabled(self):
        cfg = mock.MagicMock()
        cfg.global_memory_enabled = True
        cfg.channel_enabled.return_value = False
        assert load_global_priors(cfg) is None

    def test_sufficient_data(self):
        cfg = mock.MagicMock()
        cfg.global_memory_enabled = True
        cfg.channel_enabled.return_value = True
        cfg.global_memory_ttl_days = 30
        cfg.global_memory_alpha = 0.15
        cfg.global_memory_decay_horizon = 14

        gp = mock.MagicMock()
        gp.session_count = 10
        gp.computed_bias_adjustments = {"approach_cycling": 0.1}

        with (
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=gp,
            ),
            mock.patch("lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 3),
        ):
            result = load_global_priors(cfg)
        assert result is not None
        assert result["enabled"] is True

    def test_insufficient_data(self):
        cfg = mock.MagicMock()
        cfg.global_memory_enabled = True
        cfg.channel_enabled.return_value = True
        cfg.global_memory_ttl_days = 30

        gp = mock.MagicMock()
        gp.session_count = 1

        with (
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=gp,
            ),
            mock.patch("lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 3),
        ):
            result = load_global_priors(cfg)
        assert result is None


class TestSetupSessionAndGate:
    def test_no_session_memory(self):
        cfg = mock.MagicMock()
        cfg.session_memory = False
        cfg.channel_enabled.return_value = False
        cfg.inquiry.any_enabled.return_value = False
        cfg.inquiry.session_gate = False

        session, advisory = setup_session_and_gate(cfg, "/tmp", "Read", mock.MagicMock(), [], None)
        assert session is None
        assert advisory is None

    def test_session_memory_enabled(self):
        cfg = mock.MagicMock()
        cfg.session_memory = True
        cfg.channel_enabled.return_value = True
        cfg.inquiry.any_enabled.return_value = False
        cfg.inquiry.session_gate = False

        fake_session = mock.MagicMock()
        fake_session.behavior_compass = {}
        event = mock.MagicMock()
        event.raw_input = {}

        with mock.patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=fake_session,
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Read", event, [], None)
        assert session is fake_session

    def test_session_gate_fires_advisory(self):
        cfg = mock.MagicMock()
        cfg.session_memory = True
        cfg.channel_enabled.return_value = True
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = True

        fake_session = mock.MagicMock()
        fake_session.behavior_compass = {}
        fake_session.theory_profile_cache = None
        event = mock.MagicMock()
        event.raw_input = {}

        readiness = mock.MagicMock()
        readiness.ready = False
        readiness.missing = ["core_theory"]
        readiness.recommendation = "Run bootstrap"

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            mock.patch(
                "lintgate.theory_extractor.extract_theory",
                return_value={"theory_profile": None},
            ),
            mock.patch(
                "lintgate.context_auditor.check_session_readiness",
                return_value=readiness,
            ),
        ):
            channels = [mock.MagicMock(name="behavior")]
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Write", event, channels, None)
        assert advisory is not None
        assert "Advisory" in advisory


class TestRecordSnapshotBehavior:
    def test_non_bash_tool(self):
        snapshot = mock.MagicMock()
        record_snapshot_behavior(snapshot, "Read", {}, "")
        assert snapshot.behavior.action_type == "read"

    def test_bash_tool_with_exit_code(self):
        snapshot = mock.MagicMock()
        with (
            mock.patch(
                "lintgate.controlplane.behavior_compass.normalize_command_sig",
                return_value="pytest",
            ),
            mock.patch(
                "lintgate.controlplane.behavior_compass.extract_error_sig",
                return_value="",
            ),
        ):
            record_snapshot_behavior(snapshot, "Bash", {"command": "pytest"}, "exit_code: 0")
        assert snapshot.behavior.action_type == "bash"
        assert snapshot.behavior.exit_code == 0

    def test_bash_error_in_output(self):
        snapshot = mock.MagicMock()
        with (
            mock.patch(
                "lintgate.controlplane.behavior_compass.normalize_command_sig",
                return_value="make",
            ),
            mock.patch(
                "lintgate.controlplane.behavior_compass.extract_error_sig",
                return_value="compile_error",
            ),
        ):
            record_snapshot_behavior(
                snapshot, "Bash", {"command": "make"}, "Error: compilation failed"
            )
        assert snapshot.behavior.exit_code == 1


class TestRunConstraintProposer:
    def test_with_pattern_alerts(self):
        session = mock.MagicMock()
        session.proposed_constraints = [{"pattern_key": "ruff|E501"}]
        session.pattern_trend = {}

        cr = mock.MagicMock()
        cr.channel = "lint"
        cr.metrics = {"pattern_alerts": [{"linter": "ruff", "kind": "E501"}]}

        mesh_result = mock.MagicMock()
        mesh_result.channel_results = [cr]

        cfg = mock.MagicMock()
        cfg.constraint_proposal_threshold = 3

        with (
            mock.patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
                return_value=[{"pattern_key": "ruff|E501"}],
            ),
            mock.patch(
                "lintgate.controlplane.constraint_proposer.store_proposals_in_session",
            ),
        ):
            result = run_constraint_proposer(session, mesh_result, cfg)
        assert result == session.proposed_constraints

    def test_behavior_trend_promotion(self):
        session = mock.MagicMock()
        session.proposed_constraints = []
        session.pattern_trend = {"behavior_channel|approach_cycling": [0, 1, 0, 1, 1]}

        cr = mock.MagicMock()
        cr.channel = "lint"
        cr.metrics = {"pattern_alerts": []}

        mesh_result = mock.MagicMock()
        mesh_result.channel_results = [cr]

        cfg = mock.MagicMock()
        cfg.constraint_proposal_threshold = 3

        with (
            mock.patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
                return_value=[],
            ),
            mock.patch(
                "lintgate.controlplane.constraint_proposer.store_proposals_in_session",
            ),
        ):
            run_constraint_proposer(session, mesh_result, cfg)

    def test_exception_suppressed(self):
        session = mock.MagicMock()
        session.proposed_constraints = []
        session.pattern_trend = {}

        mesh_result = mock.MagicMock()
        mesh_result.channel_results = []

        cfg = mock.MagicMock()
        # The import inside contextlib.suppress should handle this
        result = run_constraint_proposer(session, mesh_result, cfg)
        assert result == []


class TestSaveRunDetails:
    def test_empty_finding_index(self):
        save_run_details(mock.MagicMock(), {})  # empty dict is falsy in bool
        # No error — returns early

    def test_none_finding_index(self):
        save_run_details(mock.MagicMock(), None)
        # No error

    def test_valid_details(self):
        cr = mock.MagicMock()
        cr.channel = "lint"
        cr.status = "ok"
        cr.severity = "warning"
        cr.duration_ms = 100.0
        cr.error_message = None
        cr.findings = []
        cr.repairs = []
        cr.metrics = {}

        mesh = mock.MagicMock()
        mesh.coherence.state = "stable"
        mesh.coherence.summary = "ok"
        mesh.coherence.recommended_action = "none"
        mesh.coherence.silent_channels = set()
        mesh.coherence.loud_channels = {"lint"}
        mesh.duration_ms = 500
        mesh.partial = False
        mesh.incomplete_channels = []
        mesh.channel_results = [cr]
        mesh.event.event_id = "run-123"

        with mock.patch("lintgate.state.save_controlplane_run") as m:
            save_run_details(mesh, {"lint|E501": {"count": 1}})
            m.assert_called_once()


class TestExtractFindingIndexes:
    def test_none_session(self):
        prev, base, count, last_disp, last_nudge = extract_finding_indexes(None)
        assert prev is None
        assert base is None
        assert count == 0

    def test_with_snapshots(self):
        s1 = mock.MagicMock()
        s1.finding_index = {"a": 1}
        s2 = mock.MagicMock()
        s2.finding_index = {"b": 2}
        session = mock.MagicMock()
        session.snapshots = [s1, s2]

        prev, base, count, last_disp, last_nudge = extract_finding_indexes(session)
        assert prev == {"b": 2}
        assert base == {"a": 1}
        assert count == 2

    def test_empty_snapshots(self):
        session = mock.MagicMock()
        session.snapshots = []
        prev, base, count, last_disp, last_nudge = extract_finding_indexes(session)
        assert count == 0


class TestPostProcessSession:
    def test_none_session(self):
        result = post_process_session(
            None, mock.MagicMock(), {}, mock.MagicMock(), {}, "Read", {}, ""
        )
        assert result == []

    def test_with_session(self):
        session = mock.MagicMock()
        session.behavior_compass = {}
        session.proposed_constraints = []
        session.pattern_trend = {}

        cr = mock.MagicMock()
        cr.channel = "behavior"
        cr.metrics = {}
        cr.findings = []

        mesh = mock.MagicMock()
        mesh.channel_results = [cr]

        cfg = mock.MagicMock()
        cfg.constraint_proposal_threshold = 3

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.record_mesh_run",
                return_value=mock.MagicMock(),
            ),
            mock.patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
            mock.patch(
                "lintgate.hook_controlplane.apply_behavior_delta",
                return_value=["alert1"],
            ),
            mock.patch(
                "lintgate.hook_controlplane.record_snapshot_behavior",
            ),
            mock.patch(
                "lintgate.hook_controlplane.run_constraint_proposer",
                return_value=[],
            ),
        ):
            post_process_session(session, mesh, {"k": 1}, cfg, {}, "Read", {}, "")
        assert session.theory_profile_cache is None


class TestAccumulateSessionTelemetry:
    def test_no_telemetry(self):
        accumulate_session_telemetry({}, mock.MagicMock())  # no error

    def test_none_session(self):
        accumulate_session_telemetry({"_telemetry": {"x": 1}}, None)  # no error

    def test_with_telemetry(self):
        session = mock.MagicMock()
        session.behavior_compass = {"telemetry_counters": {"x": 5}}

        with mock.patch("lintgate.controlplane.session_memory.save_session"):
            accumulate_session_telemetry({"_telemetry": {"x": 3}}, session)
        assert session.behavior_compass["telemetry_counters"]["x"] == 8

    def test_non_dict_existing(self):
        session = mock.MagicMock()
        session.behavior_compass = {"telemetry_counters": "invalid"}

        with mock.patch("lintgate.controlplane.session_memory.save_session"):
            accumulate_session_telemetry({"_telemetry": {"x": 1}}, session)
        assert session.behavior_compass["telemetry_counters"]["x"] == 1


class TestRefreshRuntimeAfterRun:
    def test_with_session(self):
        from lintgate.hook_controlplane import refresh_runtime_after_run

        session = mock.MagicMock()
        cfg = mock.MagicMock()
        mesh = mock.MagicMock()

        with (
            mock.patch(
                "lintgate.hook_runtime_state.refresh_runtime_state_with_session",
            ),
            mock.patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
        ):
            refresh_runtime_after_run("/tmp", session, cfg, mesh, "Read", {})

    def test_without_session_no_habit(self):
        from lintgate.hook_controlplane import refresh_runtime_after_run

        cfg = mock.MagicMock()
        cfg.habit_mode_enabled = False
        cfg.session_memory = False
        mesh = mock.MagicMock()

        with mock.patch(
            "lintgate.hook_runtime_state.refresh_runtime_state_lightweight",
        ):
            refresh_runtime_after_run("/tmp", None, cfg, mesh, "Read", {})

    def test_without_session_with_habit(self):
        from lintgate.hook_controlplane import refresh_runtime_after_run

        cfg = mock.MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        mesh = mock.MagicMock()

        with (
            mock.patch(
                "lintgate.hook_runtime_state.refresh_runtime_state_lightweight",
                return_value={"gen": 1},
            ),
            mock.patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(mock.MagicMock(), []),
            ),
            mock.patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={"write_scheduler": {}, "token_tracker": {}},
            ),
            mock.patch(
                "lintgate.habit_mode.save_habit_state_standalone",
            ),
        ):
            refresh_runtime_after_run("/tmp", None, cfg, mesh, "Read", {})
