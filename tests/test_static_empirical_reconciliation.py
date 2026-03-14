"""Tests for lintgate/specification/static_empirical_reconciliation.py.

Covers: overlay status classification, agreement detection, phase inference,
phase compatibility, tail detection, confidence computation, reconcile_spec_level,
and EmpiricalOverlay.to_dict() mutation-killing tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from lintgate.specification.static_empirical_reconciliation import (
    EmpiricalOverlay,
    OverlayStatus,
    _check_agreement,
    _compute_overlay_confidence,
    _detect_empirical_tail,
    _infer_empirical_phase,
    _phases_compatible,
    build_overlay,
    reconcile_spec_level,
)

# ── Overlay status classification ────────────────────────────────


class TestBuildOverlayStatus:
    def test_no_cache_returns_no_empirical_data(self):
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", None)
        assert overlay.status == OverlayStatus.NO_EMPIRICAL_DATA
        assert overlay.mutation_runs_seen == 0

    def test_missing_key_returns_no_empirical_data(self):
        cache = {"mod.py::g": {"total_mutants": 5}}
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.NO_EMPIRICAL_DATA

    def test_discovery_failure_no_test_files(self):
        cache = {
            "mod.py::f": {
                "discovery_state": "NO_TEST_FILES",
                "topology_state": "TOPOLOGY_UNKNOWN",
                "survival_interpretation": "DISCOVERY_ARTIFACT",
                "total_mutants": 8,
                "survival_rate": 1.0,
            }
        }
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.DISCOVERY_FAILURE
        assert overlay.overlay_confidence == 0.2
        assert "NO_TEST_FILES" in overlay.overlay_rationale

    def test_discovery_failure_import_failed(self):
        cache = {
            "mod.py::f": {
                "discovery_state": "DISCOVERY_IMPORT_FAILED",
                "topology_state": "TOPOLOGY_UNKNOWN",
                "survival_interpretation": "DISCOVERY_ARTIFACT",
                "total_mutants": 5,
                "survival_rate": 1.0,
            }
        }
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.DISCOVERY_FAILURE

    def test_topology_limited_mock_dominant(self):
        cache = {
            "mod.py::f": {
                "discovery_state": "DISCOVERY_OK",
                "topology_state": "MOCK_BOUNDARY_DOMINANT",
                "survival_interpretation": "MOCK_BOUNDARY_ARTIFACT",
                "total_mutants": 12,
                "survival_rate": 0.8,
            }
        }
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.TOPOLOGY_LIMITED
        assert overlay.overlay_confidence == 0.3
        assert "mock" in overlay.overlay_rationale.lower()

    def test_agrees_when_consistent(self):
        cache = {
            "mod.py::f": {
                "discovery_state": "DISCOVERY_OK",
                "topology_state": "NORMAL",
                "survival_interpretation": "MEANINGFUL",
                "total_mutants": 12,
                "survival_rate": 0.4,
            }
        }
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.AGREES

    def test_contradicts_when_divergent(self):
        cache = {
            "mod.py::f": {
                "discovery_state": "DISCOVERY_OK",
                "topology_state": "NORMAL",
                "survival_interpretation": "MEANINGFUL",
                "total_mutants": 50,
                "survival_rate": 0.8,
            }
        }
        overlay = build_overlay("mod.py::f", 5, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.CONTRADICTS
        assert "divergence" in overlay.overlay_rationale.lower()


# ── Agreement detection ──────────────────────────────────────────


class TestCheckAgreement:
    def test_sigma_consistent(self):
        agrees, rationale = _check_agreement(10, "A", "bulk", 12, 0.5, False)
        assert agrees
        assert "consistent" in rationale.lower() or "sigma" in rationale.lower()

    def test_sigma_divergence(self):
        agrees, rationale = _check_agreement(5, "A", "bulk", 50, 0.5, False)
        assert not agrees
        assert "divergence" in rationale.lower()

    def test_phase_match(self):
        agrees, rationale = _check_agreement(10, "A", "complete", 12, 0.005, False)
        assert agrees
        assert "phase" in rationale.lower()

    def test_phase_mismatch(self):
        agrees, rationale = _check_agreement(10, "A", "complete", 12, 0.8, False)
        assert not agrees
        assert "mismatch" in rationale.lower()

    def test_regime_conflict(self):
        agrees, rationale = _check_agreement(10, "A", "bulk", 12, 0.8, False)
        assert not agrees
        assert "regime" in rationale.lower()

    def test_regime_b_well_specified(self):
        agrees, rationale = _check_agreement(10, "B", "complete", 12, 0.005, False)
        assert agrees
        assert "well-specified" in rationale.lower()


# ── Phase inference and compatibility ────────────────────────────


class TestPhaseInference:
    def test_complete(self):
        assert _infer_empirical_phase(0.0, False) == "complete"

    def test_tail(self):
        assert _infer_empirical_phase(0.2, True) == "tail"

    def test_transition(self):
        assert _infer_empirical_phase(0.15, False) == "transition"

    def test_bulk(self):
        assert _infer_empirical_phase(0.6, False) == "bulk"


class TestPhasesCompatible:
    def test_same_phase(self):
        assert _phases_compatible("bulk", "bulk")

    def test_adjacent_phases(self):
        assert _phases_compatible("bulk", "transition")
        assert _phases_compatible("transition", "tail")
        assert _phases_compatible("tail", "complete")

    def test_non_adjacent_phases(self):
        assert not _phases_compatible("bulk", "tail")
        assert not _phases_compatible("bulk", "complete")

    def test_unknown_phase(self):
        assert not _phases_compatible("bulk", "unknown")


# ── Tail detection ───────────────────────────────────────────────


class TestDetectEmpiricalTail:
    def test_tail_from_phase(self):
        entry = {"trajectory": {"phase": "tail"}}
        assert _detect_empirical_tail(entry)

    def test_tail_from_onset(self):
        entry = {"trajectory": {"tail_onset_step": 3}}
        assert _detect_empirical_tail(entry)

    def test_no_tail(self):
        entry = {"trajectory": {"phase": "bulk"}}
        assert not _detect_empirical_tail(entry)

    def test_no_trajectory_key(self):
        entry: dict[str, Any] = {}
        assert not _detect_empirical_tail(entry)


# ── Confidence computation ───────────────────────────────────────


class TestOverlayConfidence:
    def test_normal_confidence(self):
        conf = _compute_overlay_confidence("NORMAL", "MEANINGFUL", 15)
        assert 0.7 <= conf <= 1.0

    def test_few_mutants_lowers_confidence(self):
        conf = _compute_overlay_confidence("NORMAL", "MEANINGFUL", 3)
        assert conf < _compute_overlay_confidence("NORMAL", "MEANINGFUL", 15)

    def test_many_mutants_raises_confidence(self):
        conf = _compute_overlay_confidence("NORMAL", "MEANINGFUL", 25)
        assert conf > _compute_overlay_confidence("NORMAL", "MEANINGFUL", 10)

    def test_patched_calls_lower_confidence(self):
        conf = _compute_overlay_confidence("PATCHED_INTERNAL_CALLS", "MEANINGFUL", 15)
        assert conf < _compute_overlay_confidence("NORMAL", "MEANINGFUL", 15)

    def test_low_confidence_interpretation(self):
        conf = _compute_overlay_confidence("NORMAL", "LOW_CONFIDENCE", 15)
        assert conf < _compute_overlay_confidence("NORMAL", "MEANINGFUL", 15)

    def test_confidence_clamped(self):
        conf = _compute_overlay_confidence("PATCHED_INTERNAL_CALLS", "LOW_CONFIDENCE", 2)
        assert conf >= 0.1


# ── reconcile_spec_level ─────────────────────────────────────────


class TestReconcileSpecLevel:
    # 7 duplicate tests removed (byte-identical to test_reconciliation_authoritative.py)

    def test_custom_confidence_threshold(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            overlay_confidence=0.6,
            empirical_survival_rate=0.05,
        )
        val, src = reconcile_spec_level(0.1, overlay)
        assert src == "static"

        val, src = reconcile_spec_level(0.1, overlay, confidence_threshold=0.5)
        assert src == "empirical_override"
        assert val == pytest.approx(0.95)

    def test_no_cache_overlay_returns_static(self):
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", None)
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == 0.1
        assert src == "static"


# ── EmpiricalOverlay.to_dict() mutation killers ──────────────────


class TestOverlayToDictMutationKillers:
    """Tests that discriminate exact to_dict output — rounding, keys, conditionals."""

    def test_confidence_rounds_to_2_decimals(self):
        d = EmpiricalOverlay(overlay_confidence=0.8567).to_dict()
        assert d["overlay_confidence"] == 0.86
        assert d["overlay_confidence"] != 0.857  # not round(_, 3)
        assert d["overlay_confidence"] != 0.9  # not round(_, 1)

    def test_survival_rate_rounds_to_3_decimals(self):
        d = EmpiricalOverlay(mutation_runs_seen=1, empirical_survival_rate=0.12345).to_dict()
        assert d["empirical_survival_rate"] == 0.123
        assert d["empirical_survival_rate"] != 0.12  # not round(_, 2)

    def test_reconciled_spec_level_rounds_to_3_decimals(self):
        d = EmpiricalOverlay(
            reconciled_spec_level=0.95678,
            reconciled_data_source="empirical_override",
        ).to_dict()
        assert d["reconciled_spec_level"] == 0.957
        assert d["reconciled_spec_level"] != 0.96  # not round(_, 2)
        assert d["reconciled_spec_level"] != 1.0  # not round(_, 0)

    def test_mutation_fields_excluded_at_zero_runs(self):
        d = EmpiricalOverlay(
            mutation_runs_seen=0,
            empirical_sigma_upper_bound=10,
            empirical_survival_rate=0.5,
        ).to_dict()
        assert "mutation_runs_seen" not in d
        assert "empirical_sigma_upper_bound" not in d
        assert "empirical_survival_rate" not in d

    def test_mutation_fields_included_at_one_run(self):
        d = EmpiricalOverlay(
            mutation_runs_seen=1,
            empirical_sigma_upper_bound=20,
            empirical_survival_rate=0.0,
        ).to_dict()
        assert d["mutation_runs_seen"] == 1
        assert d["empirical_sigma_upper_bound"] == 20
        assert d["empirical_survival_rate"] == 0.0

    def test_boundary_runs_gt_zero_not_gte(self):
        """Mutation > 0 → >= 0 would wrongly include fields at runs_seen=0."""
        zero = EmpiricalOverlay(mutation_runs_seen=0).to_dict()
        one = EmpiricalOverlay(mutation_runs_seen=1, empirical_sigma_upper_bound=5).to_dict()
        assert "mutation_runs_seen" not in zero
        assert "mutation_runs_seen" in one

    def test_boundary_runs_gt_zero_not_gt_one(self):
        """Mutation > 0 → > 1 would wrongly exclude fields at runs_seen=1."""
        d = EmpiricalOverlay(mutation_runs_seen=1, empirical_sigma_upper_bound=5).to_dict()
        assert d["mutation_runs_seen"] == 1

    def test_tail_true_not_false(self):
        d = EmpiricalOverlay(empirical_tail=True).to_dict()
        assert d["empirical_tail"] is True
        assert d["empirical_tail"] is not False

    def test_tail_absent_when_false(self):
        d = EmpiricalOverlay(empirical_tail=False).to_dict()
        assert "empirical_tail" not in d

    def test_reconciled_absent_when_none(self):
        d = EmpiricalOverlay(reconciled_spec_level=None).to_dict()
        assert "reconciled_spec_level" not in d
        assert "reconciled_data_source" not in d

    def test_default_exact_keys(self):
        d = EmpiricalOverlay().to_dict()
        assert set(d.keys()) == {"status", "overlay_confidence", "overlay_rationale"}

    def test_full_exact_keys(self):
        d = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            mutation_runs_seen=2,
            empirical_sigma_upper_bound=30,
            empirical_survival_rate=0.15,
            empirical_tail=True,
            overlay_confidence=0.9,
            overlay_rationale="Sigma divergence.",
            reconciled_spec_level=0.85,
            reconciled_data_source="empirical_override",
        ).to_dict()
        assert set(d.keys()) == {
            "status",
            "overlay_confidence",
            "overlay_rationale",
            "mutation_runs_seen",
            "empirical_sigma_upper_bound",
            "empirical_survival_rate",
            "empirical_tail",
            "reconciled_spec_level",
            "reconciled_data_source",
        }

    def test_status_key_name_exact(self):
        d = EmpiricalOverlay().to_dict()
        assert "status" in d
        assert d["status"] == "NO_EMPIRICAL_DATA"

    def test_key_names_not_empty_strings(self):
        """VALUE mutants replace key strings with ''. Verify exact keys."""
        d = EmpiricalOverlay(
            mutation_runs_seen=1,
            empirical_sigma_upper_bound=10,
            empirical_survival_rate=0.5,
        ).to_dict()
        assert "" not in d
        assert "mutation_runs_seen" in d
        assert "empirical_sigma_upper_bound" in d
        assert "empirical_survival_rate" in d

    def test_rationale_preserved_verbatim(self):
        msg = "Sigma divergence: static=5 vs empirical mutants=50."
        assert EmpiricalOverlay(overlay_rationale=msg).to_dict()["overlay_rationale"] == msg
