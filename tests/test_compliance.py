from lintgate.orchestration.compliance import ComplianceManager


def test_compliance_recording():
    session_memory = {"behavior_compass": {"compliance_rate": 1.0}}
    mgr = ComplianceManager(session_memory)

    # Record some outcomes
    mgr.record_outcomes({"approach_cycling": "accepted", "failure_amnesia": "ignored"})

    stats = session_memory["compliance_stats"]
    assert stats["total_nudges"] == 2
    assert stats["accepted_count"] == 1
    assert stats["ignored_count"] == 1

    # Check that compliance_rate was updated in compass
    assert session_memory["behavior_compass"]["compliance_rate"] == 0.5


def test_compliance_rate_default():
    mgr = ComplianceManager({})
    assert mgr.get_compliance_rate() == 1.0


def test_compliance_multiple_runs():
    session_memory = {
        "compliance_stats": {
            "accepted_count": 2,
            "ignored_count": 0,
            "overridden_count": 0,
            "total_nudges": 2,
        }
    }
    mgr = ComplianceManager(session_memory)
    mgr.record_outcomes({"stale_model": "ignored"})

    assert mgr.get_compliance_rate() == 2 / 3
