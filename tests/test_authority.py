from lintgate.orchestration.authority import AuthorityEscalationEngine, AuthorityLevel


def test_authority_escalation_basic():
    engine = AuthorityEscalationEngine()

    # Low significance, first time -> ADVISORY
    level = engine.calculate_authority(
        significance=0.2, recurrence_count=1, compliance_rate=1.0
    )
    assert level == AuthorityLevel.ADVISORY

    # High significance, first time seen (recurrence=0) -> NUDGE
    level = engine.calculate_authority(
        significance=0.8, recurrence_count=0, compliance_rate=1.0
    )
    assert level == AuthorityLevel.NUDGE

    # High significance, seen again (recurrence=1) -> WARNING
    level = engine.calculate_authority(
        significance=0.8, recurrence_count=1, compliance_rate=1.0
    )
    assert level == AuthorityLevel.WARNING

    # Recurring signal (even moderate significance) -> WARNING
    level = engine.calculate_authority(
        significance=0.5, recurrence_count=4, compliance_rate=1.0
    )
    assert level == AuthorityLevel.WARNING

    # Low compliance + recurring -> INTERVENTION
    # score = (0.5*0.5) + (1.0*0.3) + (0.4*0.2) = 0.63
    # compliance boost: 0.63 * (1.5 - 0.4) = 0.63 * 1.1 = 0.693
    # With recurrence=5, rec_score=1.0.
    # To get INTERVENTION (0.8), we need higher significance or lower compliance
    level = engine.calculate_authority(
        significance=0.7, recurrence_count=5, compliance_rate=0.2
    )
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
