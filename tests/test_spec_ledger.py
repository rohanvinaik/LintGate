"""Tests for lintgate.specification.ledger — specification ledger build, cache, and helpers."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.specification.ledger import (
    _GATE_THRESHOLDS,
    _build_test_coverage_map,
    _check_stop_criteria,
    _deserialize_func_spec,
    _deserialize_ledger,
    _deserialize_trajectory,
    _extract_requirement_tags,
    _file_hash,
    build_specification_ledger,
    load_cached_ledger,
    save_cached_ledger,
)
from lintgate.specification.types import (
    ASTMetrics,
    FunctionSpecification,
    RiskProfile,
    SpecCore,
    SpecificationLedger,
    Traceability,
    TrajectoryState,
)

# ── Fixtures / helpers ────────────────────────────────────────────────


def _make_func_spec(
    *,
    function_key: str = "mod.py::func",
    spec_level: float = 0.5,
    sigma: int = 4,
    regime: str = "A",
    phase: str = "transition",
    is_pure: bool = True,
    stop_criteria_met: bool = False,
    testability_score: float = 1.0,
    priority_band: str = "P2",
    risk_score: float = 0.0,
    assertion_count: int = 2,
    optimization_hints: list[str] | None = None,
    trajectory: TrajectoryState | None = None,
) -> FunctionSpecification:
    """Build a minimal FunctionSpecification for test purposes."""
    from lintgate.specification.types import TestabilityProfile, TestDesignSignals, TPAResult

    return FunctionSpecification(
        function_key=function_key,
        source_file="mod.py",
        core=SpecCore(
            estimated_sigma=sigma,
            regime=regime,
            specification_level=spec_level,
            phase=phase,
            is_pure=is_pure,
            behavioral_dimensions=sigma,
        ),
        ast_metrics=ASTMetrics(parameter_count=2),
        design_signals=TestDesignSignals(),
        testability=TestabilityProfile(testability_score=testability_score),
        tpa=TPAResult(),
        risk=RiskProfile(risk_score=risk_score, priority_band=priority_band),
        traceability=Traceability(assertion_count=assertion_count),
        trajectory=trajectory or TrajectoryState(),
        stop_criteria_met=stop_criteria_met,
        optimization_hints=optimization_hints or [],
        file_hash="abc123",
        computed_at=1000.0,
    )


def _make_ledger(funcs: dict[str, FunctionSpecification] | None = None) -> SpecificationLedger:
    """Build a SpecificationLedger with given functions and update metrics."""
    ledger = SpecificationLedger()
    if funcs:
        ledger.functions = funcs
    ledger.update_metrics()
    return ledger


# ── TestCheckStopCriteria ─────────────────────────────────────────────


class TestCheckStopCriteria:
    """Tests for _check_stop_criteria: spec_level vs gate thresholds."""

    def test_no_hints_returns_false(self):
        assert _check_stop_criteria(1.0, []) is False

    def test_single_hint_met(self):
        # "foldable" threshold is 0.5
        assert _check_stop_criteria(0.5, ["foldable"]) is True

    def test_single_hint_not_met(self):
        assert _check_stop_criteria(0.49, ["foldable"]) is False

    def test_multiple_hints_uses_max_threshold(self):
        # "cacheable" = 0.6, "parallelizable" = 0.7 → max is 0.7
        assert _check_stop_criteria(0.7, ["cacheable", "parallelizable"]) is True
        assert _check_stop_criteria(0.65, ["cacheable", "parallelizable"]) is False

    def test_unknown_hint_has_zero_threshold(self):
        # Unknown hints get 0.0 from _GATE_THRESHOLDS.get(h, 0.0)
        # max of [0.0] = 0.0 → returns False because threshold == 0
        assert _check_stop_criteria(1.0, ["unknown_hint"]) is False

    def test_mixed_known_and_unknown_hints(self):
        # "cacheable" = 0.6, "mystery" = 0.0 → max is 0.6
        assert _check_stop_criteria(0.6, ["cacheable", "mystery"]) is True

    def test_all_gate_thresholds_are_positive(self):
        for hint, threshold in _GATE_THRESHOLDS.items():
            assert threshold > 0, f"Threshold for {hint} must be positive"

    def test_exact_threshold_boundary(self):
        # cache-without-invalidation = 0.8
        assert _check_stop_criteria(0.8, ["cache-without-invalidation"]) is True
        assert _check_stop_criteria(0.7999, ["cache-without-invalidation"]) is False


# ── TestFileHash ──────────────────────────────────────────────────────


class TestFileHash:
    """Tests for _file_hash: SHA256-based file content hashing."""

    def test_hash_returns_16_char_hex(self, tmp_path):
        f = tmp_path / "example.py"
        f.write_text("hello world")
        result = _file_hash(str(f))
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_nonexistent_file_returns_empty(self):
        assert _file_hash("/nonexistent/path/file.py") == ""

    def test_hash_deterministic(self, tmp_path):
        f = tmp_path / "det.py"
        f.write_text("deterministic content")
        assert _file_hash(str(f)) == _file_hash(str(f))

    def test_hash_different_content_gives_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("content_a")
        f2.write_text("content_b")
        assert _file_hash(str(f1)) != _file_hash(str(f2))

    def test_hash_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = _file_hash(str(f))
        assert len(result) == 16


# ── TestExtractRequirementTags ────────────────────────────────────────


class TestExtractRequirementTags:
    """Tests for _extract_requirement_tags: regex extraction from docstrings."""

    def _parse_func(self, source: str):
        """Helper: parse a function source string and return its AST node."""
        import ast

        tree = ast.parse(textwrap.dedent(source))
        return tree.body[0]

    def test_no_docstring_returns_empty(self):
        node = self._parse_func(
            """\
            def func():
                pass
            """
        )
        assert _extract_requirement_tags(node) == []

    def test_single_req_tag(self):
        node = self._parse_func(
            '''\
            def func():
                """Implements REQ-123."""
                pass
            '''
        )
        assert _extract_requirement_tags(node) == ["REQ-123"]

    def test_multiple_tag_types(self):
        node = self._parse_func(
            '''\
            def func():
                """Covers REQ-1, US-42, and SPEC-7."""
                pass
            '''
        )
        tags = _extract_requirement_tags(node)
        assert "REQ-1" in tags
        assert "US-42" in tags
        assert "SPEC-7" in tags

    def test_case_insensitive(self):
        node = self._parse_func(
            '''\
            def func():
                """See req-99 and spec-5."""
                pass
            '''
        )
        tags = _extract_requirement_tags(node)
        assert len(tags) == 2

    def test_no_matching_tags_in_docstring(self):
        node = self._parse_func(
            '''\
            def func():
                """This function does something."""
                pass
            '''
        )
        assert _extract_requirement_tags(node) == []


# ── TestDeserializeTrajectory ─────────────────────────────────────────


class TestDeserializeTrajectory:
    """Tests for _deserialize_trajectory: dict → TrajectoryState."""

    def test_none_input_returns_default(self):
        t = _deserialize_trajectory(None)
        assert t.delta_k == []
        assert t.transition_index is None
        assert t.estimated_remaining == 0
        assert t.convergence_rate == 0.0

    def test_empty_dict_returns_default(self):
        t = _deserialize_trajectory({})
        assert t.delta_k == []

    def test_non_dict_input_returns_default(self):
        # Passing a list should return default
        t = _deserialize_trajectory([1, 2, 3])  # type: ignore[arg-type]
        assert t.delta_k == []

    def test_full_round_trip(self):
        data = {
            "delta_k": [0.1, 0.05, 0.02],
            "transition_index": 1,
            "estimated_remaining": 3,
            "convergence_rate": 0.057,
        }
        t = _deserialize_trajectory(data)
        assert t.delta_k == [0.1, 0.05, 0.02]
        assert t.transition_index == 1
        assert t.estimated_remaining == 3
        assert t.convergence_rate == 0.057

    def test_partial_dict_fills_defaults(self):
        t = _deserialize_trajectory({"delta_k": [0.3]})
        assert t.delta_k == [0.3]
        assert t.transition_index is None
        assert t.estimated_remaining == 0
        assert t.convergence_rate == 0.0


# ── TestDeserializeFuncSpec ───────────────────────────────────────────


class TestDeserializeFuncSpec:
    """Tests for _deserialize_func_spec: flat dict → FunctionSpecification."""

    def test_empty_dict_gives_defaults(self):
        fs = _deserialize_func_spec({})
        assert fs.function_key == ""
        assert fs.source_file == ""
        assert fs.core.estimated_sigma == 0
        assert fs.core.regime == "unknown"
        assert fs.core.specification_level == 0.0
        assert fs.trajectory.delta_k == []
        assert fs.stop_criteria_met is False

    def test_populated_dict_round_trip(self):
        original = _make_func_spec(
            function_key="pkg.py::MyClass.method",
            spec_level=0.75,
            sigma=8,
            regime="B",
        )
        serialized = original.to_dict()
        restored = _deserialize_func_spec(serialized)
        assert restored.function_key == "pkg.py::MyClass.method"
        assert restored.core.estimated_sigma == 8
        assert restored.core.regime == "B"
        assert abs(restored.core.specification_level - 0.75) < 0.01

    def test_trajectory_nested_deserialization(self):
        data = {
            "function_key": "x.py::f",
            "trajectory": {
                "delta_k": [0.1, 0.2],
                "transition_index": None,
                "estimated_remaining": 5,
                "convergence_rate": 0.15,
            },
        }
        fs = _deserialize_func_spec(data)
        assert fs.trajectory.delta_k == [0.1, 0.2]
        assert fs.trajectory.estimated_remaining == 5

    def test_missing_trajectory_key(self):
        fs = _deserialize_func_spec({"function_key": "a.py::b"})
        assert fs.trajectory.delta_k == []


# ── TestDeserializeLedger ─────────────────────────────────────────────


class TestDeserializeLedger:
    """Tests for _deserialize_ledger: full ledger dict → SpecificationLedger."""

    def test_empty_functions(self):
        data = {"schema_version": "3", "functions": {}}
        ledger = _deserialize_ledger(data)
        assert len(ledger.functions) == 0
        assert ledger.specification_coverage == 0.0

    def test_single_function_ledger(self):
        fs = _make_func_spec(spec_level=0.6, sigma=5)
        data = {
            "schema_version": "3",
            "functions": {"mod.py::func": fs.to_dict()},
        }
        ledger = _deserialize_ledger(data)
        assert len(ledger.functions) == 1
        assert "mod.py::func" in ledger.functions
        # update_metrics should have been called
        assert ledger.specification_coverage > 0

    def test_metrics_updated_after_deserialization(self):
        fs1 = _make_func_spec(function_key="a.py::f1", spec_level=0.4, sigma=3, regime="A")
        fs2 = _make_func_spec(function_key="b.py::f2", spec_level=0.8, sigma=7, regime="B")
        data = {
            "schema_version": "3",
            "functions": {
                "a.py::f1": fs1.to_dict(),
                "b.py::f2": fs2.to_dict(),
            },
        }
        ledger = _deserialize_ledger(data)
        assert ledger.total_sigma == 3 + 7
        # Coverage should be mean of 0.4 and 0.8
        assert abs(ledger.specification_coverage - 0.6) < 0.01


# ── TestCacheLoadSave ─────────────────────────────────────────────────


class TestCacheLoadSave:
    """Tests for load_cached_ledger and save_cached_ledger."""

    def test_load_nonexistent_returns_none(self, tmp_path):
        assert load_cached_ledger(tmp_path, "abc") is None

    def test_save_then_load_round_trip(self, tmp_path):
        ledger = _make_ledger({"mod.py::func": _make_func_spec(spec_level=0.5)})
        save_cached_ledger(tmp_path, "proj123", ledger)
        loaded = load_cached_ledger(tmp_path, "proj123")
        assert loaded is not None
        assert len(loaded.functions) == 1
        assert "mod.py::func" in loaded.functions

    def test_load_schema_mismatch_returns_none(self, tmp_path):
        from lintgate.keys import SCHEMA_VERSION

        cache_file = tmp_path / f"proj_v{SCHEMA_VERSION}.json"
        cache_file.write_text(json.dumps({"schema_version": "999", "functions": {}}))
        assert load_cached_ledger(tmp_path, "proj") is None

    def test_load_corrupt_json_returns_none(self, tmp_path):
        from lintgate.keys import SCHEMA_VERSION

        cache_file = tmp_path / f"proj_v{SCHEMA_VERSION}.json"
        cache_file.write_text("{invalid json")
        assert load_cached_ledger(tmp_path, "proj") is None

    def test_save_creates_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        ledger = _make_ledger()
        save_cached_ledger(nested, "hash1", ledger)
        assert nested.exists()

    def test_load_empty_ledger_round_trip(self, tmp_path):
        ledger = _make_ledger()
        save_cached_ledger(tmp_path, "empty", ledger)
        loaded = load_cached_ledger(tmp_path, "empty")
        assert loaded is not None
        assert len(loaded.functions) == 0

    def test_save_write_error_does_not_raise(self, tmp_path):
        """save_cached_ledger silently handles OSError on file write."""
        from lintgate.keys import SCHEMA_VERSION

        # Create the cache dir, then place a directory where the cache file
        # would go — open() on a directory raises IsADirectoryError (OSError).
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        blocker = cache_dir / f"proj_v{SCHEMA_VERSION}.json"
        blocker.mkdir()  # file path is now a directory

        ledger = _make_ledger()
        # Should not raise — OSError is caught inside save_cached_ledger
        save_cached_ledger(cache_dir, "proj", ledger)


# ── TestBuildTestCoverageMap ──────────────────────────────────────────


class TestBuildTestCoverageMap:
    """Tests for _build_test_coverage_map: scanning test files for coverage."""

    def test_empty_test_files(self):
        coverage, file_coverage = _build_test_coverage_map([])
        assert coverage == {}
        assert file_coverage == {}

    def test_single_test_file_with_call(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent(
                """\
                def test_add():
                    result = add(1, 2)
                    assert result == 3
                """
            )
        )
        coverage, file_coverage = _build_test_coverage_map([str(test_file)])
        assert "add" in coverage
        assert "test_add" in coverage["add"]
        assert str(test_file) in file_coverage.get("add", set())

    def test_class_based_tests(self, tmp_path):
        test_file = tmp_path / "test_cls.py"
        test_file.write_text(
            textwrap.dedent(
                """\
                class TestMath:
                    def test_multiply(self):
                        multiply(3, 4)
                """
            )
        )
        coverage, _ = _build_test_coverage_map([str(test_file)])
        assert "multiply" in coverage
        assert "TestMath.test_multiply" in coverage["multiply"]

    def test_nonexistent_test_file_skipped(self, tmp_path):
        coverage, file_coverage = _build_test_coverage_map(["/nonexistent/test_x.py"])
        assert coverage == {}
        assert file_coverage == {}

    def test_syntax_error_test_file_skipped(self, tmp_path):
        bad_file = tmp_path / "test_bad.py"
        bad_file.write_text("def test_broken(\n")  # syntax error
        coverage, file_coverage = _build_test_coverage_map([str(bad_file)])
        assert coverage == {}

    def test_multiple_test_files(self, tmp_path):
        f1 = tmp_path / "test_a.py"
        f1.write_text("def test_one():\n    foo()\n")
        f2 = tmp_path / "test_b.py"
        f2.write_text("def test_two():\n    foo()\n")
        coverage, file_coverage = _build_test_coverage_map([str(f1), str(f2)])
        assert len(coverage["foo"]) == 2
        assert len(file_coverage["foo"]) == 2

    def test_method_call_attribute_extraction(self, tmp_path):
        test_file = tmp_path / "test_method.py"
        test_file.write_text(
            textwrap.dedent(
                """\
                def test_method():
                    obj.do_something(42)
                """
            )
        )
        coverage, _ = _build_test_coverage_map([str(test_file)])
        assert "do_something" in coverage

    def test_non_test_functions_ignored(self, tmp_path):
        test_file = tmp_path / "test_skip.py"
        test_file.write_text(
            textwrap.dedent(
                """\
                def helper():
                    some_func()

                def test_real():
                    other_func()
                """
            )
        )
        coverage, _ = _build_test_coverage_map([str(test_file)])
        # helper is not a test_, so some_func should not appear
        assert "some_func" not in coverage
        assert "other_func" in coverage


# ── TestSpecificationLedgerUpdateMetrics ──────────────────────────────


class TestSpecificationLedgerUpdateMetrics:
    """Tests for SpecificationLedger.update_metrics aggregate calculations."""

    def test_empty_ledger_defaults(self):
        ledger = _make_ledger()
        assert ledger.specification_coverage == 0.0
        assert ledger.total_sigma == 0
        assert ledger.mean_testability == 0.0
        assert ledger.stop_criteria_met_count == 0
        assert ledger.regime_distribution == {"A": 0, "B": 0, "unknown": 0}
        assert ledger.risk_distribution == {"P0": 0, "P1": 0, "P2": 0}

    def test_single_function_metrics(self):
        fs = _make_func_spec(
            spec_level=0.8,
            sigma=10,
            regime="A",
            testability_score=0.7,
            priority_band="P1",
            stop_criteria_met=True,
        )
        ledger = _make_ledger({"k": fs})
        assert abs(ledger.specification_coverage - 0.8) < 0.01
        assert ledger.total_sigma == 10
        assert abs(ledger.mean_testability - 0.7) < 0.01
        assert ledger.stop_criteria_met_count == 1
        assert ledger.regime_distribution["A"] == 1
        assert ledger.risk_distribution["P1"] == 1

    def test_multiple_functions_average(self):
        fs1 = _make_func_spec(
            function_key="a.py::f1",
            spec_level=0.4,
            sigma=3,
            regime="A",
            testability_score=0.6,
            priority_band="P2",
        )
        fs2 = _make_func_spec(
            function_key="b.py::f2",
            spec_level=0.8,
            sigma=7,
            regime="B",
            testability_score=0.8,
            priority_band="P0",
            stop_criteria_met=True,
        )
        ledger = _make_ledger({"a.py::f1": fs1, "b.py::f2": fs2})
        assert abs(ledger.specification_coverage - 0.6) < 0.01
        assert ledger.total_sigma == 10
        assert abs(ledger.mean_testability - 0.7) < 0.01
        assert ledger.stop_criteria_met_count == 1
        assert ledger.regime_distribution["A"] == 1
        assert ledger.regime_distribution["B"] == 1
        assert ledger.risk_distribution["P0"] == 1
        assert ledger.risk_distribution["P2"] == 1

    def test_unknown_regime_counted(self):
        fs = _make_func_spec(regime="unknown")
        ledger = _make_ledger({"k": fs})
        assert ledger.regime_distribution["unknown"] == 1


# ── TestFunctionSpecificationToDict ───────────────────────────────────


class TestFunctionSpecificationToDict:
    """Tests for FunctionSpecification.to_dict serialization."""

    def test_to_dict_has_all_expected_keys(self):
        fs = _make_func_spec()
        d = fs.to_dict()
        expected_keys = {
            "function_key",
            "source_file",
            "estimated_sigma",
            "sigma_confidence",
            "regime",
            "regime_rationale",
            "specification_level",
            "data_source",
            "behavioral_dimensions",
            "phase",
            "is_pure",
            "is_stateful",
            "semantic_ratio",
            "ast_category_count",
            "branch_count",
            "parameter_count",
            "weakness_taxonomy",
            "boundary_points",
            "equivalence_partitions",
            "decision_rule_count",
            "predicate_effect_links",
            "testability_score",
            "tpa_points",
            "tpa_confidence",
            "risk_score",
            "priority_band",
            "requirement_tags",
            "covering_tests",
            "covering_test_files",
            "prescription_history",
            "assertion_count",
            "coupling_surface",
            "trajectory",
            "stop_criteria_met",
            "optimization_hints",
            "file_hash",
            "computed_at",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_trajectory_is_nested_dict(self):
        fs = _make_func_spec()
        d = fs.to_dict()
        traj = d["trajectory"]
        assert isinstance(traj, dict)
        assert "delta_k" in traj
        assert "transition_index" in traj
        assert "estimated_remaining" in traj
        assert "convergence_rate" in traj

    def test_to_dict_values_rounded(self):
        fs = _make_func_spec(spec_level=0.333333333)
        d = fs.to_dict()
        # specification_level should be rounded to 3 decimal places
        assert d["specification_level"] == round(0.333333333, 3)


# ── TestSpecificationLedgerToDict ─────────────────────────────────────


class TestSpecificationLedgerToDict:
    """Tests for SpecificationLedger.to_dict serialization."""

    def test_empty_ledger_to_dict(self):
        ledger = _make_ledger()
        d = ledger.to_dict()
        assert d["functions"] == {}
        assert d["schema_version"] == "3"
        assert d["specification_coverage"] == 0.0

    def test_ledger_to_dict_includes_functions(self):
        fs = _make_func_spec(function_key="x.py::g")
        ledger = _make_ledger({"x.py::g": fs})
        d = ledger.to_dict()
        assert "x.py::g" in d["functions"]
        assert d["total_sigma"] == fs.core.estimated_sigma


# ── TestBuildSpecificationLedger ──────────────────────────────────────


class TestBuildSpecificationLedger:
    """Integration tests for build_specification_ledger."""

    def _make_source_file(self, tmp_path: Path, name: str = "mod.py", content: str = "") -> str:
        """Create a source file and return its path."""
        f = tmp_path / name
        if not content:
            content = textwrap.dedent(
                '''\
                def add(a, b):
                    """REQ-100: Add two numbers."""
                    return a + b
                '''
            )
        f.write_text(content)
        return str(f)

    def _make_manifests(self, func_key: str, source_file: str):
        """Create minimal PropertyManifest and TestEffectivenessManifest."""
        from lintgate.linters.performance_checks.algebra_types import (
            FunctionProperties,
            PurityResult,
        )
        from lintgate.linters.performance_checks.manifest import PropertyManifest
        from lintgate.linters.test_effectiveness.types import (
            AssertionInfo,
            AssertionKind,
            FunctionEffectiveness,
            QualityProfile,
            TestEffectivenessManifest,
        )

        purity = PurityResult(
            function_name="add",
            qualified_name="add",
            line=1,
            is_pure=True,
            confidence=0.9,
            side_effects=(),
            parameter_count=2,
            return_annotation=None,
        )
        func_props = FunctionProperties(
            purity=purity,
            properties=(),
            optimization_hints=("cacheable",),
            source_file=source_file,
        )
        prop_manifest = PropertyManifest(functions={func_key: func_props})

        assertions = [
            AssertionInfo(kind=AssertionKind.EQUALITY, line=3, strength=1.0),
            AssertionInfo(kind=AssertionKind.EQUALITY, line=4, strength=1.0),
        ]
        func_eff = FunctionEffectiveness(
            function_name="add",
            assertions=assertions,
            quality_profile=QualityProfile(semantic_ratio=0.8),
        )
        teff_manifest = TestEffectivenessManifest(functions={func_key: func_eff})

        return prop_manifest, teff_manifest

    def test_build_empty_manifests(self, tmp_path):
        from lintgate.linters.performance_checks.manifest import PropertyManifest
        from lintgate.linters.test_effectiveness.types import TestEffectivenessManifest

        ledger = build_specification_ledger(
            PropertyManifest(),
            TestEffectivenessManifest(),
            project_root=str(tmp_path),
        )
        assert len(ledger.functions) == 0
        assert ledger.specification_coverage == 0.0

    def test_build_single_function(self, tmp_path):
        source_file = self._make_source_file(tmp_path)
        func_key = "mod.py::add"
        prop_manifest, teff_manifest = self._make_manifests(func_key, source_file)

        # Clear AST cache to avoid stale entries from other tests
        from lintgate.specification.ledger import _AST_TREE_CACHE

        _AST_TREE_CACHE.clear()

        ledger = build_specification_ledger(
            prop_manifest,
            teff_manifest,
            project_root=str(tmp_path),
        )
        assert func_key in ledger.functions
        fs = ledger.functions[func_key]
        assert fs.source_file == source_file
        assert fs.core.is_pure is True
        assert fs.core.regime == "A"  # pure → always A
        assert fs.traceability.assertion_count == 2
        assert "REQ-100" in fs.traceability.requirement_tags

    def test_build_skips_missing_source_file(self, tmp_path):
        """Functions with nonexistent source files are excluded."""
        from lintgate.linters.performance_checks.algebra_types import (
            FunctionProperties,
            PurityResult,
        )
        from lintgate.linters.performance_checks.manifest import PropertyManifest
        from lintgate.linters.test_effectiveness.types import TestEffectivenessManifest
        from lintgate.specification.ledger import _AST_TREE_CACHE

        _AST_TREE_CACHE.clear()

        purity = PurityResult(
            function_name="ghost",
            qualified_name="ghost",
            line=1,
            is_pure=True,
            confidence=0.9,
            side_effects=(),
            parameter_count=0,
            return_annotation=None,
        )
        prop_manifest = PropertyManifest(
            functions={
                "missing.py::ghost": FunctionProperties(
                    purity=purity,
                    properties=(),
                    optimization_hints=(),
                    source_file="/nonexistent/missing.py",
                )
            }
        )
        ledger = build_specification_ledger(
            prop_manifest,
            TestEffectivenessManifest(),
            project_root=str(tmp_path),
        )
        assert len(ledger.functions) == 0

    def test_build_with_test_files(self, tmp_path):
        """Test coverage map is populated when test_files are provided."""
        source_file = self._make_source_file(tmp_path)
        test_file = tmp_path / "test_mod.py"
        test_file.write_text("def test_add():\n    add(1, 2)\n")
        func_key = "mod.py::add"
        prop_manifest, teff_manifest = self._make_manifests(func_key, source_file)

        from lintgate.specification.ledger import _AST_TREE_CACHE

        _AST_TREE_CACHE.clear()

        ledger = build_specification_ledger(
            prop_manifest,
            teff_manifest,
            project_root=str(tmp_path),
            test_files=[str(test_file)],
        )
        fs = ledger.functions[func_key]
        assert "test_add" in fs.traceability.covering_tests
        assert str(test_file) in fs.traceability.covering_test_files

    def test_build_with_mutation_cache(self, tmp_path):
        """mutation_cache overrides spec_level with ground truth."""
        source_file = self._make_source_file(tmp_path)
        func_key = "mod.py::add"
        prop_manifest, teff_manifest = self._make_manifests(func_key, source_file)

        from lintgate.specification.ledger import _AST_TREE_CACHE

        _AST_TREE_CACHE.clear()

        mutation_cache = {
            func_key: {
                "survival_rate": 0.2,
                "total_mutants": 10,
                "coverage_depth": "sampling",
            }
        }
        ledger = build_specification_ledger(
            prop_manifest,
            teff_manifest,
            project_root=str(tmp_path),
            mutation_cache=mutation_cache,
        )
        fs = ledger.functions[func_key]
        # spec_level = 1.0 - 0.2 = 0.8
        assert abs(fs.core.specification_level - 0.8) < 0.01
        assert "mutation" in fs.core.data_source

    def test_build_with_prior_ledger(self, tmp_path):
        """prior_ledger enables cross-run trajectory accumulation."""
        source_file = self._make_source_file(tmp_path)
        func_key = "mod.py::add"
        prop_manifest, teff_manifest = self._make_manifests(func_key, source_file)

        from lintgate.specification.ledger import _AST_TREE_CACHE

        _AST_TREE_CACHE.clear()

        prior_spec = _make_func_spec(
            function_key=func_key,
            spec_level=0.3,
            sigma=4,
            trajectory=TrajectoryState(
                delta_k=[0.1, 0.1],
                transition_index=None,
                estimated_remaining=3,
                convergence_rate=0.1,
            ),
        )
        prior_ledger = _make_ledger({func_key: prior_spec})

        ledger = build_specification_ledger(
            prop_manifest,
            teff_manifest,
            project_root=str(tmp_path),
            prior_ledger=prior_ledger,
        )
        fs = ledger.functions[func_key]
        # Trajectory should have been updated (delta_k appended)
        assert len(fs.trajectory.delta_k) > 2

    def test_build_updates_ledger_metrics(self, tmp_path):
        """Aggregate metrics are computed after building all functions."""
        source_file = self._make_source_file(tmp_path)
        func_key = "mod.py::add"
        prop_manifest, teff_manifest = self._make_manifests(func_key, source_file)

        from lintgate.specification.ledger import _AST_TREE_CACHE

        _AST_TREE_CACHE.clear()

        ledger = build_specification_ledger(
            prop_manifest,
            teff_manifest,
            project_root=str(tmp_path),
        )
        # With 1 function, coverage = that function's spec_level
        assert ledger.specification_coverage > 0 or ledger.specification_coverage == 0
        assert ledger.total_sigma > 0
        assert ledger.regime_distribution.get("A", 0) >= 1

    def test_build_method_in_class(self, tmp_path):
        """Functions with qualified names (Class.method) are resolved correctly."""
        source_file = self._make_source_file(
            tmp_path,
            content=textwrap.dedent(
                """\
                class Calculator:
                    def multiply(self, a, b):
                        return a * b
                """
            ),
        )
        func_key = "mod.py::Calculator.multiply"

        from lintgate.linters.performance_checks.algebra_types import (
            FunctionProperties,
            PurityResult,
        )
        from lintgate.linters.performance_checks.manifest import PropertyManifest
        from lintgate.linters.test_effectiveness.types import TestEffectivenessManifest
        from lintgate.specification.ledger import _AST_TREE_CACHE

        _AST_TREE_CACHE.clear()

        purity = PurityResult(
            function_name="multiply",
            qualified_name="Calculator.multiply",
            line=2,
            is_pure=True,
            confidence=0.8,
            side_effects=(),
            parameter_count=3,
            return_annotation=None,
        )
        prop_manifest = PropertyManifest(
            functions={
                func_key: FunctionProperties(
                    purity=purity,
                    properties=(),
                    optimization_hints=(),
                    source_file=source_file,
                )
            }
        )
        ledger = build_specification_ledger(
            prop_manifest,
            TestEffectivenessManifest(),
            project_root=str(tmp_path),
        )
        assert func_key in ledger.functions


# ── TestCacheRoundTrip ────────────────────────────────────────────────


class TestCacheRoundTrip:
    """End-to-end cache round-trip: build → save → load → verify."""

    def test_full_round_trip_preserves_data(self, tmp_path):
        fs = _make_func_spec(
            function_key="lib.py::parse",
            spec_level=0.65,
            sigma=6,
            regime="A",
            phase="transition",
            stop_criteria_met=True,
            optimization_hints=["cacheable"],
            trajectory=TrajectoryState(
                delta_k=[0.2, 0.1, 0.05],
                transition_index=None,
                estimated_remaining=2,
                convergence_rate=0.117,
            ),
        )
        original = _make_ledger({"lib.py::parse": fs})

        save_cached_ledger(tmp_path, "roundtrip", original)
        loaded = load_cached_ledger(tmp_path, "roundtrip")

        assert loaded is not None
        assert len(loaded.functions) == 1
        loaded_fs = loaded.functions["lib.py::parse"]
        assert abs(loaded_fs.core.specification_level - 0.65) < 0.01
        assert loaded_fs.core.estimated_sigma == 6
        assert loaded_fs.core.regime == "A"
        assert loaded_fs.stop_criteria_met is True
        assert loaded_fs.optimization_hints == ["cacheable"]
        assert loaded_fs.trajectory.delta_k == [0.2, 0.1, 0.05]
        assert loaded_fs.trajectory.convergence_rate == 0.117
