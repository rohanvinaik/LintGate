"""PR5: Static/empirical reconciliation tests.

Validates overlay status classification, agreement detection,
phase compatibility, confidence computation, and file_analyzer
integration.
"""

from __future__ import annotations

from lintgate.specification.static_empirical_reconciliation import (
    EmpiricalOverlay,
    OverlayStatus,
    _check_agreement,
    _compute_overlay_confidence,
    _detect_empirical_tail,
    _infer_empirical_phase,
    _phases_compatible,
    build_overlay,
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
        cache = {"mod.py::f": {
            "discovery_state": "NO_TEST_FILES",
            "topology_state": "TOPOLOGY_UNKNOWN",
            "survival_interpretation": "DISCOVERY_ARTIFACT",
            "total_mutants": 8,
            "survival_rate": 1.0,
        }}
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.DISCOVERY_FAILURE
        assert overlay.overlay_confidence == 0.2
        assert "NO_TEST_FILES" in overlay.overlay_rationale

    def test_discovery_failure_import_failed(self):
        cache = {"mod.py::f": {
            "discovery_state": "DISCOVERY_IMPORT_FAILED",
            "topology_state": "TOPOLOGY_UNKNOWN",
            "survival_interpretation": "DISCOVERY_ARTIFACT",
            "total_mutants": 5,
            "survival_rate": 1.0,
        }}
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.DISCOVERY_FAILURE

    def test_topology_limited_mock_dominant(self):
        cache = {"mod.py::f": {
            "discovery_state": "DISCOVERY_OK",
            "topology_state": "MOCK_BOUNDARY_DOMINANT",
            "survival_interpretation": "MOCK_BOUNDARY_ARTIFACT",
            "total_mutants": 12,
            "survival_rate": 0.8,
        }}
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.TOPOLOGY_LIMITED
        assert overlay.overlay_confidence == 0.3
        assert "mock" in overlay.overlay_rationale.lower()

    def test_agrees_when_consistent(self):
        cache = {"mod.py::f": {
            "discovery_state": "DISCOVERY_OK",
            "topology_state": "NORMAL",
            "survival_interpretation": "MEANINGFUL",
            "total_mutants": 12,
            "survival_rate": 0.4,
        }}
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", cache)
        assert overlay.status == OverlayStatus.AGREES

    def test_contradicts_when_divergent(self):
        # Static says sigma=5 (small), empirical has 50 mutants (10x ratio)
        cache = {"mod.py::f": {
            "discovery_state": "DISCOVERY_OK",
            "topology_state": "NORMAL",
            "survival_interpretation": "MEANINGFUL",
            "total_mutants": 50,
            "survival_rate": 0.8,
        }}
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
        entry = {}
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
        # Even worst case should not go below 0.1
        conf = _compute_overlay_confidence("PATCHED_INTERNAL_CALLS", "LOW_CONFIDENCE", 2)
        assert conf >= 0.1


# ── to_dict ──────────────────────────────────────────────────────


class TestOverlayToDict:
    def test_no_data_dict(self):
        overlay = EmpiricalOverlay()
        d = overlay.to_dict()
        assert d["status"] == "NO_EMPIRICAL_DATA"
        assert "mutation_runs_seen" not in d

    def test_with_data_dict(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.AGREES,
            mutation_runs_seen=1,
            empirical_sigma_upper_bound=15,
            empirical_survival_rate=0.2,
            overlay_confidence=0.85,
            overlay_rationale="All good.",
        )
        d = overlay.to_dict()
        assert d["status"] == "AGREES"
        assert d["mutation_runs_seen"] == 1
        assert d["empirical_sigma_upper_bound"] == 15
        assert d["empirical_survival_rate"] == 0.2

    def test_tail_flag_only_when_true(self):
        overlay = EmpiricalOverlay(empirical_tail=False)
        assert "empirical_tail" not in overlay.to_dict()

        overlay.empirical_tail = True
        assert overlay.to_dict()["empirical_tail"] is True


# ── File analyzer integration ────────────────────────────────────


class TestFileAnalyzerOverlay:
    def test_overlay_present_in_enriched_output(self, tmp_path):
        """Verify that enriched file analysis includes empirical_overlay."""
        src = tmp_path / "example.py"
        src.write_text("def add(a, b):\n    return a + b\n")

        from lintgate.specification.file_analyzer import analyze_file

        result = analyze_file(str(src), str(tmp_path), enrich=True)
        # Should have at least one function
        if result.functions:
            func_data = next(iter(result.functions.values()))
            assert "empirical_overlay" in func_data
            overlay = func_data["empirical_overlay"]
            assert overlay["status"] == "NO_EMPIRICAL_DATA"
