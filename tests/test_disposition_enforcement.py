"""Tests for the DispositionEnforcer engine."""

from __future__ import annotations

from lintgate.controlplane.types import (
    ControlPlaneConfig,
    DispositionEnforcementConfig,
    InquiryConfig,
    SupervisionEvent,
)
from lintgate.orchestration.disposition_enforcer import DispositionEnforcer


class MockSession:
    def __init__(self):
        self.behavior_compass = {}


def test_enforcement_disabled_by_default():
    config = ControlPlaneConfig()
    # Default is disabled
    enforcer = DispositionEnforcer(config)
    event = SupervisionEvent(tool_name="write_file")
    assert enforcer.evaluate(event) is None


def test_edit_without_lint_rule():
    config = ControlPlaneConfig(disposition_enforcement=DispositionEnforcementConfig(enabled=True))
    session = MockSession()
    enforcer = DispositionEnforcer(config, session)

    # 1. First edit: no nudge (we just started needing lint)
    event1 = SupervisionEvent(tool_name="write_file")
    assert enforcer.evaluate(event1) is None
    assert session.behavior_compass["enforcement"]["flags"]["needs_lint"] is True

    # 2. Sequential edit: no nudge (still editing)
    event2 = SupervisionEvent(tool_name="edit_file")
    assert enforcer.evaluate(event2) is None

    # 3. Use something else (Bash): should nudge
    event3 = SupervisionEvent(tool_name="bash")
    nudge = enforcer.evaluate(event3)
    assert nudge is not None
    assert "PROTIP" in nudge
    assert "validation" in nudge.lower()
    assert session.behavior_compass["enforcement"]["rules"]["edit_without_lint"]["fire_count"] == 1

    # 4. Use Bash again: should nudge with escalation
    event4 = SupervisionEvent(tool_name="bash")
    nudge2 = enforcer.evaluate(event4)
    assert nudge2 is not None
    assert "IMPORTANT REMINDER" in nudge2
    assert session.behavior_compass["enforcement"]["rules"]["edit_without_lint"]["fire_count"] == 2

    # 5. Use Bash again: should nudge with max escalation
    event5 = SupervisionEvent(tool_name="bash")
    nudge3 = enforcer.evaluate(event5)
    assert nudge3 is not None
    assert "URGENT DISPOSITION" in nudge3


def test_controlplane_cadence_rule():
    config = ControlPlaneConfig(
        disposition_enforcement=DispositionEnforcementConfig(
            enabled=True, cadence_health_check_events=2
        )
    )
    session = MockSession()
    enforcer = DispositionEnforcer(config, session)

    # Event 1
    enforcer.evaluate(SupervisionEvent(tool_name="read_file"))
    assert session.behavior_compass["enforcement"]["counters"]["events"] == 1

    # Event 2: Cadence reached, should nudge
    nudge = enforcer.evaluate(SupervisionEvent(tool_name="read_file"))
    assert nudge is not None
    assert "health check" in nudge.lower()

    # Event 3: Run controlplane_run: counter resets
    enforcer.evaluate(SupervisionEvent(tool_name="controlplane_run"))
    assert session.behavior_compass["enforcement"]["counters"]["events"] == 0


def test_bash_without_prediction_rule():
    config = ControlPlaneConfig(
        disposition_enforcement=DispositionEnforcementConfig(
            enabled=True, nudge_before_bash_without_prediction=True
        ),
        inquiry=InquiryConfig(prediction_tracking=False),
    )
    session = MockSession()
    enforcer = DispositionEnforcer(config, session)

    # Tool is Bash, prediction_tracking is False
    nudge = enforcer.evaluate(SupervisionEvent(tool_name="bash"))
    assert nudge is not None
    assert "prediction_tracking" in nudge


def test_max_nudges_limit():
    config = ControlPlaneConfig(
        disposition_enforcement=DispositionEnforcementConfig(
            enabled=True, max_nudges_per_disposition=1, cadence_health_check_events=1
        )
    )
    session = MockSession()
    enforcer = DispositionEnforcer(config, session)

    # First fire
    nudge1 = enforcer.evaluate(SupervisionEvent(tool_name="read_file"))
    assert nudge1 is not None

    # Second fire blocked by limit
    nudge2 = enforcer.evaluate(SupervisionEvent(tool_name="read_file"))
    assert nudge2 is None
    assert session.behavior_compass["enforcement"]["rules"]["cadence_check"]["fire_count"] == 1


def test_compliance_and_ignore_tracking():
    config = ControlPlaneConfig(disposition_enforcement=DispositionEnforcementConfig(enabled=True))
    session = MockSession()
    enforcer = DispositionEnforcer(config, session)

    # 1. Fire edit_without_lint
    enforcer._update_post_event_flags(SupervisionEvent(tool_name="write_file"))
    nudge = enforcer.evaluate(SupervisionEvent(tool_name="bash"))
    assert nudge is not None
    assert (
        session.behavior_compass["enforcement"]["flags"]["last_fired_rule"] == "edit_without_lint"
    )

    # 2. Achiever compliance
    enforcer.evaluate(SupervisionEvent(tool_name="lint_files"))
    assert (
        session.behavior_compass["enforcement"]["rules"]["edit_without_lint"]["compliance_count"]
        == 1
    )
    assert "last_fired_rule" not in session.behavior_compass["enforcement"]["flags"]

    # 3. Fire again and ignore
    enforcer._update_post_event_flags(SupervisionEvent(tool_name="write_file"))
    enforcer.evaluate(SupervisionEvent(tool_name="bash"))  # Fire
    enforcer.evaluate(SupervisionEvent(tool_name="read_file"))  # Ignore (not lint)

    assert (
        session.behavior_compass["enforcement"]["rules"]["edit_without_lint"]["ignore_count"] == 1
    )
