from lintgate.channels.behavior_scoring import SignalCoordinator
from lintgate.orchestration.attribution import SignalSourceDecomposition
from lintgate.types import LintIssue


def test_decomposition_confidence():
    decomp = SignalSourceDecomposition(
        signal_name="approach_cycling",
        pattern_score=1.0,  # 1.0 * 0.4 = 0.4
        theory_score=0.5,  # 0.5 * 0.2 = 0.1
        outcome_score=0.8,  # 0.8 * 0.3 = 0.24
        coherence_score=0.0,  # 0.0 * 0.1 = 0
    )
    # Total = 0.74 / 0.5 = 1.48 -> cap at 1.0
    assert decomp.total_confidence == 1.0


def test_decomposition_message_attribution():
    from unittest.mock import MagicMock

    compass = MagicMock()
    compass.event_counter = 1
    compass.last_fired = {}
    compass.signal_fire_counts = {}

    coord = SignalCoordinator(compass, thresholds={"escalation_threshold": 3})

    decomp = SignalSourceDecomposition(
        signal_name="approach_cycling", pattern_score=0.8, outcome_score=0.9
    )

    finding = LintIssue(
        linter="behavior_channel",
        kind="approach_cycling",
        message="Stuck in loop",
        severity="warning",
    )

    coord.add_finding("approach_cycling", finding, is_hard=True, decomposition=decomp)

    assert "(Triggered by: pattern match, outcome evidence)" in finding.message
    assert finding.confidence > 0.5
    assert finding.evidence["attribution"]["pattern"] == 0.8


def test_decomposition_summary_empty():
    decomp = SignalSourceDecomposition(signal_name="test")
    assert decomp.to_summary() == "Triggered by mixed signals"
