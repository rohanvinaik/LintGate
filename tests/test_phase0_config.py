"""Tests for Phase 0: Foundation — InquiryConfig + theory profile caching."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.config import load_controlplane_config
from lintgate.controlplane.session_memory import (
    BehaviorEventData,
    SessionMemory,
    SessionSnapshot,
)
from lintgate.controlplane.types import ControlPlaneConfig, InquiryConfig
from lintgate.theory_extractor import get_theory_context_from_profile

# ── InquiryConfig ────────────────────────────────────────────────────


class TestInquiryConfig:
    def test_defaults_all_false(self) -> None:
        cfg = InquiryConfig()
        assert cfg.theory_grounded_signals is False
        assert cfg.prediction_tracking is False
        assert cfg.theory_coherence_check is False
        assert cfg.living_context is False
        assert cfg.session_gate is False
        assert cfg.any_enabled() is False

    def test_any_enabled_single(self) -> None:
        cfg = InquiryConfig(theory_grounded_signals=True)
        assert cfg.any_enabled() is True

    def test_any_enabled_all(self) -> None:
        cfg = InquiryConfig(
            theory_grounded_signals=True,
            prediction_tracking=True,
            theory_coherence_check=True,
            living_context=True,
            session_gate=True,
        )
        assert cfg.any_enabled() is True

    def test_controlplane_config_has_inquiry_default(self) -> None:
        cp = ControlPlaneConfig()
        assert isinstance(cp.inquiry, InquiryConfig)
        assert cp.inquiry.any_enabled() is False


# ── Config Parsing ───────────────────────────────────────────────────


class TestInquiryConfigParsing:
    def test_parse_inquiry_from_yaml(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config_file = claude_dir / "lintgate.yaml"
        config_file.write_text(
            "controlplane:\n"
            "  enabled: true\n"
            "  inquiry:\n"
            "    theory_grounded_signals: true\n"
            "    prediction_tracking: true\n"
            "    theory_coherence_check: false\n"
        )

        cp = load_controlplane_config(str(tmp_path))
        assert cp is not None
        assert cp.inquiry.theory_grounded_signals is True
        assert cp.inquiry.prediction_tracking is True
        assert cp.inquiry.theory_coherence_check is False
        assert cp.inquiry.living_context is False
        assert cp.inquiry.session_gate is False

    def test_missing_inquiry_section_uses_defaults(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config_file = claude_dir / "lintgate.yaml"
        config_file.write_text("controlplane:\n  enabled: true\n")

        cp = load_controlplane_config(str(tmp_path))
        assert cp is not None
        assert cp.inquiry.any_enabled() is False

    def test_inquiry_section_empty_dict(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config_file = claude_dir / "lintgate.yaml"
        config_file.write_text("controlplane:\n  enabled: true\n  inquiry: {}\n")

        cp = load_controlplane_config(str(tmp_path))
        assert cp is not None
        assert cp.inquiry.any_enabled() is False


# ── Theory Profile Caching ───────────────────────────────────────────


class TestTheoryProfileCache:
    def test_session_memory_cache_default_none(self) -> None:
        session = SessionMemory()
        assert session.theory_profile_cache is None

    def test_cache_set_and_clear_lifecycle(self) -> None:
        session = SessionMemory()
        fake_profile = {
            "core_theory": [{"claims": ["test"], "source": "test.md", "heading": "Test"}]
        }
        session.theory_profile_cache = fake_profile
        assert session.theory_profile_cache is not None
        assert session.theory_profile_cache["core_theory"][0]["claims"] == ["test"]

        # Clear (simulating post-mesh cleanup)
        session.theory_profile_cache = None
        assert session.theory_profile_cache is None

    def test_cache_not_persisted_in_to_dict(self) -> None:
        session = SessionMemory()
        session.theory_profile_cache = {"some": "data"}
        d = session.to_dict()
        # theory_profile_cache should NOT be in the serialized dict
        assert "theory_profile_cache" not in d

    def test_from_dict_without_cache_field(self) -> None:
        """Old session data without theory_profile_cache loads cleanly."""
        old_data = {
            "session_id": "abc123",
            "project_root": "/test",
            "started_at": 1000.0,
            "last_active": 1000.0,
            "snapshots": [],
            "coherence_trajectory": [],
            "repair_outcomes": {},
            "pattern_trend": {},
            "proposed_constraints": [],
            "agent_disagreements": [],
            "behavior_compass": {},
        }
        session = SessionMemory.from_dict(old_data)
        assert session.theory_profile_cache is None
        assert session.pending_patches == []


# ── Pending Patches ──────────────────────────────────────────────────


class TestPendingPatches:
    def test_default_empty(self) -> None:
        session = SessionMemory()
        assert session.pending_patches == []

    def test_roundtrip(self) -> None:
        session = SessionMemory()
        session.pending_patches = [{"patch_id": "p1", "section_id": "machine_rules"}]
        d = session.to_dict()
        restored = SessionMemory.from_dict(d)
        assert restored.pending_patches == [{"patch_id": "p1", "section_id": "machine_rules"}]

    # test_from_dict_without_pending_patches removed — duplicate of
    # tests/test_living_context.py::TestPendingPatches::test_from_dict_without_pending_patches


# ── SessionSnapshot Prediction Fields ────────────────────────────────


class TestSnapshotPredictionFields:
    def test_defaults(self) -> None:
        snap = SessionSnapshot()
        assert snap.prediction_accuracy is None
        assert snap.predictions_checked == 0

    def test_roundtrip(self) -> None:
        snap = SessionSnapshot(
            behavior=BehaviorEventData(prediction_accuracy=0.75, predictions_checked=10)
        )
        d = snap.to_dict()
        restored = SessionSnapshot.from_dict(d)
        assert restored.prediction_accuracy == 0.75
        assert restored.predictions_checked == 10

    def test_from_dict_without_prediction_fields(self) -> None:
        """Old snapshot data loads with defaults."""
        old_data = {
            "run_id": "r1",
            "timestamp": 1000.0,
            "coherence_state": "stable",
        }
        snap = SessionSnapshot.from_dict(old_data)
        assert snap.prediction_accuracy is None
        assert snap.predictions_checked == 0


# ── get_theory_context_from_profile ──────────────────────────────────


class TestGetTheoryContextFromProfile:
    SAMPLE_PROFILE = {
        "core_theory": [
            {
                "claims": [
                    "Constraint maps become architectures of possibility",
                    "Named anti-patterns serve as cognitive anchors",
                ],
                "source": "docs/theory.md",
                "heading": "Core Theory",
            }
        ],
        "problem_solving": [
            {
                "claims": [
                    "Decompose complex problems before attempting solutions",
                    "Approach cycling indicates insufficient constraint understanding",
                ],
                "source": "docs/theory.md",
                "heading": "Problem Solving",
            }
        ],
    }

    def test_basic_keyword_match(self) -> None:
        result = get_theory_context_from_profile(
            self.SAMPLE_PROFILE,
            keywords=["constraint"],
        )
        assert result["total_matched"] >= 2
        assert any("constraint" in c["claim"].lower() for c in result["claims"])

    def test_facet_filter(self) -> None:
        result = get_theory_context_from_profile(
            self.SAMPLE_PROFILE,
            facet="core_theory",
        )
        # Should only return claims from core_theory
        assert all(c["facet"] == "core_theory" for c in result["claims"])

    def test_max_claims_truncation(self) -> None:
        result = get_theory_context_from_profile(
            self.SAMPLE_PROFILE,
            max_claims=1,
        )
        assert result["returned_count"] <= 1
        assert result["truncated"] is True or result["total_matched"] <= 1

    def test_empty_profile(self) -> None:
        result = get_theory_context_from_profile({})
        assert result["claims"] == []
        assert result["total_matched"] == 0

    def test_none_profile(self) -> None:
        result = get_theory_context_from_profile(None)
        assert result["claims"] == []

    def test_no_matching_keywords(self) -> None:
        result = get_theory_context_from_profile(
            self.SAMPLE_PROFILE,
            keywords=["zzz_nonexistent"],
        )
        assert result["total_matched"] == 0

    def test_combined_facet_and_keywords(self) -> None:
        result = get_theory_context_from_profile(
            self.SAMPLE_PROFILE,
            facet="problem_solving",
            keywords=["approach"],
        )
        assert result["total_matched"] >= 1
        assert all(c["facet"] == "problem_solving" for c in result["claims"])

    def test_zero_max_claims(self) -> None:
        result = get_theory_context_from_profile(
            self.SAMPLE_PROFILE,
            max_claims=0,
        )
        assert result["claims"] == []
