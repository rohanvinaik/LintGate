"""Tests for authoritative reconciliation — Phases 1-6.

Covers reconcile_spec_level(), compute_health() with reconciliation,
_compute_file_health() double-count fix, _reconciliation_priority(),
prescribe() overlay adjustment, and scheduler priority boost.
"""

from __future__ import annotations

import pytest

from lintgate.specification.static_empirical_reconciliation import (
    EmpiricalOverlay,
    OverlayStatus,
    build_overlay,
    reconcile_spec_level,
)

# ── Phase 1A: reconcile_spec_level() ────────────────────────────


class TestReconcileSpecLevel:
    def test_no_empirical_data_returns_static(self):
        overlay = EmpiricalOverlay(status=OverlayStatus.NO_EMPIRICAL_DATA)
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == 0.1
        assert src == "static"

    def test_discovery_failure_returns_static(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.DISCOVERY_FAILURE,
            overlay_confidence=0.9,
        )
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == 0.1
        assert src == "static"

    def test_topology_limited_returns_static(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.TOPOLOGY_LIMITED,
            overlay_confidence=0.9,
        )
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == 0.1
        assert src == "static"

    def test_agrees_returns_static(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.AGREES,
            overlay_confidence=0.9,
        )
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == 0.1
        assert src == "static"

    def test_contradicts_low_confidence_returns_static(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            overlay_confidence=0.5,
            empirical_survival_rate=0.05,
        )
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == 0.1
        assert src == "static"

    def test_contradicts_high_confidence_overrides_when_better(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            overlay_confidence=0.9,
            empirical_survival_rate=0.05,
        )
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == pytest.approx(0.95)
        assert src == "empirical_override"

    def test_contradicts_no_downgrade(self):
        """Conservative: don't downgrade spec_level even if empirical is worse."""
        overlay = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            overlay_confidence=0.9,
            empirical_survival_rate=0.95,
        )
        val, src = reconcile_spec_level(0.5, overlay)
        assert val == 0.5
        assert src == "static"

    def test_custom_confidence_threshold(self):
        overlay = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            overlay_confidence=0.6,
            empirical_survival_rate=0.05,
        )
        # Default threshold 0.7 — should not override
        val, src = reconcile_spec_level(0.1, overlay)
        assert src == "static"

        # Lower threshold — should override
        val, src = reconcile_spec_level(0.1, overlay, confidence_threshold=0.5)
        assert src == "empirical_override"
        assert val == pytest.approx(0.95)

    def test_overlay_to_dict_includes_reconciled_fields(self):
        overlay = EmpiricalOverlay(
            reconciled_spec_level=0.95,
            reconciled_data_source="empirical_override",
        )
        d = overlay.to_dict()
        assert d["reconciled_spec_level"] == 0.95
        assert d["reconciled_data_source"] == "empirical_override"

    def test_overlay_to_dict_omits_when_none(self):
        overlay = EmpiricalOverlay()
        d = overlay.to_dict()
        assert "reconciled_spec_level" not in d


# ── Phase 1B: compute_health() with reconciliation ──────────────


class TestComputeHealthReconciliation:
    def test_reconciled_spec_level_used_for_axis(self):
        from lintgate.specification.health_vector import HealthAxis, compute_health

        h = compute_health(
            spec_level=0.1,
            kill_rate=0.8,
            convergence=0.7,
            test_efficiency=0.6,
            reconciled_spec_level=0.9,
        )
        assert h.axes[HealthAxis.SPEC_LEVEL.value] == pytest.approx(0.9)
        assert h.reconciliation_active is True
        assert h.axes["static_spec_level"] == pytest.approx(0.1)

    def test_no_reconciliation_preserves_behavior(self):
        from lintgate.specification.health_vector import HealthAxis, compute_health

        h = compute_health(spec_level=0.5, kill_rate=0.5, convergence=0.5, test_efficiency=0.5)
        assert h.axes[HealthAxis.SPEC_LEVEL.value] == pytest.approx(0.5)
        assert h.reconciliation_active is False
        assert "static_spec_level" not in h.axes

    def test_scalar_uses_reconciled(self):
        from lintgate.specification.health_vector import compute_health

        h_low = compute_health(spec_level=0.1, kill_rate=0.8, convergence=0.7, test_efficiency=0.6)
        h_rec = compute_health(
            spec_level=0.1,
            kill_rate=0.8,
            convergence=0.7,
            test_efficiency=0.6,
            reconciled_spec_level=0.9,
        )
        # Reconciled health should be significantly higher
        assert h_rec.scalar > h_low.scalar


# ── Phase 2A: _compute_file_health double-count fix ─────────────


class TestComputeFileHealthFix:
    def test_no_double_count(self):
        """Function in both orch_state and mutation_cache should count once."""
        from unittest.mock import MagicMock

        from mcp_tools._platonic_impl import _compute_file_health

        spec_functions = {
            "mod.py::func": {
                "specification_level": 0.1,
                "testability_score": 0.5,
                "sigma": 10,
                "regime": "A",
                "phase": "bulk",
            }
        }
        mutation_cache = {
            "mod.py::func": {
                "survival_rate": 0.2,
                "total_mutants": 10,
                "discovery_state": "DISCOVERY_OK",
                "topology_state": "NORMAL",
                "survival_interpretation": "MEANINGFUL",
            }
        }

        target = MagicMock()
        target.kill_rate = 0.8
        target.convergence_rate = 0.5

        orch_state = MagicMock()
        orch_state.targets = {"mod.py::func": target}

        result = _compute_file_health(spec_functions, mutation_cache, orch_state)

        # Kill rate should be 0.8 (from orch_state), not 0.8 + 0.8 (double)
        assert result["axes"]["kill_rate"] == pytest.approx(0.8)

    def test_fallback_to_mutation_cache(self):
        """When orch_state doesn't have the function, use mutation cache."""
        from unittest.mock import MagicMock

        from mcp_tools._platonic_impl import _compute_file_health

        spec_functions = {
            "mod.py::func": {
                "specification_level": 0.1,
                "testability_score": 0.5,
                "sigma": 10,
                "regime": "A",
                "phase": "bulk",
            }
        }
        mutation_cache = {
            "mod.py::func": {
                "survival_rate": 0.3,
                "total_mutants": 10,
                "discovery_state": "DISCOVERY_OK",
                "topology_state": "NORMAL",
                "survival_interpretation": "MEANINGFUL",
            }
        }

        orch_state = MagicMock()
        orch_state.targets = {}  # empty — no orch data

        result = _compute_file_health(spec_functions, mutation_cache, orch_state)
        assert result["axes"]["kill_rate"] == pytest.approx(0.7)

    def test_discovery_artifact_veto(self):
        """Discovery failures should trigger the veto gate."""
        from unittest.mock import MagicMock

        from mcp_tools._platonic_impl import _compute_file_health

        spec_functions = {
            "mod.py::func": {
                "specification_level": 0.5,
                "testability_score": 0.5,
                "sigma": 10,
                "regime": "A",
                "phase": "bulk",
            }
        }
        mutation_cache = {
            "mod.py::func": {
                "survival_rate": 1.0,
                "total_mutants": 10,
                "discovery_state": "NO_TEST_FILES",
                "topology_state": "NORMAL",
                "survival_interpretation": "MEANINGFUL",
            }
        }

        orch_state = MagicMock()
        orch_state.targets = {}

        result = _compute_file_health(spec_functions, mutation_cache, orch_state)
        assert result["vetoed"] is True
        assert result["vetoes"]["discovery_artifact"] is True

    def test_mock_boundary_veto(self):
        """Mock-boundary dominant should fire the veto gate."""
        from unittest.mock import MagicMock

        from mcp_tools._platonic_impl import _compute_file_health

        spec_functions = {
            "mod.py::func": {
                "specification_level": 0.5,
                "testability_score": 0.5,
                "sigma": 10,
                "regime": "A",
                "phase": "bulk",
            }
        }
        mutation_cache = {
            "mod.py::func": {
                "survival_rate": 0.5,
                "total_mutants": 10,
                "discovery_state": "DISCOVERY_OK",
                "topology_state": "MOCK_BOUNDARY_DOMINANT",
                "survival_interpretation": "MOCK_BOUNDARY_ARTIFACT",
            }
        }

        orch_state = MagicMock()
        orch_state.targets = {}

        result = _compute_file_health(spec_functions, mutation_cache, orch_state)
        assert result["vetoed"] is True
        assert result["vetoes"]["mock_boundary"] is True

    def test_reconciliation_uses_live_orchestrator_kill_rate(self):
        """Fresh target kill_rate should drive reconciliation over stale cache survival."""
        from unittest.mock import MagicMock, patch

        from mcp_tools._platonic_impl import _compute_file_health

        spec_functions = {
            "mod.py::func": {
                "specification_level": 0.1,
                "testability_score": 0.5,
                "sigma": 10,
                "regime": "A",
                "phase": "bulk",
            }
        }
        mutation_cache = {
            "mod.py::func": {
                "survival_rate": 1.0,
                "total_mutants": 12,
                "discovery_state": "DISCOVERY_OK",
                "topology_state": "NORMAL",
                "survival_interpretation": "MEANINGFUL",
            }
        }

        target = MagicMock()
        target.kill_rate = 0.9
        target.convergence_rate = 0.5

        orch_state = MagicMock()
        orch_state.targets = {"mod.py::func": target}

        seen_survival: list[float] = []

        def fake_build_overlay(func_key, sigma, regime, phase, mutation_cache_arg):
            del sigma, regime, phase
            seen_survival.append(mutation_cache_arg[func_key]["survival_rate"])
            return EmpiricalOverlay(
                status=OverlayStatus.CONTRADICTS,
                empirical_survival_rate=mutation_cache_arg[func_key]["survival_rate"],
                overlay_confidence=1.0,
            )

        with patch(
            "lintgate.specification.static_empirical_reconciliation.build_overlay",
            side_effect=fake_build_overlay,
        ):
            result = _compute_file_health(spec_functions, mutation_cache, orch_state)

        assert seen_survival == [pytest.approx(0.1)]
        assert result["reconciliation_active"] is True
        assert result["axes"]["spec_level"] == pytest.approx(0.9)
        assert result["axes"]["static_spec_level"] == pytest.approx(0.1)


# ── Phase 4: _reconciliation_priority ────────────────────────────


class TestReconciliationPriority:
    def test_both_under_specified(self):
        from lintgate.specification.project_rollup import _reconciliation_priority

        score, reason = _reconciliation_priority({}, "AGREES", 0.9, 0.7, 0.1, 10)
        assert score == 3.0
        assert reason == "both_under_specified"

    def test_under_specified_contradiction(self):
        from lintgate.specification.project_rollup import _reconciliation_priority

        score, reason = _reconciliation_priority({}, "CONTRADICTS", 0.9, 0.7, 0.1, 10)
        assert score == 2.5
        assert reason == "under_specified_contradiction"

    def test_genuinely_under_specified(self):
        from lintgate.specification.project_rollup import _reconciliation_priority

        score, reason = _reconciliation_priority({}, "AGREES", 0.9, 0.3, 0.1, 10)
        assert score == 2.0
        assert reason == "genuinely_under_specified"

    def test_measurement_artifact(self):
        from lintgate.specification.project_rollup import _reconciliation_priority

        score, reason = _reconciliation_priority({}, "CONTRADICTS", 0.9, 0.1, 0.1, 10)
        assert score == 0.5
        assert reason == "measurement_artifact"

    def test_needs_profiling(self):
        from lintgate.specification.project_rollup import _reconciliation_priority

        score, reason = _reconciliation_priority({}, "NO_EMPIRICAL_DATA", 0.0, 0.0, 0.1, 10)
        assert score == 1.5
        assert reason == "needs_profiling"

    def test_default_case(self):
        from lintgate.specification.project_rollup import _reconciliation_priority

        score, reason = _reconciliation_priority({}, "AGREES", 0.9, 0.3, 0.5, 10)
        assert score == 1.0
        assert reason == "default"


# ── Phase 6A: prescribe() overlay adjustment ─────────────────────


class TestPrescribeOverlay:
    def test_contradicts_low_survival_deprioritizes(self):
        """Low survival + CONTRADICTS → measurement artifact → P0→P1."""
        from unittest.mock import MagicMock

        from lintgate.specification.prescriptions import prescribe

        fs = MagicMock()
        fs.core.phase = "bulk"
        fs.core.estimated_sigma = 10
        fs.core.specification_level = 0.1
        fs.core.is_pure = False
        fs.traceability.assertion_count = 2
        fs.traceability.prescription_history = []
        fs.risk.priority_band = "P0"
        fs.design_signals.equivalence_partitions = 3
        fs.design_signals.boundary_points = 2
        fs.function_key = "mod.py::f"

        overlay = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            overlay_confidence=0.9,
            empirical_survival_rate=0.1,  # low survival
        )

        rxs = prescribe(fs, overlay=overlay)
        # All P0 prescriptions should have been deprioritized to P1
        assert all(rx.priority_band != "P0" for rx in rxs)

    def test_contradicts_high_survival_boosts(self):
        """High survival + CONTRADICTS → both agree bad → P2→P1."""
        from unittest.mock import MagicMock

        from lintgate.specification.prescriptions import prescribe

        fs = MagicMock()
        fs.core.phase = "bulk"
        fs.core.estimated_sigma = 10
        fs.core.specification_level = 0.1
        fs.core.is_pure = False
        fs.traceability.assertion_count = 2
        fs.traceability.prescription_history = []
        fs.risk.priority_band = "P2"
        fs.design_signals.equivalence_partitions = 3
        fs.design_signals.boundary_points = 2
        fs.function_key = "mod.py::f"

        overlay = EmpiricalOverlay(
            status=OverlayStatus.CONTRADICTS,
            overlay_confidence=0.9,
            empirical_survival_rate=0.7,  # high survival
        )

        rxs = prescribe(fs, overlay=overlay)
        # P2 prescriptions should have been boosted to P1
        boosted = [rx for rx in rxs if rx.priority_band == "P1"]
        assert len(boosted) > 0

    def test_no_overlay_no_change(self):
        """Without overlay, prescriptions are unchanged."""
        from unittest.mock import MagicMock

        from lintgate.specification.prescriptions import prescribe

        fs = MagicMock()
        fs.core.phase = "bulk"
        fs.core.estimated_sigma = 10
        fs.core.specification_level = 0.1
        fs.core.is_pure = False
        fs.traceability.assertion_count = 2
        fs.traceability.prescription_history = []
        fs.risk.priority_band = "P0"
        fs.design_signals.equivalence_partitions = 3
        fs.function_key = "mod.py::f"

        rxs = prescribe(fs, overlay=None)
        # Should work without error
        assert isinstance(rxs, list)


# ── Phase 6B: scheduler priority boost ───────────────────────────


class TestSchedulerReconciliationBoost:
    def test_contradicts_boost(self):
        from lintgate.specification.scheduler import _compute_priority

        base = _compute_priority(sigma=10, risk_score=0.5)
        boosted = _compute_priority(
            sigma=10,
            risk_score=0.5,
            overlay_status="CONTRADICTS",
            overlay_confidence=0.9,
        )
        assert boosted == base + 15

    def test_no_empirical_data_boost(self):
        from lintgate.specification.scheduler import _compute_priority

        base = _compute_priority(sigma=10, risk_score=0.5)
        boosted = _compute_priority(
            sigma=10,
            risk_score=0.5,
            overlay_status="NO_EMPIRICAL_DATA",
        )
        assert boosted == base + 8

    def test_agrees_no_boost(self):
        from lintgate.specification.scheduler import _compute_priority

        base = _compute_priority(sigma=10, risk_score=0.5)
        same = _compute_priority(
            sigma=10,
            risk_score=0.5,
            overlay_status="AGREES",
            overlay_confidence=0.9,
        )
        assert same == base

    def test_contradicts_low_confidence_no_boost(self):
        from lintgate.specification.scheduler import _compute_priority

        base = _compute_priority(sigma=10, risk_score=0.5)
        same = _compute_priority(
            sigma=10,
            risk_score=0.5,
            overlay_status="CONTRADICTS",
            overlay_confidence=0.5,
        )
        assert same == base


# ── Phase 1C: config parsing ────────────────────────────────────


class TestConfigReconciliationThreshold:
    def test_default_threshold(self):
        from lintgate.controlplane.types import TestRegenerationConfig

        config = TestRegenerationConfig()
        assert config.reconciliation_confidence_threshold == 0.7

    def test_parsed_from_yaml(self, tmp_path):
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text(
            "controlplane:\n"
            "  enabled: true\n"
            "  test_regeneration:\n"
            "    reconciliation_confidence_threshold: 0.85\n"
        )
        from lintgate.config import load_controlplane_config

        cfg = load_controlplane_config(str(tmp_path))
        assert cfg is not None
        assert cfg.test_regeneration.reconciliation_confidence_threshold == pytest.approx(0.85)


class TestPrescriptionOverlayPropagation:
    def test_collect_prescriptions_passes_overlay_from_mutation_cache(self):
        from types import SimpleNamespace

        from mcp_tools._specification_helpers import _collect_prescriptions

        fs = SimpleNamespace(
            function_key="mod.py::f",
            core=SimpleNamespace(
                estimated_sigma=10,
                regime="A",
                phase="bulk",
            ),
        )
        calls: list[EmpiricalOverlay] = []

        def fake_prescribe(func_spec, max_prescriptions, regression_mode, overlay=None):
            del func_spec, max_prescriptions, regression_mode
            calls.append(overlay)
            return []

        _collect_prescriptions(
            {"mod.py::f": fs},
            max_prescriptions=5,
            regression_mode=False,
            prescribe_fn=fake_prescribe,
            mutation_cache={
                "mod.py::f": {
                    "survival_rate": 0.1,
                    "total_mutants": 10,
                    "discovery_state": "DISCOVERY_OK",
                    "topology_state": "NORMAL",
                    "survival_interpretation": "MEANINGFUL",
                }
            },
        )

        assert len(calls) == 1
        assert isinstance(calls[0], EmpiricalOverlay)
        assert calls[0].status in (OverlayStatus.AGREES, OverlayStatus.CONTRADICTS)


# ── Regression: no mutation cache → identical behavior ───────────


class TestRegressionNoCacheUnchanged:
    def test_reconcile_spec_level_no_cache_overlay(self):
        overlay = build_overlay("mod.py::f", 10, "A", "bulk", None)
        val, src = reconcile_spec_level(0.1, overlay)
        assert val == 0.1
        assert src == "static"

    def test_compute_health_no_reconciliation(self):
        from lintgate.specification.health_vector import HealthAxis, compute_health

        h = compute_health(0.5, 0.5, 0.5, 0.0, 0.5)
        assert h.reconciliation_active is False
        assert h.axes[HealthAxis.SPEC_LEVEL.value] == pytest.approx(0.5)
        assert "static_spec_level" not in h.axes
