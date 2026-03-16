"""Tests for test_effectiveness_logic.py — core logic for test effectiveness analysis."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
    _ASSERTION_UPGRADE_MAP,
    apply_filters,
    build_assertion_upgrades,
    build_summary,
    handle_no_mapped_functions,
    reconcile_with_coverage,
)
from lintgate.linters.test_effectiveness.types import (
    TEFF_SCHEMA_VERSION,
    AnalysisState,
    AssertionInfo,
    AssertionKind,
    DropAnalysis,
    EffectivenessWeakness,
    FunctionEffectiveness,
    MappingCounts,
    MappingDiagnostics,
    QualityProfile,
    TestEffectivenessManifest,
)

# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------


def _make_fe(
    name: str,
    *,
    vulnerability: float = 0.5,
    effectiveness: float = 0.5,
    semantic_ratio: float = 0.5,
    test_count: int = 1,
    assertions: list[AssertionInfo] | None = None,
    weakness: EffectivenessWeakness | None = None,
) -> FunctionEffectiveness:
    """Build a FunctionEffectiveness with the given overrides."""
    fe = FunctionEffectiveness(
        function_name=name,
        test_count=test_count,
        assertions=assertions or [],
        effectiveness_score=effectiveness,
        mutation_vulnerability=vulnerability,
        quality_profile=QualityProfile(semantic_ratio=semantic_ratio),
        weakness_taxonomy=weakness,
    )
    return fe


def _make_manifest(
    functions: dict[str, FunctionEffectiveness] | None = None,
    project_score: float = 0.5,
    diagnostics: MappingDiagnostics | None = None,
) -> TestEffectivenessManifest:
    """Build a TestEffectivenessManifest with defaults."""
    funcs = functions or {}
    manifest = TestEffectivenessManifest(
        functions=funcs,
        project_score=project_score,
        diagnostics=diagnostics or MappingDiagnostics(),
    )
    manifest.update_metrics()
    return manifest


def _make_assertion(
    kind: AssertionKind = AssertionKind.EQUALITY,
    line: int = 10,
    strength: float = 0.9,
    target_expression: str = "result",
    confidence: str = "structural",
) -> AssertionInfo:
    return AssertionInfo(
        kind=kind,
        line=line,
        strength=strength,
        target_expression=target_expression,
        confidence=confidence,
    )


# ===================================================================
# build_summary
# ===================================================================


class TestBuildSummary:
    """Tests for build_summary()."""

    def test_empty_manifest(self):
        """Empty manifest produces zero counts and empty lists."""
        manifest = _make_manifest()
        result = build_summary(manifest, "/project")

        assert result["project"] == "/project"
        assert result["schema_version"] == TEFF_SCHEMA_VERSION
        assert result["summary"]["functions_analyzed"] == 0
        assert result["summary"]["mutation_vulnerable_count"] == 0
        assert result["summary"]["untested_count"] == 0
        assert result["summary"]["weakness_taxonomy_counts"] == {}
        assert result["top_vulnerable"] == []
        assert result["untested_functions"] == []

    def test_vulnerable_functions_sorted_desc(self):
        """Vulnerable functions (vulnerability > 0.5, test_count > 0) are sorted descending."""
        fe_high = _make_fe(
            "mod.py::high", vulnerability=0.9, effectiveness=0.1, semantic_ratio=0.2, test_count=2
        )
        fe_mid = _make_fe(
            "mod.py::mid", vulnerability=0.7, effectiveness=0.3, semantic_ratio=0.4, test_count=1
        )
        fe_low = _make_fe(
            "mod.py::low", vulnerability=0.3, effectiveness=0.7, semantic_ratio=0.8, test_count=1
        )
        manifest = _make_manifest(
            functions={"mod.py::high": fe_high, "mod.py::mid": fe_mid, "mod.py::low": fe_low}
        )
        result = build_summary(manifest, "/p")

        # Only high and mid have vulnerability > 0.5 AND test_count > 0
        assert len(result["top_vulnerable"]) == 2
        assert result["top_vulnerable"][0]["function"] == "mod.py::high"
        assert result["top_vulnerable"][0]["vulnerability"] == 0.9
        assert result["top_vulnerable"][1]["function"] == "mod.py::mid"
        assert result["top_vulnerable"][1]["vulnerability"] == 0.7

    def test_untested_functions_sorted(self):
        """Untested functions (test_count == 0) are collected and sorted alphabetically."""
        fe_a = _make_fe("z_func", test_count=0)
        fe_b = _make_fe("a_func", test_count=0)
        fe_c = _make_fe("m_func", test_count=1)
        manifest = _make_manifest(functions={"z_func": fe_a, "a_func": fe_b, "m_func": fe_c})
        result = build_summary(manifest, "/p")

        assert result["untested_functions"] == ["a_func", "z_func"]

    def test_untested_functions_capped_at_20(self):
        """At most 20 untested functions are reported."""
        funcs = {}
        for i in range(30):
            name = f"func_{i:03d}"
            funcs[name] = _make_fe(name, test_count=0)
        manifest = _make_manifest(functions=funcs)
        result = build_summary(manifest, "/p")

        assert len(result["untested_functions"]) == 20

    def test_taxonomy_counts_aggregated(self):
        """Weakness taxonomy counts are aggregated across all functions."""
        fe1 = _make_fe("a", weakness=EffectivenessWeakness.GENUINELY_WEAK)
        fe2 = _make_fe("b", weakness=EffectivenessWeakness.GENUINELY_WEAK)
        fe3 = _make_fe("c", weakness=EffectivenessWeakness.HEALTHY)
        fe4 = _make_fe("d", weakness=None)
        manifest = _make_manifest(functions={"a": fe1, "b": fe2, "c": fe3, "d": fe4})
        result = build_summary(manifest, "/p")

        counts = result["summary"]["weakness_taxonomy_counts"]
        assert counts["genuinely_weak"] == 2
        assert counts["healthy"] == 1
        assert "untested" not in counts  # fe4 has None taxonomy

    def test_vulnerable_entry_includes_weakness(self):
        """Vulnerability entries include weakness field when set."""
        fe = _make_fe(
            "mod.py::f",
            vulnerability=0.8,
            effectiveness=0.2,
            semantic_ratio=0.1,
            test_count=1,
            weakness=EffectivenessWeakness.STRUCTURAL_ONLY,
        )
        manifest = _make_manifest(functions={"mod.py::f": fe})
        result = build_summary(manifest, "/p")

        assert len(result["top_vulnerable"]) == 1
        assert result["top_vulnerable"][0]["weakness"] == "structural_only"

    def test_vulnerable_entry_without_weakness(self):
        """Vulnerability entries omit weakness field when taxonomy is None."""
        fe = _make_fe(
            "mod.py::f", vulnerability=0.8, effectiveness=0.2, semantic_ratio=0.1, test_count=1
        )
        manifest = _make_manifest(functions={"mod.py::f": fe})
        result = build_summary(manifest, "/p")

        assert "weakness" not in result["top_vulnerable"][0]

    def test_vulnerable_with_zero_test_count_excluded(self):
        """Functions with vulnerability > 0.5 but test_count == 0 are not listed as vulnerable."""
        fe = _make_fe("mod.py::untested", vulnerability=0.9, effectiveness=0.1, test_count=0)
        manifest = _make_manifest(functions={"mod.py::untested": fe})
        result = build_summary(manifest, "/p")

        assert result["top_vulnerable"] == []

    def test_top_vulnerable_capped_at_10(self):
        """At most 10 vulnerable functions are reported."""
        funcs = {}
        for i in range(15):
            name = f"mod.py::func_{i:02d}"
            funcs[name] = _make_fe(
                name, vulnerability=0.6 + i * 0.01, effectiveness=0.4 - i * 0.01, test_count=1
            )
        manifest = _make_manifest(functions=funcs)
        result = build_summary(manifest, "/p")

        assert len(result["top_vulnerable"]) == 10

    def test_diagnostics_included(self):
        """Summary includes the diagnostics dict."""
        diag = MappingDiagnostics(
            counts=MappingCounts(attempted=5, mapped=3),
        )
        manifest = _make_manifest(diagnostics=diag)
        result = build_summary(manifest, "/p")

        assert result["diagnostics"]["counts"]["attempted"] == 5
        assert result["diagnostics"]["counts"]["mapped"] == 3

    def test_rounding(self):
        """Numeric fields are rounded to 3 decimal places."""
        fe = _make_fe(
            "mod.py::f",
            vulnerability=0.123456789,
            effectiveness=0.987654321,
            semantic_ratio=0.555555555,
            test_count=1,
            assertions=[_make_assertion(strength=0.9)],
        )
        manifest = _make_manifest(functions={"mod.py::f": fe}, project_score=0.123456789)
        result = build_summary(manifest, "/p")

        assert result["summary"]["effectiveness_score"] == round(manifest.project_score, 3)
        _entry = result["top_vulnerable"]
        # vulnerability <= 0.5 so none in top_vulnerable; check project score rounding
        assert isinstance(result["summary"]["effectiveness_score"], float)


# ===================================================================
# handle_no_mapped_functions
# ===================================================================


class TestHandleNoMappedFunctions:
    """Tests for handle_no_mapped_functions()."""

    def _parse(self, manifest, source_files, test_files) -> dict:
        raw = handle_no_mapped_functions(manifest, source_files, test_files)
        return json.loads(raw)  # type: ignore[no-any-return]

    def test_attempted_zero_unmapped_tests(self):
        """When attempted == 0, state is UNMAPPED_TESTS with naming hint."""
        diag = MappingDiagnostics(counts=MappingCounts(attempted=0, mapped=0))
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, ["a.py"], ["test_a.py"])

        assert result["state"] == AnalysisState.UNMAPPED_TESTS.value
        assert "naming conventions" in result["hint"]
        assert result["note"] == "No mapped functions analyzed."
        assert "1 source files" in result["details"]
        assert "1 test files" in result["details"]

    def test_dominant_ambiguous(self):
        """When dominant_drop_reason is ambiguous, hint references ambiguity."""
        diag = MappingDiagnostics(
            counts=MappingCounts(attempted=10, mapped=0, dropped_ambiguous=8),
            drop_analysis=DropAnalysis(dominant_reason="ambiguous"),
        )
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, ["a.py", "b.py"], ["test_a.py"])

        assert result["state"] == AnalysisState.NO_MAPPED_FUNCTIONS.value
        assert "8 ambiguous" in result["hint"]

    def test_dominant_no_candidate(self):
        """When dominant_drop_reason is no_candidate, state is NO_SOURCE_SYMBOLS."""
        diag = MappingDiagnostics(
            counts=MappingCounts(attempted=5, mapped=0, dropped_no_candidate=4),
            drop_analysis=DropAnalysis(dominant_reason="no_candidate"),
        )
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, ["a.py"], ["test_a.py"])

        assert result["state"] == AnalysisState.NO_SOURCE_SYMBOLS.value
        assert "4 test calls" in result["hint"]

    def test_dominant_shadowed(self):
        """When dominant_drop_reason is shadowed, hint references shadowing."""
        diag = MappingDiagnostics(
            counts=MappingCounts(attempted=3, mapped=0, dropped_shadowed=2),
            drop_analysis=DropAnalysis(dominant_reason="shadowed"),
        )
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, [], [])

        assert result["state"] == AnalysisState.NO_MAPPED_FUNCTIONS.value
        assert "2 test names shadowed" in result["hint"]

    def test_unknown_dominant_reason_fallback(self):
        """When dominant_drop_reason is something unexpected, fallback hint is used."""
        diag = MappingDiagnostics(
            counts=MappingCounts(attempted=1, mapped=0),
            drop_analysis=DropAnalysis(dominant_reason="unknown_reason"),
        )
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, ["a.py"], ["test_a.py"])

        assert "project root" in result["hint"]
        assert result["state"] == AnalysisState.NO_MAPPED_FUNCTIONS.value

    def test_schema_version_present(self):
        """Output always includes schema_version."""
        diag = MappingDiagnostics(counts=MappingCounts(attempted=0))
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, [], [])

        assert result["schema_version"] == TEFF_SCHEMA_VERSION

    def test_diagnostics_included(self):
        """Output includes diagnostics dict."""
        diag = MappingDiagnostics(counts=MappingCounts(attempted=2, mapped=1, dropped_ambiguous=1))
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, ["s.py"], ["t.py"])

        assert "diagnostics" in result
        assert result["diagnostics"]["counts"]["attempted"] == 2

    def test_returns_valid_json_string(self):
        """Return value is a valid JSON string with exact expected structure."""
        diag = MappingDiagnostics()
        manifest = _make_manifest(diagnostics=diag)
        raw = handle_no_mapped_functions(manifest, [], [])
        parsed = json.loads(raw)
        assert parsed["note"] == "No mapped functions analyzed."
        assert parsed["state"] == AnalysisState.UNMAPPED_TESTS.value
        assert (
            parsed["hint"]
            == "Check if source functions are public and tests follow naming conventions."
        )
        assert (
            parsed["details"]
            == "Scanned 0 source files and 0 test files. (Mapped: 0 / Attempted: 0)"
        )
        assert parsed["schema_version"] == TEFF_SCHEMA_VERSION
        assert set(parsed.keys()) == {
            "note",
            "details",
            "hint",
            "state",
            "diagnostics",
            "schema_version",
        }

    def test_no_dominant_reason_with_attempted(self):
        """When attempted > 0 but no dominant_drop_reason, fallback hint is used."""
        diag = MappingDiagnostics(
            counts=MappingCounts(attempted=3, mapped=0),
            drop_analysis=DropAnalysis(dominant_reason=None),
        )
        manifest = _make_manifest(diagnostics=diag)
        result = self._parse(manifest, ["a.py"], ["test_a.py"])

        assert "project root" in result["hint"]


# ===================================================================
# build_assertion_upgrades
# ===================================================================


class TestBuildAssertionUpgrades:
    """Tests for build_assertion_upgrades()."""

    def test_empty_manifest(self):
        """No functions means no upgrades."""
        manifest = _make_manifest()
        upgrades = build_assertion_upgrades(manifest)
        assert upgrades == []

    def test_no_structural_assertions(self):
        """Semantic assertions (high strength) don't need upgrading."""
        fe = _make_fe(
            "f",
            assertions=[
                _make_assertion(kind=AssertionKind.EQUALITY, strength=0.9, confidence="structural")
            ],
        )
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)
        assert upgrades == []

    def test_isinstance_upgrade(self):
        """isinstance assertions with structural confidence and low strength produce upgrades."""
        a = _make_assertion(
            kind=AssertionKind.ISINSTANCE_CHECK,
            strength=0.3,
            confidence="structural",
            target_expression="result",
        )
        fe = _make_fe("f", assertions=[a])
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)

        assert len(upgrades) == 1
        assert "isinstance" in upgrades[0]["current"]
        assert "expected_value" in upgrades[0]["suggested"]
        assert "isinstance" in upgrades[0]["reason"]

    def test_is_not_none_upgrade(self):
        """IS_NOT_NONE assertions with low strength produce upgrades."""
        a = _make_assertion(
            kind=AssertionKind.IS_NOT_NONE,
            strength=0.3,
            confidence="structural",
            target_expression="obj",
        )
        fe = _make_fe("f", assertions=[a])
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)

        assert len(upgrades) == 1
        assert "is not None" in upgrades[0]["current"]
        assert "is_not_none" in upgrades[0]["reason"]

    def test_is_true_upgrade(self):
        """IS_TRUE assertions with low strength produce upgrades."""
        a = _make_assertion(
            kind=AssertionKind.IS_TRUE,
            strength=0.2,
            confidence="structural",
            target_expression="flag",
        )
        fe = _make_fe("f", assertions=[a])
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)

        assert len(upgrades) == 1
        assert "assert flag" in upgrades[0]["current"]
        assert "bare assert" in upgrades[0]["reason"]

    def test_heuristic_confidence_skipped(self):
        """Assertions with heuristic confidence are skipped (only structural considered)."""
        a = _make_assertion(
            kind=AssertionKind.IS_NOT_NONE,
            strength=0.3,
            confidence="heuristic",
            target_expression="result",
        )
        fe = _make_fe("f", assertions=[a])
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)
        assert upgrades == []

    def test_high_strength_skipped(self):
        """Assertions with strength >= 0.5 are skipped."""
        a = _make_assertion(
            kind=AssertionKind.IS_NOT_NONE,
            strength=0.5,
            confidence="structural",
            target_expression="result",
        )
        fe = _make_fe("f", assertions=[a])
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)
        assert upgrades == []

    def test_dedup_same_pattern(self):
        """Duplicate (kind, target_expression) patterns are deduplicated."""
        a1 = _make_assertion(
            kind=AssertionKind.IS_NOT_NONE,
            strength=0.3,
            confidence="structural",
            target_expression="x",
        )
        a2 = _make_assertion(
            kind=AssertionKind.IS_NOT_NONE,
            strength=0.3,
            confidence="structural",
            target_expression="x",
        )
        fe = _make_fe("f", assertions=[a1, a2])
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)

        assert len(upgrades) == 1

    def test_max_10_upgrades(self):
        """At most 10 upgrades are returned."""
        assertions = []
        for i in range(15):
            assertions.append(
                _make_assertion(
                    kind=AssertionKind.IS_NOT_NONE,
                    strength=0.3,
                    confidence="structural",
                    target_expression=f"var_{i}",
                )
            )
        fe = _make_fe("f", assertions=assertions)
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)

        assert len(upgrades) == 10

    def test_unmapped_kind_ignored(self):
        """Assertion kinds not in _ASSERTION_UPGRADE_MAP are skipped."""
        a = _make_assertion(
            kind=AssertionKind.HASATTR_CHECK,
            strength=0.1,
            confidence="structural",
            target_expression="obj",
        )
        fe = _make_fe("f", assertions=[a])
        manifest = _make_manifest(functions={"f": fe})
        upgrades = build_assertion_upgrades(manifest)
        assert upgrades == []

    def test_assertion_upgrade_map_completeness(self):
        """The upgrade map covers the three weakest structural assertion kinds."""
        assert AssertionKind.ISINSTANCE_CHECK in _ASSERTION_UPGRADE_MAP
        assert AssertionKind.IS_NOT_NONE in _ASSERTION_UPGRADE_MAP
        assert AssertionKind.IS_TRUE in _ASSERTION_UPGRADE_MAP
        # Each value is a 3-tuple
        for _kind, templates in _ASSERTION_UPGRADE_MAP.items():
            assert len(templates) == 3


# ===================================================================
# apply_filters
# ===================================================================


class TestApplyFilters:
    """Tests for apply_filters()."""

    @pytest.fixture()
    def base_result(self):
        return {
            "top_vulnerable": [
                {"function": "src/mod.py::foo", "vulnerability": 0.8},
                {"function": "src/util.py::bar", "vulnerability": 0.7},
                {"function": "baz", "vulnerability": 0.6},
            ],
            "untested_functions": [
                "src/mod.py::alpha",
                "src/util.py::beta",
                "gamma",
            ],
        }

    def test_no_filters_no_change(self, base_result):
        """When both filters are None, result is unmodified."""
        original_vuln = list(base_result["top_vulnerable"])
        original_untested = list(base_result["untested_functions"])
        apply_filters(base_result, None, None)

        assert base_result["top_vulnerable"] == original_vuln
        assert base_result["untested_functions"] == original_untested
        assert "filter_applied" not in base_result

    def test_file_filter(self, base_result):
        """File filter matches on the relpath portion (case-insensitive)."""
        apply_filters(base_result, "mod.py", None)

        assert len(base_result["top_vulnerable"]) == 1
        assert base_result["top_vulnerable"][0]["function"] == "src/mod.py::foo"
        assert base_result["untested_functions"] == ["src/mod.py::alpha"]
        assert base_result["filter_applied"]["file"] == "mod.py"
        assert base_result["file_filter"] == "mod.py"

    def test_function_filter(self, base_result):
        """Function filter matches on the function name portion (case-insensitive)."""
        apply_filters(base_result, None, "bar")

        assert len(base_result["top_vulnerable"]) == 1
        assert base_result["top_vulnerable"][0]["function"] == "src/util.py::bar"
        assert base_result["filter_applied"]["function"] == "bar"
        assert base_result["function_filter"] == "bar"

    def test_both_filters(self, base_result):
        """Both file and function filter applied together."""
        apply_filters(base_result, "util", "bar")

        assert len(base_result["top_vulnerable"]) == 1
        assert base_result["top_vulnerable"][0]["function"] == "src/util.py::bar"
        assert base_result["untested_functions"] == []
        assert base_result["filter_applied"] == {"file": "util", "function": "bar"}

    def test_case_insensitive(self, base_result):
        """Filters are case-insensitive."""
        apply_filters(base_result, "MOD.PY", "FOO")

        assert len(base_result["top_vulnerable"]) == 1
        assert base_result["top_vulnerable"][0]["function"] == "src/mod.py::foo"

    def test_no_match(self, base_result):
        """When filter matches nothing, lists are empty."""
        apply_filters(base_result, "nonexistent", None)

        assert base_result["top_vulnerable"] == []
        assert base_result["untested_functions"] == []

    def test_function_without_separator(self, base_result):
        """Functions without :: have empty relpath and full name as func."""
        apply_filters(base_result, None, "baz")

        assert len(base_result["top_vulnerable"]) == 1
        assert base_result["top_vulnerable"][0]["function"] == "baz"

    def test_function_without_separator_file_filter(self, base_result):
        """File filter on a function without :: means relpath is empty, so no match."""
        apply_filters(base_result, "somefile", None)

        # "baz" has empty relpath, so file_filter won't match it
        vuln_names = [v["function"] for v in base_result["top_vulnerable"]]
        assert "baz" not in vuln_names

    def test_empty_lists(self):
        """Filters on empty lists produce empty lists."""
        result: dict[str, Any] = {"top_vulnerable": [], "untested_functions": []}
        apply_filters(result, "foo", "bar")

        assert result["top_vulnerable"] == []
        assert result["untested_functions"] == []
        assert result["filter_applied"] == {"file": "foo", "function": "bar"}


# ===================================================================
# reconcile_with_coverage
# ===================================================================


class TestReconcileWithCoverage:
    """Tests for reconcile_with_coverage()."""

    def test_empty_manifest_and_coverage(self):
        """Empty inputs produce empty reconciliation report."""
        manifest = _make_manifest()
        report = reconcile_with_coverage(manifest, {})

        assert report["high_coverage_low_semantic"] == []
        assert report["low_coverage_high_semantic"] == []
        assert report["coverage_source"] == "json"

    def test_high_coverage_low_semantic(self):
        """Detects high coverage (>80%) with low semantic ratio (<0.3)."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.2)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data = {
            "files": {
                "src/mod.py": {"summary": {"percent_covered": 95.0}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        assert len(report["high_coverage_low_semantic"]) == 1
        entry = report["high_coverage_low_semantic"][0]
        assert entry["function"] == "src/mod.py::func"
        assert entry["coverage"] == 95.0
        assert entry["semantic_ratio"] == 0.2
        assert (
            "equality" in entry["recommendation"].lower()
            or "weak" in entry["recommendation"].lower()
        )

    def test_low_coverage_high_semantic(self):
        """Detects low coverage (<20%) with high semantic ratio (>0.7)."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.9)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data = {
            "files": {
                "src/mod.py": {"summary": {"percent_covered": 10.0}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        assert len(report["low_coverage_high_semantic"]) == 1
        entry = report["low_coverage_high_semantic"][0]
        assert entry["function"] == "src/mod.py::func"
        assert entry["coverage"] == 10.0
        assert entry["semantic_ratio"] == 0.9
        assert (
            "branch" in entry["recommendation"].lower()
            or "expand" in entry["recommendation"].lower()
        )

    def test_function_without_separator_skipped(self):
        """Functions without :: in the key are skipped (no relpath)."""
        fe = _make_fe("standalone_func", semantic_ratio=0.1)
        manifest = _make_manifest(functions={"standalone_func": fe})
        coverage_data = {
            "files": {
                "standalone_func": {"summary": {"percent_covered": 100.0}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        assert report["high_coverage_low_semantic"] == []
        assert report["low_coverage_high_semantic"] == []

    def test_no_coverage_data_for_file(self):
        """Functions with no coverage data for their file are not flagged."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.1)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data: dict[str, Any] = {"files": {}}
        report = reconcile_with_coverage(manifest, coverage_data)

        # percent defaults to 0.0 which is < 20.0, but semantic_ratio 0.1 < 0.7
        # so neither condition matches
        assert report["high_coverage_low_semantic"] == []
        assert report["low_coverage_high_semantic"] == []

    def test_middle_range_not_flagged(self):
        """Functions in the middle range (coverage 20-80, semantic 0.3-0.7) are not flagged."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.5)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data = {
            "files": {
                "src/mod.py": {"summary": {"percent_covered": 50.0}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        assert report["high_coverage_low_semantic"] == []
        assert report["low_coverage_high_semantic"] == []

    def test_boundary_values_not_flagged(self):
        """Exact boundary values (80.0 coverage, 0.3 semantic) are not flagged."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.3)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data = {
            "files": {
                "src/mod.py": {"summary": {"percent_covered": 80.0}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        # 80.0 is NOT > 80.0, so not flagged
        assert report["high_coverage_low_semantic"] == []

    def test_boundary_low_coverage_not_flagged(self):
        """Exact boundary (20.0 coverage, 0.7 semantic) is not flagged."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.7)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data = {
            "files": {
                "src/mod.py": {"summary": {"percent_covered": 20.0}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        # 20.0 is NOT < 20.0, so not flagged
        assert report["low_coverage_high_semantic"] == []

    def test_rounding_in_report(self):
        """Coverage and semantic_ratio are rounded in the report."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.123456)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data = {
            "files": {
                "src/mod.py": {"summary": {"percent_covered": 95.6789}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        entry = report["high_coverage_low_semantic"][0]
        assert entry["coverage"] == 95.7
        assert entry["semantic_ratio"] == 0.123

    def test_multiple_functions_mixed(self):
        """Multiple functions can be flagged simultaneously in different categories."""
        fe1 = _make_fe("src/a.py::high_cov_low_sem", semantic_ratio=0.1)
        fe2 = _make_fe("src/b.py::low_cov_high_sem", semantic_ratio=0.9)
        fe3 = _make_fe("src/c.py::normal", semantic_ratio=0.5)
        manifest = _make_manifest(
            functions={
                "src/a.py::high_cov_low_sem": fe1,
                "src/b.py::low_cov_high_sem": fe2,
                "src/c.py::normal": fe3,
            }
        )
        coverage_data = {
            "files": {
                "src/a.py": {"summary": {"percent_covered": 95.0}},
                "src/b.py": {"summary": {"percent_covered": 5.0}},
                "src/c.py": {"summary": {"percent_covered": 50.0}},
            },
        }
        report = reconcile_with_coverage(manifest, coverage_data)

        assert len(report["high_coverage_low_semantic"]) == 1
        assert report["high_coverage_low_semantic"][0]["function"] == "src/a.py::high_cov_low_sem"
        assert len(report["low_coverage_high_semantic"]) == 1
        assert report["low_coverage_high_semantic"][0]["function"] == "src/b.py::low_cov_high_sem"

    def test_missing_summary_key(self):
        """When coverage file data lacks summary, percent defaults to 0.0."""
        fe = _make_fe("src/mod.py::func", semantic_ratio=0.9)
        manifest = _make_manifest(functions={"src/mod.py::func": fe})
        coverage_data: dict[str, Any] = {"files": {"src/mod.py": {}}}
        report = reconcile_with_coverage(manifest, coverage_data)

        # percent = 0.0 which is < 20.0, and semantic_ratio 0.9 > 0.7
        assert len(report["low_coverage_high_semantic"]) == 1


# ===================================================================
# analyze_function_effectiveness (wrapper)
# ===================================================================


class TestAnalyzeFunctionEffectiveness:
    """Tests for analyze_function_effectiveness() in test_effectiveness_logic.py."""

    def test_basic_call(self):
        """Wrapper returns func_data dict and anti_patterns list."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            analyze_function_effectiveness,
        )

        assertions = [
            _make_assertion(kind=AssertionKind.EQUALITY, strength=0.9, target_expression="result"),
        ]
        func_data, anti_patterns = analyze_function_effectiveness("test_foo", assertions)

        assert isinstance(func_data, dict)
        assert isinstance(anti_patterns, list)
        assert func_data["function_name"] == "test_foo"
        assert "semantic_count" in func_data
        assert "structural_count" in func_data
        assert "count" in func_data
        assert "warnings" in func_data
        assert "has_isolated_sentinel" in func_data

    def test_semantic_count(self):
        """semantic_count counts assertions with strength >= SEMANTIC_STRENGTH_THRESHOLD."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            analyze_function_effectiveness,
        )

        assertions = [
            _make_assertion(kind=AssertionKind.EQUALITY, strength=0.9),
            _make_assertion(kind=AssertionKind.IS_TRUE, strength=0.2),
            _make_assertion(kind=AssertionKind.COMPARISON, strength=0.85),
        ]
        func_data, _ = analyze_function_effectiveness("test_f", assertions)

        # EQUALITY (0.9) and COMPARISON (0.85) are >= 0.7 threshold
        assert func_data["semantic_count"] == 2
        assert func_data["structural_count"] == 1
        assert func_data["count"] == 3

    def test_empty_assertions(self):
        """Empty assertions list produces zero counts."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            analyze_function_effectiveness,
        )

        func_data, anti_patterns = analyze_function_effectiveness("test_f", [])

        assert func_data["count"] == 0
        assert func_data["semantic_count"] == 0
        assert func_data["structural_count"] == 0
        assert func_data["has_isolated_sentinel"] is False

    def test_isolated_sentinel_detected(self):
        """When an isolated sentinel pattern is present, has_isolated_sentinel is True."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            analyze_function_effectiveness,
        )

        # IS_NOT_NONE with no semantic checks on the same root -> isolated sentinel
        assertions = [
            _make_assertion(
                kind=AssertionKind.IS_NOT_NONE,
                strength=0.3,
                target_expression="result",
            ),
        ]
        # Set target_root to match
        assertions[0].target_root = "result"

        func_data, anti_patterns = analyze_function_effectiveness("test_f", assertions)

        assert func_data["has_isolated_sentinel"] is True
        assert any(w.get("kind") == "isolated_sentinel" for w in anti_patterns)

    def test_derivation_methods_passed(self):
        """Custom derivation_methods are passed through to the inner function."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            analyze_function_effectiveness,
        )

        assertions = [
            _make_assertion(kind=AssertionKind.EQUALITY, strength=0.9),
        ]
        func_data, _ = analyze_function_effectiveness(
            "test_f", assertions, derivation_methods=["mutation_calibration"]
        )

        assert func_data["confidence"]["derivation_methods"] == ["mutation_calibration"]


# ===================================================================
# build_manifest_for_project
# ===================================================================


class TestBuildManifestForProject:
    """Tests for build_manifest_for_project()."""

    def test_no_python_files(self):
        """When no Python files exist, manifest is None."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            build_manifest_for_project,
        )

        with (
            patch(
                "lintgate.linters.test_effectiveness.test_effectiveness_logic._discover_test_files",
                return_value=[],
            ),
            patch(
                "lintgate.linters.test_effectiveness.test_effectiveness_logic._discover_source_files",
                return_value=[],
            ),
            patch(
                "lintgate.channels.structure_channel._discover_python_files",
                return_value=[],
            ),
        ):
            manifest, py_files, test_files, source_files = build_manifest_for_project("/fake")

        assert manifest is None
        assert py_files == []
        assert test_files == []
        assert source_files == []

    def test_no_test_files(self):
        """When no test files exist, manifest is None."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            build_manifest_for_project,
        )

        with (
            patch(
                "lintgate.linters.test_effectiveness.test_effectiveness_logic._discover_test_files",
                return_value=[],
            ),
            patch(
                "lintgate.linters.test_effectiveness.test_effectiveness_logic._discover_source_files",
                return_value=["src.py"],
            ),
            patch(
                "lintgate.channels.structure_channel._discover_python_files",
                return_value=["src.py"],
            ),
        ):
            manifest, py_files, test_files, source_files = build_manifest_for_project("/fake")

        assert manifest is None
        assert py_files == ["src.py"]
        assert test_files == []
        assert source_files == ["src.py"]

    def test_returns_four_element_tuple(self):
        """Return value is always a 4-tuple."""
        from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
            build_manifest_for_project,
        )

        with (
            patch(
                "lintgate.linters.test_effectiveness.test_effectiveness_logic._discover_test_files",
                return_value=[],
            ),
            patch(
                "lintgate.linters.test_effectiveness.test_effectiveness_logic._discover_source_files",
                return_value=[],
            ),
            patch(
                "lintgate.channels.structure_channel._discover_python_files",
                return_value=[],
            ),
        ):
            result = build_manifest_for_project("/fake")

        assert isinstance(result, tuple)
        assert len(result) == 4
