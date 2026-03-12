"""Tests for Model Profile System."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

from lintgate.controlplane.model.profiles import (
    DEFAULT_MIN_CONFIDENCE,
    ModelProfile,
    ModelProfileStore,
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


class TestResolveModelKey:
    def test_already_canonical(self):
        assert resolve_model_key("anthropic:claude-opus-4") == "anthropic:claude-opus-4"

    def test_claude_prefix(self):
        assert resolve_model_key("claude-opus-4") == "anthropic:claude-opus-4"
        assert resolve_model_key("claude-sonnet-4") == "anthropic:claude-sonnet-4"
        assert resolve_model_key("claude-haiku-4.5") == "anthropic:claude-haiku-4.5"

    def test_gpt_prefix(self):
        assert resolve_model_key("gpt-4o") == "openai:gpt-4o"
        assert resolve_model_key("gpt-4o-mini") == "openai:gpt-4o-mini"

    def test_o3_prefix(self):
        assert resolve_model_key("o3-mini") == "openai:o3-mini"

    def test_gemini_prefix(self):
        assert resolve_model_key("gemini-2.0-flash") == "google:gemini-2.0-flash"

    def test_deepseek_prefix(self):
        assert resolve_model_key("deepseek-coder") == "deepseek:deepseek-coder"

    def test_unknown_model_returns_none(self):
        assert resolve_model_key("some-new-model") is None

    def test_none_returns_none(self):
        assert resolve_model_key(None) is None

    def test_empty_returns_none(self):
        assert resolve_model_key("") is None
        assert resolve_model_key("  ") is None

    def test_case_normalization(self):
        assert resolve_model_key("Claude-Opus-4") == "anthropic:claude-opus-4"
        assert resolve_model_key("GPT-4o") == "openai:gpt-4o"


class TestModelProfile:
    def test_fresh_defaults(self):
        p = ModelProfile()
        assert p.model_key == ""
        assert p.confidence == 0.0
        assert p.signal_risk == {}
        assert p.telemetry_samples == 0
        assert p.probe_runs == 0

    def test_roundtrip(self):
        p = ModelProfile(
            model_key="anthropic:claude-opus-4",
            confidence=0.75,
            signal_risk={"approach_cycling": 0.3, "verification_debt": 0.1},
            custom_anti_patterns=["Do not cycle approaches."],
        )
        d = p.to_dict()
        p2 = ModelProfile.from_dict(d)
        assert p2.model_key == "anthropic:claude-opus-4"
        assert p2.confidence == 0.75
        assert p2.signal_risk["approach_cycling"] == 0.3
        assert len(p2.custom_anti_patterns) == 1

    def test_is_stale_when_old(self):
        p = ModelProfile(updated_at=time.time() - (31 * 86400))
        assert p.is_stale()

    def test_is_not_stale_when_fresh(self):
        p = ModelProfile(updated_at=time.time())
        assert not p.is_stale()

    def test_is_usable_when_confident_and_fresh(self):
        p = ModelProfile(confidence=0.75, updated_at=time.time())
        assert p.is_usable()

    def test_not_usable_when_low_confidence(self):
        p = ModelProfile(confidence=0.3, updated_at=time.time())
        assert not p.is_usable()

    def test_not_usable_when_stale(self):
        p = ModelProfile(
            confidence=0.75,
            updated_at=time.time() - (31 * 86400),
        )
        assert not p.is_usable()

    def test_custom_min_confidence(self):
        p = ModelProfile(confidence=0.4, updated_at=time.time())
        assert not p.is_usable(min_confidence=0.55)
        assert p.is_usable(min_confidence=0.3)


class TestModelProfileStore:
    def test_empty_store(self):
        store = ModelProfileStore()
        assert len(store.profiles) == 0
        assert store.format_version == 1

    def test_roundtrip(self):
        store = ModelProfileStore()
        store.profiles["test:model"] = ModelProfile(model_key="test:model", confidence=0.8)
        d = store.to_dict()
        store2 = ModelProfileStore.from_dict(d)
        assert "test:model" in store2.profiles
        assert store2.profiles["test:model"].confidence == 0.8

    def test_from_dict_handles_bad_profiles(self):
        """Non-dict profile entries should be skipped."""
        store = ModelProfileStore.from_dict(
            {"format_version": 1, "profiles": {"bad": "not-a-dict"}}
        )
        assert len(store.profiles) == 0


class TestPersistence:
    def test_load_empty(self, tmp_path):
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            store = load_profiles()
            assert len(store.profiles) == 0

    def test_save_and_load(self, tmp_path):
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            p = ModelProfile(model_key="anthropic:claude-opus-4", confidence=0.7)
            upsert_profile(p)
            loaded = get_profile("claude-opus-4")
            assert loaded is not None
            assert loaded.confidence == 0.7

    def test_get_profile_returns_none_for_missing(self, tmp_path):
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            assert get_profile("claude-opus-4") is None

    def test_get_profile_returns_none_for_unresolvable(self, tmp_path):
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            assert get_profile("some-unknown") is None

    def test_reset_profile(self, tmp_path):
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            upsert_profile(ModelProfile(model_key="anthropic:claude-opus-4", confidence=0.8))
            assert get_profile("claude-opus-4") is not None
            assert reset_profile("claude-opus-4") is True
            assert get_profile("claude-opus-4") is None

    def test_reset_nonexistent_returns_false(self, tmp_path):
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            assert reset_profile("claude-opus-4") is False

    def test_exact_match_model_isolation(self, tmp_path):
        """Different model keys get different profiles."""
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            upsert_profile(
                ModelProfile(
                    model_key="anthropic:claude-opus-4",
                    confidence=0.8,
                    signal_risk={"approach_cycling": 0.5},
                )
            )
            upsert_profile(
                ModelProfile(
                    model_key="openai:gpt-4o",
                    confidence=0.7,
                    signal_risk={"verification_debt": 0.6},
                )
            )

            opus = get_profile("claude-opus-4")
            gpt = get_profile("gpt-4o")

            assert opus is not None
            assert gpt is not None
            assert opus.signal_risk.get("approach_cycling") == 0.5
            assert "verification_debt" not in opus.signal_risk
            assert gpt.signal_risk.get("verification_debt") == 0.6
            assert "approach_cycling" not in gpt.signal_risk

    def test_corrupt_file_returns_empty(self, tmp_path):
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=tmp_path,
        ):
            (tmp_path / "model_profiles.json").write_text("not valid json{{{")
            store = load_profiles()
            assert len(store.profiles) == 0


class TestTelemetryUpdate:
    def test_ema_update_changes_risk(self):
        p = ModelProfile(
            model_key="test:m",
            signal_risk={"approach_cycling": 0.5},
            confidence=0.8,
        )
        apply_telemetry_update(p, {"approach_cycling": 3}, event_count=30)
        assert p.telemetry_samples == 1
        # EMA should nudge toward observed (3/30*10 = 1.0)
        # new = 0.15 * 1.0 + 0.85 * 0.5 = 0.575
        assert p.signal_risk["approach_cycling"] != 0.5

    def test_skip_low_event_count(self):
        p = ModelProfile(model_key="test:m", signal_risk={"x": 0.5})
        apply_telemetry_update(p, {"x": 1}, event_count=5)
        assert p.telemetry_samples == 0
        assert p.signal_risk["x"] == 0.5

    def test_skip_zero_fires(self):
        p = ModelProfile(model_key="test:m", signal_risk={"x": 0.5})
        apply_telemetry_update(p, {"x": 0}, event_count=30)
        assert p.telemetry_samples == 0

    def test_clamps_to_bounds(self):
        p = ModelProfile(model_key="test:m", signal_risk={"x": 0.99})
        apply_telemetry_update(p, {"x": 100}, event_count=10)
        assert p.signal_risk["x"] <= 1.0

    def test_adds_new_signal(self):
        p = ModelProfile(model_key="test:m", signal_risk={})
        apply_telemetry_update(p, {"new_signal": 2}, event_count=20)
        assert "new_signal" in p.signal_risk
        assert 0.0 <= p.signal_risk["new_signal"] <= 1.0

    def test_lifetime_samples_do_not_block_updates(self):
        p = ModelProfile(
            model_key="test:m",
            signal_risk={"x": 0.5},
            telemetry_samples=10,
        )
        original = p.signal_risk["x"]
        apply_telemetry_update(p, {"x": 5}, event_count=20)
        assert p.telemetry_samples == 11
        assert p.signal_risk["x"] != original


# ── Targeted Coverage Fixes ──────────────────────────────────────────


class TestLintgateHome:
    def test_default_path(self, monkeypatch) -> None:
        monkeypatch.delenv("LINTGATE_HOME", raising=False)
        assert _lintgate_home() == Path.home() / ".lintgate"


class TestResolveModelKeyExtended:
    def test_o1_prefix(self) -> None:
        assert resolve_model_key("o1-preview") == "openai:o1-preview"

    def test_llama_prefix(self) -> None:
        assert resolve_model_key("llama-3.1-70b") == "meta:llama-3.1-70b"


class TestModelProfileExtended:
    def test_from_dict_missing_keys_use_defaults(self) -> None:
        p = ModelProfile.from_dict({})
        assert p.model_key == ""
        assert p.probe_version == 1

    def test_is_usable_at_exact_min_confidence(self) -> None:
        p = ModelProfile(
            confidence=DEFAULT_MIN_CONFIDENCE,
            updated_at=time.time(),
        )
        assert p.is_usable()


class TestModelProfileStoreExtended:
    def test_from_dict_missing_profiles_key(self) -> None:
        store = ModelProfileStore.from_dict({"format_version": 1})
        assert len(store.profiles) == 0


class TestPersistenceExtended:
    def test_save_profiles_creates_parent_dirs(self, tmp_path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        with mock.patch(
            "lintgate.controlplane.model.profiles._lintgate_home",
            return_value=nested,
        ):
            store = ModelProfileStore()
            store.profiles["test:m"] = ModelProfile(model_key="test:m")
            save_profiles(store)
            assert (nested / "model_profiles.json").exists()


class TestFromDictFieldPreservation:
    """Kill SWAP+VALUE mutants on ModelProfile.from_dict."""

    def test_from_dict_preserves_all_fields(self) -> None:
        now = time.time()
        data = {
            "model_key": "anthropic:claude-opus-4",
            "created_at": now - 1000,
            "updated_at": now - 500,
            "last_seen_at": now - 100,
            "probe_version": 3,
            "probe_runs": 7,
            "confidence": 0.82,
            "signal_risk": {"approach_cycling": 0.4, "verification_debt": 0.2},
            "custom_anti_patterns": ["avoid X"],
            "custom_dispositions": ["prefer Y"],
            "telemetry_samples": 42,
            "stale_after_days": 14,
        }
        p = ModelProfile.from_dict(data)
        assert p.model_key == "anthropic:claude-opus-4"
        assert p.created_at == now - 1000
        assert p.updated_at == now - 500
        assert p.last_seen_at == now - 100
        assert p.probe_version == 3
        assert p.probe_runs == 7
        assert p.confidence == 0.82
        assert p.signal_risk == {"approach_cycling": 0.4, "verification_debt": 0.2}
        assert p.custom_anti_patterns == ["avoid X"]
        assert p.custom_dispositions == ["prefer Y"]
        assert p.telemetry_samples == 42
        assert p.stale_after_days == 14

    def test_from_dict_fields_not_swapped(self) -> None:
        """Ensure created_at != updated_at != last_seen_at after deserialization."""
        data = {
            "created_at": 1000.0,
            "updated_at": 2000.0,
            "last_seen_at": 3000.0,
        }
        p = ModelProfile.from_dict(data)
        assert p.created_at == 1000.0
        assert p.updated_at == 2000.0
        assert p.last_seen_at == 3000.0


class TestIsStaleEdgeCases:
    """Kill BOUNDARY+VALUE mutants on ModelProfile.is_stale."""

    def test_stale_at_exact_boundary(self) -> None:
        """Exactly stale_after_days old should be stale (> not >=)."""
        p = ModelProfile(
            stale_after_days=30,
            updated_at=time.time() - (30 * 86400) - 1,
        )
        assert p.is_stale()

    def test_not_stale_just_under_boundary(self) -> None:
        p = ModelProfile(
            stale_after_days=30,
            updated_at=time.time() - (30 * 86400) + 3600,
        )
        assert not p.is_stale()

    def test_stale_uses_stale_after_days_field(self) -> None:
        """Custom stale_after_days=7 should use that, not the default 30."""
        p = ModelProfile(
            stale_after_days=7,
            updated_at=time.time() - (8 * 86400),
        )
        assert p.is_stale()
        p2 = ModelProfile(
            stale_after_days=7,
            updated_at=time.time() - (6 * 86400),
        )
        assert not p2.is_stale()

    def test_stale_division_constant(self) -> None:
        """Verify 86400 seconds per day is correct."""
        p = ModelProfile(
            stale_after_days=1,
            updated_at=time.time() - 86401,
        )
        assert p.is_stale()
        p2 = ModelProfile(
            stale_after_days=1,
            updated_at=time.time() - 86000,
        )
        assert not p2.is_stale()


class TestStoreToDictValues:
    """Kill VALUE mutants on ModelProfileStore.to_dict."""

    def test_to_dict_includes_format_version(self) -> None:
        store = ModelProfileStore(format_version=2)
        d = store.to_dict()
        assert d["format_version"] == 2

    def test_to_dict_profiles_serialized(self) -> None:
        store = ModelProfileStore()
        store.profiles["k1"] = ModelProfile(model_key="k1", confidence=0.9)
        store.profiles["k2"] = ModelProfile(model_key="k2", confidence=0.3)
        d = store.to_dict()
        assert len(d["profiles"]) == 2
        assert d["profiles"]["k1"]["confidence"] == 0.9
        assert d["profiles"]["k2"]["confidence"] == 0.3
        assert d["profiles"]["k1"]["model_key"] == "k1"


class TestStoreFromDictValues:
    """Kill SWAP+TYPE+VALUE mutants on ModelProfileStore.from_dict."""

    def test_from_dict_preserves_format_version(self) -> None:
        store = ModelProfileStore.from_dict({"format_version": 3, "profiles": {}})
        assert store.format_version == 3

    def test_from_dict_preserves_profile_data(self) -> None:
        store = ModelProfileStore.from_dict({
            "format_version": 1,
            "profiles": {
                "test:a": {"model_key": "test:a", "confidence": 0.77},
                "test:b": {"model_key": "test:b", "confidence": 0.33},
            },
        })
        assert len(store.profiles) == 2
        assert store.profiles["test:a"].confidence == 0.77
        assert store.profiles["test:b"].confidence == 0.33

    def test_from_dict_type_filter(self) -> None:
        """Non-dict profiles should be filtered out, dict ones preserved."""
        store = ModelProfileStore.from_dict({
            "profiles": {
                "good": {"model_key": "good", "confidence": 0.5},
                "bad": "string-not-dict",
                "also_bad": 42,
            },
        })
        assert "good" in store.profiles
        assert "bad" not in store.profiles
        assert "also_bad" not in store.profiles


class TestApplyConfidenceDecay:
    def test_no_decay_within_grace_period(self) -> None:
        p = ModelProfile(
            confidence=0.8,
            updated_at=time.time() - (12 * 3600),
        )
        original = apply_confidence_decay(p)
        assert original == 0.8
        assert p.confidence == 0.8

    def test_decay_at_half_life(self) -> None:
        p = ModelProfile(
            confidence=1.0,
            updated_at=time.time() - (15 * 86400),
        )
        apply_confidence_decay(p)
        assert abs(p.confidence - 0.5) < 0.01
