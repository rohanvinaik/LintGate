"""Tests for lintgate.specification.test_regeneration_strategy.

Mutation-killing tests for the four-strategy classifier, confidence
computation, evidence builder, manifest operations, and name-based vetoes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lintgate.specification.test_regeneration_strategy import (
    ClassificationResult,
    ExistingTestAction,
    FunctionEvidence,
    MutationEvidence,
    RebuildManifest,
    SpecEvidence,
    Strategy,
    _compute_confidence,
    _compute_target_test_file,
    _has_high_risk_name,
    _has_system_surface_name,
    _is_entrypoint_surface,
    build_evidence,
    build_manifest,
    classify_function,
    load_manifest,
    write_manifest,
)


def _ev(
    function_key: str = "",
    source_file: str = "",
    *,
    specification_level: float = 0.0,
    sigma_upper_bound: int = 0,
    regime: str = "unknown",
    phase: str = "bulk",
    is_pure: bool = False,
    is_stateful: bool = False,
    has_side_effects: bool = False,
    testability_score: float = 1.0,
    discovery_state: str = "",
    topology_state: str = "",
    survival_interpretation: str = "",
    survival_rate: float = 1.0,
    tests_loaded: int = 0,
    covering_tests: list[str] | None = None,
    assertion_count: int = 0,
) -> FunctionEvidence:
    """Factory for FunctionEvidence with flat kwargs."""
    return FunctionEvidence(
        function_key=function_key,
        source_file=source_file,
        spec=SpecEvidence(
            specification_level=specification_level,
            sigma_upper_bound=sigma_upper_bound,
            regime=regime,
            phase=phase,
            is_pure=is_pure,
            is_stateful=is_stateful,
            has_side_effects=has_side_effects,
            testability_score=testability_score,
        ),
        mutation=MutationEvidence(
            discovery_state=discovery_state,
            topology_state=topology_state,
            survival_interpretation=survival_interpretation,
            survival_rate=survival_rate,
            tests_loaded=tests_loaded,
        ),
        covering_tests=covering_tests or [],
        assertion_count=assertion_count,
    )


# ── _is_entrypoint_surface ───────────────────────────────────────────


class TestIsEntrypointSurface:
    def test_bare_main_is_entrypoint(self) -> None:
        assert _is_entrypoint_surface("module::main") is True

    def test_bare_cli_is_entrypoint(self) -> None:
        assert _is_entrypoint_surface("module::cli") is True

    def test_bare_run_is_entrypoint(self) -> None:
        assert _is_entrypoint_surface("module::run") is True

    def test_bare_entry_is_entrypoint(self) -> None:
        assert _is_entrypoint_surface("module::entry") is True

    def test_dunder_main_is_entrypoint(self) -> None:
        assert _is_entrypoint_surface("module::__main__") is True

    def test_dotted_name_uses_last_part(self) -> None:
        assert _is_entrypoint_surface("module::SomeClass.main") is True

    def test_non_entrypoint_name(self) -> None:
        assert _is_entrypoint_surface("module::compute_score") is False

    def test_partial_match_not_entrypoint(self) -> None:
        assert _is_entrypoint_surface("module::main_loop") is False

    def test_case_insensitive(self) -> None:
        assert _is_entrypoint_surface("module::MAIN") is True
        assert _is_entrypoint_surface("module::Main") is True

    def test_empty_string(self) -> None:
        assert _is_entrypoint_surface("") is False

    def test_no_separator(self) -> None:
        assert _is_entrypoint_surface("main") is True


# ── _has_high_risk_name ──────────────────────────────────────────────


class TestHasHighRiskName:
    def test_hook_fragment(self) -> None:
        assert _has_high_risk_name("module::post_hook_handler") is True

    def test_register_fragment(self) -> None:
        assert _has_high_risk_name("module::register_tools") is True

    def test_bootstrap_fragment(self) -> None:
        assert _has_high_risk_name("module::bootstrap_context") is True

    def test_posttooluse_fragment(self) -> None:
        assert _has_high_risk_name("module::posttooluse_callback") is True

    def test_setup_fragment(self) -> None:
        assert _has_high_risk_name("module::setup_env") is True

    def test_teardown_fragment(self) -> None:
        assert _has_high_risk_name("module::teardown_fixtures") is True

    def test_conftest_fragment(self) -> None:
        assert _has_high_risk_name("module::conftest_helper") is True

    def test_normal_name_no_risk(self) -> None:
        assert _has_high_risk_name("module::compute_sigma") is False

    def test_case_insensitive(self) -> None:
        assert _has_high_risk_name("module::HOOK_handler") is True


# ── _has_system_surface_name ─────────────────────────────────────────


class TestHasSystemSurfaceName:
    def test_cli_fragment(self) -> None:
        assert _has_system_surface_name("module::cli_parser") is True

    def test_callback_fragment(self) -> None:
        assert _has_system_surface_name("module::on_callback") is True

    def test_pretooluse_fragment(self) -> None:
        assert _has_system_surface_name("module::pretooluse_guard") is True

    def test_normal_name(self) -> None:
        assert _has_system_surface_name("module::adapt_specification") is False


# ── _compute_confidence ──────────────────────────────────────────────


class TestComputeConfidence:
    def test_normal_topology_zero_survival_bulk(self) -> None:
        ev = _ev(topology_state="NORMAL", survival_rate=0.0, phase="bulk")
        assert abs(_compute_confidence(ev) - 0.6) < 1e-10

    def test_normal_topology_zero_survival_complete(self) -> None:
        ev = _ev(topology_state="NORMAL", survival_rate=0.0, phase="complete")
        assert abs(_compute_confidence(ev) - 1.0) < 1e-10

    def test_abnormal_topology_caps_at_half(self) -> None:
        ev = _ev(topology_state="DIVERGENT", survival_rate=0.0, phase="complete")
        assert abs(_compute_confidence(ev) - 0.5) < 1e-10

    def test_artifact_discovery_caps_at_point_two(self) -> None:
        ev = _ev(
            topology_state="NORMAL",
            discovery_state="DISCOVERY_ARTIFACT",
            survival_rate=0.0,
            phase="complete",
        )
        assert abs(_compute_confidence(ev) - 0.2) < 1e-10

    def test_survival_rate_reduces_mutation_conf(self) -> None:
        ev = _ev(topology_state="NORMAL", survival_rate=0.7, phase="complete")
        assert abs(_compute_confidence(ev) - 0.3) < 1e-10

    def test_transition_phase_weight(self) -> None:
        ev = _ev(survival_rate=0.0, phase="transition")
        assert abs(_compute_confidence(ev) - 0.8) < 1e-10

    def test_tail_phase_weight(self) -> None:
        ev = _ev(survival_rate=0.0, phase="tail")
        assert abs(_compute_confidence(ev) - 0.95) < 1e-10

    def test_unknown_phase_defaults_to_bulk_weight(self) -> None:
        ev = _ev(survival_rate=0.0, phase="unknown_phase")
        assert abs(_compute_confidence(ev) - 0.6) < 1e-10

    def test_both_topology_and_survival_take_min(self) -> None:
        ev = _ev(topology_state="DIVERGENT", survival_rate=0.8, phase="complete")
        # min(0.5, 0.2) * 1.0 = 0.2
        assert abs(_compute_confidence(ev) - 0.2) < 1e-10

    def test_empty_topology_treated_as_normal(self) -> None:
        ev = _ev(survival_rate=0.0, phase="bulk")
        assert abs(_compute_confidence(ev) - 0.6) < 1e-10


# ── _compute_target_test_file ────────────────────────────────────────


class TestComputeTargetTestFile:
    def test_simple_path(self) -> None:
        assert (
            _compute_target_test_file("lintgate/foo.py") == "tests/generated/test_lintgate_foo.py"
        )

    def test_nested_path(self) -> None:
        assert (
            _compute_target_test_file("lintgate/a/b/bar.py")
            == "tests/generated/test_lintgate_a_b_bar.py"
        )

    def test_no_py_extension(self) -> None:
        assert _compute_target_test_file("lintgate/baz") == "tests/generated/test_lintgate_baz.py"

    def test_basename_only(self) -> None:
        assert _compute_target_test_file("module.py") == "tests/generated/test_module.py"

    def test_no_collision(self) -> None:
        """Different source paths with same basename produce different targets."""
        a = _compute_target_test_file("lintgate/api/utils.py")
        b = _compute_target_test_file("lintgate/core/utils.py")
        assert a != b


# ── classify_function ────────────────────────────────────────────────


class TestClassifyFunction:
    """Tests for the four-tier strategy classifier."""

    # ── Tier 1: Hard exclusions ──────────────────────────────────

    def test_discovery_artifact_excludes(self) -> None:
        ev = _ev("mod::func", "mod.py", discovery_state="DISCOVERY_ARTIFACT")
        result = classify_function(ev)
        assert result.strategy == Strategy.EXCLUDE_MUTATION
        assert result.confidence == 0.0
        assert "discovery_artifact" in result.reason_codes
        assert result.existing_test_action == ExistingTestAction.PRESERVE

    def test_tests_linked_zero_kills_excludes(self) -> None:
        ev = _ev("mod::func", "mod.py", discovery_state="TESTS_LINKED_ZERO_KILLS")
        assert classify_function(ev).strategy == Strategy.EXCLUDE_MUTATION

    def test_mock_boundary_artifact_excludes(self) -> None:
        ev = _ev("mod::func", "mod.py", discovery_state="MOCK_BOUNDARY_ARTIFACT")
        assert classify_function(ev).strategy == Strategy.EXCLUDE_MUTATION

    def test_entrypoint_surface_excludes(self) -> None:
        ev = _ev("mod::main", "mod.py")
        result = classify_function(ev)
        assert result.strategy == Strategy.EXCLUDE_MUTATION
        assert "entrypoint_surface" in result.reason_codes

    def test_artifact_checked_before_entrypoint(self) -> None:
        """Artifact veto takes priority over entrypoint veto."""
        ev = _ev("mod::main", "mod.py", discovery_state="DISCOVERY_ARTIFACT")
        result = classify_function(ev)
        assert "discovery_artifact" in result.reason_codes
        assert "entrypoint_surface" not in result.reason_codes

    # ── Tier 2: Preserve-system ──────────────────────────────────

    def test_integration_coverage_with_system_name_preserves(self) -> None:
        ev = _ev(
            "mod::register_hooks",
            "mod.py",
            covering_tests=["t1.py", "t2.py", "t3.py"],
        )
        result = classify_function(ev)
        assert result.strategy == Strategy.PRESERVE_SYSTEM
        assert result.confidence == 0.9
        assert "integration_coverage" in result.reason_codes
        assert "system_surface" in result.reason_codes
        assert result.existing_test_action == ExistingTestAction.PRESERVE

    def test_many_tests_without_system_name_not_preserved(self) -> None:
        ev = _ev(
            "mod::compute_sigma",
            "mod.py",
            covering_tests=["t1.py", "t2.py", "t3.py"],
            is_pure=True,
            sigma_upper_bound=5,
        )
        assert classify_function(ev).strategy != Strategy.PRESERVE_SYSTEM

    def test_system_name_without_enough_tests_not_preserved(self) -> None:
        ev = _ev(
            "mod::register_tools",
            "mod.py",
            covering_tests=["t1.py", "t2.py"],
            is_stateful=True,
        )
        assert classify_function(ev).strategy != Strategy.PRESERVE_SYSTEM

    # ── Tier 3: Auto-generate-unit ───────────────────────────────

    def test_pure_function_with_signal_auto_generates(self) -> None:
        ev = _ev(
            "mod::compute",
            "lintgate/foo.py",
            is_pure=True,
            sigma_upper_bound=5,
            topology_state="NORMAL",
            survival_interpretation="MEANINGFUL",
            survival_rate=0.3,
            phase="bulk",
        )
        result = classify_function(ev)
        assert result.strategy == Strategy.AUTO_GENERATE_UNIT
        assert "pure_or_local" in result.reason_codes
        assert "mutation_meaningful" in result.reason_codes
        assert result.existing_test_action == ExistingTestAction.QUARANTINE_REPLACE
        assert result.target_test_file == "tests/generated/test_lintgate_foo.py"
        assert result.generation_mode == "spec+mutation+inputs"

    def test_local_non_stateful_non_sideeffect_auto_generates(self) -> None:
        ev = _ev(
            "mod::helper",
            "lintgate/bar.py",
            is_pure=False,
            is_stateful=False,
            has_side_effects=False,
            sigma_upper_bound=3,
            phase="transition",
        )
        assert classify_function(ev).strategy == Strategy.AUTO_GENERATE_UNIT

    def test_auto_generate_low_confidence_triggers_review(self) -> None:
        ev = _ev(
            "mod::func",
            "lintgate/x.py",
            is_pure=True,
            sigma_upper_bound=2,
            topology_state="NORMAL",
            survival_interpretation="MEANINGFUL",
            survival_rate=0.8,
            phase="bulk",
        )
        result = classify_function(ev)
        assert result.strategy == Strategy.AUTO_GENERATE_UNIT
        assert result.manual_review_required is True
        assert result.confidence < 0.5

    def test_auto_generate_high_confidence_no_review(self) -> None:
        ev = _ev(
            "mod::func",
            "lintgate/x.py",
            is_pure=True,
            sigma_upper_bound=2,
            topology_state="NORMAL",
            survival_interpretation="MEANINGFUL",
            survival_rate=0.0,
            phase="complete",
        )
        result = classify_function(ev)
        assert result.strategy == Strategy.AUTO_GENERATE_UNIT
        assert result.manual_review_required is False
        assert result.confidence >= 0.5

    # ── Tier 4: Manual-contract ──────────────────────────────────

    def test_stateful_function_gets_manual_contract(self) -> None:
        ev = _ev("mod::update_state", "mod.py", is_stateful=True, sigma_upper_bound=3)
        result = classify_function(ev)
        assert result.strategy == Strategy.MANUAL_CONTRACT
        assert "stateful_or_side_effects" in result.reason_codes
        assert result.existing_test_action == ExistingTestAction.QUARANTINE_ONLY
        assert result.confidence == 0.3

    def test_side_effects_gets_manual_contract(self) -> None:
        ev = _ev("mod::write_file", "mod.py", has_side_effects=True, sigma_upper_bound=2)
        result = classify_function(ev)
        assert result.strategy == Strategy.MANUAL_CONTRACT
        assert "stateful_or_side_effects" in result.reason_codes

    def test_high_risk_name_gets_manual_contract(self) -> None:
        ev = _ev("mod::hook_handler", "mod.py", is_stateful=True)
        result = classify_function(ev)
        assert result.strategy == Strategy.MANUAL_CONTRACT
        assert "high_risk_name" in result.reason_codes

    def test_topology_abnormal_gets_manual_contract(self) -> None:
        ev = _ev("mod::func", "mod.py", topology_state="DIVERGENT", sigma_upper_bound=3)
        result = classify_function(ev)
        assert result.strategy == Strategy.MANUAL_CONTRACT
        assert "topology_abnormal" in result.reason_codes

    def test_no_signal_gets_conservative_default(self) -> None:
        ev = _ev("mod::func", "mod.py", is_pure=False, sigma_upper_bound=0, topology_state="NORMAL")
        result = classify_function(ev)
        assert result.strategy == Strategy.MANUAL_CONTRACT
        assert "conservative_default" in result.reason_codes

    # ── Edge: artifact trumps everything ─────────────────────────

    def test_artifact_trumps_pure_auto_eligible(self) -> None:
        ev = _ev(
            "mod::compute",
            "mod.py",
            is_pure=True,
            sigma_upper_bound=5,
            discovery_state="DISCOVERY_ARTIFACT",
            topology_state="NORMAL",
            survival_interpretation="MEANINGFUL",
        )
        assert classify_function(ev).strategy == Strategy.EXCLUDE_MUTATION


# ── build_evidence ───────────────────────────────────────────────────


class TestBuildEvidence:
    def test_empty_inputs(self) -> None:
        ev = build_evidence("mod::f", "mod.py")
        assert ev.function_key == "mod::f"
        assert ev.source_file == "mod.py"
        assert ev.specification_level == 0.0
        assert ev.survival_rate == 1.0

    def test_spec_data_populates_fields(self) -> None:
        spec = {
            "specification_level": 0.75,
            "estimated_sigma": 8,
            "regime": "A",
            "phase": "tail",
            "is_pure": True,
            "is_stateful": False,
            "has_side_effects": False,
            "testability_score": 0.9,
            "covering_tests": ["t1", "t2"],
            "assertion_count": 12,
        }
        ev = build_evidence("mod::f", "mod.py", spec_data=spec)
        assert ev.specification_level == 0.75
        assert ev.sigma_upper_bound == 8
        assert ev.regime == "A"
        assert ev.phase == "tail"
        assert ev.is_pure is True
        assert ev.covering_tests == ["t1", "t2"]
        assert ev.assertion_count == 12

    def test_mutation_data_populates_fields(self) -> None:
        mut = {
            "discovery_state": "TESTS_LINKED_ZERO_KILLS",
            "topology_state": "DIVERGENT",
            "survival_interpretation": "ARTIFACT",
            "survival_rate": 0.5,
            "tests_loaded": 3,
        }
        ev = build_evidence("mod::f", "mod.py", mutation_data=mut)
        assert ev.discovery_state == "TESTS_LINKED_ZERO_KILLS"
        assert ev.topology_state == "DIVERGENT"
        assert ev.survival_interpretation == "ARTIFACT"
        assert abs(ev.survival_rate - 0.5) < 1e-10
        assert ev.tests_loaded == 3

    def test_both_sources_compose(self) -> None:
        spec = {"is_pure": True, "estimated_sigma": 4}
        mut = {"survival_rate": 0.2, "discovery_state": ""}
        ev = build_evidence("mod::f", "mod.py", spec, mut)
        assert ev.is_pure is True
        assert ev.sigma_upper_bound == 4
        assert abs(ev.survival_rate - 0.2) < 1e-10

    def test_missing_keys_use_defaults(self) -> None:
        ev = build_evidence("mod::f", "mod.py", spec_data={}, mutation_data={})
        assert ev.specification_level == 0.0
        assert ev.sigma_upper_bound == 0
        assert ev.regime == "unknown"
        assert ev.discovery_state == ""
        assert ev.survival_rate == 1.0


# ── FunctionEvidence.to_dict ─────────────────────────────────────────


class TestFunctionEvidenceToDict:
    def test_roundtrip_fields(self) -> None:
        ev = _ev(
            specification_level=0.123456,
            sigma_upper_bound=5,
            regime="A",
            phase="tail",
            discovery_state="NORMAL",
            topology_state="NORMAL",
            survival_interpretation="MEANINGFUL",
            is_pure=True,
        )
        d = ev.to_dict()
        assert d["specification_level"] == 0.123
        assert d["sigma_upper_bound"] == 5
        assert d["regime"] == "A"
        assert d["phase"] == "tail"
        assert d["purity"] is True


# ── ClassificationResult.to_dict ─────────────────────────────────────


class TestClassificationResultToDict:
    def test_strategy_serialized_as_value(self) -> None:
        cr = ClassificationResult(
            function_key="mod::f",
            strategy=Strategy.AUTO_GENERATE_UNIT,
            existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
            target_test_file="tests/generated/test_mod.py",
            confidence=0.756789,
            reason_codes=["pure_or_local"],
            evidence=FunctionEvidence(),
            generation_mode="spec+mutation+inputs",
            manual_review_required=False,
        )
        d = cr.to_dict()
        assert d["strategy"] == "auto_generate_unit"
        assert d["existing_test_action"] == "quarantine_replace"
        assert d["confidence"] == 0.757
        assert d["generation_mode"] == "spec+mutation+inputs"


# ── build_manifest ───────────────────────────────────────────────────


class TestBuildManifest:
    def _make_result(
        self,
        key: str = "mod::f",
        strategy: Strategy = Strategy.AUTO_GENERATE_UNIT,
        action: ExistingTestAction = ExistingTestAction.QUARANTINE_REPLACE,
        covering: list[str] | None = None,
    ) -> ClassificationResult:
        ev = FunctionEvidence(
            function_key=key,
            source_file="mod.py",
            covering_tests=covering or [],
        )
        return ClassificationResult(
            function_key=key,
            strategy=strategy,
            existing_test_action=action,
            target_test_file="tests/generated/test_mod.py",
            confidence=0.8,
            reason_codes=["pure_or_local"],
            evidence=ev,
        )

    def test_preserve_action_adds_to_preserve_files(self) -> None:
        cr = self._make_result(
            action=ExistingTestAction.PRESERVE,
            covering=["tests/test_a.py"],
        )
        manifest = build_manifest("/tmp/proj", [cr])
        assert "tests/test_a.py" in manifest.preserve_test_files

    def test_quarantine_action_adds_to_quarantine_files(self) -> None:
        cr = self._make_result(
            action=ExistingTestAction.QUARANTINE_REPLACE,
            covering=["tests/test_b.py"],
        )
        manifest = build_manifest("/tmp/proj", [cr])
        assert "tests/test_b.py" in manifest.quarantine_test_files

    def test_preserve_takes_priority_over_quarantine(self) -> None:
        cr1 = self._make_result(
            key="mod::f1",
            action=ExistingTestAction.PRESERVE,
            covering=["tests/test_shared.py"],
        )
        cr2 = self._make_result(
            key="mod::f2",
            action=ExistingTestAction.QUARANTINE_REPLACE,
            covering=["tests/test_shared.py"],
        )
        manifest = build_manifest("/tmp/proj", [cr1, cr2])
        assert "tests/test_shared.py" in manifest.preserve_test_files
        assert "tests/test_shared.py" not in manifest.quarantine_test_files

    def test_non_py_covering_tests_ignored(self) -> None:
        cr = self._make_result(
            action=ExistingTestAction.PRESERVE,
            covering=["test_func_name", "tests/test_x.py"],
        )
        manifest = build_manifest("/tmp/proj", [cr])
        assert "tests/test_x.py" in manifest.preserve_test_files
        assert len(manifest.preserve_test_files) == 1

    def test_version_and_timestamp_set(self) -> None:
        manifest = build_manifest("/tmp/proj", [])
        assert manifest.version == 1
        assert manifest.generated_at != ""
        assert manifest.project_root == "/tmp/proj"


# ── RebuildManifest.summary ──────────────────────────────────────────


class TestManifestSummary:
    def test_empty_manifest_summary(self) -> None:
        s = RebuildManifest().summary()
        assert s["total_functions"] == 0
        assert s["manual_review_share"] == 0.0
        assert s["mean_confidence"] == 0.0

    def test_strategy_distribution(self) -> None:
        funcs = [
            ClassificationResult(
                function_key="a",
                strategy=Strategy.AUTO_GENERATE_UNIT,
                existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
                target_test_file="",
                confidence=0.8,
                reason_codes=[],
                evidence=FunctionEvidence(),
            ),
            ClassificationResult(
                function_key="b",
                strategy=Strategy.AUTO_GENERATE_UNIT,
                existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
                target_test_file="",
                confidence=0.6,
                reason_codes=[],
                evidence=FunctionEvidence(),
            ),
            ClassificationResult(
                function_key="c",
                strategy=Strategy.MANUAL_CONTRACT,
                existing_test_action=ExistingTestAction.QUARANTINE_ONLY,
                target_test_file="",
                confidence=0.3,
                reason_codes=[],
                evidence=FunctionEvidence(),
                manual_review_required=True,
            ),
        ]
        s = RebuildManifest(functions=funcs).summary()
        assert s["total_functions"] == 3
        assert s["strategy_distribution"]["auto_generate_unit"] == 2
        assert s["strategy_distribution"]["manual_contract"] == 1
        assert s["manual_review_required"] == 1
        assert abs(s["manual_review_share"] - 0.333) < 0.01
        assert abs(s["mean_confidence"] - (0.8 + 0.6 + 0.3) / 3) < 0.01


# ── write_manifest / load_manifest roundtrip ─────────────────────────


class TestManifestRoundtrip:
    def test_write_and_load(self, tmp_path: Path) -> None:
        ev = _ev(
            "mod::compute",
            "lintgate/compute.py",
            specification_level=0.75,
            sigma_upper_bound=8,
            regime="A",
            phase="tail",
            is_pure=True,
            topology_state="NORMAL",
            survival_interpretation="MEANINGFUL",
        )
        cr = ClassificationResult(
            function_key="mod::compute",
            strategy=Strategy.AUTO_GENERATE_UNIT,
            existing_test_action=ExistingTestAction.QUARANTINE_REPLACE,
            target_test_file="tests/generated/test_compute.py",
            confidence=0.85,
            reason_codes=["pure_or_local", "mutation_meaningful"],
            evidence=ev,
            generation_mode="spec+mutation+inputs",
            manual_review_required=False,
        )
        manifest = RebuildManifest(
            version=1,
            project_root=str(tmp_path),
            generated_at="2026-03-11T00:00:00Z",
            functions=[cr],
            preserve_test_files=["tests/test_a.py"],
            quarantine_test_files=["tests/test_b.py"],
        )

        path = write_manifest(manifest, str(tmp_path))
        assert os.path.isfile(path)

        loaded = load_manifest(str(tmp_path))
        assert loaded is not None
        assert loaded.version == 1
        assert len(loaded.functions) == 1
        assert loaded.functions[0].strategy == Strategy.AUTO_GENERATE_UNIT
        assert loaded.functions[0].confidence == 0.85
        assert loaded.functions[0].evidence.is_pure is True
        assert loaded.preserve_test_files == ["tests/test_a.py"]
        assert loaded.quarantine_test_files == ["tests/test_b.py"]

    def test_load_missing_manifest_returns_none(self, tmp_path: Path) -> None:
        assert load_manifest(str(tmp_path)) is None

    def test_load_corrupted_json_returns_none(self, tmp_path: Path) -> None:
        lintgate_dir = tmp_path / ".lintgate"
        lintgate_dir.mkdir()
        (lintgate_dir / "test_rebuild_manifest.json").write_text("not json{{{")
        assert load_manifest(str(tmp_path)) is None

    def test_write_creates_lintgate_dir(self, tmp_path: Path) -> None:
        manifest = RebuildManifest(project_root=str(tmp_path))
        path = write_manifest(manifest, str(tmp_path))
        assert (tmp_path / ".lintgate").is_dir()
        assert os.path.isfile(path)


# ── RebuildManifest.to_dict ──────────────────────────────────────────


class TestManifestToDict:
    def test_serialization_structure(self) -> None:
        cr = ClassificationResult(
            function_key="mod::f",
            strategy=Strategy.EXCLUDE_MUTATION,
            existing_test_action=ExistingTestAction.PRESERVE,
            target_test_file="",
            confidence=0.0,
            reason_codes=["discovery_artifact"],
            evidence=FunctionEvidence(),
        )
        m = RebuildManifest(
            version=1,
            project_root="/proj",
            generated_at="2026-01-01T00:00:00Z",
            functions=[cr],
            preserve_test_files=["tests/a.py"],
            quarantine_test_files=[],
        )
        d = m.to_dict()
        assert d["version"] == 1
        assert len(d["functions"]) == 1
        assert d["functions"][0]["strategy"] == "exclude_mutation"
        assert d["preserve_test_files"] == ["tests/a.py"]
        json.dumps(d)
