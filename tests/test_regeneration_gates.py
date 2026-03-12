"""Tests for mcp_tools._test_regeneration_gates validation gates."""

from __future__ import annotations

import json
import os
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from lintgate.testing.fresh_validation import count_test_assertions, run_fresh_kill_rates
from mcp_tools._test_regeneration_gates import (
    _build_scorecard,
    _check_artifact_gate,
    _check_generated_gate,
    _check_quality_gates,
)


def _mock_func(strategy_name, function_key="m::f", discovery_state="",
               source_file="m.py", target_test_file="tests/generated/test_m.py"):
    from lintgate.specification.test_regeneration_strategy import Strategy

    strategy_map = {s.name: s for s in Strategy}
    func = MagicMock()
    func.strategy = strategy_map.get(strategy_name, strategy_name)
    func.evidence.function_key = function_key
    func.evidence.source_file = source_file
    func.evidence.discovery_state = discovery_state
    func.target_test_file = target_test_file
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
        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT", discovery_state="GROUNDED")])
        _, gates = _check_artifact_gate(plan, {}, True)
        assert gates["no_artifact_auto_targets"] is True

    def test_artifact_auto_target_fails(self):
        plan = _mock_plan(
            [_mock_func("AUTO_GENERATE_UNIT", discovery_state="DISCOVERY_ARTIFACT")]
        )
        gate_pass, gates = _check_artifact_gate(plan, {}, True)
        assert gates["no_artifact_auto_targets"] is False
        assert gate_pass is False


class TestCheckGeneratedGate:
    def test_no_auto_no_generated_passes(self):
        plan = _mock_plan([_mock_func("PRESERVE_SYSTEM")])
        gate_pass, gates = _check_generated_gate(plan, "/nonexistent", {}, True)
        assert gates["generated_tests_run"] is True
        assert gate_pass is True

    def test_auto_targets_no_generated_fails(self):
        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT")])
        gate_pass, gates = _check_generated_gate(plan, "/nonexistent", {}, True)
        assert gates["generated_tests_run"] is False
        assert gate_pass is False


class TestCountAssertions:
    def test_empty_file(self, tmp_path):
        f = tmp_path / "test_empty.py"
        f.write_text("def test_noop(): pass\n")
        assert count_test_assertions([str(f)]) == 0

    def test_trivial_assert_true(self, tmp_path):
        f = tmp_path / "test_trivial.py"
        f.write_text("def test_t(): assert True\n")
        assert count_test_assertions([str(f)]) == 0

    def test_real_assertions_counted(self, tmp_path):
        f = tmp_path / "test_real.py"
        f.write_text(textwrap.dedent("""\
            def test_a():
                assert 1 == 1
            def test_b():
                assert foo() is not None
        """))
        assert count_test_assertions([str(f)]) == 2

    def test_mixed_trivial_and_real(self, tmp_path):
        f = tmp_path / "test_mix.py"
        f.write_text(textwrap.dedent("""\
            def test_a():
                assert True
                assert 1 + 1 == 2
        """))
        assert count_test_assertions([str(f)]) == 1

    def test_pass_stub_gate2_fails(self, tmp_path):
        """Gate 2 should fail when generated tests are all pass stubs."""
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_stub.py").write_text(textwrap.dedent("""\
            def test_foo_value_mutation():
                # TODO: fill in
                pass
        """))
        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT")])
        # Patch subprocess to simulate pytest passing (stubs pass)
        with patch("mcp_tools._test_regeneration_gates.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1 passed")
            gate_pass, gates = _check_generated_gate(plan, str(tmp_path), {}, True)
        assert gates["generated_assertion_count"] == 0
        assert gates["generated_tests_run"] is False
        assert gate_pass is False


class TestFreshKillRates:
    def test_no_auto_funcs(self):
        plan = _mock_plan([_mock_func("PRESERVE_SYSTEM")])
        rates, zero, details = run_fresh_kill_rates(plan, "/tmp/proj")
        assert rates == []
        assert zero == 0
        assert details == []

    def test_missing_generated_file(self, tmp_path):
        plan = _mock_plan([_mock_func(
            "AUTO_GENERATE_UNIT",
            target_test_file="tests/generated/test_missing.py",
        )])
        rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))
        assert len(rates) == 1
        assert rates[0] == 0.0
        assert zero == 1
        assert details[0]["status"] == "no_generated_file"

    def test_generated_file_no_callables(self, tmp_path):
        """Generated file exists but has no test_ functions."""
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_m.py").write_text("# empty\n")

        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT")])
        rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))
        assert rates[0] == 0.0
        assert details[0]["status"] == "no_test_callables"

    def test_source_unresolved(self, tmp_path):
        """Source file doesn't exist → source_unresolved."""
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_m.py").write_text("def test_f(): assert True\n")

        plan = _mock_plan([_mock_func(
            "AUTO_GENERATE_UNIT",
            source_file="nonexistent.py",
        )])
        rates, zero, details = run_fresh_kill_rates(plan, str(tmp_path))
        assert details[0]["status"] == "source_unresolved"


class TestCheckQualityGates:
    def _helpers(self):
        return {"_validate_project_root": lambda p: p, "_json_dumps": str}

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([], 0, []),
    )
    def test_no_auto_no_data_passes(self, _mock):
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert gates["kill_rate_ok"] is True
        assert gates["zero_kill_ok"] is True
        assert gate_pass is True

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([], 0, []),
    )
    def test_auto_targets_no_data_fails(self, _mock):
        plan = _mock_plan([_mock_func("AUTO_GENERATE_UNIT")])
        gate_pass, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert gates["kill_rate_ok"] is False
        assert gates["zero_kill_ok"] is False
        assert gate_pass is False

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([0.9, 0.8, 0.7], 0, []),
    )
    def test_high_kill_rate_passes(self, _mock):
        plan = _mock_plan([])
        _, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert gates["kill_rate_ok"] is True
        assert gates["kill_rate"] == pytest.approx(0.8, abs=0.01)

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([0.3, 0.2], 0, []),
    )
    def test_low_kill_rate_fails(self, _mock):
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert gates["kill_rate_ok"] is False
        assert gate_pass is False

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([0.0, 0.0, 0.0, 0.9], 3, []),
    )
    def test_zero_kill_ceiling_fails(self, _mock):
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert gates["zero_kill_ok"] is False
        assert gate_pass is False

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([0.9, 0.8], 0, []),
    )
    def test_effectiveness_advisory(self, _mock):
        plan = _mock_plan([])
        gate_pass, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert gates["effectiveness_ok"] is True
        assert gate_pass is True

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([0.9], 0, []),
    )
    def test_redundancy_advisory(self, _mock):
        plan = _mock_plan([])
        _, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert gates["redundancy_ok"] is True

    @patch(
        "mcp_tools._test_regeneration_gates.run_fresh_kill_rates",
        return_value=([0.9], 0, [{"function_key": "m::f", "status": "sampled"}]),
    )
    def test_sampling_details_in_gates(self, _mock):
        """Fresh sampling details should be visible in gate output."""
        plan = _mock_plan([])
        _, gates = _check_quality_gates(
            plan, "/tmp/proj", self._helpers(), {}, True,
        )
        assert "fresh_sampling_details" in gates
        assert gates["fresh_sampling_details"][0]["status"] == "sampled"


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


class TestValidationPersistence:
    def test_persist_and_load(self, tmp_path):
        from mcp_tools._test_regeneration_apply import _load_validation, persist_validation

        persist_validation(str(tmp_path), {"kill_rate_ok": True}, True)
        result = _load_validation(str(tmp_path))
        assert result is not None
        assert result["ready_to_apply"] is True
        assert result["gates"]["kill_rate_ok"] is True

    def test_load_missing(self, tmp_path):
        from mcp_tools._test_regeneration_apply import _load_validation

        assert _load_validation(str(tmp_path)) is None

    def test_apply_requires_validation(self, tmp_path):
        from lintgate.specification._regeneration_types import RebuildManifest, write_manifest
        from mcp_tools._test_regeneration_apply import impl_rebuild_apply

        manifest = RebuildManifest(project_root=str(tmp_path))
        write_manifest(manifest, str(tmp_path))

        helpers = {
            "_validate_project_root": lambda p: str(tmp_path),
            "_json_dumps": lambda d, **kw: json.dumps(d),
        }
        raw = impl_rebuild_apply(helpers, str(tmp_path))
        result = json.loads(raw)
        assert "error" in result


class TestManifestRoundTrip:
    def test_evidence_survives_roundtrip(self, tmp_path):
        from lintgate.specification._regeneration_types import (
            ClassificationResult, ExistingTestAction, FunctionEvidence,
            MutationEvidence, RebuildManifest, SpecEvidence, Strategy,
            load_manifest, write_manifest,
        )

        ev = FunctionEvidence(
            function_key="mod::func", source_file="mod/func.py",
            spec=SpecEvidence(
                specification_level=0.75, sigma_upper_bound=5, regime="A",
                phase="transition", is_pure=True, is_stateful=False,
                has_side_effects=True, testability_score=0.9,
            ),
            mutation=MutationEvidence(
                discovery_state="GROUNDED", topology_state="LOCAL",
                survival_interpretation="partial", survival_rate=0.3,
                tests_loaded=4,
            ),
            covering_tests=["tests/test_mod.py"], assertion_count=7,
        )
        cr = ClassificationResult(
            function_key="mod::func", strategy=Strategy.AUTO_GENERATE_UNIT,
            existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
            target_test_file="tests/generated/test_mod_func.py",
            confidence=0.85, reason_codes=["auto_ok"], evidence=ev,
        )
        manifest = RebuildManifest(project_root=str(tmp_path), functions=[cr])
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
        from mcp_tools._test_regeneration_apply import _quarantine_files

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
        dests = {a["destination"] for a in actions}
        assert len(dests) == 2
        for a in actions:
            assert os.path.isfile(os.path.join(str(tmp_path), a["destination"]))
