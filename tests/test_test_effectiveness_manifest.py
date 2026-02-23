"""Tests for test effectiveness manifest build + cache."""

from __future__ import annotations

from lintgate.linters.test_effectiveness.types import (
    AssertionInfo,
    AssertionKind,
    FunctionEffectiveness,
    TestEffectivenessManifest,
)


def test_manifest_update_metrics_empty():
    """Empty manifest has zero scores."""
    m = TestEffectivenessManifest()
    m.update_metrics()
    assert m.project_score == 0.0
    assert m.functions_analyzed == 0
    assert m.mutation_vulnerable_count == 0


def test_manifest_update_metrics_with_functions():
    """Manifest correctly computes aggregate metrics."""
    fe1 = FunctionEffectiveness(
        function_name="foo",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
    )
    fe1.compute_scores()

    fe2 = FunctionEffectiveness(
        function_name="bar",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.IS_TRUE, line=1, strength=0.2)],
    )
    fe2.compute_scores()

    m = TestEffectivenessManifest(functions={"foo": fe1, "bar": fe2})
    m.update_metrics()

    assert m.functions_analyzed == 2
    assert m.project_score == (0.9 + 0.2) / 2
    assert m.mutation_vulnerable_count == 1  # bar has vulnerability 0.8 > 0.7


def test_manifest_roundtrip():
    """Serialize and deserialize TestEffectivenessManifest."""
    fe = FunctionEffectiveness(
        function_name="baz",
        test_count=2,
        assertions=[
            AssertionInfo(kind=AssertionKind.EQUALITY, line=5, strength=0.9),
            AssertionInfo(kind=AssertionKind.LENGTH_CHECK, line=6, strength=0.8),
        ],
    )
    fe.compute_scores()

    m = TestEffectivenessManifest(
        functions={"baz": fe},
        file_scores={"src/module.py": 0.85},
    )
    m.update_metrics()

    d = m.to_dict()
    restored = TestEffectivenessManifest.from_dict(d)

    assert restored.functions_analyzed == 1
    assert "baz" in restored.functions
    assert restored.functions["baz"].function_name == "baz"
    assert len(restored.functions["baz"].assertions) == 2
    assert restored.mutation_vulnerable_count == 0  # baz has low vulnerability


def test_manifest_vulnerability_count():
    """Mutation vulnerable count only counts functions above threshold."""
    # All strong → no vulnerable
    fe = FunctionEffectiveness(
        function_name="strong",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
    )
    fe.compute_scores()

    m = TestEffectivenessManifest(functions={"strong": fe})
    m.update_metrics()
    assert m.mutation_vulnerable_count == 0

    # Weak → vulnerable
    fe_weak = FunctionEffectiveness(
        function_name="weak",
        test_count=1,
        assertions=[AssertionInfo(kind=AssertionKind.IS_TRUE, line=1, strength=0.2)],
    )
    fe_weak.compute_scores()

    m2 = TestEffectivenessManifest(functions={"weak": fe_weak})
    m2.update_metrics()
    assert m2.mutation_vulnerable_count == 1
