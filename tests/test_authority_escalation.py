from lintgate.orchestration.authority import AuthorityEscalationEngine, AuthorityLevel


def test_authority_escalation_base_advisory():
    engine = AuthorityEscalationEngine()
    level = engine.calculate_authority(significance=0.2, recurrence_count=0, model_risk="none")
    assert level == AuthorityLevel.ADVISORY


def test_authority_escalation_recurrence():
    engine = AuthorityEscalationEngine()
    # Significance 0.5 * 0.5 = 0.25
    # Recurrence 5 -> rec_score 1.0 * 0.3 = 0.3
    # Risk moderate -> 0.4 * 0.2 = 0.08
    # Total = 0.63 -> Nudge (threshold 0.4)
    level = engine.calculate_authority(significance=0.5, recurrence_count=5, model_risk="moderate")
    assert level == AuthorityLevel.NUDGE


def test_authority_escalation_high_significance_blocking():
    engine = AuthorityEscalationEngine()
    # Significance 0.9 * 0.5 = 0.45
    # Risk structural -> 0.7 * 0.2 = 0.14
    # Total = 0.59 + recurrence(0) = 0.59 -> Nudge

    # Let's try higher significance + risk architectural
    # 0.9 * 0.5 = 0.45
    # Risk architectural -> 1.0 * 0.2 = 0.2
    # Recurrence 3 -> log(4, 6) = 0.77 -> 0.77 * 0.3 = 0.23
    # Total = 0.45 + 0.2 + 0.23 = 0.88 -> Blocking
    level = engine.calculate_authority(
        significance=0.9, recurrence_count=3, model_risk="architectural"
    )
    assert level == AuthorityLevel.BLOCKING


def test_authority_escalation_low_compliance_accelerant():
    engine = AuthorityEscalationEngine()
    # Significance 0.6, no recurrence, moderate risk
    # 0.6 * 0.5 = 0.3
    # 0 * 0.3 = 0
    # 0.4 * 0.2 = 0.08
    # Base total = 0.38 -> Advisory

    # With compliance 0.2: multiplier (1.5 - 0.2) = 1.3
    # 0.38 * 1.3 = 0.494 -> Nudge
    level = engine.calculate_authority(significance=0.6, compliance_rate=0.2)
    assert level == AuthorityLevel.NUDGE


def test_authority_comparison():
    assert AuthorityLevel.ADVISORY < AuthorityLevel.NUDGE
    assert AuthorityLevel.NUDGE < AuthorityLevel.BLOCKING
    assert AuthorityLevel.BLOCKING < AuthorityLevel.INTERVENTION
