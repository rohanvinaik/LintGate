"""Tests for mcp_tools._test_regeneration_gates validation gates."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from mcp_tools._test_regeneration_gates import (
    _build_scorecard,
    _check_artifact_gate,
    _check_generated_gate,
    _check_quality_gates,
    _sample_kill_rates,
)


def _mock_func(strategy_name, function_key="m::f", discovery_state=""):
    from lintgate.specification.test_regeneration_strategy import Strategy

    strategy_map = {s.name: s for s in Strategy}
    func = MagicMock()
    func.strategy = strategy_map.get(strategy_name, strategy_name)
    func.evidence.function_key = function_key
    func.evidence.discovery_state = discovery_state
    return func


def _mock_plan(functions):
    plan = MagicMock()
    plan.functions = functions
    return plan


class TestCheckArtifactGate:
    def test_no_auto_targets(self):
        plan = _mock_plan([_mock_func("PRESERVE_SYSTEM")])
        gate_pass, gates = _check_artifact_gate(plan, {}, True)
        assert gates["no_artifact_auto_targets"] is True
        assert gate_pass is True

    def test_clean_auto_targets(self):
        plan = _mock_plan(
            [
                _mock_func("AUTO_GENERATE_UNIT", discovery_state="GROUNDED"),
            ]
        )
        _, gates = _check_artifact_gate(plan, {}, True)
        assert gates["no_artifact_auto_targets"] is True

    def test_artifact_auto_target_fails(self):
        plan = _mock_plan(
            [
                _mock_func("AUTO_GENERATE_UNIT", discovery_state="DISCOVERY_ARTIFACT"),
            ]
        )
        gate_pass, gates = _check_artifact_gate(plan, {}, True)
        assert gates["no_artifact_auto_targets"] is False
        assert gate_pass is False


class TestCheckGeneratedGate:
    def test_no_auto_no_generated_passes(self):
        """No auto targets and no generated dir → pass."""
        plan = _mock_plan([_mock_func("PRESERVE_SYSTEM")])
        gate_pass, gates = _check_generated_gate(plan, "/nonexistent", {}, True)
        assert gates["generated_tests_run"] is True
        assert gate_pass is True

    def test_auto_targets_no_generated_fails(self):
        """Auto targets exist but no generated tests → fail."""
        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT")])
        gate_pass, gates = _check_generated_gate(plan, "/nonexistent", {}, True)
        assert gates["generated_tests_run"] is False
        assert gate_pass is False


class TestSampleKillRates:
    def test_no_auto_funcs(self):
        plan = _mock_plan([_mock_func("PRESERVE_SYSTEM")])
        rates, zero = _sample_kill_rates(plan, "/tmp/proj")
        assert rates == []
        assert zero == 0

    @patch("mcp_tools._mutation_impl.iter_cached_states")
    @patch("mcp_tools._mutation_impl.get_cache_dir")
    def test_with_cached_states(self, mock_dir, mock_iter):
        mock_dir.return_value = "/tmp/cache"
        mock_iter.return_value = [
            {"function_key": "m::f", "survival_rate": 0.2},
            {"function_key": "m::g", "survival_rate": 1.0},
        ]
        plan = _mock_plan(
            [
                _mock_func("AUTO_GENERATE_UNIT", function_key="m::f"),
                _mock_func("AUTO_GENERATE_UNIT", function_key="m::g"),
            ]
        )
        rates, zero = _sample_kill_rates(plan, "/tmp/proj")
        assert len(rates) == 2
        assert rates[0] == pytest.approx(0.8)
        assert rates[1] == pytest.approx(0.0)
        assert zero == 1

    @patch("mcp_tools._mutation_impl.iter_cached_states", side_effect=Exception)
    @patch("mcp_tools._mutation_impl.get_cache_dir", return_value="/tmp/cache")
    def test_cache_error_degrades(self, _dir, _iter):
        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT")])
        rates, _ = _sample_kill_rates(plan, "/tmp/proj")
        assert rates == []


class TestCheckQualityGates:
    def _helpers(self):
        return {"_validate_project_root": lambda p: p, "_json_dumps": str}

    @patch("mcp_tools._test_regeneration_gates._sample_kill_rates", return_value=([], 0))
    def test_no_auto_no_data_passes(self, _mock):
        """No auto targets, no mutation data → kill gates pass."""
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan,
            "/tmp/proj",
            self._helpers(),
            {},
            True,
        )
        assert gates["kill_rate_ok"] is True
        assert gates["zero_kill_ok"] is True
        assert gate_pass is True

    @patch("mcp_tools._test_regeneration_gates._sample_kill_rates", return_value=([], 0))
    def test_auto_targets_no_data_fails(self, _mock):
        """Auto targets exist but no mutation data → kill gates fail."""
        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT")])
        gate_pass, gates = _check_quality_gates(
            plan,
            "/tmp/proj",
            self._helpers(),
            {},
            True,
        )
        assert gates["kill_rate_ok"] is False
        assert gates["zero_kill_ok"] is False
        assert gate_pass is False

    @patch(
        "mcp_tools._test_regeneration_gates._sample_kill_rates", return_value=([0.9, 0.8, 0.7], 0)
    )
    def test_high_kill_rate_passes(self, _mock):
        plan = _mock_plan([])
        _, gates = _check_quality_gates(
            plan,
            "/tmp/proj",
            self._helpers(),
            {},
            True,
        )
        assert gates["kill_rate_ok"] is True
        assert gates["kill_rate"] == pytest.approx(0.8, abs=0.01)

    @patch("mcp_tools._test_regeneration_gates._sample_kill_rates", return_value=([0.3, 0.2], 0))
    def test_low_kill_rate_fails(self, _mock):
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan,
            "/tmp/proj",
            self._helpers(),
            {},
            True,
        )
        assert gates["kill_rate_ok"] is False
        assert gate_pass is False

    @patch(
        "mcp_tools._test_regeneration_gates._sample_kill_rates",
        return_value=([0.0, 0.0, 0.0, 0.9], 3),
    )
    def test_zero_kill_ceiling_fails(self, _mock):
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan,
            "/tmp/proj",
            self._helpers(),
            {},
            True,
        )
        assert gates["zero_kill_ok"] is False
        assert gate_pass is False

    @patch("mcp_tools._test_regeneration_gates._sample_kill_rates", return_value=([0.9, 0.8], 0))
    def test_effectiveness_advisory(self, _mock):
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan,
            "/tmp/proj",
            self._helpers(),
            {},
            True,
        )
        assert gates["effectiveness_ok"] is True
        assert gate_pass is True

    @patch("mcp_tools._test_regeneration_gates._sample_kill_rates", return_value=([0.9], 0))
    def test_redundancy_advisory(self, _mock):
        plan = _mock_plan([])
        _, gates = _check_quality_gates(
            plan,
            "/tmp/proj",
            self._helpers(),
            {},
            True,
        )
        assert gates["redundancy_ok"] is True


class TestBuildScorecard:
    def test_all_pass(self):
        gates = {
            "preserve_tests_pass": True,
            "generated_tests_run": True,
            "review_share_ok": True,
            "kill_rate_ok": True,
            "hygiene_ok": True,
        }
        lines = _build_scorecard(gates)
        assert len(lines) == 5
        assert all("[PASS]" in line for line in lines)

    def test_mixed_results(self):
        gates = {"preserve_tests_pass": True, "kill_rate_ok": False}
        lines = _build_scorecard(gates)
        assert "[PASS] Preserve pass" in lines[0]
        assert "[FAIL] Kill rate" in lines[1]

    def test_empty_gates(self):
        assert _build_scorecard({}) == []

    def test_unknown_keys_ignored(self):
        assert _build_scorecard({"some_unknown_gate": True}) == []


class TestValidationPersistence:
    def test_persist_and_load(self, tmp_path):
        from mcp_tools._test_regeneration_apply import (
            _load_validation,
            persist_validation,
        )

        persist_validation(str(tmp_path), {"kill_rate_ok": True}, True)
        result = _load_validation(str(tmp_path))
        assert result is not None
        assert result["ready_to_apply"] is True
        assert result["gates"]["kill_rate_ok"] is True

    def test_load_missing(self, tmp_path):
        from mcp_tools._test_regeneration_apply import _load_validation

        assert _load_validation(str(tmp_path)) is None

    def test_apply_requires_validation(self, tmp_path):
        """Apply must refuse if no validation result exists."""
        from lintgate.specification._regeneration_types import (
            RebuildManifest,
            write_manifest,
        )
        from mcp_tools._test_regeneration_apply import impl_rebuild_apply

        # Write a manifest but no validation
        manifest = RebuildManifest(project_root=str(tmp_path))
        write_manifest(manifest, str(tmp_path))

        helpers = {
            "_validate_project_root": lambda p: str(tmp_path),
            "_json_dumps": lambda d, **kw: json.dumps(d),
        }
        raw = impl_rebuild_apply(helpers, str(tmp_path))
        result = json.loads(raw)
        assert "error" in result
        assert "validation" in result["error"].lower() or "validate" in result["error"].lower()


class TestManifestRoundTrip:
    def test_evidence_survives_roundtrip(self, tmp_path):
        """All evidence fields must survive write→load."""
        from lintgate.specification._regeneration_types import (
            ClassificationResult,
            ExistingTestAction,
            FunctionEvidence,
            MutationEvidence,
            RebuildManifest,
            SpecEvidence,
            Strategy,
            load_manifest,
            write_manifest,
        )

        ev = FunctionEvidence(
            function_key="mod::func",
            source_file="mod/func.py",
            spec=SpecEvidence(
                specification_level=0.75,
                sigma_upper_bound=5,
                regime="A",
                phase="transition",
                is_pure=True,
                is_stateful=False,
                has_side_effects=True,
                testability_score=0.9,
            ),
            mutation=MutationEvidence(
                discovery_state="GROUNDED",
                topology_state="LOCAL",
                survival_interpretation="partial",
                survival_rate=0.3,
                tests_loaded=4,
            ),
            covering_tests=["tests/test_mod.py"],
            assertion_count=7,
        )
        cr = ClassificationResult(
            function_key="mod::func",
            strategy=Strategy.AUTO_GENERATE_UNIT,
            existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
            target_test_file="tests/generated/test_mod_func.py",
            confidence=0.85,
            reason_codes=["auto_ok"],
            evidence=ev,
        )
        manifest = RebuildManifest(
            project_root=str(tmp_path),
            functions=[cr],
        )
        write_manifest(manifest, str(tmp_path))
        loaded = load_manifest(str(tmp_path))
        assert loaded is not None

        lev = loaded.functions[0].evidence
        assert lev.source_file == "mod/func.py"
        assert lev.specification_level == pytest.approx(0.75)
        assert lev.sigma_upper_bound == 5
        assert lev.is_pure is True
        assert lev.has_side_effects is True
        assert lev.testability_score == pytest.approx(0.9)
        assert lev.survival_rate == pytest.approx(0.3)
        assert lev.tests_loaded == 4
        assert lev.covering_tests == ["tests/test_mod.py"]
        assert lev.assertion_count == 7


class TestQuarantinePathPreservation:
    def test_nested_paths_no_collision(self, tmp_path):
        """Quarantine must preserve directory structure to avoid collisions."""
        from mcp_tools._test_regeneration_apply import _quarantine_files

        # Create two files with same basename in different dirs
        (tmp_path / "tests" / "api").mkdir(parents=True)
        (tmp_path / "tests" / "core").mkdir(parents=True)
        (tmp_path / "tests" / "api" / "test_utils.py").write_text("# api")
        (tmp_path / "tests" / "core" / "test_utils.py").write_text("# core")

        plan = MagicMock()
        plan.quarantine_test_files = [
            "tests/api/test_utils.py",
            "tests/core/test_utils.py",
        ]

        actions = _quarantine_files(plan, str(tmp_path), False, [])
        assert len(actions) == 2
        # Destinations must differ
        dests = {a["destination"] for a in actions}
        assert len(dests) == 2
        # Both quarantined files must exist
        for a in actions:
            assert os.path.isfile(os.path.join(str(tmp_path), a["destination"]))
