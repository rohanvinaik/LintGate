from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
    analyze_function_effectiveness,
)
from lintgate.linters.test_effectiveness.types import (
    TEFF_SCHEMA_VERSION,
    AssertionInfo,
    AssertionKind,
    EffectivenessWeakness,
    FunctionEffectiveness,
    MappingDiagnostics,
)


def test_v2_schema_and_taxonomy():
    print(f"Verifying Test Effectiveness Schema v{TEFF_SCHEMA_VERSION}")

    # 1. Test UNTESTED taxonomy
    fe_untested = FunctionEffectiveness(function_name="untested_func", assertions=[])
    fe_untested.compute_scores()
    print(f"UNTESTED Taxonomy: {fe_untested.weakness_taxonomy}")
    assert fe_untested.weakness_taxonomy == EffectivenessWeakness.UNTESTED
    assert fe_untested.confidence.score == 0.1

    # 2. Test GENUINELY_WEAK
    weak_assertions = [
        AssertionInfo(
            kind=AssertionKind.IS_TRUE, target_expression="x", strength=0.2, line=10
        ),
    ]
    fe_weak = FunctionEffectiveness(
        function_name="weak_func", assertions=weak_assertions
    )
    fe_weak.compute_scores()
    print(f"GENUINELY_WEAK Taxonomy: {fe_weak.weakness_taxonomy}")
    assert fe_weak.weakness_taxonomy == EffectivenessWeakness.GENUINELY_WEAK

    # 3. Test SENTINEL_HEAVY
    # Needs to NOT be GENUINELY_WEAK (sem_ratio >= 0.3 or effectiveness >= 0.4)
    sentinel_assertions = [
        AssertionInfo(
            kind=AssertionKind.EQUALITY, target_expression="y", strength=0.9, line=9
        ),
        AssertionInfo(
            kind=AssertionKind.IS_NOT_NONE, target_expression="x", strength=0.3, line=10
        ),
        AssertionInfo(
            kind=AssertionKind.IS_NOT_NONE, target_expression="x", strength=0.3, line=11
        ),
    ]
    # sem_ratio = 1/3 = 0.33 (> 0.3) -> Not Genuinely Weak
    # effectiveness = (0.9 + 0.3 + 0.3) / 3 = 0.5 (> 0.4) -> Not Genuinely Weak
    fe_sentinel = FunctionEffectiveness(
        function_name="sentinel_func", assertions=sentinel_assertions
    )
    fe_sentinel.compute_scores(has_isolated_sentinel=True)
    print(f"SENTINEL_HEAVY Taxonomy: {fe_sentinel.weakness_taxonomy}")
    assert fe_sentinel.weakness_taxonomy == EffectivenessWeakness.SENTINEL_HEAVY

    # 4. Test BOOLEAN_CONTRACT_HEAVY
    bool_assertions = [
        AssertionInfo(
            kind=AssertionKind.BOOLEAN_CONTRACT_CALL,
            target_expression="is_valid()",
            strength=0.4,
            line=10,
        ),
        AssertionInfo(
            kind=AssertionKind.BOOLEAN_CONTRACT_CALL,
            target_expression="is_ready()",
            strength=0.4,
            line=11,
        ),
    ]
    fe_bool = FunctionEffectiveness(
        function_name="bool_func", assertions=bool_assertions
    )
    fe_bool.compute_scores()
    print(f"BOOLEAN_CONTRACT_HEAVY Taxonomy: {fe_bool.weakness_taxonomy}")
    assert fe_bool.weakness_taxonomy == EffectivenessWeakness.BOOLEAN_CONTRACT_HEAVY

    # 5. Test STRUCTURAL_ONLY
    # Per user waterfall, GENUINELY_WEAK (sem < 0.3, eff < 0.4) takes precedence.
    # To hit STRUCTURAL_ONLY, we need sem_ratio >= 0.3 OR eff_score >= 0.4 while remaining structural.
    # This is tricky because structural kinds have low strength.
    # Let's verify that it currently hits GENUINELY_WEAK as per waterfall.
    structural_assertions = [
        AssertionInfo(
            kind=AssertionKind.ISINSTANCE_CHECK,
            target_expression="x",
            strength=0.3,
            line=10,
        ),
    ]
    fe_structural = FunctionEffectiveness(
        function_name="structural_func", assertions=structural_assertions
    )
    fe_structural.compute_scores()
    print(
        f"STRUCTURAL_ONLY case (actually GENUINELY_WEAK): {fe_structural.weakness_taxonomy}"
    )
    assert fe_structural.weakness_taxonomy == EffectivenessWeakness.GENUINELY_WEAK

    # 6. Test HEALTHY
    healthy_assertions = [
        AssertionInfo(
            kind=AssertionKind.EQUALITY, target_expression="x", strength=0.9, line=10
        ),
    ]
    fe_healthy = FunctionEffectiveness(
        function_name="healthy_func", assertions=healthy_assertions
    )
    fe_healthy.compute_scores()
    print(f"HEALTHY Taxonomy: {fe_healthy.weakness_taxonomy}")
    assert fe_healthy.weakness_taxonomy == EffectivenessWeakness.HEALTHY

    # 7. Test Scope Provenance in Diagnostics
    diag = MappingDiagnostics()
    diag.scope_provenance = {"files": ["a.py", "b.py"]}
    diag_dict = diag.to_dict()
    print(f"Scope Provenance in dict: {diag_dict.get('scope_provenance')}")
    assert diag_dict["scope_provenance"] == {"files": ["a.py", "b.py"]}

    # 8. Test analyze_function_effectiveness output
    data, anti_patterns = analyze_function_effectiveness(
        "test_func", healthy_assertions
    )
    print(f"analyze_function_effectiveness keys: {list(data.keys())}")
    assert "weakness_taxonomy" in data
    assert "confidence" in data
    assert data["weakness_taxonomy"] == "healthy"

    print("\nAll v2.0 schema and taxonomy tests PASSED!")


if __name__ == "__main__":
    test_v2_schema_and_taxonomy()
