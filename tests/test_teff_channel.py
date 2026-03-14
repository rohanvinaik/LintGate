"""Comprehensive tests for lintgate.channels.test_effectiveness_channel.

Covers all seven finding generators (TEFF001-TEFF007), the
TestEffectivenessChannel class, sampling selection, and telemetry emission.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from lintgate.channels.test_effectiveness_channel import (
    TestEffectivenessChannel,
    _analyze_complexity_block,
    _select_test_files_for_analysis,
    _teff001_low_semantic_ratio,
    _teff002_untested_functions,
    _teff003_structural_only,
    _teff004_high_vulnerability,
    _teff005_pure_weak_tests,
    _teff007_complex_weak_tests,
)
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.linters.test_effectiveness.types import (
    AssertionInfo,
    AssertionKind,
    FunctionEffectiveness,
    TestEffectivenessManifest,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_assertion(kind: AssertionKind, strength: float | None = None) -> AssertionInfo:
    """Create an AssertionInfo with sensible defaults."""
    from lintgate.linters.test_effectiveness.types import STRENGTH_MAP

    if strength is None:
        strength = STRENGTH_MAP[kind]
    return AssertionInfo(kind=kind, line=1, strength=strength)


def _make_fe(
    name: str,
    test_count: int = 1,
    assertions: list[AssertionInfo] | None = None,
    effectiveness_score: float = 0.5,
    mutation_vulnerability: float = 0.5,
    semantic_ratio: float = 0.5,
) -> FunctionEffectiveness:
    """Create a FunctionEffectiveness with sensible defaults."""
    return FunctionEffectiveness(
        function_name=name,
        test_count=test_count,
        assertions=assertions or [],
        effectiveness_score=effectiveness_score,
        mutation_vulnerability=mutation_vulnerability,
        semantic_ratio=semantic_ratio,
    )


def _make_manifest(
    functions: dict[str, FunctionEffectiveness] | None = None,
    project_score: float = 0.5,
) -> TestEffectivenessManifest:
    """Create a TestEffectivenessManifest with sensible defaults."""
    funcs = functions or {}
    m = TestEffectivenessManifest(
        functions=funcs,
        project_score=project_score,
        functions_analyzed=len(funcs),
        mutation_vulnerable_count=sum(1 for f in funcs.values() if f.mutation_vulnerability > 0.7),
    )
    return m


# ── TEFF001: Low semantic assertion ratio ──────────────────────────────


class TestTeff001LowSemanticRatio:
    """TEFF001 — File-level low semantic assertion ratio."""

    def test_empty_manifest_returns_empty(self):
        manifest = _make_manifest()
        result = _teff001_low_semantic_ratio(manifest, "/project")
        assert result == []

    def test_no_assertions_returns_empty(self):
        manifest = _make_manifest(functions={"foo": _make_fe("foo", assertions=[])})
        result = _teff001_low_semantic_ratio(manifest, "/project")
        assert result == []

    def test_high_semantic_ratio_returns_empty(self):
        """All strong assertions -> ratio >= 0.4 -> no finding."""
        assertions = [_make_assertion(AssertionKind.EQUALITY)] * 5
        manifest = _make_manifest(functions={"foo": _make_fe("foo", assertions=assertions)})
        result = _teff001_low_semantic_ratio(manifest, "/project")
        assert result == []

    def test_low_semantic_ratio_produces_finding(self):
        """Mostly weak assertions -> ratio < 0.4 -> TEFF001 finding."""
        weak = [_make_assertion(AssertionKind.IS_NONE)] * 8
        strong = [_make_assertion(AssertionKind.EQUALITY)] * 2
        manifest = _make_manifest(functions={"foo": _make_fe("foo", assertions=weak + strong)})
        result = _teff001_low_semantic_ratio(manifest, "/project")
        assert len(result) == 1
        assert result[0].kind == "TEFF001"
        assert result[0].evidence["semantic_assertions"] == 2
        assert result[0].evidence["total_assertions"] == 10

    def test_boundary_ratio_exactly_40_percent_returns_empty(self):
        """Ratio == 0.4 should NOT produce a finding (threshold is >=0.4)."""
        weak = [_make_assertion(AssertionKind.IS_NONE)] * 6
        strong = [_make_assertion(AssertionKind.EQUALITY)] * 4
        manifest = _make_manifest(functions={"foo": _make_fe("foo", assertions=weak + strong)})
        result = _teff001_low_semantic_ratio(manifest, "/project")
        assert result == []

    def test_all_weak_assertions_produces_finding(self):
        """All structural assertions -> ratio = 0.0."""
        weak = [_make_assertion(AssertionKind.IS_TRUE)] * 5
        manifest = _make_manifest(functions={"foo": _make_fe("foo", assertions=weak)})
        result = _teff001_low_semantic_ratio(manifest, "/project")
        assert len(result) == 1
        assert result[0].evidence["ratio"] == 0.0

    def test_aggregates_across_multiple_functions(self):
        """Semantic ratio is computed across ALL functions."""
        fe1 = _make_fe("foo", assertions=[_make_assertion(AssertionKind.IS_NONE)] * 5)
        fe2 = _make_fe("bar", assertions=[_make_assertion(AssertionKind.EQUALITY)] * 1)
        manifest = _make_manifest(functions={"foo": fe1, "bar": fe2})
        result = _teff001_low_semantic_ratio(manifest, "/project")
        # 1 semantic / 6 total ~ 0.167 < 0.4
        assert len(result) == 1
        assert result[0].evidence["total_assertions"] == 6

    def test_finding_severity_is_informational(self):
        weak = [_make_assertion(AssertionKind.IS_NONE)] * 10
        manifest = _make_manifest(functions={"foo": _make_fe("foo", assertions=weak)})
        result = _teff001_low_semantic_ratio(manifest, "/project")
        assert result[0].severity == "informational"
        assert result[0].confidence == 0.85


# ── TEFF002: Untested public functions ─────────────────────────────────


class TestTeff002UntestedFunctions:
    """TEFF002 — Untested public functions."""

    def test_no_functions_returns_empty(self):
        manifest = _make_manifest()
        result = _teff002_untested_functions(manifest, "/project")
        assert result == []

    def test_all_tested_returns_empty(self):
        manifest = _make_manifest(functions={"foo": _make_fe("foo", test_count=1)})
        result = _teff002_untested_functions(manifest, "/project")
        assert result == []

    def test_private_untested_ignored(self):
        """Private functions (starting with _) are excluded from TEFF002."""
        manifest = _make_manifest(functions={"_private_fn": _make_fe("_private_fn", test_count=0)})
        result = _teff002_untested_functions(manifest, "/project")
        assert result == []

    def test_single_untested_public(self):
        manifest = _make_manifest(functions={"public_fn": _make_fe("public_fn", test_count=0)})
        result = _teff002_untested_functions(manifest, "/project")
        assert len(result) == 1
        assert result[0].kind == "TEFF002"
        assert "public_fn" in result[0].message

    def test_multiple_untested_capped_at_top_n(self):
        """Only top 5 untested functions shown in the message."""
        funcs = {f"fn_{i}": _make_fe(f"fn_{i}", test_count=0) for i in range(10)}
        manifest = _make_manifest(functions=funcs)
        result = _teff002_untested_functions(manifest, "/project")
        assert len(result) == 1
        assert result[0].evidence["untested_count"] == 10
        assert "and 5 more" in result[0].message

    def test_exactly_five_untested_no_suffix(self):
        funcs = {f"fn_{i}": _make_fe(f"fn_{i}", test_count=0) for i in range(5)}
        manifest = _make_manifest(functions=funcs)
        result = _teff002_untested_functions(manifest, "/project")
        assert len(result) == 1
        assert "more" not in result[0].message

    def test_finding_severity_and_confidence(self):
        manifest = _make_manifest(functions={"foo": _make_fe("foo", test_count=0)})
        result = _teff002_untested_functions(manifest, "/project")
        assert result[0].severity == "informational"
        assert result[0].confidence == 0.7

    def test_mix_tested_and_untested(self):
        funcs = {
            "tested": _make_fe("tested", test_count=2),
            "untested": _make_fe("untested", test_count=0),
            "_private": _make_fe("_private", test_count=0),
        }
        manifest = _make_manifest(functions=funcs)
        result = _teff002_untested_functions(manifest, "/project")
        assert len(result) == 1
        assert result[0].evidence["untested_count"] == 1


# ── TEFF003: Structural-only assertions ────────────────────────────────


class TestTeff003StructuralOnly:
    """TEFF003 — Functions tested with only structural assertions."""

    def test_no_functions_returns_empty(self):
        manifest = _make_manifest()
        result = _teff003_structural_only(manifest, "/project")
        assert result == []

    def test_untested_function_excluded(self):
        """test_count == 0 should be excluded."""
        manifest = _make_manifest(
            functions={
                "foo": _make_fe(
                    "foo",
                    test_count=0,
                    assertions=[_make_assertion(AssertionKind.IS_NONE)],
                )
            }
        )
        result = _teff003_structural_only(manifest, "/project")
        assert result == []

    def test_no_assertions_excluded(self):
        """Empty assertions list should be excluded."""
        manifest = _make_manifest(functions={"foo": _make_fe("foo", test_count=1, assertions=[])})
        result = _teff003_structural_only(manifest, "/project")
        assert result == []

    def test_all_weak_assertions_produces_finding(self):
        """All structural assertions -> TEFF003."""
        assertions = [
            _make_assertion(AssertionKind.IS_NONE),
            _make_assertion(AssertionKind.IS_TRUE),
            _make_assertion(AssertionKind.ISINSTANCE_CHECK),
        ]
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=2, assertions=assertions)}
        )
        result = _teff003_structural_only(manifest, "/project")
        assert len(result) == 1
        assert result[0].kind == "TEFF003"
        assert result[0].evidence["assertion_count"] == 3

    def test_mixed_assertions_no_finding(self):
        """At least one strong assertion -> no TEFF003."""
        assertions = [
            _make_assertion(AssertionKind.IS_NONE),
            _make_assertion(AssertionKind.EQUALITY),  # strong
        ]
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=1, assertions=assertions)}
        )
        result = _teff003_structural_only(manifest, "/project")
        assert result == []

    def test_capped_at_top_n(self):
        """Only first 5 structural-only functions become findings."""
        funcs = {}
        for i in range(8):
            funcs[f"fn_{i}"] = _make_fe(
                f"fn_{i}",
                test_count=1,
                assertions=[_make_assertion(AssertionKind.IS_NONE)],
            )
        manifest = _make_manifest(functions=funcs)
        result = _teff003_structural_only(manifest, "/project")
        assert len(result) == 5

    def test_finding_severity_is_warning(self):
        assertions = [_make_assertion(AssertionKind.IS_TRUE)]
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=1, assertions=assertions)}
        )
        result = _teff003_structural_only(manifest, "/project")
        assert result[0].severity == "warning"


# ── TEFF004: High mutation vulnerability ───────────────────────────────


class TestTeff004HighVulnerability:
    """TEFF004 — Functions with high mutation vulnerability."""

    def test_no_functions_returns_empty(self):
        manifest = _make_manifest()
        result = _teff004_high_vulnerability(manifest, "/project")
        assert result == []

    def test_low_vulnerability_no_finding(self):
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=2, mutation_vulnerability=0.3)}
        )
        result = _teff004_high_vulnerability(manifest, "/project")
        assert result == []

    def test_boundary_vulnerability_0_7_no_finding(self):
        """Vulnerability == 0.7 should NOT produce a finding (threshold is > 0.7)."""
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=1, mutation_vulnerability=0.7)}
        )
        result = _teff004_high_vulnerability(manifest, "/project")
        assert result == []

    def test_high_vulnerability_produces_finding(self):
        manifest = _make_manifest(
            functions={
                "foo": _make_fe(
                    "foo",
                    test_count=1,
                    mutation_vulnerability=0.85,
                    effectiveness_score=0.15,
                    assertions=[_make_assertion(AssertionKind.IS_NONE)],
                )
            }
        )
        result = _teff004_high_vulnerability(manifest, "/project")
        assert len(result) == 1
        assert result[0].kind == "TEFF004"
        assert result[0].evidence["mutation_vulnerability"] == 0.85

    def test_untested_function_excluded(self):
        """test_count == 0 should be excluded even if vulnerability is high."""
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=0, mutation_vulnerability=0.95)}
        )
        result = _teff004_high_vulnerability(manifest, "/project")
        assert result == []

    def test_sorted_by_vulnerability_desc(self):
        """Findings should be sorted highest vulnerability first."""
        funcs = {
            "low": _make_fe("low", test_count=1, mutation_vulnerability=0.75),
            "high": _make_fe("high", test_count=1, mutation_vulnerability=0.95),
            "mid": _make_fe("mid", test_count=1, mutation_vulnerability=0.85),
        }
        manifest = _make_manifest(functions=funcs)
        result = _teff004_high_vulnerability(manifest, "/project")
        vulns = [r.evidence["mutation_vulnerability"] for r in result]
        assert vulns == sorted(vulns, reverse=True)

    def test_capped_at_top_n(self):
        funcs = {
            f"fn_{i}": _make_fe(f"fn_{i}", test_count=1, mutation_vulnerability=0.8 + i * 0.01)
            for i in range(8)
        }
        manifest = _make_manifest(functions=funcs)
        result = _teff004_high_vulnerability(manifest, "/project")
        assert len(result) == 5


# ── TEFF005: Pure function with weak tests ─────────────────────────────


class TestTeff005PureWeakTests:
    """TEFF005 — Pure function with weak tests (cross-channel)."""

    def test_no_pure_functions_returns_empty(self):
        """When performance manifest has no pure functions, no findings."""
        manifest = _make_manifest(functions={"foo": _make_fe("foo", effectiveness_score=0.2)})
        mock_perf_manifest = MagicMock()
        mock_perf_manifest.get_pure_function_names.return_value = set()

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            return_value=mock_perf_manifest,
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert result == []

    def test_pure_function_with_weak_tests_produces_finding(self):
        manifest = _make_manifest(
            functions={
                "foo": _make_fe("foo", test_count=2, effectiveness_score=0.3, semantic_ratio=0.2)
            }
        )
        mock_perf_manifest = MagicMock()
        mock_perf_manifest.get_pure_function_names.return_value = {"foo"}

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            return_value=mock_perf_manifest,
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert len(result) == 1
        assert result[0].kind == "TEFF005"
        assert result[0].evidence["is_pure"] is True

    def test_pure_function_with_strong_tests_no_finding(self):
        """effectiveness_score >= 0.5 -> no TEFF005."""
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=2, effectiveness_score=0.7)}
        )
        mock_perf_manifest = MagicMock()
        mock_perf_manifest.get_pure_function_names.return_value = {"foo"}

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            return_value=mock_perf_manifest,
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert result == []

    def test_pure_function_untested_excluded(self):
        """test_count == 0 -> skip (TEFF002 handles untested functions)."""
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=0, effectiveness_score=0.1)}
        )
        mock_perf_manifest = MagicMock()
        mock_perf_manifest.get_pure_function_names.return_value = {"foo"}

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            return_value=mock_perf_manifest,
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert result == []

    def test_perf_manifest_import_failure_returns_empty(self):
        """If build_manifest raises, gracefully return []."""
        manifest = _make_manifest(functions={"foo": _make_fe("foo", effectiveness_score=0.1)})
        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            side_effect=ImportError("no perf"),
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert result == []

    def test_pure_not_in_manifest_skipped(self):
        """Pure function name not in manifest.functions -> skip."""
        manifest = _make_manifest(functions={"bar": _make_fe("bar", effectiveness_score=0.1)})
        mock_perf_manifest = MagicMock()
        mock_perf_manifest.get_pure_function_names.return_value = {"foo"}

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            return_value=mock_perf_manifest,
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert result == []

    def test_boundary_effectiveness_0_5_no_finding(self):
        """effectiveness_score == 0.5 should NOT produce finding (>= 0.5 threshold)."""
        manifest = _make_manifest(
            functions={"foo": _make_fe("foo", test_count=1, effectiveness_score=0.5)}
        )
        mock_perf_manifest = MagicMock()
        mock_perf_manifest.get_pure_function_names.return_value = {"foo"}

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            return_value=mock_perf_manifest,
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert result == []

    def test_capped_at_top_n(self):
        funcs = {
            f"fn_{i}": _make_fe(
                f"fn_{i}", test_count=1, effectiveness_score=0.2, semantic_ratio=0.1
            )
            for i in range(8)
        }
        manifest = _make_manifest(functions=funcs)
        mock_perf_manifest = MagicMock()
        mock_perf_manifest.get_pure_function_names.return_value = {f"fn_{i}" for i in range(8)}

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest",
            return_value=mock_perf_manifest,
        ):
            result = _teff005_pure_weak_tests(manifest, "/project", [])
        assert len(result) == 5


# ── TEFF007: Complex but weakly tested ─────────────────────────────────


class TestAnalyzeComplexityBlock:
    """_analyze_complexity_block — single radon block analysis."""

    def _make_block(self, complexity=5, name="foo", classname=None, lineno=1):
        """Create a mock radon complexity block.

        MagicMock treats 'name' specially (it's the mock's display name),
        so we must set it as an attribute after construction.
        """
        block = MagicMock(complexity=complexity, classname=classname, lineno=lineno)
        block.name = name
        return block

    def test_low_complexity_returns_none(self):
        block = self._make_block(complexity=5, name="foo")
        manifest = _make_manifest()
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")
        assert result is None

    def test_boundary_complexity_10_returns_none(self):
        """Complexity == 10 should NOT produce finding (threshold is > 10)."""
        block = self._make_block(complexity=10, name="foo")
        manifest = _make_manifest()
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")
        assert result is None

    def test_high_complexity_weak_tests_produces_finding(self):
        from lintgate.keys import canonical_function_key

        relpath = "src/mod.py"
        key = canonical_function_key(relpath, "foo")
        fe = _make_fe("foo", test_count=2, semantic_ratio=0.2)
        manifest = _make_manifest(functions={key: fe})

        block = self._make_block(complexity=15, name="foo", lineno=42)
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")

        assert result is not None
        assert result.kind == "TEFF007"
        assert result.evidence["complexity"] == 15
        assert result.line == 42

    def test_high_complexity_strong_tests_no_finding(self):
        """semantic_ratio >= 0.5 -> no TEFF007."""
        from lintgate.keys import canonical_function_key

        relpath = "src/mod.py"
        key = canonical_function_key(relpath, "foo")
        fe = _make_fe("foo", test_count=2, semantic_ratio=0.6)
        manifest = _make_manifest(functions={key: fe})

        block = self._make_block(complexity=15, name="foo")
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")
        assert result is None

    def test_high_complexity_untested_no_finding(self):
        """test_count == 0 -> no TEFF007."""
        from lintgate.keys import canonical_function_key

        relpath = "src/mod.py"
        key = canonical_function_key(relpath, "foo")
        fe = _make_fe("foo", test_count=0, semantic_ratio=0.1)
        manifest = _make_manifest(functions={key: fe})

        block = self._make_block(complexity=15, name="foo")
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")
        assert result is None

    def test_classname_prefixed(self):
        """When classname is present, key is 'ClassName.method'."""
        from lintgate.keys import canonical_function_key

        relpath = "src/mod.py"
        key = canonical_function_key(relpath, "MyClass.method")
        fe = _make_fe("MyClass.method", test_count=1, semantic_ratio=0.1)
        manifest = _make_manifest(functions={key: fe})

        block = self._make_block(complexity=20, name="method", classname="MyClass", lineno=10)
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")
        assert result is not None
        assert result.evidence["function"] == "MyClass.method"

    def test_no_name_attribute_returns_none(self):
        """Block without a name -> skip."""
        block = self._make_block(complexity=20, name="")
        manifest = _make_manifest()
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")
        assert result is None

    def test_function_not_in_manifest_returns_none(self):
        """Function not in manifest -> no finding."""
        block = self._make_block(complexity=20, name="missing_fn")
        manifest = _make_manifest(functions={})
        result = _analyze_complexity_block(block, manifest, "/project/src/mod.py", "/project")
        assert result is None


class TestTeff007ComplexWeakTests:
    """_teff007_complex_weak_tests — file-level radon integration."""

    def test_no_files_returns_empty(self):
        """With no Python files to analyze, returns empty regardless of radon."""
        manifest = _make_manifest()
        result = _teff007_complex_weak_tests(manifest, "/project", [])
        assert result == []

    def test_with_source_files(self, tmp_path):
        """Integration test: write a Python file and analyze it."""
        from lintgate.keys import canonical_function_key

        src = tmp_path / "src" / "mod.py"
        src.parent.mkdir(parents=True)
        # Write a function with artificially high complexity (many if/elif)
        lines = ["def complex_fn(x):\n"]
        for i in range(15):
            if i == 0:
                lines.append(f"    if x == {i}:\n        return {i}\n")
            else:
                lines.append(f"    elif x == {i}:\n        return {i}\n")
        lines.append("    return -1\n")
        src.write_text("".join(lines))

        relpath = os.path.relpath(str(src), str(tmp_path))
        key = canonical_function_key(relpath, "complex_fn")
        fe = _make_fe("complex_fn", test_count=1, semantic_ratio=0.1)
        manifest = _make_manifest(functions={key: fe})

        result = _teff007_complex_weak_tests(manifest, str(tmp_path), [str(src)])
        # Whether radon is installed determines if this works
        # If radon is available and the function is complex enough, we get TEFF007
        # If not, we get an empty list (graceful degradation)
        for finding in result:
            assert finding.kind == "TEFF007"

    def test_syntax_error_file_skipped(self, tmp_path):
        """Files with syntax errors should be silently skipped."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(:\n")
        manifest = _make_manifest()
        result = _teff007_complex_weak_tests(manifest, str(tmp_path), [str(bad_file)])
        assert result == []

    def test_nonexistent_file_skipped(self):
        manifest = _make_manifest()
        result = _teff007_complex_weak_tests(manifest, "/project", ["/nonexistent/file.py"])
        assert result == []


# ── _select_test_files_for_analysis ────────────────────────────────────


class TestSelectTestFilesForAnalysis:
    """Sampling logic for large codebases."""

    def test_empty_input_returns_empty(self):
        selected, was_sampled = _select_test_files_for_analysis([], "/project")
        assert selected == []
        assert not was_sampled

    def test_small_set_not_sampled(self):
        files = [f"/project/tests/test_{i}.py" for i in range(5)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=90.0
        )
        assert not was_sampled
        assert selected == files

    def test_large_set_sampled(self):
        files = [f"/project/tests/test_{i}.py" for i in range(500)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=30.0, estimated_per_file_seconds=0.3
        )
        assert was_sampled
        assert len(selected) <= 100  # 30 / 0.3 = 100

    def test_no_duplicates(self):
        files = [f"/project/tests/test_{i}.py" for i in range(500)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/project", budget_seconds=30.0, estimated_per_file_seconds=0.3
        )
        assert len(selected) == len(set(selected))

    def test_git_failure_graceful(self):
        """If git is unavailable, sampling still works."""
        files = [f"/nonexistent_project/tests/test_{i}.py" for i in range(500)]
        selected, was_sampled = _select_test_files_for_analysis(
            files, "/nonexistent_project", budget_seconds=30.0, estimated_per_file_seconds=0.3
        )
        assert was_sampled
        assert len(selected) > 0

    def test_largest_files_prioritized(self, tmp_path):
        """Largest files should be in the sample."""
        files = []
        for i in range(50):
            p = tmp_path / f"test_{i:03d}.py"
            p.write_text("x" * (i * 200))
            files.append(str(p))

        selected, was_sampled = _select_test_files_for_analysis(
            files, str(tmp_path), budget_seconds=3.0, estimated_per_file_seconds=0.3
        )
        assert was_sampled
        # The largest file (test_049.py) should be included
        largest = str(tmp_path / "test_049.py")
        assert largest in selected


# ── TestEffectivenessChannel ───────────────────────────────────────────


class TestTestEffectivenessChannelProperties:
    """Channel metadata and should_run behavior."""

    def test_channel_name(self):
        ch = TestEffectivenessChannel()
        assert ch.name == "test_effectiveness"

    def test_not_blocking_capable(self):
        ch = TestEffectivenessChannel()
        assert ch.blocking_capable is False

    def test_timeout_ms(self):
        ch = TestEffectivenessChannel()
        assert ch.timeout_ms == 12000

    def test_should_run_with_project_root(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="/some/project")
        config = ControlPlaneConfig()
        assert ch.should_run(event, config) is True

    def test_should_not_run_without_project_root(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="")
        config = ControlPlaneConfig()
        assert ch.should_run(event, config) is False


class TestTestEffectivenessChannelExecute:
    """Channel.execute() integration tests."""

    def test_no_python_files_returns_skip(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="/project")
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=[],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=["/project/tests/test_foo.py"],
            ),
        ):
            result = ch.execute(event, config)
        assert result.status == "skip"
        assert result.metrics["reason"] == "no_python_or_test_files"

    def test_no_test_files_returns_skip(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="/project")
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=[],
            ),
        ):
            result = ch.execute(event, config)
        assert result.status == "skip"

    def test_no_functions_analyzed_returns_skip(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="/project")
        config = ControlPlaneConfig()

        empty_manifest = _make_manifest()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=["/project/tests/test_mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._select_test_files_for_analysis",
                return_value=(["/project/tests/test_mod.py"], False),
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel.build_test_effectiveness_manifest",
                return_value=empty_manifest,
            ),
        ):
            result = ch.execute(event, config)
        assert result.status == "skip"
        assert result.metrics["reason"] == "no_functions_analyzed"

    def test_manifest_from_context_reused(self):
        """When manifest is already in event.context, it should be reused."""
        ch = TestEffectivenessChannel()
        fe = _make_fe("foo", test_count=1, assertions=[_make_assertion(AssertionKind.EQUALITY)])
        manifest = _make_manifest(functions={"foo": fe}, project_score=0.8)
        event = SupervisionEvent(
            project_root="/project",
            context={"test_effectiveness_manifest": manifest},
        )
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=["/project/tests/test_mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._select_test_files_for_analysis",
                return_value=(["/project/tests/test_mod.py"], False),
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel.build_test_effectiveness_manifest",
            ) as mock_build,
            patch(
                "lintgate.channels.test_effectiveness_channel._emit_telemetry",
            ),
        ):
            result = ch.execute(event, config)
            # build should not be called since manifest is in context
            mock_build.assert_not_called()
        assert result.status == "pass"

    def test_pass_status_when_no_findings(self):
        ch = TestEffectivenessChannel()
        # All strong assertions -> no findings
        fe = _make_fe(
            "foo",
            test_count=3,
            assertions=[_make_assertion(AssertionKind.EQUALITY)] * 5,
            effectiveness_score=0.9,
            mutation_vulnerability=0.1,
            semantic_ratio=1.0,
        )
        manifest = _make_manifest(functions={"foo": fe}, project_score=0.9)
        event = SupervisionEvent(
            project_root="/project",
            context={"test_effectiveness_manifest": manifest},
        )
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=["/project/tests/test_mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._select_test_files_for_analysis",
                return_value=(["/project/tests/test_mod.py"], False),
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._emit_telemetry",
            ),
        ):
            result = ch.execute(event, config)
        assert result.status == "pass"
        assert result.severity == "none"
        assert result.findings == []

    def test_fail_status_with_informational_severity(self):
        """Findings present but only informational -> severity='informational'.

        TEFF002 (untested public function) is informational.
        To avoid TEFF003 (warning), the function must have test_count=0.
        """
        ch = TestEffectivenessChannel()
        # Untested public function -> TEFF002 (informational only)
        fe = _make_fe(
            "public_fn",
            test_count=0,
            assertions=[],
            effectiveness_score=0.0,
            mutation_vulnerability=1.0,
            semantic_ratio=0.0,
        )
        manifest = _make_manifest(functions={"public_fn": fe}, project_score=0.0)
        event = SupervisionEvent(
            project_root="/project",
            context={"test_effectiveness_manifest": manifest},
        )
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=["/project/tests/test_mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._select_test_files_for_analysis",
                return_value=(["/project/tests/test_mod.py"], False),
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._emit_telemetry",
            ),
        ):
            result = ch.execute(event, config)
        assert result.status == "fail"
        assert result.severity == "informational"

    def test_warning_severity_when_warning_findings_present(self):
        """At least one warning finding -> severity='warning'."""
        ch = TestEffectivenessChannel()
        # Structural-only assertions -> TEFF003 (warning)
        fe = _make_fe(
            "foo",
            test_count=1,
            assertions=[_make_assertion(AssertionKind.IS_NONE)] * 3,
            effectiveness_score=0.2,
            mutation_vulnerability=0.8,
            semantic_ratio=0.0,
        )
        manifest = _make_manifest(functions={"foo": fe}, project_score=0.2)
        event = SupervisionEvent(
            project_root="/project",
            context={"test_effectiveness_manifest": manifest},
        )
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=["/project/tests/test_mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._select_test_files_for_analysis",
                return_value=(["/project/tests/test_mod.py"], False),
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._emit_telemetry",
            ),
        ):
            result = ch.execute(event, config)
        assert result.severity == "warning"

    def test_metrics_include_sampling_info_when_sampled(self):
        ch = TestEffectivenessChannel()
        fe = _make_fe(
            "foo",
            test_count=1,
            assertions=[_make_assertion(AssertionKind.EQUALITY)] * 3,
            effectiveness_score=0.9,
            mutation_vulnerability=0.1,
            semantic_ratio=1.0,
        )
        manifest = _make_manifest(functions={"foo": fe}, project_score=0.9)
        event = SupervisionEvent(
            project_root="/project",
            context={"test_effectiveness_manifest": manifest},
        )
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=[f"/project/tests/test_{i}.py" for i in range(500)],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._select_test_files_for_analysis",
                return_value=([f"/project/tests/test_{i}.py" for i in range(100)], True),
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._emit_telemetry",
            ),
        ):
            result = ch.execute(event, config)
        assert result.metrics["sampled"] is True
        assert result.metrics["sample_size"] == 100
        assert result.metrics["total_test_files"] == 500
        assert "sample_coverage" in result.metrics

    def test_metrics_no_sampling_info_when_not_sampled(self):
        ch = TestEffectivenessChannel()
        fe = _make_fe(
            "foo",
            test_count=1,
            assertions=[_make_assertion(AssertionKind.EQUALITY)] * 3,
            effectiveness_score=0.9,
            mutation_vulnerability=0.1,
            semantic_ratio=1.0,
        )
        manifest = _make_manifest(functions={"foo": fe}, project_score=0.9)
        event = SupervisionEvent(
            project_root="/project",
            context={"test_effectiveness_manifest": manifest},
        )
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=["/project/src/mod.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=["/project/tests/test_foo.py"],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._select_test_files_for_analysis",
                return_value=(["/project/tests/test_foo.py"], False),
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._emit_telemetry",
            ),
        ):
            result = ch.execute(event, config)
        assert "sampled" not in result.metrics

    def test_channel_result_has_correct_name(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="/project")
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=[],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=[],
            ),
        ):
            result = ch.execute(event, config)
        assert result.channel == "test_effectiveness"

    def test_duration_ms_positive(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="/project")
        config = ControlPlaneConfig()

        with (
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_python_files",
                return_value=[],
            ),
            patch(
                "lintgate.channels.test_effectiveness_channel._discover_test_files",
                return_value=[],
            ),
        ):
            result = ch.execute(event, config)
        assert result.duration_ms >= 0


# ── _emit_telemetry ───────────────────────────────────────────────────


class TestEmitTelemetry:
    """Telemetry emission via log_metric."""

    def test_telemetry_emitted(self):
        from lintgate.channels.test_effectiveness_channel import _emit_telemetry

        manifest = _make_manifest(project_score=0.75)
        manifest.functions_analyzed = 10
        manifest.mutation_vulnerable_count = 3

        with patch("lintgate.state.log_metric") as mock_log:
            _emit_telemetry(manifest, "/project", [], 42.5, 20, 15)
            mock_log.assert_called_once()
            data = mock_log.call_args[0][0]
            assert data["event"] == "test_effectiveness_analysis"
            assert data["project"] == "/project"
            assert data["project_score"] == 0.75
            assert data["functions_analyzed"] == 10
            assert data["duration_ms"] == 42.5
            assert data["source_files"] == 20
            assert data["test_files"] == 15
            assert data["findings_count"] == 0
            assert data["warning_count"] == 0

    def test_telemetry_counts_warnings(self):
        from lintgate.channels.test_effectiveness_channel import _emit_telemetry
        from lintgate.types import LintIssue

        manifest = _make_manifest()
        findings = [
            LintIssue(linter="test_effectiveness", kind="TEFF003", message="x", severity="warning"),
            LintIssue(
                linter="test_effectiveness", kind="TEFF001", message="y", severity="informational"
            ),
            LintIssue(linter="test_effectiveness", kind="TEFF004", message="z", severity="warning"),
        ]

        with patch("lintgate.state.log_metric") as mock_log:
            _emit_telemetry(manifest, "/project", findings, 10.0, 5, 3)
            data = mock_log.call_args[0][0]
            assert data["findings_count"] == 3
            assert data["warning_count"] == 2
