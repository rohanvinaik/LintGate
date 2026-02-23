"""Tests for test_analyzer — effectiveness scoring."""

from __future__ import annotations

from lintgate.linters.test_effectiveness.types import (
    STRENGTH_MAP,
    AssertionInfo,
    AssertionKind,
    FunctionEffectiveness,
)


def test_compute_scores_no_assertions():
    """Empty assertions → vulnerability 1.0."""
    fe = FunctionEffectiveness(function_name="foo", test_count=0)
    fe.compute_scores()
    assert fe.effectiveness_score == 0.0
    assert fe.mutation_vulnerability == 1.0
    assert fe.semantic_ratio == 0.0


def test_compute_scores_all_structural():
    """Only structural assertions → low effectiveness."""
    fe = FunctionEffectiveness(
        function_name="foo",
        test_count=1,
        assertions=[
            AssertionInfo(kind=AssertionKind.IS_NOT_NONE, line=10, strength=0.3),
            AssertionInfo(kind=AssertionKind.IS_TRUE, line=11, strength=0.2),
        ],
    )
    fe.compute_scores()
    assert fe.semantic_ratio == 0.0
    assert fe.structural_ratio == 1.0
    assert fe.effectiveness_score == 0.25  # (0.3 + 0.2) / 2
    assert fe.mutation_vulnerability == 0.75


def test_compute_scores_all_semantic():
    """Only semantic assertions → high effectiveness."""
    fe = FunctionEffectiveness(
        function_name="foo",
        test_count=2,
        assertions=[
            AssertionInfo(kind=AssertionKind.EQUALITY, line=10, strength=0.9),
            AssertionInfo(kind=AssertionKind.LENGTH_CHECK, line=11, strength=0.8),
        ],
    )
    fe.compute_scores()
    assert fe.semantic_ratio == 1.0
    assert fe.structural_ratio == 0.0
    assert abs(fe.effectiveness_score - 0.85) < 1e-9  # (0.9 + 0.8) / 2
    assert abs(fe.mutation_vulnerability - 0.15) < 1e-9  # 1.0 - 0.85


def test_compute_scores_mixed():
    """Mix of structural and semantic → intermediate score."""
    fe = FunctionEffectiveness(
        function_name="foo",
        test_count=1,
        assertions=[
            AssertionInfo(kind=AssertionKind.IS_NOT_NONE, line=10, strength=0.3),
            AssertionInfo(kind=AssertionKind.EQUALITY, line=11, strength=0.9),
        ],
    )
    fe.compute_scores()
    assert fe.semantic_ratio == 0.5
    assert fe.structural_ratio == 0.5
    assert fe.effectiveness_score == 0.6  # (0.3 + 0.9) / 2


def test_function_effectiveness_roundtrip():
    """Serialize and deserialize FunctionEffectiveness."""
    fe = FunctionEffectiveness(
        function_name="bar",
        test_count=3,
        assertions=[
            AssertionInfo(
                kind=AssertionKind.EQUALITY, line=5, strength=0.9, target_expression="result"
            ),
        ],
    )
    fe.compute_scores()

    d = fe.to_dict()
    assert d["function_name"] == "bar"
    assert d["test_count"] == 3
    assert len(d["assertions"]) == 1

    restored = FunctionEffectiveness.from_dict(d)
    assert restored.function_name == "bar"
    assert restored.test_count == 3
    assert len(restored.assertions) == 1
    assert restored.assertions[0].kind == AssertionKind.EQUALITY


def test_strength_map_completeness():
    """Every AssertionKind has a strength in STRENGTH_MAP."""
    for kind in AssertionKind:
        assert kind in STRENGTH_MAP, f"Missing strength for {kind}"
        assert 0.0 <= STRENGTH_MAP[kind] <= 1.0
