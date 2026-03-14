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


def test_authority_escalation_basic():
    engine = AuthorityEscalationEngine()

    # Low significance, first time -> ADVISORY
    level = engine.calculate_authority(significance=0.2, recurrence_count=1, compliance_rate=1.0)
    assert level == AuthorityLevel.ADVISORY

    # High significance, first time seen (recurrence=0) -> NUDGE
    level = engine.calculate_authority(significance=0.8, recurrence_count=0, compliance_rate=1.0)
    assert level == AuthorityLevel.NUDGE

    # High significance, seen again (recurrence=1) -> WARNING
    level = engine.calculate_authority(significance=0.8, recurrence_count=1, compliance_rate=1.0)
    assert level == AuthorityLevel.WARNING

    # Recurring signal (even moderate significance) -> WARNING
    level = engine.calculate_authority(significance=0.5, recurrence_count=4, compliance_rate=1.0)
    assert level == AuthorityLevel.WARNING

    # Low compliance + recurring -> INTERVENTION
    # score = (0.5*0.5) + (1.0*0.3) + (0.4*0.2) = 0.63
    # compliance boost: 0.63 * (1.5 - 0.4) = 0.63 * 1.1 = 0.693
    # With recurrence=5, rec_score=1.0.
    # To get INTERVENTION (0.8), we need higher significance or lower compliance
    level = engine.calculate_authority(significance=0.7, recurrence_count=5, compliance_rate=0.2)
    # score = (0.7*0.5) + (1.0*0.3) + (0.4*0.2) = 0.35 + 0.3 + 0.08 = 0.73
    # compliance boost: 0.73 * (1.5 - 0.2) = 0.73 * 1.3 = 0.949
    assert level == AuthorityLevel.INTERVENTION


def test_authority_model_risk():
    engine = AuthorityEscalationEngine()

    # Structural risk escalates faster
    # score = (0.5*0.5) + (0*0.3) + (0.7*0.2) = 0.25 + 0.14 = 0.39
    # (Threshold for nudge is 0.4)
    # If recurrence=1, rec_score=0.386 -> score = 0.39 + 0.1158 = 0.5058 (NUDGE)
    level = engine.calculate_authority(
        significance=0.5, recurrence_count=1, model_risk="structural"
    )
    assert level == AuthorityLevel.NUDGE
