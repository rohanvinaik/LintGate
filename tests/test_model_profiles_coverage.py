"""Coverage-targeted tests for lintgate/controlplane/model_profiles.py.

Targets uncovered branches and functions not exercised by existing test files:
- _lintgate_home (LINTGATE_HOME env var path, default fallback)
- resolve_model_key (additional provider prefixes: o1, o4, llama, mistral, codestral, command)
- ModelProfile.from_dict (missing keys fall to defaults)
- ModelProfile.is_stale (boundary conditions around stale_after_days)
- ModelProfile.is_usable (boundary at exact min_confidence)
- ModelProfileStore.from_dict (edge cases)
- load_profiles (non-dict JSON, OSError)
- save_profiles (OSError on write)
- get_profile (confidence decay triggers persist)
- reset_profile (unresolvable key)
- apply_confidence_decay (grace period, zero confidence, deep decay)
- apply_telemetry_update (negative fires, multiple signals, boundary event_count)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest import mock

from lintgate.controlplane.model_profiles import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_STALENESS_DAYS,
    PROFILE_FORMAT_VERSION,
    ModelProfile,
    ModelProfileStore,
    _EMA_ALPHA,
    _lintgate_home,
    apply_confidence_decay,
    apply_telemetry_update,
    get_profile,
    load_profiles,
    reset_profile,
    resolve_model_key,
    save_profiles,
    upsert_profile,
)


# ── _lintgate_home ───────────────────────────────────────────────────


class TestLintgateHome:
    def test_default_path(self, monkeypatch: Any) -> None:
        """Without LINTGATE_HOME, falls back to ~/.lintgate."""
        monkeypatch.delenv("LINTGATE_HOME", raising=False)
        result = _lintgate_home()
        assert result == Path.home() / ".lintgate"

    def test_env_override(self, monkeypatch: Any, tmp_path: Path) -> None:
        """LINTGATE_HOME env var overrides the default."""
        monkeypatch.setenv("LINTGATE_HOME", str(tmp_path / "custom"))
        result = _lintgate_home()
        assert result == (tmp_path / "custom").resolve()

    def test_env_with_tilde(self, monkeypatch: Any) -> None:
        """Tilde in LINTGATE_HOME is expanded."""
        monkeypatch.setenv("LINTGATE_HOME", "~/my_lintgate")
        result = _lintgate_home()
        assert "~" not in str(result)
        assert result == Path("~/my_lintgate").expanduser().resolve()


# ── resolve_model_key (additional providers) ─────────────────────────


class TestResolveModelKeyExtended:
    def test_o1_prefix(self) -> None:
        assert resolve_model_key("o1-preview") == "openai:o1-preview"

    def test_o4_prefix(self) -> None:
        assert resolve_model_key("o4-mini") == "openai:o4-mini"

    def test_llama_prefix(self) -> None:
        assert resolve_model_key("llama-3.1-70b") == "meta:llama-3.1-70b"

    def test_mistral_prefix(self) -> None:
        assert resolve_model_key("mistral-large") == "mistralai:mistral-large"

    def test_codestral_prefix(self) -> None:
        assert resolve_model_key("codestral-latest") == "mistralai:codestral-latest"

    def test_command_prefix(self) -> None:
        assert resolve_model_key("command-r-plus") == "cohere:command-r-plus"

    def test_whitespace_stripped(self) -> None:
        assert resolve_model_key("  claude-opus-4  ") == "anthropic:claude-opus-4"

    def test_already_canonical_with_colon(self) -> None:
        assert resolve_model_key("custom:my-model") == "custom:my-model"

    def test_unknown_model_no_prefix_match(self) -> None:
        assert resolve_model_key("unknown-model-xyz") is None

    def test_none_input(self) -> None:
        assert resolve_model_key(None) is None

    def test_empty_string_input(self) -> None:
        assert resolve_model_key("") is None

    def test_whitespace_only_input(self) -> None:
        assert resolve_model_key("   ") is None


# ── ModelProfile ─────────────────────────────────────────────────────


class TestModelProfileExtended:
    def test_from_dict_missing_keys_use_defaults(self) -> None:
        """from_dict with empty dict produces valid defaults."""
        p = ModelProfile.from_dict({})
        assert p.model_key == ""
        assert p.probe_version == 1
        assert p.probe_runs == 0
        assert p.confidence == 0.0
        assert p.signal_risk == {}
        assert p.custom_anti_patterns == []
        assert p.custom_dispositions == []
        assert p.telemetry_samples == 0
        assert p.stale_after_days == DEFAULT_STALENESS_DAYS

    def test_to_dict_roundtrip_preserves_all_fields(self) -> None:
        p = ModelProfile(
            model_key="test:m",
            confidence=0.85,
            probe_runs=3,
            probe_version=2,
            signal_risk={"a": 0.5, "b": 0.2},
            custom_anti_patterns=["no cycling"],
            custom_dispositions=["verify first"],
            telemetry_samples=7,
            stale_after_days=60,
        )
        d = p.to_dict()
        p2 = ModelProfile.from_dict(d)
        assert p2.model_key == "test:m"
        assert p2.confidence == 0.85
        assert p2.probe_runs == 3
        assert p2.probe_version == 2
        assert p2.signal_risk == {"a": 0.5, "b": 0.2}
        assert p2.custom_anti_patterns == ["no cycling"]
        assert p2.custom_dispositions == ["verify first"]
        assert p2.telemetry_samples == 7
        assert p2.stale_after_days == 60

    def test_is_stale_boundary_just_under_threshold(self) -> None:
        """Profile just under stale_after_days boundary is not stale (> check)."""
        p = ModelProfile(
            updated_at=time.time() - ((DEFAULT_STALENESS_DAYS - 0.1) * 86400),
            stale_after_days=DEFAULT_STALENESS_DAYS,
        )
        assert not p.is_stale()

    def test_is_stale_custom_threshold(self) -> None:
        p = ModelProfile(
            updated_at=time.time() - (10 * 86400),
            stale_after_days=5,
        )
        assert p.is_stale()

    def test_is_usable_at_exact_min_confidence(self) -> None:
        """Confidence exactly at min_confidence is usable (>= check)."""
        p = ModelProfile(
            confidence=DEFAULT_MIN_CONFIDENCE,
            updated_at=time.time(),
        )
        assert p.is_usable()

    def test_is_usable_just_below_min_confidence(self) -> None:
        p = ModelProfile(
            confidence=DEFAULT_MIN_CONFIDENCE - 0.01,
            updated_at=time.time(),
        )
        assert not p.is_usable()


# ── ModelProfileStore ────────────────────────────────────────────────


class TestModelProfileStoreExtended:
    def test_from_dict_missing_profiles_key(self) -> None:
        """from_dict with no 'profiles' key returns empty store."""
        store = ModelProfileStore.from_dict({"format_version": 1})
        assert len(store.profiles) == 0

    def test_from_dict_skips_non_dict_entries(self) -> None:
        store = ModelProfileStore.from_dict({
            "format_version": 1,
            "profiles": {
                "good": {"model_key": "test:good", "confidence": 0.9},
                "bad_string": "not-a-dict",
                "bad_int": 42,
                "bad_list": [1, 2, 3],
            },
        })
        assert "good" in store.profiles
        assert "bad_string" not in store.profiles
        assert "bad_int" not in store.profiles
        assert "bad_list" not in store.profiles

    def test_to_dict_format_version(self) -> None:
        store = ModelProfileStore(format_version=2)
        d = store.to_dict()
        assert d["format_version"] == 2

    def test_default_format_version(self) -> None:
        store = ModelProfileStore()
        assert store.format_version == PROFILE_FORMAT_VERSION


# ── Persistence ──────────────────────────────────────────────────────


class TestPersistenceExtended:
    def test_load_profiles_non_dict_json(self, tmp_path: Path) -> None:
        """JSON file with a non-dict top-level (e.g., a list) returns empty store."""
        (tmp_path / "model_profiles.json").write_text("[1, 2, 3]")
        with mock.patch(
            "lintgate.controlplane.model_profiles._lintgate_home",
            return_value=tmp_path,
        ):
            store = load_profiles()
            assert len(store.profiles) == 0

    def test_load_profiles_oserror(self, tmp_path: Path) -> None:
        """OSError during read returns empty store."""
        with mock.patch(
            "lintgate.controlplane.model_profiles._lintgate_home",
            return_value=tmp_path,
        ):
            # Create a directory where the file should be to cause an error
            profile_path = tmp_path / "model_profiles.json"
            profile_path.mkdir()
            store = load_profiles()
            assert len(store.profiles) == 0

    def test_save_profiles_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        with mock.patch(
            "lintgate.controlplane.model_profiles._lintgate_home",
            return_value=nested,
        ):
            store = ModelProfileStore()
            store.profiles["test:m"] = ModelProfile(model_key="test:m")
            save_profiles(store)
            assert (nested / "model_profiles.json").exists()

    def test_save_profiles_oserror_nonfatal(self, tmp_path: Path) -> None:
        """OSError during write is silently swallowed."""
        with mock.patch(
            "lintgate.controlplane.model_profiles._lintgate_home",
            return_value=tmp_path,
        ):
            with mock.patch("builtins.open", side_effect=OSError("disk full")):
                # Should not raise
                save_profiles(ModelProfileStore())

    def test_get_profile_decay_persisted(self, tmp_path: Path) -> None:
        """get_profile applies decay and persists if confidence changed."""
        old_time = time.time() - (20 * 86400)  # 20 days old
        with mock.patch(
            "lintgate.controlplane.model_profiles._lintgate_home",
            return_value=tmp_path,
        ):
            p = ModelProfile(
                model_key="anthropic:claude-opus-4",
                confidence=0.9,
                updated_at=old_time,
                created_at=old_time,
            )
            upsert_profile(p)

            loaded = get_profile("claude-opus-4")
            assert loaded is not None
            # After 20 days with 15-day half-life, confidence should decay
            assert loaded.confidence < 0.9
            assert loaded.confidence > 0.0

    def test_reset_profile_unresolvable_key(self, tmp_path: Path) -> None:
        """reset_profile with unresolvable key returns False."""
        with mock.patch(
            "lintgate.controlplane.model_profiles._lintgate_home",
            return_value=tmp_path,
        ):
            assert reset_profile("totally-unknown-model") is False

    def test_upsert_profile_overwrites(self, tmp_path: Path) -> None:
        """upsert_profile replaces an existing profile."""
        with mock.patch(
            "lintgate.controlplane.model_profiles._lintgate_home",
            return_value=tmp_path,
        ):
            p1 = ModelProfile(model_key="anthropic:claude-opus-4", confidence=0.5)
            upsert_profile(p1)
            p2 = ModelProfile(model_key="anthropic:claude-opus-4", confidence=0.99)
            upsert_profile(p2)
            loaded = get_profile("claude-opus-4")
            assert loaded is not None
            assert loaded.confidence == 0.99


# ── apply_confidence_decay ───────────────────────────────────────────


class TestApplyConfidenceDecay:
    def test_no_decay_within_grace_period(self) -> None:
        """Within 24 hours, no decay applied."""
        p = ModelProfile(
            confidence=0.8,
            updated_at=time.time() - (12 * 3600),  # 12 hours ago
        )
        original = apply_confidence_decay(p)
        assert original == 0.8
        assert p.confidence == 0.8

    def test_zero_confidence_no_decay(self) -> None:
        """Zero confidence returns immediately, no decay formula."""
        p = ModelProfile(
            confidence=0.0,
            updated_at=time.time() - (100 * 86400),
        )
        original = apply_confidence_decay(p)
        assert original == 0.0
        assert p.confidence == 0.0

    def test_negative_confidence_no_decay(self) -> None:
        """Negative confidence (<= 0.0 guard) returns immediately."""
        p = ModelProfile(confidence=-0.1, updated_at=time.time() - (50 * 86400))
        original = apply_confidence_decay(p)
        assert original == -0.1

    def test_decay_after_grace_period(self) -> None:
        """After 24 hours, exponential decay is applied."""
        p = ModelProfile(
            confidence=1.0,
            updated_at=time.time() - (48 * 3600),  # 2 days ago
        )
        original = apply_confidence_decay(p)
        assert original == 1.0
        assert p.confidence < 1.0
        assert p.confidence > 0.0

    def test_decay_at_half_life(self) -> None:
        """At exactly 15 days, confidence should halve (approximately)."""
        p = ModelProfile(
            confidence=1.0,
            updated_at=time.time() - (15 * 86400),
        )
        apply_confidence_decay(p)
        # 2^(-15/15) = 0.5
        assert abs(p.confidence - 0.5) < 0.01

    def test_decay_floor_at_zero(self) -> None:
        """Very old profile decays to 0.0 floor."""
        p = ModelProfile(
            confidence=0.001,
            updated_at=time.time() - (365 * 86400),  # 1 year old
        )
        apply_confidence_decay(p)
        assert p.confidence == 0.0

    def test_decay_just_inside_grace_boundary(self) -> None:
        """At 23 hours (inside 24-hour grace), no decay applied."""
        p = ModelProfile(
            confidence=0.8,
            updated_at=time.time() - (23 * 3600),
        )
        original = apply_confidence_decay(p)
        assert original == 0.8
        assert p.confidence == 0.8


# ── apply_telemetry_update ───────────────────────────────────────────


class TestApplyTelemetryUpdateExtended:
    def test_below_min_events_no_update(self) -> None:
        """event_count < 10 skips update entirely."""
        p = ModelProfile(model_key="t:m", signal_risk={"x": 0.5})
        apply_telemetry_update(p, {"x": 5}, event_count=9)
        assert p.signal_risk["x"] == 0.5
        assert p.telemetry_samples == 0

    def test_exactly_10_events_updates(self) -> None:
        """event_count == 10 is NOT below the threshold (< 10 guard)."""
        p = ModelProfile(model_key="t:m", signal_risk={"x": 0.5})
        apply_telemetry_update(p, {"x": 5}, event_count=10)
        assert p.telemetry_samples == 1
        assert p.signal_risk["x"] != 0.5

    def test_negative_fires_skipped(self) -> None:
        """Fires <= 0 are skipped for each signal."""
        p = ModelProfile(model_key="t:m", signal_risk={"x": 0.5})
        apply_telemetry_update(p, {"x": -1}, event_count=20)
        assert p.signal_risk["x"] == 0.5
        assert p.telemetry_samples == 0

    def test_multiple_signals_updated(self) -> None:
        """Multiple signals are updated in a single call."""
        p = ModelProfile(
            model_key="t:m",
            signal_risk={"a": 0.3, "b": 0.7},
        )
        apply_telemetry_update(p, {"a": 2, "b": 3}, event_count=20)
        assert p.telemetry_samples == 1
        assert p.signal_risk["a"] != 0.3
        assert p.signal_risk["b"] != 0.7

    def test_new_signal_created(self) -> None:
        """Signal not previously in signal_risk is added with default 0.0 base."""
        p = ModelProfile(model_key="t:m", signal_risk={})
        apply_telemetry_update(p, {"new_sig": 1}, event_count=20)
        assert "new_sig" in p.signal_risk
        # EMA: 0.15 * (1/20*10=0.5) + 0.85 * 0.0 = 0.075
        assert abs(p.signal_risk["new_sig"] - 0.075) < 0.01

    def test_ema_formula_correctness(self) -> None:
        """Verify EMA calculation matches expected value."""
        p = ModelProfile(model_key="t:m", signal_risk={"x": 0.5})
        # observed_risk = min(1.0, 3/30*10) = 1.0
        # new = 0.15 * 1.0 + 0.85 * 0.5 = 0.575
        apply_telemetry_update(p, {"x": 3}, event_count=30)
        assert abs(p.signal_risk["x"] - 0.575) < 0.001

    def test_observed_risk_capped_at_1(self) -> None:
        """Very high fire counts are capped at observed_risk=1.0."""
        p = ModelProfile(model_key="t:m", signal_risk={"x": 0.0})
        apply_telemetry_update(p, {"x": 1000}, event_count=10)
        # observed_risk = min(1.0, 1000/10*10) = 1.0
        # new = 0.15 * 1.0 + 0.85 * 0.0 = 0.15
        assert abs(p.signal_risk["x"] - 0.15) < 0.001

    def test_timestamps_updated(self) -> None:
        """last_seen_at and updated_at are refreshed on successful update."""
        before = time.time()
        p = ModelProfile(
            model_key="t:m",
            signal_risk={"x": 0.5},
            last_seen_at=before - 1000,
            updated_at=before - 1000,
        )
        apply_telemetry_update(p, {"x": 2}, event_count=20)
        assert p.last_seen_at >= before
        assert p.updated_at >= before

    def test_mixed_positive_and_zero_fires(self) -> None:
        """Only positive fires cause updates; zero fires are skipped."""
        p = ModelProfile(
            model_key="t:m",
            signal_risk={"a": 0.5, "b": 0.5},
        )
        apply_telemetry_update(p, {"a": 3, "b": 0}, event_count=20)
        assert p.telemetry_samples == 1  # updated_any is True from 'a'
        assert p.signal_risk["a"] != 0.5
        assert p.signal_risk["b"] == 0.5

    def test_result_clamped_to_bounds(self) -> None:
        """Signal risk values are clamped to [0.0, 1.0]."""
        p = ModelProfile(model_key="t:m", signal_risk={"x": 0.99})
        apply_telemetry_update(p, {"x": 500}, event_count=10)
        assert p.signal_risk["x"] <= 1.0
        assert p.signal_risk["x"] >= 0.0
