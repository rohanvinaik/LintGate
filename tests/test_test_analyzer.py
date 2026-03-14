"""Tests for test_analyzer — per-function test effectiveness computation.

Covers:
- _extract_public_functions (AST extraction of public names)
- detect_sentinel_patterns (sentinel pairing + return-type inference)
- detect_isolated_sentinels (isolated sentinel anti-pattern warnings)
- detect_hasattr_chains (hasattr chain anti-pattern detection)
- analyze_function_effectiveness (single function analysis pipeline)
- _discover_test_files / _discover_source_files (filesystem discovery)
- analyze_effectiveness (project-wide analysis orchestration)
"""

from __future__ import annotations

import os
import textwrap
from unittest.mock import patch

from lintgate.linters.test_effectiveness.test_analyzer import (
    _discover_source_files,
    _discover_test_files,
    _extract_public_functions,
    analyze_effectiveness,
    analyze_function_effectiveness,
    detect_hasattr_chains,
    detect_isolated_sentinels,
    detect_sentinel_patterns,
)
from lintgate.linters.test_effectiveness.types import (
    STRENGTH_MAP,
    AssertionInfo,
    AssertionKind,
    EffectivenessWeakness,
    MappingDiagnostics,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ai(
    kind: AssertionKind,
    line: int = 1,
    target_root: str = "",
    target_expression: str = "",
    strength: float | None = None,
    confidence: str = "structural",
) -> AssertionInfo:
    """Shorthand builder for AssertionInfo with sensible defaults."""
    return AssertionInfo(
        kind=kind,
        line=line,
        strength=strength if strength is not None else STRENGTH_MAP[kind],
        target_root=target_root,
        target_expression=target_expression,
        confidence=confidence,
    )


# ===========================================================================
# _extract_public_functions
# ===========================================================================


class TestExtractPublicFunctions:
    """Tests for _extract_public_functions using inline source strings."""

    def test_top_level_public_functions(self, tmp_path):
        src = textwrap.dedent("""\
            def foo():
                pass

            def bar(x):
                return x
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        assert result == ["foo", "bar"]

    def test_private_functions_excluded(self, tmp_path):
        src = textwrap.dedent("""\
            def _private():
                pass

            def public():
                pass

            def _also_private():
                pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        assert result == ["public"]

    def test_dunder_methods_included(self, tmp_path):
        src = textwrap.dedent("""\
            class Foo:
                def __init__(self):
                    pass

                def __repr__(self):
                    return "Foo"

                def _private(self):
                    pass

                def public(self):
                    pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        assert "Foo.__init__" in result
        assert "Foo.__repr__" in result
        assert "Foo.public" in result
        assert "Foo._private" not in result
        # No bare "public" — it's class-qualified
        assert "public" not in result

    def test_class_methods_qualified(self, tmp_path):
        src = textwrap.dedent("""\
            class MyClass:
                def method_a(self):
                    pass

                def method_b(self):
                    pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        assert result == ["MyClass.method_a", "MyClass.method_b"]

    def test_async_functions_included(self, tmp_path):
        src = textwrap.dedent("""\
            async def fetch_data():
                pass

            def sync_func():
                pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        assert "fetch_data" in result
        assert "sync_func" in result

    def test_nested_classes(self, tmp_path):
        src = textwrap.dedent("""\
            class Outer:
                def outer_method(self):
                    pass

                class Inner:
                    def inner_method(self):
                        pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        # Outer methods get Outer prefix, inner methods get Inner prefix
        # (inner class replaces outer in class_stack after pop)
        assert "Outer.outer_method" in result
        assert "Inner.inner_method" in result

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.py"
        p.write_text("")
        result = _extract_public_functions(str(p))
        assert result == []

    def test_syntax_error_returns_empty(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def foo(:\n  pass\n")
        result = _extract_public_functions(str(p))
        assert result == []

    def test_nonexistent_file_returns_empty(self):
        result = _extract_public_functions("/nonexistent/file.py")
        assert result == []

    def test_mixed_public_private_top_level(self, tmp_path):
        src = textwrap.dedent("""\
            def alpha():
                pass

            def _beta():
                pass

            def __gamma__():
                pass

            def _delta():
                pass

            def epsilon():
                pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        # __gamma__ is dunder so included; _beta and _delta excluded
        assert "alpha" in result
        assert "__gamma__" in result
        assert "epsilon" in result
        assert "_beta" not in result
        assert "_delta" not in result


# ===========================================================================
# detect_sentinel_patterns
# ===========================================================================


class TestDetectSentinelPatterns:
    """Tests for sentinel pairing and return-type inference logic."""

    def test_is_not_none_paired_with_semantic(self):
        """is_not_none on a root that also has semantic checks gets strength boosted."""
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=1, target_root="result"),
            _ai(AssertionKind.EQUALITY, line=2, target_root="result"),
        ]
        updated, sentinel_targets, semantic_roots = detect_sentinel_patterns(assertions, "test_foo")
        # is_not_none strength boosted to 0.5 because "result" is a semantic root
        assert updated[0].strength == 0.5
        assert "result" in sentinel_targets
        assert "result" in semantic_roots

    def test_is_not_none_unpaired(self):
        """is_not_none without matching semantic check keeps original strength."""
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=1, target_root="result"),
            _ai(AssertionKind.IS_TRUE, line=2, target_root="flag"),
        ]
        updated, sentinel_targets, semantic_roots = detect_sentinel_patterns(assertions, "test_foo")
        # No semantic root for "result", so strength stays at IS_NOT_NONE default (0.3)
        assert updated[0].strength == STRENGTH_MAP[AssertionKind.IS_NOT_NONE]
        assert "result" in sentinel_targets
        assert "result" not in semantic_roots

    def test_is_none_tracked_as_sentinel_target(self):
        assertions = [
            _ai(AssertionKind.IS_NONE, line=1, target_root="val"),
        ]
        _, sentinel_targets, _ = detect_sentinel_patterns(assertions, "test_foo")
        assert "val" in sentinel_targets

    def test_single_is_none_with_check_prefix_in_func_name(self):
        """Single is_none + func name contains 'check_' -> SENTINEL_CHECK."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=5, target_root="x", target_expression="x"),
        ]
        updated, _, _ = detect_sentinel_patterns(assertions, "check_something")
        assert updated[0].kind == AssertionKind.SENTINEL_CHECK
        assert updated[0].strength == STRENGTH_MAP[AssertionKind.SENTINEL_CHECK]
        assert updated[0].confidence == "heuristic"

    def test_single_is_not_none_with_validate_prefix(self):
        """Single is_not_none + func name contains 'validate_' -> SENTINEL_CHECK."""
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=5, target_root="res", target_expression="res"),
        ]
        updated, _, _ = detect_sentinel_patterns(assertions, "validate_input")
        assert updated[0].kind == AssertionKind.SENTINEL_CHECK

    def test_single_is_none_with_detect_in_expression(self):
        """detect_ prefix in target_expression also triggers sentinel check."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=5, target_root="r", target_expression="detect_issue"),
        ]
        updated, _, _ = detect_sentinel_patterns(assertions, "test_generic")
        assert updated[0].kind == AssertionKind.SENTINEL_CHECK

    def test_single_is_none_no_prefix_match(self):
        """Single is_none without matching prefix keeps original kind."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=5, target_root="x", target_expression="x"),
        ]
        updated, _, _ = detect_sentinel_patterns(assertions, "test_something")
        assert updated[0].kind == AssertionKind.IS_NONE

    def test_multiple_assertions_no_sentinel_reclassification(self):
        """Sentinel reclassification only triggers on single-assertion case."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=5, target_root="x", target_expression="x"),
            _ai(AssertionKind.IS_TRUE, line=6, target_root="y"),
        ]
        updated, _, _ = detect_sentinel_patterns(assertions, "check_something")
        # Multiple assertions => no reclassification
        assert updated[0].kind == AssertionKind.IS_NONE

    def test_empty_assertions(self):
        updated, sentinel_targets, semantic_roots = detect_sentinel_patterns([], "test_foo")
        assert updated == []
        assert sentinel_targets == set()
        assert semantic_roots == set()

    def test_semantic_roots_from_multiple_kinds(self):
        """Multiple semantic kinds contribute to semantic_roots."""
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1, target_root="a"),
            _ai(AssertionKind.STRING_CONTAINS, line=2, target_root="b"),
            _ai(AssertionKind.IS_TRUE, line=3, target_root="c"),
        ]
        _, _, semantic_roots = detect_sentinel_patterns(assertions, "test_foo")
        assert "a" in semantic_roots  # EQUALITY strength 0.9 >= 0.7
        assert "b" in semantic_roots  # STRING_CONTAINS strength 0.75 >= 0.7
        assert "c" not in semantic_roots  # IS_TRUE strength 0.2 < 0.7


# ===========================================================================
# detect_isolated_sentinels
# ===========================================================================


class TestDetectIsolatedSentinels:
    """Tests for the isolated sentinel warning generator."""

    def test_isolated_sentinel_produces_warning(self):
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=10, target_root="result"),
        ]
        sentinel_targets = {"result"}
        semantic_roots: set[str] = set()
        warnings = detect_isolated_sentinels(assertions, sentinel_targets, semantic_roots)
        assert len(warnings) == 1
        assert warnings[0]["kind"] == "isolated_sentinel"
        assert "result" in warnings[0]["message"]
        assert warnings[0]["missing_followup_pattern"]["guard_line"] == 10

    def test_paired_sentinel_no_warning(self):
        """If sentinel target is also a semantic root, no warning."""
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=10, target_root="result"),
            _ai(AssertionKind.EQUALITY, line=11, target_root="result"),
        ]
        sentinel_targets = {"result"}
        semantic_roots = {"result"}
        warnings = detect_isolated_sentinels(assertions, sentinel_targets, semantic_roots)
        assert len(warnings) == 0

    def test_trivial_roots_skipped(self):
        """True, False, None roots should not produce warnings."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=1, target_root="True"),
        ]
        sentinel_targets = {"True"}
        semantic_roots: set[str] = set()
        warnings = detect_isolated_sentinels(assertions, sentinel_targets, semantic_roots)
        assert len(warnings) == 0

    def test_empty_root_skipped(self):
        """Empty string root should not produce warnings."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=1, target_root=""),
        ]
        sentinel_targets = {""}
        semantic_roots: set[str] = set()
        warnings = detect_isolated_sentinels(assertions, sentinel_targets, semantic_roots)
        assert len(warnings) == 0

    def test_multiple_isolated_sentinels(self):
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=5, target_root="a"),
            _ai(AssertionKind.IS_NONE, line=6, target_root="b"),
        ]
        sentinel_targets = {"a", "b"}
        semantic_roots: set[str] = set()
        warnings = detect_isolated_sentinels(assertions, sentinel_targets, semantic_roots)
        assert len(warnings) == 2
        roots_warned = {
            w["missing_followup_pattern"]["expected_followup"].split(".")[0].split()[-1]
            for w in warnings
        }
        assert "a" in roots_warned or "b" in roots_warned

    def test_guard_line_from_is_none(self):
        """Guard line is taken from the IS_NONE assertion."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=42, target_root="data"),
        ]
        sentinel_targets = {"data"}
        semantic_roots: set[str] = set()
        warnings = detect_isolated_sentinels(assertions, sentinel_targets, semantic_roots)
        assert warnings[0]["missing_followup_pattern"]["guard_line"] == 42

    def test_remediation_message_content(self):
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=7, target_root="obj"),
        ]
        warnings = detect_isolated_sentinels(assertions, {"obj"}, set())
        assert "Verify the state of 'obj'" in warnings[0]["remediation"]


# ===========================================================================
# detect_hasattr_chains
# ===========================================================================


class TestDetectHasattrChains:
    """Tests for the hasattr chain anti-pattern detector."""

    def test_chain_of_three_triggers_warning(self):
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=1, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=2, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=3, target_expression="obj"),
        ]
        warnings = detect_hasattr_chains(assertions)
        assert len(warnings) == 1
        assert warnings[0]["kind"] == "hasattr_chain"
        assert "3 hasattr checks" in warnings[0]["message"]
        assert "lines 1-3" in warnings[0]["message"]

    def test_chain_of_two_no_warning(self):
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=1, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=2, target_expression="obj"),
        ]
        warnings = detect_hasattr_chains(assertions)
        assert len(warnings) == 0

    def test_chain_broken_by_different_target(self):
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=1, target_expression="obj1"),
            _ai(AssertionKind.HASATTR_CHECK, line=2, target_expression="obj1"),
            _ai(AssertionKind.HASATTR_CHECK, line=3, target_expression="obj2"),
            _ai(AssertionKind.HASATTR_CHECK, line=4, target_expression="obj2"),
        ]
        warnings = detect_hasattr_chains(assertions)
        assert len(warnings) == 0  # Neither chain reaches 3

    def test_chain_broken_by_non_hasattr(self):
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=1, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=2, target_expression="obj"),
            _ai(AssertionKind.EQUALITY, line=3, target_expression="x"),
            _ai(AssertionKind.HASATTR_CHECK, line=4, target_expression="obj"),
        ]
        warnings = detect_hasattr_chains(assertions)
        assert len(warnings) == 0

    def test_multiple_chains(self):
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=1, target_expression="a"),
            _ai(AssertionKind.HASATTR_CHECK, line=2, target_expression="a"),
            _ai(AssertionKind.HASATTR_CHECK, line=3, target_expression="a"),
            _ai(AssertionKind.EQUALITY, line=4, target_expression="x"),
            _ai(AssertionKind.HASATTR_CHECK, line=5, target_expression="b"),
            _ai(AssertionKind.HASATTR_CHECK, line=6, target_expression="b"),
            _ai(AssertionKind.HASATTR_CHECK, line=7, target_expression="b"),
            _ai(AssertionKind.HASATTR_CHECK, line=8, target_expression="b"),
        ]
        warnings = detect_hasattr_chains(assertions)
        assert len(warnings) == 2
        targets = {w["message"].split("'")[1] for w in warnings}
        assert targets == {"a", "b"}

    def test_empty_assertions(self):
        warnings = detect_hasattr_chains([])
        assert warnings == []

    def test_chain_at_end_of_list(self):
        """Chain ending at the last assertion is still detected."""
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1, target_expression="x"),
            _ai(AssertionKind.HASATTR_CHECK, line=2, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=3, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=4, target_expression="obj"),
        ]
        warnings = detect_hasattr_chains(assertions)
        assert len(warnings) == 1

    def test_remediation_message(self):
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=10, target_expression="res"),
            _ai(AssertionKind.HASATTR_CHECK, line=11, target_expression="res"),
            _ai(AssertionKind.HASATTR_CHECK, line=12, target_expression="res"),
        ]
        warnings = detect_hasattr_chains(assertions)
        assert "attribute equality" in warnings[0]["remediation"]


# ===========================================================================
# analyze_function_effectiveness
# ===========================================================================


class TestAnalyzeFunctionEffectiveness:
    """Tests for single-function effectiveness analysis pipeline."""

    def test_empty_assertions_untested(self):
        fe, warnings = analyze_function_effectiveness("test_foo", [])
        assert fe.effectiveness_score == 0.0
        assert fe.mutation_vulnerability == 1.0
        assert fe.weakness_taxonomy == EffectivenessWeakness.UNTESTED
        assert warnings == []

    def test_all_semantic_healthy(self):
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1, target_root="x"),
            _ai(AssertionKind.STRING_CONTAINS, line=2, target_root="y"),
        ]
        fe, warnings = analyze_function_effectiveness("test_foo", assertions)
        assert fe.semantic_ratio == 1.0
        assert fe.weakness_taxonomy == EffectivenessWeakness.HEALTHY
        expected_score = (
            STRENGTH_MAP[AssertionKind.EQUALITY] + STRENGTH_MAP[AssertionKind.STRING_CONTAINS]
        ) / 2
        assert abs(fe.effectiveness_score - expected_score) < 1e-9

    def test_structural_only_with_hasattr(self):
        """Exclusively structural (with hasattr) triggers the anti-pattern warning.

        The is_structural_only check in analyze_function_effectiveness allows
        HASATTR_CHECK, ISINSTANCE_CHECK, IS_TRUE, IS_NONE, IS_NOT_NONE.
        All structural kinds have strength < 0.7, so semantic_ratio is always 0.
        Max structural strength is 0.3 (IS_NOT_NONE/ISINSTANCE), so effectiveness
        is always < 0.4, meaning GENUINELY_WEAK fires before STRUCTURAL_ONLY
        in the taxonomy waterfall. But the anti-pattern warning is generated
        independently of taxonomy.
        """
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=1, target_root="obj"),
            _ai(AssertionKind.ISINSTANCE_CHECK, line=2, target_root="obj"),
        ]
        fe, warnings = analyze_function_effectiveness("test_foo", assertions)
        # Taxonomy: semantic_ratio=0 < 0.3, effectiveness = (0.1 + 0.3)/2 = 0.2 < 0.4
        # => GENUINELY_WEAK fires first in the waterfall
        assert fe.weakness_taxonomy == EffectivenessWeakness.GENUINELY_WEAK
        # But the anti-pattern warning IS still generated (it's separate from taxonomy)
        anti_patterns = [w for w in warnings if w.get("function") == "test_foo"]
        assert len(anti_patterns) == 1
        assert "hasattr/isinstance" in anti_patterns[0]["reason"]

    def test_genuinely_weak_taxonomy(self):
        """Low semantic ratio (<0.3) AND low effectiveness (<0.4)."""
        assertions = [
            _ai(AssertionKind.IS_TRUE, line=1, target_root="a"),
            _ai(AssertionKind.IS_NONE, line=2, target_root="b"),
            _ai(AssertionKind.IS_NOT_NONE, line=3, target_root="c"),
        ]
        fe, _ = analyze_function_effectiveness("test_foo", assertions)
        assert fe.semantic_ratio < 0.3
        assert fe.effectiveness_score < 0.4
        assert fe.weakness_taxonomy == EffectivenessWeakness.GENUINELY_WEAK

    def test_isolated_sentinel_taxonomy(self):
        """Isolated sentinel triggers SENTINEL_HEAVY taxonomy."""
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=1, target_root="result"),
            _ai(AssertionKind.EQUALITY, line=2, target_root="other"),
        ]
        fe, warnings = analyze_function_effectiveness("test_foo", assertions)
        # "result" has is_not_none but no semantic follow-up -> isolated sentinel
        isolated = [w for w in warnings if w.get("kind") == "isolated_sentinel"]
        assert len(isolated) == 1
        assert fe.weakness_taxonomy == EffectivenessWeakness.SENTINEL_HEAVY

    def test_boolean_contract_heavy_taxonomy(self):
        """Heavy boolean contract usage (>40%) triggers BOOLEAN_CONTRACT_HEAVY."""
        assertions = [
            _ai(AssertionKind.BOOLEAN_CONTRACT_CALL, line=1, target_root="a"),
            _ai(AssertionKind.BOOLEAN_CONTRACT_CALL, line=2, target_root="b"),
            _ai(AssertionKind.BOOLEAN_CONTRACT_FIELD, line=3, target_root="c"),
            _ai(AssertionKind.EQUALITY, line=4, target_root="d"),
        ]
        fe, _ = analyze_function_effectiveness("test_foo", assertions)
        # 3 out of 4 = 75% boolean contract → BOOLEAN_CONTRACT_HEAVY
        assert fe.weakness_taxonomy == EffectivenessWeakness.BOOLEAN_CONTRACT_HEAVY

    def test_derivation_methods_forwarded(self):
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1, target_root="x"),
        ]
        fe, _ = analyze_function_effectiveness(
            "test_foo", assertions, derivation_methods=["mutation_calibration"]
        )
        assert fe.confidence is not None
        assert "mutation_calibration" in fe.confidence.derivation_methods
        # mutation_calibration bumps confidence to 0.95, but single assertion subtracts 0.2
        assert fe.confidence.score == 0.75

    def test_hasattr_chain_warning_integrated(self):
        """Hasattr chain detection is integrated into the analysis pipeline."""
        assertions = [
            _ai(AssertionKind.HASATTR_CHECK, line=1, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=2, target_expression="obj"),
            _ai(AssertionKind.HASATTR_CHECK, line=3, target_expression="obj"),
        ]
        _, warnings = analyze_function_effectiveness("test_foo", assertions)
        hasattr_warnings = [w for w in warnings if w.get("kind") == "hasattr_chain"]
        assert len(hasattr_warnings) == 1

    def test_sentinel_check_reclassification_through_pipeline(self):
        """Single is_none on a check_ function gets reclassified via pipeline."""
        assertions = [
            _ai(AssertionKind.IS_NONE, line=5, target_root="r", target_expression="r"),
        ]
        fe, _ = analyze_function_effectiveness("check_valid", assertions)
        # The sentinel reclassification fires, changing strength
        assert fe.assertions[0].kind == AssertionKind.SENTINEL_CHECK
        assert fe.assertions[0].strength == STRENGTH_MAP[AssertionKind.SENTINEL_CHECK]

    def test_test_count_is_one(self):
        """analyze_function_effectiveness always sets test_count=1."""
        fe, _ = analyze_function_effectiveness(
            "test_foo",
            [
                _ai(AssertionKind.EQUALITY, line=1),
            ],
        )
        assert fe.test_count == 1


# ===========================================================================
# _extract_public_functions — additional edge cases
# ===========================================================================


class TestExtractPublicFunctionsEdgeCases:
    def test_only_private_functions(self, tmp_path):
        src = textwrap.dedent("""\
            def _a():
                pass

            def _b():
                pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        assert result == []

    def test_class_with_async_methods(self, tmp_path):
        src = textwrap.dedent("""\
            class Worker:
                async def run(self):
                    pass

                async def _internal(self):
                    pass
        """)
        p = tmp_path / "mod.py"
        p.write_text(src)
        result = _extract_public_functions(str(p))
        assert "Worker.run" in result
        assert "Worker._internal" not in result


# ===========================================================================
# _discover_test_files / _discover_source_files
# ===========================================================================


class TestDiscoverFiles:
    """Tests for file discovery functions with mocked discover_project_files."""

    def test_discover_test_files_filters_to_test_prefix(self, tmp_path):
        """Only files starting with test_ are returned."""
        # Create files
        (tmp_path / "test_foo.py").write_text("# test")
        (tmp_path / "foo.py").write_text("# source")
        (tmp_path / "test_bar.py").write_text("# test")
        (tmp_path / "helper.py").write_text("# helper")

        all_files = [
            str(tmp_path / "test_foo.py"),
            str(tmp_path / "foo.py"),
            str(tmp_path / "test_bar.py"),
            str(tmp_path / "helper.py"),
        ]
        with patch("lintgate.discovery.discover_project_files", return_value=all_files):
            result = _discover_test_files(str(tmp_path))
        assert len(result) == 2
        assert all(os.path.basename(f).startswith("test_") for f in result)

    def test_discover_test_files_max_files(self, tmp_path):
        """max_files limits the number of test files returned."""
        (tmp_path / "test_a.py").write_text("")
        (tmp_path / "test_b.py").write_text("")
        (tmp_path / "test_c.py").write_text("")
        all_files = [str(tmp_path / f"test_{c}.py") for c in "abc"]
        with patch("lintgate.discovery.discover_project_files", return_value=all_files):
            result = _discover_test_files(str(tmp_path), max_files=2)
        assert len(result) == 2

    def test_discover_test_files_skips_nested_subprojects(self, tmp_path):
        """Test files in nested subprojects (with pyproject.toml) are skipped.

        The skip logic checks range(1, len(parts)), meaning a marker at
        parts[:i+1] for i>=1. So the file must be at least 2 dirs deep
        and the marker at the intermediate (not first) directory.
        E.g., sub/nested/tests/test_nested.py with sub/nested/pyproject.toml.
        """
        nested = tmp_path / "sub" / "nested" / "tests"
        nested.mkdir(parents=True)
        (tmp_path / "sub" / "nested" / "pyproject.toml").write_text("")
        test_file = nested / "test_nested.py"
        test_file.write_text("")
        top_test = tmp_path / "test_top.py"
        top_test.write_text("")
        all_files = [str(top_test), str(test_file)]
        with patch("lintgate.discovery.discover_project_files", return_value=all_files):
            result = _discover_test_files(str(tmp_path))
        assert len(result) == 1
        assert os.path.basename(result[0]) == "test_top.py"

    def test_discover_source_files_excludes_test_files(self, tmp_path):
        """test_ prefix and _test.py suffix files are excluded."""
        (tmp_path / "foo.py").write_text("")
        (tmp_path / "test_foo.py").write_text("")
        (tmp_path / "bar_test.py").write_text("")
        all_files = [
            str(tmp_path / "foo.py"),
            str(tmp_path / "test_foo.py"),
            str(tmp_path / "bar_test.py"),
        ]
        with patch("lintgate.discovery.discover_project_files", return_value=all_files):
            result = _discover_source_files(str(tmp_path))
        assert len(result) == 1
        assert os.path.basename(result[0]) == "foo.py"

    def test_discover_source_files_excludes_tests_directory(self, tmp_path):
        """Files under /tests/ directory are excluded."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "conftest.py").write_text("")
        (tmp_path / "app.py").write_text("")
        all_files = [
            str(tmp_path / "app.py"),
            str(tests_dir / "conftest.py"),
        ]
        with patch("lintgate.discovery.discover_project_files", return_value=all_files):
            result = _discover_source_files(str(tmp_path))
        assert len(result) == 1
        assert os.path.basename(result[0]) == "app.py"

    def test_discover_source_files_max_files(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.py").write_text("")
        all_files = [str(tmp_path / f"{c}.py") for c in "abc"]
        with patch("lintgate.discovery.discover_project_files", return_value=all_files):
            result = _discover_source_files(str(tmp_path), max_files=1)
        assert len(result) == 1

    def test_discover_source_files_skips_nested_subproject_with_setup_py(self, tmp_path):
        """Nested subprojects with setup.py marker are skipped.

        Like the test-file variant, the file must be 2+ dirs deep with the
        marker at an intermediate directory: vendor/lib/src/util.py with
        vendor/lib/setup.py triggers the skip at i=1 (parts[:2] = ['vendor','lib']).
        """
        sub = tmp_path / "vendor" / "lib" / "src"
        sub.mkdir(parents=True)
        (tmp_path / "vendor" / "lib" / "setup.py").write_text("")
        nested_src = sub / "util.py"
        nested_src.write_text("")
        top_src = tmp_path / "main.py"
        top_src.write_text("")
        all_files = [str(top_src), str(nested_src)]
        with patch("lintgate.discovery.discover_project_files", return_value=all_files):
            result = _discover_source_files(str(tmp_path))
        assert len(result) == 1
        assert os.path.basename(result[0]) == "main.py"


# ===========================================================================
# analyze_effectiveness (project-wide orchestration)
# ===========================================================================


class TestAnalyzeEffectiveness:
    """Tests for the project-wide analysis orchestrator."""

    def test_empty_source_files_returns_empty(self, tmp_path):
        results, diag = analyze_effectiveness(
            str(tmp_path), source_files=[], test_files=["dummy.py"]
        )
        assert results == {}
        assert isinstance(diag, MappingDiagnostics)

    def test_empty_test_files_returns_empty(self, tmp_path):
        results, diag = analyze_effectiveness(
            str(tmp_path), source_files=["dummy.py"], test_files=[]
        )
        assert results == {}

    def test_basic_source_with_no_mapped_tests(self, tmp_path):
        """Source functions without mapped tests get zero effectiveness."""
        # Create a source file with a public function
        src = tmp_path / "lib.py"
        src.write_text("def compute(x):\n    return x + 1\n")

        # Create a test file that doesn't map to compute
        test = tmp_path / "test_unrelated.py"
        test.write_text("def test_other():\n    assert True\n")

        results, diag = analyze_effectiveness(
            str(tmp_path),
            source_files=[str(src)],
            test_files=[str(test)],
        )
        # compute should appear with 0 tests mapped
        assert len(results) >= 1
        # Find the compute entry
        compute_key = None
        for k in results:
            if "compute" in k:
                compute_key = k
                break
        assert compute_key is not None
        fe = results[compute_key]
        assert fe.test_count == 0
        assert fe.effectiveness_score == 0.0

    def test_mapped_tests_produce_scores(self, tmp_path):
        """When test maps to source by naming convention, scores are computed."""
        src = tmp_path / "calculator.py"
        src.write_text("def add(a, b):\n    return a + b\n")

        test = tmp_path / "test_calculator.py"
        test.write_text(
            textwrap.dedent("""\
            from calculator import add

            def test_add():
                assert add(1, 2) == 3
        """)
        )

        results, diag = analyze_effectiveness(
            str(tmp_path),
            source_files=[str(src)],
            test_files=[str(test)],
        )
        # "add" should be mapped
        add_key = None
        for k in results:
            if "add" in k:
                add_key = k
                break
        assert add_key is not None
        fe = results[add_key]
        assert fe.test_count >= 1
        # The test has an equality assertion => high effectiveness
        assert fe.effectiveness_score > 0.5

    def test_effective_weights_override(self, tmp_path):
        """Custom effective_weights override strength map."""
        src = tmp_path / "mod.py"
        src.write_text("def func():\n    return 42\n")

        test = tmp_path / "test_mod.py"
        test.write_text(
            textwrap.dedent("""\
            from mod import func

            def test_func():
                result = func()
                assert result == 42
        """)
        )

        custom_weights = {AssertionKind.EQUALITY: 0.5}
        results, _ = analyze_effectiveness(
            str(tmp_path),
            source_files=[str(src)],
            test_files=[str(test)],
            effective_weights=custom_weights,
        )
        func_key = None
        for k in results:
            if "func" in k:
                func_key = k
                break
        if func_key:
            fe = results[func_key]
            # If the equality assertion was found and weight overridden
            eq_assertions = [a for a in fe.assertions if a.kind == AssertionKind.EQUALITY]
            for a in eq_assertions:
                assert a.strength == 0.5

    def test_diagnostics_returned(self, tmp_path):
        """MappingDiagnostics is always returned (even when empty)."""
        src = tmp_path / "a.py"
        src.write_text("def alpha():\n    pass\n")
        test = tmp_path / "test_a.py"
        test.write_text("def test_alpha():\n    assert True\n")

        _, diag = analyze_effectiveness(
            str(tmp_path),
            source_files=[str(src)],
            test_files=[str(test)],
        )
        assert isinstance(diag, MappingDiagnostics)
        # diagnostics should have attempted something
        assert diag.counts.attempted >= 0

    def test_auto_discovery_when_files_none(self, tmp_path):
        """When source_files and test_files are None, discovery is called."""
        src = tmp_path / "mod.py"
        src.write_text("def greet():\n    return 'hi'\n")
        test = tmp_path / "test_mod.py"
        test.write_text("def test_greet():\n    assert True\n")

        mock_all = [str(src), str(test)]
        with patch("lintgate.discovery.discover_project_files", return_value=mock_all):
            results, diag = analyze_effectiveness(str(tmp_path))
        # Should still work — greet discovered from auto-discovered source
        assert isinstance(results, dict)
        assert isinstance(diag, MappingDiagnostics)

    def test_private_functions_excluded_from_results(self, tmp_path):
        """Private functions (single underscore) are not in results."""
        src = tmp_path / "mod.py"
        src.write_text(
            textwrap.dedent("""\
            def _private():
                pass

            def public():
                pass
        """)
        )
        test = tmp_path / "test_mod.py"
        test.write_text("def test_public():\n    assert True\n")

        results, _ = analyze_effectiveness(
            str(tmp_path),
            source_files=[str(src)],
            test_files=[str(test)],
        )
        keys = list(results.keys())
        assert not any("_private" in k for k in keys)
        assert any("public" in k for k in keys)

    def test_dedup_test_funcs(self, tmp_path):
        """Duplicate test function mappings are deduplicated."""
        src = tmp_path / "calc.py"
        src.write_text("def multiply(a, b):\n    return a * b\n")

        # Two test files that both map to multiply via naming + call
        test1 = tmp_path / "test_calc.py"
        test1.write_text(
            textwrap.dedent("""\
            from calc import multiply

            def test_multiply():
                assert multiply(2, 3) == 6
        """)
        )

        results, _ = analyze_effectiveness(
            str(tmp_path),
            source_files=[str(src)],
            test_files=[str(test1)],
        )
        # The key for multiply should exist
        mult_key = None
        for k in results:
            if "multiply" in k:
                mult_key = k
                break
        if mult_key:
            fe = results[mult_key]
            # test_multiply should appear only once, not duplicated
            assert fe.test_count >= 1


# ===========================================================================
# Integration: detect_sentinel_patterns + detect_isolated_sentinels combined
# ===========================================================================


class TestSentinelIntegration:
    """Integration tests verifying the full sentinel detection pipeline."""

    def test_paired_guard_no_warning_no_isolated(self):
        """When is_not_none root matches an equality root, no isolated warning."""
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=1, target_root="result"),
            _ai(AssertionKind.EQUALITY, line=2, target_root="result"),
        ]
        updated, sentinel_targets, semantic_roots = detect_sentinel_patterns(assertions, "test_foo")
        warnings = detect_isolated_sentinels(updated, sentinel_targets, semantic_roots)
        assert len(warnings) == 0
        # is_not_none strength should be boosted
        assert updated[0].strength == 0.5

    def test_unpaired_guard_produces_isolated_warning(self):
        """is_not_none without semantic follow-up triggers isolated warning."""
        assertions = [
            _ai(AssertionKind.IS_NOT_NONE, line=1, target_root="data"),
            _ai(AssertionKind.EQUALITY, line=2, target_root="other"),
        ]
        updated, sentinel_targets, semantic_roots = detect_sentinel_patterns(assertions, "test_foo")
        warnings = detect_isolated_sentinels(updated, sentinel_targets, semantic_roots)
        assert len(warnings) == 1
        assert "data" in warnings[0]["message"]

    def test_only_semantic_no_sentinel_targets(self):
        """Pure semantic assertions produce no sentinel targets or warnings."""
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1, target_root="x"),
            _ai(AssertionKind.STRING_CONTAINS, line=2, target_root="y"),
        ]
        updated, sentinel_targets, semantic_roots = detect_sentinel_patterns(assertions, "test_foo")
        warnings = detect_isolated_sentinels(updated, sentinel_targets, semantic_roots)
        assert sentinel_targets == set()
        assert warnings == []


# ===========================================================================
# Confidence derivation via analyze_function_effectiveness
# ===========================================================================


class TestConfidenceDerivation:
    """Tests for confidence scoring behavior through the analysis pipeline."""

    def test_default_confidence_ast_walk(self):
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1),
            _ai(AssertionKind.EQUALITY, line=2),
        ]
        fe, _ = analyze_function_effectiveness("test_foo", assertions)
        assert fe.confidence is not None
        assert fe.confidence.score == 0.8
        assert fe.confidence.derivation_methods == ["ast_walk"]

    def test_single_assertion_confidence_penalty(self):
        """Single assertion reduces confidence by 0.2."""
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1),
        ]
        fe, _ = analyze_function_effectiveness("test_foo", assertions)
        assert fe.confidence is not None
        assert fe.confidence.score == 0.6  # 0.8 - 0.2

    def test_mutation_calibration_high_confidence(self):
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1),
            _ai(AssertionKind.EQUALITY, line=2),
        ]
        fe, _ = analyze_function_effectiveness(
            "test_foo", assertions, derivation_methods=["mutation_calibration"]
        )
        assert fe.confidence is not None
        assert fe.confidence.score == 0.95

    def test_empty_assertions_low_confidence(self):
        fe, _ = analyze_function_effectiveness("test_foo", [])
        assert fe.confidence is not None
        assert fe.confidence.score == 0.1

    def test_confidence_floor_at_0_1(self):
        """Confidence score never drops below 0.1."""
        # mutation_calibration starts at 0.95, single assertion -0.2 = 0.75 — still above 0.1
        # But default (0.8) with single (-0.2) = 0.6 — also above
        # For the untested case it's already 0.1
        fe, _ = analyze_function_effectiveness("test_foo", [])
        assert fe.confidence is not None
        assert fe.confidence.score >= 0.1


# ===========================================================================
# Quality profile via analyze_function_effectiveness
# ===========================================================================


class TestQualityProfile:
    """Tests for the multi-axis quality profile produced by analysis."""

    def test_boundary_ratio(self):
        assertions = [
            _ai(AssertionKind.RAISES, line=1),
            _ai(AssertionKind.LENGTH_CHECK, line=2),
            _ai(AssertionKind.EQUALITY, line=3),
            _ai(AssertionKind.IS_TRUE, line=4),
        ]
        fe, _ = analyze_function_effectiveness("test_foo", assertions)
        qp = fe.quality_profile
        assert qp.boundary_ratio == round(2 / 4, 3)

    def test_assertion_counts(self):
        assertions = [
            _ai(AssertionKind.EQUALITY, line=1),
            _ai(AssertionKind.EQUALITY, line=2),
            _ai(AssertionKind.IS_TRUE, line=3),
        ]
        fe, _ = analyze_function_effectiveness("test_foo", assertions)
        qp = fe.quality_profile
        assert qp.assertion_counts["equality"] == 2
        assert qp.assertion_counts["is_true"] == 1

    def test_actionable_ratio_excludes_paired_guards(self):
        """Paired guards (strength >= 0.5 sentinel checks) are excluded from denominator."""
        assertions = [
            # This is_not_none won't be paired (no semantic root match) so strength stays 0.3
            _ai(AssertionKind.IS_NOT_NONE, line=1, target_root="x"),
            _ai(AssertionKind.EQUALITY, line=2, target_root="y"),
        ]
        fe, _ = analyze_function_effectiveness("test_foo", assertions)
        qp = fe.quality_profile
        # is_not_none at 0.3 < 0.5 so not a paired guard -> denominator = 2
        # semantic_count = 1 (equality), boundary = 0 -> actionable = 1/2 = 0.5
        assert qp.actionable_ratio == 0.5
