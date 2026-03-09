"""Tests for the Global Behavior Profile — cross-session learning layer."""

from __future__ import annotations

import time
from unittest import mock

import pytest

from lintgate.controlplane.global_behavior_profile import (
    _GLOBAL_ADJ_CAP,
    DEFAULT_ALPHA,
    MIN_SAMPLE_SIZE,
    GlobalBehaviorProfile,
    apply_session_delta,
    compute_alpha,
    compute_bias_adjustments,
    load_global_profile,
    save_global_profile,
)

# ── TestGlobalBehaviorProfile (dataclass basics) ────────────────────────


class TestGlobalBehaviorProfile:
    def test_fresh_defaults(self):
        p = GlobalBehaviorProfile()
        assert p.session_count == 0
        assert p.signal_priors == {}
        assert p.intent_ratios == {}
        assert p.nudge_outcomes == {}
        assert p.computed_bias_adjustments == {}
        assert p.last_session_id == ""

    def test_to_dict_from_dict_roundtrip(self):
        p = GlobalBehaviorProfile(
            session_count=5,
            last_session_id="abc123",
            seen_session_ids=["abc123", "def456"],
            signal_priors={"verification_debt": {"total_firings": 10, "sessions_present": 3}},
            intent_ratios={"execute": 100, "verify": 50},
            nudge_outcomes={"verification_debt": {"accepted": 3, "ignored": 1}},
            computed_bias_adjustments={"verification_debt": 0.05},
        )
        d = p.to_dict()
        p2 = GlobalBehaviorProfile.from_dict(d)
        assert p2.session_count == 5
        assert p2.last_session_id == "abc123"
        assert p2.seen_session_ids == ["abc123", "def456"]
        assert p2.signal_priors["verification_debt"]["total_firings"] == 10
        assert p2.intent_ratios["execute"] == 100
        assert p2.nudge_outcomes["verification_debt"]["accepted"] == 3
        assert p2.computed_bias_adjustments["verification_debt"] == 0.05

    def test_from_dict_backward_compat(self):
        """Old profiles without new fields should get safe defaults."""
        p = GlobalBehaviorProfile.from_dict({})
        assert p.session_count == 0
        assert p.last_session_id == ""
        assert p.signal_priors == {}
        assert p.nudge_outcomes == {}

    def test_from_dict_partial_data(self):
        p = GlobalBehaviorProfile.from_dict({"session_count": 7, "intent_ratios": {"modify": 42}})
        assert p.session_count == 7
        assert p.intent_ratios == {"modify": 42}
        assert p.signal_priors == {}


# ── TestComputeAlpha ────────────────────────────────────────────────────


class TestComputeAlpha:
    def test_alpha_at_zero_events(self):
        alpha = compute_alpha(0, alpha_initial=0.6, decay_horizon=50)
        assert alpha == pytest.approx(0.6)

    def test_alpha_decays_linearly(self):
        alpha = compute_alpha(25, alpha_initial=0.6, decay_horizon=50)
        assert alpha == pytest.approx(0.3)

    def test_alpha_zero_at_horizon(self):
        alpha = compute_alpha(50, alpha_initial=0.6, decay_horizon=50)
        assert alpha == pytest.approx(0.0)

    def test_alpha_zero_beyond_horizon(self):
        alpha = compute_alpha(100, alpha_initial=0.6, decay_horizon=50)
        assert alpha == pytest.approx(0.0)

    def test_alpha_zero_when_horizon_zero(self):
        alpha = compute_alpha(10, alpha_initial=0.6, decay_horizon=0)
        assert alpha == 0.0

    def test_alpha_with_defaults(self):
        alpha = compute_alpha(0)
        assert alpha == pytest.approx(DEFAULT_ALPHA)


# ── TestComputeBiasAdjustments ──────────────────────────────────────────


class TestComputeBiasAdjustments:
    def test_returns_empty_below_min_sample_size(self):
        p = GlobalBehaviorProfile(session_count=MIN_SAMPLE_SIZE - 1)
        p.signal_priors = {"verification_debt": {"total_firings": 10, "sessions_present": 2}}
        assert compute_bias_adjustments(p) == {}

    def test_high_frequency_signal_gets_boost(self):
        p = GlobalBehaviorProfile(session_count=5)
        p.signal_priors = {"verification_debt": {"total_firings": 12, "sessions_present": 4}}
        adj = compute_bias_adjustments(p)
        # freq = 12/5 = 2.4 >= 2.0 → +0.05
        assert adj["verification_debt"] == pytest.approx(0.05)

    def test_moderate_frequency_gets_small_boost(self):
        p = GlobalBehaviorProfile(session_count=5)
        p.signal_priors = {"stale_model": {"total_firings": 6, "sessions_present": 3}}
        adj = compute_bias_adjustments(p)
        # freq = 6/5 = 1.2 >= 1.0 → +0.02
        assert adj["stale_model"] == pytest.approx(0.02)

    def test_high_nudge_acceptance_adds_boost(self):
        p = GlobalBehaviorProfile(session_count=5)
        p.signal_priors = {"verification_debt": {"total_firings": 12, "sessions_present": 4}}
        p.nudge_outcomes = {"verification_debt": {"accepted": 4, "ignored": 1}}
        adj = compute_bias_adjustments(p)
        # freq boost = +0.05, nudge acceptance (4/5 = 0.8 >= 0.6) = +0.03 → total 0.08
        assert adj["verification_debt"] == pytest.approx(0.08)

    def test_low_nudge_acceptance_dampens(self):
        p = GlobalBehaviorProfile(session_count=5)
        p.signal_priors = {"failure_amnesia": {"total_firings": 12, "sessions_present": 4}}
        p.nudge_outcomes = {"failure_amnesia": {"accepted": 0, "ignored": 5}}
        adj = compute_bias_adjustments(p)
        # freq boost = +0.05, nudge rejection (0/5 = 0.0 <= 0.2) = -0.05 → total 0.0
        assert adj["failure_amnesia"] == pytest.approx(0.0)

    def test_clamp_to_global_adj_cap(self):
        """Even with extreme values, adjustments stay within bounds."""
        p = GlobalBehaviorProfile(session_count=3)
        p.signal_priors = {"x": {"total_firings": 100, "sessions_present": 3}}
        p.nudge_outcomes = {"x": {"accepted": 100, "ignored": 0}}
        adj = compute_bias_adjustments(p)
        # freq=33.3 → +0.05, nudge=1.0 → +0.03 = 0.08 (within cap)
        assert -_GLOBAL_ADJ_CAP <= adj["x"] <= _GLOBAL_ADJ_CAP

    def test_zero_sessions_present_skipped(self):
        p = GlobalBehaviorProfile(session_count=5)
        p.signal_priors = {"weird": {"total_firings": 0, "sessions_present": 0}}
        adj = compute_bias_adjustments(p)
        assert "weird" not in adj

    def test_nudge_outcomes_below_threshold_ignored(self):
        """Need >= 3 nudge outcomes to factor in acceptance rate."""
        p = GlobalBehaviorProfile(session_count=5)
        p.signal_priors = {"verification_debt": {"total_firings": 12, "sessions_present": 4}}
        p.nudge_outcomes = {"verification_debt": {"accepted": 0, "ignored": 2}}
        adj = compute_bias_adjustments(p)
        # Only frequency boost, nudge not counted (total_nudges=2 < 3)
        assert adj["verification_debt"] == pytest.approx(0.05)


# ── TestApplySessionDelta ───────────────────────────────────────────────


class TestApplySessionDelta:
    def test_merges_signal_fire_counts(self):
        p = GlobalBehaviorProfile()
        apply_session_delta(p, {"signal_fire_counts": {"approach_cycling": 3}}, session_id="s1")
        assert p.signal_priors["approach_cycling"]["total_firings"] == 3
        assert p.signal_priors["approach_cycling"]["sessions_present"] == 1

    def test_merges_intent_counts(self):
        p = GlobalBehaviorProfile()
        apply_session_delta(p, {"intent_summary": {"execute": 10, "verify": 5}}, session_id="s1")
        assert p.intent_ratios["execute"] == 10
        assert p.intent_ratios["verify"] == 5
        # Second session adds
        apply_session_delta(p, {"intent_summary": {"execute": 8}}, session_id="s2")
        assert p.intent_ratios["execute"] == 18

    def test_merges_nudge_outcomes(self):
        p = GlobalBehaviorProfile()
        apply_session_delta(
            p,
            {"nudge_outcomes": {"verification_debt": "accepted"}},
            session_id="s1",
        )
        assert p.nudge_outcomes["verification_debt"]["accepted"] == 1
        assert p.nudge_outcomes["verification_debt"]["ignored"] == 0

    def test_session_count_increments_on_new_session(self):
        p = GlobalBehaviorProfile()
        apply_session_delta(p, {}, session_id="s1")
        assert p.session_count == 1
        # Same session ID → no increment
        apply_session_delta(p, {}, session_id="s1")
        assert p.session_count == 1
        # New session → increment
        apply_session_delta(p, {}, session_id="s2")
        assert p.session_count == 2

    def test_recomputes_bias_adjustments(self):
        p = GlobalBehaviorProfile(session_count=MIN_SAMPLE_SIZE - 1)
        # Just below threshold
        apply_session_delta(
            p,
            {"signal_fire_counts": {"verification_debt": 5}},
            session_id="s_final",
        )
        # Now at MIN_SAMPLE_SIZE → adjustments should be computed
        assert p.session_count == MIN_SAMPLE_SIZE
        # With 5 firings across MIN_SAMPLE_SIZE sessions, freq ≥ 1.0
        assert "verification_debt" in p.computed_bias_adjustments

    def test_sessions_present_not_incremented_for_same_session(self):
        p = GlobalBehaviorProfile()
        apply_session_delta(p, {"signal_fire_counts": {"x": 2}}, session_id="s1")
        apply_session_delta(p, {"signal_fire_counts": {"x": 3}}, session_id="s1")
        assert p.signal_priors["x"]["total_firings"] == 5
        assert p.signal_priors["x"]["sessions_present"] == 1

    def test_interleaved_session_ids_do_not_recount_seen_sessions(self):
        p = GlobalBehaviorProfile()
        apply_session_delta(p, {"signal_fire_counts": {"x": 1}}, session_id="sA")
        apply_session_delta(p, {"signal_fire_counts": {"x": 1}}, session_id="sB")
        apply_session_delta(p, {"signal_fire_counts": {"x": 1}}, session_id="sA")
        assert p.session_count == 2
        assert p.signal_priors["x"]["sessions_present"] == 2
        assert set(p.seen_session_ids) == {"sA", "sB"}


# ── TestLoadSaveProfile ─────────────────────────────────────────────────


class TestLoadSaveProfile:
    def test_save_and_load_roundtrip(self, tmp_path):
        profile_path = tmp_path / "test_profile.json"
        p = GlobalBehaviorProfile(session_count=3, intent_ratios={"execute": 42})

        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH",
            profile_path,
        ):
            save_global_profile(p)
            loaded = load_global_profile()
            assert loaded.session_count == 3
            assert loaded.intent_ratios["execute"] == 42

    def test_returns_fresh_if_absent(self, tmp_path):
        profile_path = tmp_path / "nonexistent.json"
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH",
            profile_path,
        ):
            loaded = load_global_profile()
            assert loaded.session_count == 0

    def test_returns_fresh_if_corrupt(self, tmp_path):
        profile_path = tmp_path / "corrupt.json"
        profile_path.write_text("not json{{{")
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH",
            profile_path,
        ):
            loaded = load_global_profile()
            assert loaded.session_count == 0


# ── TestTTLPruning ──────────────────────────────────────────────────────


class TestTTLPruning:
    def test_prunes_old_profile(self, tmp_path):
        profile_path = tmp_path / "old_profile.json"
        old_time = time.time() - (100 * 86400)  # 100 days ago
        p = GlobalBehaviorProfile(session_count=10, updated_at=old_time)
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH",
            profile_path,
        ):
            save_global_profile(p)
            loaded = load_global_profile(ttl_days=90)
            # Should return fresh profile since 100 > 90 days
            assert loaded.session_count == 0

    def test_keeps_recent_profile(self, tmp_path):
        profile_path = tmp_path / "recent_profile.json"
        p = GlobalBehaviorProfile(session_count=10, updated_at=time.time())
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH",
            profile_path,
        ):
            save_global_profile(p)
            loaded = load_global_profile(ttl_days=90)
            assert loaded.session_count == 10

    def test_ttl_zero_disables_pruning(self, tmp_path):
        """When ttl_days <= 0, no pruning occurs."""
        profile_path = tmp_path / "old_profile.json"
        old_time = time.time() - (365 * 86400)  # 1 year ago
        p = GlobalBehaviorProfile(session_count=10, updated_at=old_time)
        with mock.patch(
            "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH",
            profile_path,
        ):
            save_global_profile(p)
            loaded = load_global_profile(ttl_days=0)
            assert loaded.session_count == 10
