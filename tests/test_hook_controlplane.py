"""Tests for hook_controlplane.py helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.hooks.controlplane import (
    _SESSION_TELEMETRY_UPDATE_CAP,
    PostProcessContext,
    accumulate_session_telemetry,
    apply_behavior_delta,
    can_apply_session_telemetry,
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
        # Should not raise — no return value to check
        mark_session_telemetry_applied(None)

    def test_no_attr(self):
        s = MagicMock(spec=[])
        mark_session_telemetry_applied(s)
        # Session without behavior_compass attr: no side effects

    def test_bc_not_dict(self):
        s = MagicMock()
        s.behavior_compass = "not-a-dict"
        mark_session_telemetry_applied(s)
        assert s.behavior_compass == "not-a-dict"

    def test_increments_from_2_to_3(self):
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": 2}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 3

    def test_first_increment_from_zero(self):
        s = MagicMock()
        s.behavior_compass = {}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 1

    def test_double_increment_reaches_exact_value(self):
        """Two successive calls: 0 -> 1 -> 2."""
        s = MagicMock()
        s.behavior_compass = {}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 1
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 2

    def test_increment_does_not_touch_other_keys(self):
        """Only the counter key is modified; other keys preserved."""
        s = MagicMock()
        s.behavior_compass = {"other_key": "preserved", "_model_profile_telem_updates": 5}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 6
        assert s.behavior_compass["other_key"] == "preserved"

    def test_negative_counter_treated_as_zero_then_increments_to_1(self):
        """Negative values are clamped to 0 by session_telemetry_updates_used, so increment = 0+1 = 1."""
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": -5}
        mark_session_telemetry_applied(s)
        assert s.behavior_compass["_model_profile_telem_updates"] == 1


class TestCanApplySessionTelemetry:
    """Exact-value tests for can_apply_session_telemetry (sigma=2)."""

    def test_none_session_returns_true(self):
        """None session => updates_used returns 0, which is < cap => True."""
        assert can_apply_session_telemetry(None) is True

    def test_fresh_session_returns_true(self):
        """Empty compass => counter=0, under cap => True."""
        s = MagicMock()
        s.behavior_compass = {}
        assert can_apply_session_telemetry(s) is True

    def test_under_cap_returns_true(self):
        """Counter at cap-1 => still under cap => True."""
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": _SESSION_TELEMETRY_UPDATE_CAP - 1}
        assert can_apply_session_telemetry(s) is True

    def test_at_cap_returns_false(self):
        """Counter exactly at cap => not under cap => False."""
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": _SESSION_TELEMETRY_UPDATE_CAP}
        assert can_apply_session_telemetry(s) is False

    def test_over_cap_returns_false(self):
        """Counter above cap => False."""
        s = MagicMock()
        s.behavior_compass = {"_model_profile_telem_updates": _SESSION_TELEMETRY_UPDATE_CAP + 5}
        assert can_apply_session_telemetry(s) is False

    def test_cap_value_is_10(self):
        """Pin the cap constant so changes are caught."""
        assert _SESSION_TELEMETRY_UPDATE_CAP == 10

    def test_no_behavior_compass_attr_returns_true(self):
        """Session without behavior_compass attr => updates_used returns 0 => True."""
        s = MagicMock(spec=[])
        assert can_apply_session_telemetry(s) is True

    def test_bc_not_dict_returns_true(self):
        """Non-dict compass => updates_used returns 0 => True."""
        s = MagicMock()
        s.behavior_compass = "not-a-dict"
        assert can_apply_session_telemetry(s) is True


class TestResolveEventModelKey:
    def test_model_field(self):
        with patch(
            "lintgate.controlplane.model.profiles.resolve_model_key",
            return_value="anthropic:claude-3",
        ):
            result = resolve_event_model_key({"model": "claude-3"})
        assert result == "anthropic:claude-3"

    def test_no_candidates(self):
        with patch("lintgate.controlplane.model.profiles.resolve_model_key", return_value=None):
            result = resolve_event_model_key({})
        assert result is None

    def test_session_dict_branch(self):
        with patch(
            "lintgate.controlplane.model.profiles.resolve_model_key",
            return_value="found",
        ):
            result = resolve_event_model_key({"session": {"model": "x"}})
        assert result == "found"

    def test_tool_input_dict_branch(self):
        with patch(
            "lintgate.controlplane.model.profiles.resolve_model_key",
            return_value="found",
        ):
            result = resolve_event_model_key({"tool_input": {"model": "x"}})
        assert result == "found"

    def test_session_not_dict_skipped(self):
        with patch("lintgate.controlplane.model.profiles.resolve_model_key", return_value=None):
            result = resolve_event_model_key({"session": "not-dict"})
        assert result is None

    def test_metadata_dict(self):
        with patch(
            "lintgate.controlplane.model.profiles.resolve_model_key",
            return_value="found",
        ):
            result = resolve_event_model_key({"metadata": {"model": "x"}})
        assert result == "found"

    def test_blank_candidates_skipped(self):
        with patch("lintgate.controlplane.model.profiles.resolve_model_key", return_value=None):
            result = resolve_event_model_key({"model": "", "model_id": "  "})
        assert result is None


class TestSelectTelemetryProfile:
    def test_no_model_key(self):
        with patch("lintgate.hooks.controlplane.resolve_event_model_key", return_value=None):
            assert select_telemetry_profile(MagicMock(), {}) is None

    def test_profile_missing(self):
        store = MagicMock()
        store.profiles = {}
        with patch("lintgate.hooks.controlplane.resolve_event_model_key", return_value="key"):
            assert select_telemetry_profile(store, {}) is None

    def test_profile_not_usable(self):
        profile = MagicMock()
        profile.is_usable.return_value = False
        store = MagicMock()
        store.profiles = {"key": profile}
        with patch("lintgate.hooks.controlplane.resolve_event_model_key", return_value="key"):
            assert select_telemetry_profile(store, {}) is None

    def test_profile_usable(self):
        profile = MagicMock()
        profile.is_usable.return_value = True
        store = MagicMock()
        store.profiles = {"key": profile}
        with patch("lintgate.hooks.controlplane.resolve_event_model_key", return_value="key"):
            assert select_telemetry_profile(store, {}) == profile


class TestLoadGlobalPriors:
    def test_disabled(self):
        cfg = MagicMock()
        cfg.global_memory_enabled = False
        with patch("lintgate.controlplane.global_behavior_profile.load_global_profile") as load_fn:
            assert load_global_priors(cfg) is None
        load_fn.assert_not_called()

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
        assert result is not None
        assert result["enabled"] is True
        assert result["alpha"] == 0.6
        assert result["decay_horizon"] == 50
        assert result["computed_bias_adjustments"] == {"sig": 0.1}
        assert set(result.keys()) == {
            "enabled",
            "alpha",
            "decay_horizon",
            "computed_bias_adjustments",
        }

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
    """Value-oriented tests covering all 10 equivalence partitions and 8 decision rules."""

    def test_session_memory_disabled_returns_none_none(self):
        """EP1: session_memory=False => session is None, advisory is None."""
        cfg = MagicMock()
        cfg.session_memory = False
        cfg.channel_enabled = MagicMock(return_value=False)
        event = MagicMock()
        event.raw_input = {}
        session, advisory = setup_session_and_gate(cfg, "/tmp", "Edit", event, [], None)
        assert session is None
        assert advisory is None
        # raw_input should NOT have behavior_compass injected
        assert "behavior_compass" not in event.raw_input
        # raw_input should NOT have theory_profile injected
        assert "theory_profile" not in event.raw_input

    def test_session_memory_disabled_no_global_priors_leaves_raw_input_clean(self):
        """EP2: session_memory=False, global_priors=None => raw_input untouched."""
        cfg = MagicMock()
        cfg.session_memory = False
        cfg.channel_enabled = MagicMock(return_value=False)
        event = MagicMock()
        event.raw_input = {}
        session, advisory = setup_session_and_gate(cfg, "/tmp", "Read", event, [], None)
        assert event.raw_input == {}

    def test_global_priors_injected_exact_value(self):
        """EP3: global_priors provided => exact dict injected into raw_input."""
        cfg = MagicMock()
        cfg.session_memory = False
        cfg.channel_enabled = MagicMock(return_value=False)
        event = MagicMock()
        event.raw_input = {}
        priors = {"enabled": True, "alpha": 0.6, "decay_horizon": 50}
        setup_session_and_gate(cfg, "/tmp", "Edit", event, [], priors)
        assert event.raw_input["behavior_global_priors"] == {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
        }

    def test_session_created_behavior_compass_injected(self):
        """EP4: session_memory=True, behavior enabled => compass dict injected."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 24
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = False
        cfg.inquiry.session_gate = False
        fake_session = MagicMock()
        compass_dict = {"signal_fire_counts": {"sig1": 2}, "event_counter": 5}
        fake_session.behavior_compass = compass_dict
        event = MagicMock()
        event.raw_input = {}
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=fake_session,
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Edit", event, [], None)
        assert session is fake_session
        assert advisory is None
        # Exact value: behavior_compass dict is the same object
        assert event.raw_input["behavior_compass"] is compass_dict
        assert event.raw_input["behavior_compass"]["event_counter"] == 5

    def test_session_created_behavior_disabled_no_compass_injection(self):
        """EP5: session exists but behavior channel disabled => no compass in raw_input."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 24
        cfg.channel_enabled = MagicMock(return_value=False)
        cfg.inquiry.any_enabled.return_value = False
        fake_session = MagicMock()
        fake_session.behavior_compass = {"x": 1}
        event = MagicMock()
        event.raw_input = {}
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=fake_session,
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Read", event, [], None)
        assert session is fake_session
        assert "behavior_compass" not in event.raw_input

    def test_theory_profile_cached_and_injected(self):
        """EP6: inquiry enabled => theory profile cached on session and injected into raw_input."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = False
        fake_session = MagicMock()
        fake_session.behavior_compass = {}
        fake_session.theory_profile_cache = None
        theory_data = {"core_theory": {"claims": ["c1"]}, "problem_solving": {}}
        event = MagicMock()
        event.raw_input = {}
        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value={"theory_profile": theory_data},
            ),
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Read", event, [], None)
        assert session.theory_profile_cache == theory_data
        assert event.raw_input["theory_profile"] == theory_data
        assert advisory is None

    def test_theory_extraction_failure_sets_cache_none(self):
        """EP7: extract_theory raises => theory_profile_cache = None, no injection."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = False
        fake_session = MagicMock()
        fake_session.behavior_compass = {}
        event = MagicMock()
        event.raw_input = {}
        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            patch(
                "lintgate.theory_extractor.extract_theory",
                side_effect=RuntimeError("boom"),
            ),
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Read", event, [], None)
        assert session.theory_profile_cache is None
        assert "theory_profile" not in event.raw_input
        assert advisory is None

    def test_session_gate_fires_advisory_on_not_ready(self):
        """EP8: session_gate=True, tool=Edit, not ready => advisory string with exact format."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = True
        fake_session = MagicMock()
        fake_session.behavior_compass = {"_session_ready": False}
        fake_session.theory_profile_cache = None
        event = MagicMock()
        event.raw_input = {}

        from lintgate.context_auditor import SessionReadiness

        not_ready = SessionReadiness(
            ready=False,
            missing=["core_theory", "enforceable_rules"],
            recommendation="Run build_theory_pack",
        )
        behavior_ch = MagicMock()
        behavior_ch.name = "behavior"
        lint_ch = MagicMock()
        lint_ch.name = "lint"
        channels = [behavior_ch, lint_ch]

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value={"theory_profile": None},
            ),
            patch(
                "lintgate.context_auditor.check_session_readiness",
                return_value=not_ready,
            ),
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Edit", event, channels, None)

        # Exact advisory format
        assert advisory == (
            "[Session Advisory] Context not ready for deep supervision. "
            "Missing: core_theory, enforceable_rules. "
            "Run build_theory_pack"
        )
        # behavior channel removed from list, lint remains
        assert len(channels) == 1
        assert channels[0].name == "lint"

    def test_session_gate_ready_marks_session(self):
        """EP9: session_gate=True, readiness.ready=True => _session_ready set, no advisory."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = True
        fake_session = MagicMock()
        fake_session.behavior_compass = {"_session_ready": False}
        fake_session.theory_profile_cache = None
        event = MagicMock()
        event.raw_input = {}

        from lintgate.context_auditor import SessionReadiness

        ready = SessionReadiness(ready=True, missing=[], recommendation="")
        behavior_ch = MagicMock()
        behavior_ch.name = "behavior"
        channels = [behavior_ch]

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value={"theory_profile": None},
            ),
            patch(
                "lintgate.context_auditor.check_session_readiness",
                return_value=ready,
            ),
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Write", event, channels, None)

        assert advisory is None
        assert fake_session.behavior_compass["_session_ready"] is True
        # channels should be unchanged (behavior not removed)
        assert len(channels) == 1

    def test_session_gate_skipped_for_non_edit_tools(self):
        """EP10: session_gate=True but tool=Read => no advisory check."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = True
        fake_session = MagicMock()
        fake_session.behavior_compass = {"_session_ready": False}
        fake_session.theory_profile_cache = None
        event = MagicMock()
        event.raw_input = {}

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value={"theory_profile": None},
            ),
            patch(
                "lintgate.context_auditor.check_session_readiness",
            ) as readiness_fn,
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Read", event, [], None)

        assert advisory is None
        readiness_fn.assert_not_called()

    def test_session_gate_skipped_when_already_ready(self):
        """Decision rule: _session_ready=True => gate check is bypassed."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = True
        fake_session = MagicMock()
        fake_session.behavior_compass = {"_session_ready": True}
        fake_session.theory_profile_cache = None
        event = MagicMock()
        event.raw_input = {}

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value={"theory_profile": None},
            ),
            patch(
                "lintgate.context_auditor.check_session_readiness",
            ) as readiness_fn,
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Edit", event, [], None)

        assert advisory is None
        readiness_fn.assert_not_called()

    def test_session_gate_multi_edit_tool_triggers_gate(self):
        """Decision rule: tool_name=MultiEdit is in the gate set."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = True
        cfg.inquiry.session_gate = True
        fake_session = MagicMock()
        fake_session.behavior_compass = {"_session_ready": False}
        fake_session.theory_profile_cache = None
        event = MagicMock()
        event.raw_input = {}

        from lintgate.context_auditor import SessionReadiness

        not_ready = SessionReadiness(ready=False, missing=["alignment"], recommendation="Fix it")

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value={"theory_profile": None},
            ),
            patch(
                "lintgate.context_auditor.check_session_readiness",
                return_value=not_ready,
            ),
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "MultiEdit", event, [], None)

        assert advisory == (
            "[Session Advisory] Context not ready for deep supervision. Missing: alignment. Fix it"
        )

    def test_get_or_create_session_exception_suppressed(self):
        """Decision rule: ImportError in get_or_create_session => session stays None."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=False)
        event = MagicMock()
        event.raw_input = {}
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            side_effect=RuntimeError("boom"),
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Edit", event, [], None)
        assert session is None
        assert advisory is None

    def test_both_compass_and_priors_injected(self):
        """Combined path: both behavior_compass and global_priors end up in raw_input."""
        cfg = MagicMock()
        cfg.session_memory = True
        cfg.session_max_age_hours = 4
        cfg.channel_enabled = MagicMock(return_value=True)
        cfg.inquiry.any_enabled.return_value = False
        cfg.inquiry.session_gate = False
        fake_session = MagicMock()
        compass = {"event_counter": 10}
        fake_session.behavior_compass = compass
        event = MagicMock()
        event.raw_input = {}
        priors = {"enabled": True, "alpha": 0.5}
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=fake_session,
        ):
            session, advisory = setup_session_and_gate(cfg, "/tmp", "Edit", event, [], priors)
        assert event.raw_input["behavior_compass"] is compass
        assert event.raw_input["behavior_global_priors"] == {"enabled": True, "alpha": 0.5}
        assert advisory is None


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
                "lintgate.controlplane.session_memory.load_behavior_compass",
                return_value=fake_bc,
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
    def test_non_bash_sets_action_type_only(self):
        """Non-Bash tools set action_type to lowercased name and return early."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Edit", {}, "ok")
        assert snapshot.behavior.action_type == "edit"

    def test_non_bash_write_tool(self):
        """Write tool lowercases to 'write'."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Write", {}, "ok")
        assert snapshot.behavior.action_type == "write"

    def test_bash_exit_code_from_explicit_match(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "Exit code: 0")
        assert snapshot.behavior.exit_code == 0
        assert snapshot.behavior.action_type == "bash"

    def test_bash_exit_code_nonzero(self):
        """Explicit exit_code: 2 is parsed as integer 2."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "exit_code: 2")
        assert snapshot.behavior.exit_code == 2

    def test_bash_error_keyword_sets_exit_1(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "Error occurred")
        assert snapshot.behavior.exit_code == 1

    def test_bash_failed_keyword_sets_exit_1(self):
        """'failed' (case-insensitive) in output triggers exit_code=1."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "make"}, "Build Failed")
        assert snapshot.behavior.exit_code == 1

    def test_bash_clean_output_sets_exit_0(self):
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "file.txt")
        assert snapshot.behavior.exit_code == 0

    def test_bash_command_signature_exact_value(self):
        """command_signature is set to the normalized command signature."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "git status"}, "clean")
        assert snapshot.behavior.command_signature == "git:status"

    def test_bash_error_signature_empty_on_empty_output(self):
        """Empty output produces empty error_signature."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "true"}, "")
        assert snapshot.behavior.error_signature == ""

    def test_bash_error_signature_from_output(self):
        """extract_error_sig returns the last meaningful line from output."""
        snapshot = MagicMock()
        record_snapshot_behavior(
            snapshot, "Bash", {"command": "python test.py"}, "ImportError: No module named foo"
        )
        assert snapshot.behavior.error_signature == "ImportError: No module named foo"

    def test_bash_error_signature_strips_absolute_paths(self):
        """Absolute paths in error output are stripped by extract_error_sig."""
        snapshot = MagicMock()
        record_snapshot_behavior(
            snapshot,
            "Bash",
            {"command": "python test.py"},
            "FileNotFoundError: /home/user/project/missing.py not found",
        )
        # extract_error_sig strips absolute paths, keeping just the basename
        assert "/home/user/project/" not in snapshot.behavior.error_signature
        assert "missing.py" in snapshot.behavior.error_signature

    def test_bash_str_input_extracts_command(self):
        """String tool_input is used as the command directly."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", "git diff", "ok")
        assert snapshot.behavior.action_type == "bash"
        assert snapshot.behavior.command_signature == "git:diff"

    def test_bash_non_dict_non_str_input_uses_empty_command(self):
        """Integer tool_input => empty command => 'unknown:unknown' signature."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", 42, "ok")
        assert snapshot.behavior.action_type == "bash"
        assert snapshot.behavior.command_signature == "unknown:unknown"

    def test_bash_non_str_output_sets_exit_0(self):
        """Non-string output is stringified; no error keywords => exit_code=0."""
        snapshot = MagicMock()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, 42)
        assert snapshot.behavior.exit_code == 0


class TestRunConstraintProposer:
    """Value-oriented tests for run_constraint_proposer decision paths."""

    def test_lint_channel_alerts_passed_to_proposer_exact_args(self):
        """Verify proposer receives exact alert dicts and threshold from config."""
        session = MagicMock()
        session.pattern_trend = {}
        session.proposed_constraints = [{"id": "c1", "rule": "no-retry"}]
        lint_cr = MagicMock()
        lint_cr.channel = "lint"
        lint_cr.metrics = {"pattern_alerts": [{"kind": "test", "linter": "ruff"}]}
        mesh = MagicMock()
        mesh.channel_results = [lint_cr]
        cfg = MagicMock()
        cfg.constraint_proposal_threshold = 0.7
        with (
            patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
                return_value=[{"id": "p1"}],
            ) as propose_fn,
            patch(
                "lintgate.controlplane.constraint_proposer.store_proposals_in_session"
            ) as store_fn,
        ):
            result = run_constraint_proposer(session, mesh, cfg)

        # Exact return value: session.proposed_constraints (not the propose return)
        assert result == [{"id": "c1", "rule": "no-retry"}]
        # proposer called with exact alert data and threshold
        propose_fn.assert_called_once()
        call_kwargs = propose_fn.call_args[1]
        assert call_kwargs["threshold"] == 0.7
        assert call_kwargs["session"] is session
        assert call_kwargs["config"] is cfg
        call_args = propose_fn.call_args[0]
        assert call_args[0]["alerted_patterns"] == [{"kind": "test", "linter": "ruff"}]
        # store called with exact proposals
        store_fn.assert_called_once_with(session, [{"id": "p1"}])

    def test_no_lint_channel_returns_empty_proposed(self):
        """No lint channel => pattern_alerts stays empty, result is session.proposed_constraints."""
        session = MagicMock()
        session.pattern_trend = {}
        session.proposed_constraints = []
        other_cr = MagicMock()
        other_cr.channel = "tests"
        mesh = MagicMock()
        mesh.channel_results = [other_cr]
        cfg = MagicMock()
        with (
            patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns"
            ) as propose_fn,
            patch("lintgate.controlplane.constraint_proposer.store_proposals_in_session"),
        ):
            result = run_constraint_proposer(session, mesh, cfg)
        assert result == []
        # propose_constraints not called because pattern_alerts is empty
        propose_fn.assert_not_called()

    def test_behavior_trend_promotes_recurring_signals(self):
        """Behavior channel trend entries with recent_run_count > 0 are promoted to alerts."""
        session = MagicMock()
        session.pattern_trend = {
            "behavior_channel|approach_cycling": [0, 1, 0, 2, 1],
            "behavior_channel|verification_debt": [0, 0, 0, 0, 0],
            "ruff|E501": [3, 2, 1],  # non-behavior, should be skipped
            "no_pipe": [1, 1],  # no pipe separator, should be skipped
        }
        session.proposed_constraints = [{"id": "from_session"}]
        lint_cr = MagicMock()
        lint_cr.channel = "lint"
        lint_cr.metrics = {"pattern_alerts": []}
        mesh = MagicMock()
        mesh.channel_results = [lint_cr]
        cfg = MagicMock()
        cfg.constraint_proposal_threshold = 0.5
        captured_alerts = []

        def capture_propose(data, **kwargs):
            captured_alerts.extend(data["alerted_patterns"])
            return []

        with (
            patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
                side_effect=capture_propose,
            ),
            patch("lintgate.controlplane.constraint_proposer.store_proposals_in_session"),
        ):
            result = run_constraint_proposer(session, mesh, cfg)

        # Only approach_cycling promoted (3 recent runs with count > 0)
        assert len(captured_alerts) == 1
        assert captured_alerts[0]["linter"] == "behavior_channel"
        assert captured_alerts[0]["kind"] == "approach_cycling"
        assert captured_alerts[0]["alert_reason"] == "recurring_across_runs"
        assert captured_alerts[0]["recent_run_count"] == 3
        assert result == [{"id": "from_session"}]

    def test_no_proposals_returned_skips_store(self):
        """When proposer returns empty list, store_proposals is NOT called."""
        session = MagicMock()
        session.pattern_trend = {}
        session.proposed_constraints = []
        lint_cr = MagicMock()
        lint_cr.channel = "lint"
        lint_cr.metrics = {"pattern_alerts": [{"kind": "x"}]}
        mesh = MagicMock()
        mesh.channel_results = [lint_cr]
        cfg = MagicMock()
        cfg.constraint_proposal_threshold = 0.5
        with (
            patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
                return_value=[],
            ),
            patch(
                "lintgate.controlplane.constraint_proposer.store_proposals_in_session"
            ) as store_fn,
        ):
            result = run_constraint_proposer(session, mesh, cfg)
        store_fn.assert_not_called()
        assert result == []

    def test_exception_suppressed_returns_empty(self):
        """Any exception in the suppress block => returns empty list (not crash)."""
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

    def test_multiple_channels_only_first_lint_used(self):
        """Only the first lint channel is processed; second lint channel is ignored."""
        session = MagicMock()
        session.pattern_trend = {}
        session.proposed_constraints = []
        lint1 = MagicMock()
        lint1.channel = "lint"
        lint1.metrics = {"pattern_alerts": [{"kind": "from_first"}]}
        lint2 = MagicMock()
        lint2.channel = "lint"
        lint2.metrics = {"pattern_alerts": [{"kind": "from_second"}]}
        mesh = MagicMock()
        mesh.channel_results = [lint1, lint2]
        cfg = MagicMock()
        cfg.constraint_proposal_threshold = 0.5
        captured = []

        def capture(data, **kwargs):
            captured.extend(data["alerted_patterns"])
            return []

        with (
            patch(
                "lintgate.controlplane.constraint_proposer.propose_constraints_from_patterns",
                side_effect=capture,
            ),
            patch("lintgate.controlplane.constraint_proposer.store_proposals_in_session"),
        ):
            run_constraint_proposer(session, mesh, cfg)
        # Only first lint channel's alerts
        assert captured == [{"kind": "from_first"}]


class TestSaveRunDetails:
    def test_empty_index_skips_save(self):
        """Empty finding_index => early return, no save call."""
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(MagicMock(), {})
        save_fn.assert_not_called()

    def test_none_index_skips_save(self):
        """None finding_index => early return, no save call."""
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(MagicMock(), None)  # type: ignore[arg-type]  # intentional: test None handling
        save_fn.assert_not_called()

    def test_saves_complete_details_structure(self):
        """Full save path: verify all detail fields have exact expected values."""
        cr = MagicMock()
        cr.channel = "lint"
        cr.status = "pass"
        cr.severity = "warning"
        cr.duration_ms = 10.123
        cr.error_message = None
        cr.findings = []
        cr.repairs = []
        cr.metrics = {"key": "val"}
        mesh = MagicMock()
        mesh.channel_results = [cr]
        mesh.event.event_id = "run1"
        mesh.partial = False
        mesh.incomplete_channels = []
        mesh.coherence.state = "aligned"
        mesh.coherence.summary = "all channels agree"
        mesh.coherence.recommended_action = "none"
        mesh.coherence.silent_channels = {"git"}
        mesh.coherence.loud_channels = {"lint"}
        mesh.duration_ms = 250.5
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(mesh, {"lint": [{"msg": "test"}]}, compliance_outcome="pass")
        save_fn.assert_called_once()
        run_id, details = save_fn.call_args[0]
        assert run_id == "run1"
        # Top-level fields
        assert details["compliance_outcome"] == "pass"
        assert details["finding_index"] == {"lint": [{"msg": "test"}]}
        assert details["partial"] is False
        assert details["incomplete_channels"] == []
        assert details["duration_ms"] == 250.5
        # Coherence sub-dict
        assert details["coherence"]["state"] == "aligned"
        assert details["coherence"]["summary"] == "all channels agree"
        assert details["coherence"]["recommended_action"] == "none"
        assert details["coherence"]["silent_channels"] == ["git"]
        assert details["coherence"]["loud_channels"] == ["lint"]
        # Channel details
        lint_ch = details["channels"]["lint"]
        assert lint_ch["status"] == "pass"
        assert lint_ch["severity"] == "warning"
        assert lint_ch["duration_ms"] == 10.1  # round(10.123, 1)
        assert lint_ch["error"] is None
        assert lint_ch["findings"] == []
        assert lint_ch["repairs"] == []
        assert lint_ch["metrics"] == {"key": "val"}

    def test_skipped_channels_excluded(self):
        """Channels with status='skip' are not included in details."""
        cr_skip = MagicMock()
        cr_skip.channel = "behavior"
        cr_skip.status = "skip"
        cr_pass = MagicMock()
        cr_pass.channel = "lint"
        cr_pass.status = "pass"
        cr_pass.severity = "info"
        cr_pass.duration_ms = 5.0
        cr_pass.error_message = None
        cr_pass.findings = []
        cr_pass.repairs = []
        cr_pass.metrics = {}
        mesh = MagicMock()
        mesh.channel_results = [cr_skip, cr_pass]
        mesh.event.event_id = "run2"
        mesh.partial = False
        mesh.incomplete_channels = []
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(mesh, {"lint": [{"msg": "x"}]})
        _, details = save_fn.call_args[0]
        assert "behavior" not in details["channels"]
        assert "lint" in details["channels"]

    def test_compliance_outcome_none_by_default(self):
        """When compliance_outcome is not passed, it defaults to None in details."""
        cr = MagicMock()
        cr.channel = "lint"
        cr.status = "pass"
        cr.severity = "info"
        cr.duration_ms = 1.0
        cr.error_message = None
        cr.findings = []
        cr.repairs = []
        cr.metrics = {}
        mesh = MagicMock()
        mesh.channel_results = [cr]
        mesh.event.event_id = "run3"
        mesh.partial = False
        mesh.incomplete_channels = []
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(mesh, {"lint": ["f"]})
        _, details = save_fn.call_args[0]
        assert details["compliance_outcome"] is None

    def test_no_event_id_skips_save(self):
        """Empty event_id => save_controlplane_run is not called."""
        mesh = MagicMock()
        mesh.channel_results = []
        mesh.event.event_id = ""
        mesh.partial = False
        mesh.incomplete_channels = []
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(mesh, {"k": "v"})
        save_fn.assert_not_called()

    def test_repair_fields_serialized(self):
        """Repairs are serialized with exact field names."""
        repair = MagicMock()
        repair.action_id = "r1"
        repair.kind = "auto_fix"
        repair.summary = "fix import"
        repair.safe = True
        repair.payload = {"file": "foo.py"}
        cr = MagicMock()
        cr.channel = "lint"
        cr.status = "fail"
        cr.severity = "error"
        cr.duration_ms = 20.0
        cr.error_message = "found issues"
        cr.findings = []
        cr.repairs = [repair]
        cr.metrics = {}
        mesh = MagicMock()
        mesh.channel_results = [cr]
        mesh.event.event_id = "run4"
        mesh.partial = True
        mesh.incomplete_channels = ["structure"]
        with patch("lintgate.state.save_controlplane_run") as save_fn:
            save_run_details(mesh, {"lint": ["f"]})
        _, details = save_fn.call_args[0]
        assert details["partial"] is True
        assert details["incomplete_channels"] == ["structure"]
        lint_ch = details["channels"]["lint"]
        assert lint_ch["error"] == "found issues"
        assert len(lint_ch["repairs"]) == 1
        r = lint_ch["repairs"][0]
        assert r == {
            "action_id": "r1",
            "kind": "auto_fix",
            "summary": "fix import",
            "safe": True,
            "payload": {"file": "foo.py"},
        }


class TestExtractFindingIndexes:
    def test_none_session(self):
        prev, base, count, last_disp, last_nudge = extract_finding_indexes(None)
        assert prev is None and base is None and count == 0
        assert last_disp is None and last_nudge is None

    def test_empty_snapshots(self):
        s = MagicMock()
        s.snapshots = []
        prev, base, count, last_disp, last_nudge = extract_finding_indexes(s)
        assert count == 0
        assert last_disp is None and last_nudge is None

    def test_with_snapshots(self):
        snap1 = MagicMock()
        snap1.finding_index = {"baseline": True}
        snap2 = MagicMock()
        snap2.finding_index = {"latest": True}
        snap2.disposition = "cautious"
        snap2.last_nudge = {"type": "slow_down"}
        s = MagicMock()
        s.snapshots = [snap1, snap2]
        prev, base, count, last_disp, last_nudge = extract_finding_indexes(s)
        assert prev == {"latest": True}
        assert base == {"baseline": True}
        assert count == 2
        assert last_disp == "cautious"
        assert last_nudge == {"type": "slow_down"}


class TestPostProcessSession:
    def test_session_none(self):
        ctx = PostProcessContext(
            session=None,
            mesh_result=MagicMock(),
            finding_index={},
            cp_config=MagicMock(),
            input_data={},
            tool_name="E",
            tool_input={},
            tool_output="",
        )
        result = post_process_session(ctx)
        assert result == []

    def test_session_no_behavior(self):
        session = MagicMock()
        other_cr = MagicMock()
        other_cr.channel = "lint"
        mesh = MagicMock()
        mesh.channel_results = [other_cr]
        with (
            patch(
                "lintgate.controlplane.session_memory.record_mesh_run",
                return_value=MagicMock(),
            ),
            patch("lintgate.controlplane.session_memory.save_session"),
            patch("lintgate.hooks.controlplane.run_constraint_proposer", return_value=[]),
        ):
            ctx = PostProcessContext(
                session=session,
                mesh_result=mesh,
                finding_index={},
                cp_config=MagicMock(),
                input_data={},
                tool_name="E",
                tool_input={},
                tool_output="",
            )
            result = post_process_session(ctx)
        assert result == []


class TestAccumulateSessionTelemetry:
    """Value-oriented tests for accumulate_session_telemetry (sigma=7)."""

    def test_none_report_no_save(self):
        """EP1: report=None => no save, no mutation."""
        s = MagicMock()
        s.behavior_compass = {}
        with patch("lintgate.controlplane.session_memory.save_session") as save_fn:
            accumulate_session_telemetry(None, s)
        save_fn.assert_not_called()
        assert "telemetry_counters" not in s.behavior_compass

    def test_report_without_telemetry_key_no_save(self):
        """EP2: report has no _telemetry key => no save."""
        s = MagicMock()
        s.behavior_compass = {}
        with patch("lintgate.controlplane.session_memory.save_session") as save_fn:
            accumulate_session_telemetry({"systemMessage": "ok"}, s)
        save_fn.assert_not_called()
        assert "telemetry_counters" not in s.behavior_compass

    def test_empty_telemetry_dict_no_save(self):
        """EP3: _telemetry={} => early return, no save."""
        s = MagicMock()
        s.behavior_compass = {}
        with patch("lintgate.controlplane.session_memory.save_session") as save_fn:
            accumulate_session_telemetry({"_telemetry": {}}, s)
        save_fn.assert_not_called()

    def test_session_none_no_save(self):
        """EP4: session=None => no save (even with valid telemetry)."""
        with patch("lintgate.controlplane.session_memory.save_session") as save_fn:
            accumulate_session_telemetry({"_telemetry": {"a": 1}}, None)
        save_fn.assert_not_called()

    def test_accumulates_exact_values_additive(self):
        """EP5: existing counters are summed with new telemetry values."""
        s = MagicMock()
        s.behavior_compass = {"telemetry_counters": {"a": 5, "c": 10}}
        with patch("lintgate.controlplane.session_memory.save_session") as save_fn:
            accumulate_session_telemetry({"_telemetry": {"a": 3, "b": 1}}, s)
        counters = s.behavior_compass["telemetry_counters"]
        # a: 5 + 3 = 8 (existing + new)
        assert counters["a"] == 8
        # b: 0 + 1 = 1 (new key)
        assert counters["b"] == 1
        # c: 10 (untouched, not in telemetry)
        assert counters["c"] == 10
        assert set(counters.keys()) == {"a", "b", "c"}
        save_fn.assert_called_once_with(s)

    def test_existing_counters_not_dict_replaced(self):
        """EP6: telemetry_counters is non-dict => replaced with fresh dict."""
        s = MagicMock()
        s.behavior_compass = {"telemetry_counters": "corrupt"}
        with patch("lintgate.controlplane.session_memory.save_session") as save_fn:
            accumulate_session_telemetry({"_telemetry": {"x": 7}}, s)
        counters = s.behavior_compass["telemetry_counters"]
        assert counters == {"x": 7}
        save_fn.assert_called_once_with(s)

    def test_no_existing_counters_key_creates_fresh(self):
        """EP7: behavior_compass has no telemetry_counters key => created from scratch."""
        s = MagicMock()
        s.behavior_compass = {"other_key": True}
        with patch("lintgate.controlplane.session_memory.save_session") as save_fn:
            accumulate_session_telemetry({"_telemetry": {"events": 42}}, s)
        counters = s.behavior_compass["telemetry_counters"]
        assert counters == {"events": 42}
        assert s.behavior_compass["other_key"] is True  # untouched
        save_fn.assert_called_once_with(s)


class TestRefreshRuntimeAfterRun:
    """Value-oriented tests for refresh_runtime_after_run (sigma=12)."""

    def test_session_present_calls_with_session_and_saves(self):
        """EP1: session not None => refresh_with_session called with exact kwargs, then saved."""
        fake_session = MagicMock()
        fake_mesh = MagicMock()
        with (
            patch("lintgate.hooks.runtime_state.refresh_runtime_state_with_session") as fn,
            patch("lintgate.controlplane.session_memory.save_session") as save_fn,
        ):
            refresh_runtime_after_run(
                "/proj", fake_session, MagicMock(), fake_mesh, "Bash", {"command": "ls"}
            )
        fn.assert_called_once()
        call_args, call_kwargs = fn.call_args
        assert call_args[0] == "/proj"
        assert call_args[1] is fake_session
        assert call_kwargs["mesh_result"] is fake_mesh
        assert call_kwargs["tool_name"] == "Bash"
        assert call_kwargs["tool_input"] == {"command": "ls"}
        assert call_kwargs["trigger"] == "lint_complete"
        save_fn.assert_called_once_with(fake_session)

    def test_session_present_save_exception_suppressed(self):
        """EP2: save_session raises => no crash (contextlib.suppress)."""
        with (
            patch("lintgate.hooks.runtime_state.refresh_runtime_state_with_session"),
            patch(
                "lintgate.controlplane.session_memory.save_session",
                side_effect=RuntimeError("disk full"),
            ),
        ):
            # Should not raise
            refresh_runtime_after_run("/tmp", MagicMock(), MagicMock(), MagicMock(), "Edit", {})

    def test_session_none_no_habit_calls_lightweight_directly(self):
        """EP3: session=None, habit disabled => lightweight called with exact kwargs."""
        cfg = MagicMock()
        cfg.habit_mode_enabled = False
        cfg.session_memory = True
        fake_mesh = MagicMock()
        with patch("lintgate.hooks.runtime_state.refresh_runtime_state_lightweight") as fn:
            refresh_runtime_after_run("/proj", None, cfg, fake_mesh, "Write", {"path": "/a"})
        fn.assert_called_once()
        call_kwargs = fn.call_args[1]
        assert call_kwargs["mesh_result"] is fake_mesh
        assert call_kwargs["tool_name"] == "Write"
        assert call_kwargs["tool_input"] == {"path": "/a"}
        assert call_kwargs["trigger"] == "lint_complete"

    def test_session_none_habit_enabled_session_memory_true_falls_through(self):
        """EP4: habit enabled but session_memory=True => lightweight directly (else branch)."""
        cfg = MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = True  # This makes the inner `if` false
        with patch("lintgate.hooks.runtime_state.refresh_runtime_state_lightweight") as fn:
            refresh_runtime_after_run("/tmp", None, cfg, MagicMock(), "Edit", {})
        fn.assert_called_once()

    def test_session_none_habit_full_path_scheduler_passed_through(self):
        """EP5: habit enabled, session_memory=False => full habit path with scheduler."""
        cfg = MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        scheduler_input = {"next_write": 100}
        extras = {
            "write_scheduler": scheduler_input,
            "token_tracker": {"total": 500},
            "config_overrides": {"x": 1},
            "habit_last_snapshot": {"ts": 99},
        }
        fake_habit_state = MagicMock()
        fake_action_ring = [{"act": 1}]
        updated_scheduler = {"next_write": 200, "new_field": True}

        with (
            patch(
                "lintgate.hooks.runtime_state.refresh_runtime_state_lightweight",
                return_value=updated_scheduler,
            ) as refresh_fn,
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value=extras,
            ),
            patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(fake_habit_state, fake_action_ring),
            ),
            patch("lintgate.habit_mode.save_habit_state_standalone") as save_fn,
        ):
            refresh_runtime_after_run("/proj", None, cfg, MagicMock(), "Edit", {})

        # Lightweight called with scheduler_dict from extras
        refresh_kwargs = refresh_fn.call_args[1]
        assert refresh_kwargs["scheduler_dict"] is scheduler_input

        # save called with exact args including updated scheduler
        save_fn.assert_called_once()
        save_kwargs = save_fn.call_args[1]
        save_args = save_fn.call_args[0]
        assert save_args[0] == "/proj"
        assert save_args[1] is fake_habit_state
        assert save_args[2] is fake_action_ring
        assert save_kwargs["tracker_dict"] == {"total": 500}
        assert save_kwargs["config_overrides"] == {"x": 1}
        assert save_kwargs["last_snapshot"] == {"ts": 99}
        assert save_kwargs["scheduler_dict"] is updated_scheduler

    def test_session_none_habit_non_dict_return_skips_save(self):
        """EP6: lightweight returns non-dict => load/save habit state NOT called."""
        cfg = MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        with (
            patch(
                "lintgate.hooks.runtime_state.refresh_runtime_state_lightweight",
                return_value="not-dict",
            ),
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={"write_scheduler": {"s": 1}},
            ),
            patch("lintgate.habit_mode.load_habit_state_standalone") as load_fn,
            patch("lintgate.habit_mode.save_habit_state_standalone") as save_fn,
        ):
            refresh_runtime_after_run("/tmp", None, cfg, MagicMock(), "Edit", {})
        load_fn.assert_not_called()
        save_fn.assert_not_called()

    def test_session_none_habit_extras_non_dict_scheduler_passes_none(self):
        """EP7: write_scheduler in extras is not a dict => scheduler_dict stays None."""
        cfg = MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        with (
            patch(
                "lintgate.hooks.runtime_state.refresh_runtime_state_lightweight",
                return_value={"result": True},
            ) as refresh_fn,
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={"write_scheduler": "not-a-dict"},
            ),
            patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(MagicMock(), []),
            ),
            patch("lintgate.habit_mode.save_habit_state_standalone") as save_fn,
        ):
            refresh_runtime_after_run("/tmp", None, cfg, MagicMock(), "Edit", {})
        # scheduler_dict passed as None since extras value wasn't a dict
        refresh_kwargs = refresh_fn.call_args[1]
        assert refresh_kwargs["scheduler_dict"] is None
        # But save still happens because return was a dict
        save_fn.assert_called_once()

    def test_session_none_habit_extras_non_dict_values_pass_none(self):
        """EP8: extras with non-dict tracker/overrides/snapshot pass None to save."""
        cfg = MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        extras = {
            "write_scheduler": {"valid": True},
            "token_tracker": 42,  # not dict
            "config_overrides": "string",  # not dict
            "habit_last_snapshot": [1, 2],  # not dict
        }
        with (
            patch(
                "lintgate.hooks.runtime_state.refresh_runtime_state_lightweight",
                return_value={"ok": True},
            ),
            patch("lintgate.habit_mode.load_standalone_extras", return_value=extras),
            patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(MagicMock(), []),
            ),
            patch("lintgate.habit_mode.save_habit_state_standalone") as save_fn,
        ):
            refresh_runtime_after_run("/tmp", None, cfg, MagicMock(), "Edit", {})
        save_kwargs = save_fn.call_args[1]
        assert save_kwargs["tracker_dict"] is None
        assert save_kwargs["config_overrides"] is None
        assert save_kwargs["last_snapshot"] is None
