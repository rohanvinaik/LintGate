"""Tests for hook arbitration and cycle interventions."""

from __future__ import annotations

from unittest.mock import MagicMock

from lintgate.hook_arbitration import arbitrate_output


def test_arbitrate_output_injects_cycle_intervention():
    """Verify that a detected cycle triggers the intervention disposition."""
    cp_config = MagicMock()
    cp_config.hook_dispositions_enabled = True
    cp_config.hook_verbosity = "full"

    # Session data with a detected cycle
    session_data = {
        "event_counter": 10,
        "cycle_state": {
            "detected": True,
            "reason_codes": ["CYCLE_SAME_FILE"],
            "escalation_level": "advisory",
        },
        "_disposition_cooldowns": {},
    }

    report = {"systemMessage": "original report"}

    result = arbitrate_output(report, cp_config, session_data)

    dispositions = result.get("hookSpecificOutput", {}).get("dispositions", [])
    assert len(dispositions) > 0
    intervention = dispositions[0]
    assert "STUCK DETECTED" in intervention["disposition"]
    assert intervention["priority"] == 2

    # Verify cooldown was updated
    assert session_data["_disposition_cooldowns"]["cycle_intervention"] == 10


def test_cycle_intervention_escalation():
    """Verify that 'enforced' escalation level boosts priority to 3."""
    cp_config = MagicMock()
    cp_config.hook_dispositions_enabled = True
    cp_config.hook_verbosity = "full"

    session_data = {
        "event_counter": 10,
        "cycle_state": {
            "detected": True,
            "reason_codes": ["CYCLE_REPLACE_FAIL"],
            "escalation_level": "enforced",
        },
        "_disposition_cooldowns": {},
    }

    report = {"systemMessage": "original report"}
    result = arbitrate_output(report, cp_config, session_data)

    intervention = result["hookSpecificOutput"]["dispositions"][0]
    assert intervention["priority"] == 3
    assert "(!) SYMBOL/SYNTAX ERROR" in intervention["disposition"]


def test_cycle_intervention_cooldown():
    """Verify that interventions are rate-limited by cooldown."""
    cp_config = MagicMock()
    cp_config.hook_dispositions_enabled = True

    session_data = {
        "event_counter": 11,
        "cycle_state": {
            "detected": True,
            "reason_codes": ["CYCLE_SAME_FILE"],
            "escalation_level": "advisory",
        },
        "_disposition_cooldowns": {"cycle_intervention": 10},  # Fired 1 event ago
    }

    report = {"systemMessage": "original report"}
    result = arbitrate_output(report, cp_config, session_data)

    # Cooldown is 3 events, so it should NOT fire at event 11
    dispositions = result.get("hookSpecificOutput", {}).get("dispositions", [])
    assert len(dispositions) == 0
