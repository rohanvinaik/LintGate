from lintgate.orchestration.authority import AuthorityEscalationEngine, AuthorityLevel


def test_authority_escalation_enum():
    assert AuthorityLevel.ADVISORY < AuthorityLevel.NUDGE
    assert AuthorityLevel.NUDGE < AuthorityLevel.WARNING
    assert AuthorityLevel.WARNING < AuthorityLevel.INTERVENTION


def test_calculate_authority_cap():
    engine = AuthorityEscalationEngine()
    level = engine.calculate_authority(significance=1.0, significance_cap=0.1)
    # With a cap of 0.1, score should be very low
    assert level == AuthorityLevel.ADVISORY


def test_calculate_authority_model_signal_risk():
    engine = AuthorityEscalationEngine()
    level = engine.calculate_authority(
        significance=0.0, recurrence_count=0, model_risk="none", model_signal_risk=1.0
    )
    # significance 0, rec 0, risk 1.0 (due to signal risk) -> score = 0.2
    assert level == AuthorityLevel.ADVISORY


def test_calculate_authority_escalation():
    engine = AuthorityEscalationEngine()
    level = engine.calculate_authority(
        significance=0.9,
        recurrence_count=5,
        model_risk="architectural",
        model_signal_risk=0.5,
        compliance_rate=0.2,
    )
    # high significance, high recurrence, high risk, low compliance -> should be INTERVENTION
    assert level == AuthorityLevel.INTERVENTION


def test_get_escalation_reason():
    engine = AuthorityEscalationEngine()
    assert "Critical recursive" in engine.get_escalation_reason(
        AuthorityLevel.INTERVENTION, 1.0, 5, 0.1
    )
    assert "Escalated due to high recurrence" in engine.get_escalation_reason(
        AuthorityLevel.WARNING, 0.5, 4, 1.0
    )
