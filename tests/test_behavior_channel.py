"""Tests for behavior channel — behavioral drift detection rules."""

from __future__ import annotations

import time

import pytest

from lintgate.channels.behavior_channel import (
    BehaviorChannel,
    _apply_prediction_modulation,
    _build_channel_result,
    _compute_nudge_outcomes,
    _IntentBiasScorer,
    _load_execute_config,
    _SignalCoordinator,
)
from lintgate.controlplane.behavior_compass import (
    DEFAULT_THRESHOLDS,
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    error_memory_key,
    new_compass,
)
from lintgate.controlplane.types import (
    ChannelConfig,
    ControlPlaneConfig,
    SupervisionEvent,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_event(compass: BehaviorCompass | None = None, **kwargs) -> SupervisionEvent:
    """Build a SupervisionEvent with compass data injected."""
    raw_input = kwargs.pop("raw_input", {})
    if compass is not None:
        raw_input["behavior_compass"] = compass.to_dict()
    return SupervisionEvent(
        surface="hook",
        project_root="/tmp/test",
        tool_name="Bash",
        raw_input=raw_input,
        **kwargs,
    )


def _default_config() -> ControlPlaneConfig:
    return ControlPlaneConfig(enabled=True)


# ── BehaviorChannel protocol ────────────────────────────────────────


class TestChannelProtocol:
    def test_name(self):
        ch = BehaviorChannel()
        assert ch.name == "behavior"

    def test_not_blocking(self):
        ch = BehaviorChannel()
        assert ch.blocking_capable is False

    def test_should_run_with_compass(self):
        ch = BehaviorChannel()
        event = _make_event(new_compass())
        assert ch.should_run(event, _default_config()) is True

    def test_should_run_mcp_surface(self):
        ch = BehaviorChannel()
        event = SupervisionEvent(surface="mcp")
        assert ch.should_run(event, _default_config()) is True

    def test_should_not_run_without_compass(self):
        ch = BehaviorChannel()
        event = SupervisionEvent(surface="hook", raw_input={})
        assert ch.should_run(event, _default_config()) is False

    def test_empty_compass_passes(self):
        ch = BehaviorChannel()
        event = _make_event(new_compass())
        result = ch.execute(event, _default_config())
        assert result.status == "pass"
        assert result.severity == "none"
        assert len(result.findings) == 0

    def test_threshold_overrides_loaded_from_channel_settings(self):
        ch = BehaviorChannel()
        now = time.time()
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(3)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:2", "exit": 1, "err": ""}]

        cfg = ControlPlaneConfig(
            enabled=True,
            channels={
                "behavior": ChannelConfig(
                    enabled=True,
                    settings={"thresholds": {"approach_cycling_count": 4}},
                ),
            },
        )

        result = ch.execute(_make_event(compass), cfg)
        cycling = [f for f in result.findings if f.kind == "approach_cycling"]
        assert len(cycling) == 0


# ── approach_cycling ─────────────────────────────────────────────────


class TestApproachCycling:
    def test_fires_on_3_failed_approaches(self):
        compass = new_compass()
        now = time.time()

        # Create 3 failed approaches within 30 minutes
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:attempt{i}",
                outcome="failed",
                started_at=now - 1000 + (i * 100),
                last_event=now - 500 + (i * 100),
                event_count=2,
            )
            for i in range(3)
        ]
        # Need action_history for timestamp reference
        compass.action_history = [
            {"tool": "Bash", "ts": now, "sig": "cmd:attempt2", "exit": 1, "err": "fail"}
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        assert result.status == "fail"
        cycling_findings = [f for f in result.findings if f.kind == "approach_cycling"]
        assert len(cycling_findings) == 1
        assert cycling_findings[0].severity == "warning"

    def test_does_not_fire_on_2_failed(self):
        compass = new_compass()
        now = time.time()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(2)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:1", "exit": 1, "err": ""}]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        cycling_findings = [f for f in result.findings if f.kind == "approach_cycling"]
        assert len(cycling_findings) == 0

    def test_triggers_precheck_next_action(self):
        compass = new_compass()
        now = time.time()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(4)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:3", "exit": 1, "err": ""}]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        next_actions = result.metrics.get("next_actions", [])
        assert any(a["tool"] == "constraint_check" for a in next_actions)


# ── failure_amnesia ──────────────────────────────────────────────────


class TestFailureAmnesia:
    def test_fires_on_repeated_error(self):
        compass = new_compass()
        now = time.time()

        compass.action_history = [
            {
                "tool": "Bash",
                "ts": now - 600,
                "sig": "idevicerestore:restore",
                "exit": 1,
                "err": "Unable to send iBSS",
            },
            {"tool": "Read", "ts": now - 400, "sig": "", "exit": None, "err": ""},
            {
                "tool": "Bash",
                "ts": now - 200,
                "sig": "idevicerestore:restore",
                "exit": 1,
                "err": "Unable to send iBSS",
            },
            {
                "tool": "Bash",
                "ts": now,
                "sig": "idevicerestore:restore",
                "exit": 1,
                "err": "Unable to send iBSS",
            },
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        amnesia_findings = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia_findings) == 1
        assert amnesia_findings[0].severity == "warning"
        assert "Unable to send iBSS" in amnesia_findings[0].message

    def test_no_fire_on_unique_errors(self):
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {
                "tool": "Bash",
                "ts": now - 300,
                "sig": "cmd:a",
                "exit": 1,
                "err": "error A",
            },
            {"tool": "Bash", "ts": now, "sig": "cmd:b", "exit": 1, "err": "error B"},
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        amnesia = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia) == 0


# ── brute_force_escalation ───────────────────────────────────────────


class TestBruteForceEscalation:
    def test_fires_when_approaches_exceed_constraints(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(approach_sig=f"cmd:{i}", outcome="failed", event_count=1)
            for i in range(5)
        ]
        # 5 approaches, 0 verified constraints
        compass.coverage = CoverageMetrics(
            approaches_attempted=5,
            constraints_verified=0,
        )

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        bf_findings = [f for f in result.findings if f.kind == "brute_force_escalation"]
        assert len(bf_findings) == 1
        assert bf_findings[0].severity == "warning"

    def test_no_fire_when_balanced(self):
        compass = new_compass()
        compass.coverage = CoverageMetrics(
            approaches_attempted=3,
            constraints_verified=3,
        )

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        bf = [f for f in result.findings if f.kind == "brute_force_escalation"]
        assert len(bf) == 0


# ── premature_action ─────────────────────────────────────────────────


class TestPrematureAction:
    def test_fires_on_high_ratio_and_failures(self):
        compass = new_compass()
        now = time.time()

        # 8 Bash commands, 0 reads, 6 failures
        compass.action_history = [
            {
                "tool": "Bash",
                "ts": now - i,
                "sig": f"cmd:{i}",
                "exit": 1 if i < 6 else 0,
                "err": "fail" if i < 6 else "",
            }
            for i in range(8)
        ]
        compass.coverage = CoverageMetrics(bash_count_recent=8, read_count_recent=0)

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        pa_findings = [f for f in result.findings if f.kind == "premature_action"]
        assert len(pa_findings) == 1
        assert pa_findings[0].severity in (
            "informational",
            "warning",
        )  # Authority engine may escalate

    def test_no_fire_with_reads(self):
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {"tool": "Bash", "ts": now - 4, "sig": "cmd:a", "exit": 1, "err": "fail"},
            {"tool": "Read", "ts": now - 3, "sig": "", "exit": None, "err": ""},
            {"tool": "Read", "ts": now - 2, "sig": "", "exit": None, "err": ""},
            {"tool": "Bash", "ts": now - 1, "sig": "cmd:b", "exit": 0, "err": ""},
        ]
        compass.coverage = CoverageMetrics(bash_count_recent=2, read_count_recent=2)

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        pa = [f for f in result.findings if f.kind == "premature_action"]
        assert len(pa) == 0


# ── serial_discovery ─────────────────────────────────────────────────


class TestSerialDiscovery:
    def test_fires_with_3_failure_sourced_0_precheck(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id=f"h{i}",
                claim=f"fail {i}",
                confidence=0.4,
                source="command_failure",
                status="active",
            )
            for i in range(3)
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd_findings = [f for f in result.findings if f.kind == "serial_discovery"]
        # v2: two-stage serial discovery — stage 1 (early nudge) + stage 2 (3+ failure-sourced)
        assert len(sd_findings) == 2
        stages = {f.evidence.get("stage") for f in sd_findings}
        assert stages == {1, 2}
        assert all(
            f.severity in ("informational", "warning") for f in sd_findings
        )  # Authority engine may escalate

    def test_no_fire_with_precheck_declared(self):
        compass = new_compass()
        # v2: set constraint_check_count_session so stage 1 is suppressed
        compass.constraint_check_count_session = 1
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h0",
                claim="fail",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
            BehaviorHypothesis(
                id="h1",
                claim="fail",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
            BehaviorHypothesis(
                id="h2",
                claim="fail",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
            BehaviorHypothesis(
                id="h3",
                claim="declared",
                confidence=0.5,
                source="precheck_declared",
                status="active",
            ),
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        sd = [f for f in result.findings if f.kind == "serial_discovery"]
        assert len(sd) == 0  # Has a precheck-declared hypothesis + precheck_count > 0


# ── tool_repetition ──────────────────────────────────────────────────


class TestToolRepetition:
    def test_fires_on_4_same_sig(self):
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {
                "tool": "Bash",
                "ts": now - i * 60,
                "sig": "irecovery:query",
                "exit": 0,
                "err": "",
            }
            for i in range(5)
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        rep_findings = [f for f in result.findings if f.kind == "tool_repetition"]
        assert len(rep_findings) == 1
        assert "irecovery:query" in rep_findings[0].message


# ── consecutive_failures ─────────────────────────────────────────────


class TestConsecutiveFailures:
    def test_triggers_precheck_on_3_consecutive(self):
        compass = new_compass()
        now = time.time()
        # Use unique error sigs so failure_amnesia doesn't fire (which would
        # take priority in the nudge dedup), letting consecutive_failures
        # be the nudge winner.
        compass.action_history = [
            {"tool": "Bash", "ts": now - 3, "sig": "cmd:a", "exit": 1, "err": "err_a"},
            {"tool": "Bash", "ts": now - 2, "sig": "cmd:b", "exit": 1, "err": "err_b"},
            {"tool": "Bash", "ts": now - 1, "sig": "cmd:c", "exit": 1, "err": "err_c"},
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        next_actions = result.metrics.get("next_actions", [])
        assert any("consecutive" in a.get("reason", "") for a in next_actions)


# ── Severity classification ──────────────────────────────────────────


class TestSeverityClassification:
    def test_hard_signal_produces_warning(self):
        """approach_cycling is a hard signal → severity should be 'warning'."""
        compass = new_compass()
        now = time.time()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(3)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:2", "exit": 1, "err": ""}]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        assert result.severity == "warning"

    def test_soft_signal_only_produces_informational(self):
        """premature_action alone → severity should be 'informational'."""
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {"tool": "Bash", "ts": now - i, "sig": f"cmd:{i}", "exit": 1, "err": "fail"}
            for i in range(8)
        ]
        compass.coverage = CoverageMetrics(bash_count_recent=8, read_count_recent=0)

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        # Only soft signals fired — should be informational
        hard = [f for f in result.findings if f.severity == "warning"]
        if not hard:
            assert result.severity == "informational"


# ── Coherence integration ────────────────────────────────────────────


class TestCoherenceIntegration:
    def test_metrics_include_compass_summary(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="test", confidence=0.5, status="active"),
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        summary = result.metrics.get("compass_summary", {})
        assert summary.get("hypotheses_active") == 1


# ── v2: IntentBiasScorer ────────────────────────────────────────────


class TestIntentBiasScorer:
    def test_verification_debt_bias_fires_on_execute_streak(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 10
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.verification_debt_bias()
        assert bias > 0
        assert len(terms) == 1
        assert "execute_streak" in terms[0]

    def test_verification_debt_no_bias_with_verify(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 7 + ["verify"] + ["execute"] * 2
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.verification_debt_bias()
        # Streak from end is only 2 (< 8), so no bias
        assert bias == 0.0

    def test_failure_amnesia_bias_fires_on_repeated_error(self):
        compass = new_compass()
        compass.action_history = [
            {"tool": "Bash", "ts": 100, "sig": "cmd:a", "exit": 1, "err": "error X"},
            {"tool": "Bash", "ts": 200, "sig": "cmd:b", "exit": 0, "err": ""},
            {"tool": "Bash", "ts": 300, "sig": "cmd:a", "exit": 1, "err": "error X"},
        ]
        compass.intent_history = ["execute", "execute", "execute"]
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.failure_amnesia_bias()
        assert bias > 0
        assert "repeated_error" in terms[0]

    def test_serial_discovery_bias_fires_with_no_precheck(self):
        compass = new_compass()
        compass.constraint_check_count_session = 0
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="test",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
        ]
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.serial_discovery_bias()
        assert bias > 0
        assert "precheck=0" in terms[0]

    def test_serial_discovery_no_bias_with_precheck(self):
        compass = new_compass()
        compass.constraint_check_count_session = 1
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="test",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
        ]
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.serial_discovery_bias()
        assert bias == 0.0

    def test_stale_model_bias_fires_on_approach_streak(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig="cmd:a",
                started_at=100,
                last_event=100,
                hyp_version_at_start=0,
            ),
            ApproachAttempt(
                approach_sig="cmd:b",
                started_at=200,
                last_event=200,
                hyp_version_at_start=0,
            ),
        ]
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.stale_model_bias()
        assert bias > 0
        assert "approach_streak=2" in terms[0]

    def test_bias_capped_at_025(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 20
        scorer = _IntentBiasScorer(compass, {"verification_debt_bias": 0.50})
        bias, terms = scorer.verification_debt_bias()
        assert bias <= 0.25

    def test_evidence_trace_includes_counts(self):
        compass = new_compass()
        compass.intent_history = ["inspect", "execute", "execute", "modify"]
        scorer = _IntentBiasScorer(compass, {})
        trace = scorer.build_evidence_trace()
        assert "window" in trace
        assert "intent_counts" in trace
        assert trace["intent_counts"]["execute"] == 2


# ── v2: verification_debt ───────────────────────────────────────────


class TestVerificationDebt:
    def test_fires_at_streak_8(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 8
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        vd = [f for f in result.findings if f.kind == "verification_debt"]
        assert len(vd) == 1
        assert vd[0].severity in (
            "informational",
            "warning",
        )  # Authority engine may escalate
        assert "8" in vd[0].message

    def test_does_not_fire_at_streak_7(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 7
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        vd = [f for f in result.findings if f.kind == "verification_debt"]
        assert len(vd) == 0

    def test_evidence_trace_present(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 10
        compass.event_counter = 15

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        vd = [f for f in result.findings if f.kind == "verification_debt"]
        assert len(vd) == 1
        assert "intent_counts" in vd[0].evidence
        assert "score_delta" in vd[0].evidence
        assert "execute_streak" in vd[0].evidence

    def test_triggers_precheck_nudge(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 10
        compass.event_counter = 20

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        next_actions = result.metrics.get("next_actions", [])
        assert any("verification_debt" in a.get("reason", "") for a in next_actions)


# ── v2: stale_model ─────────────────────────────────────────────────


class TestStaleModel:
    def test_fires_on_2_approaches_same_version(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig="cmd:a",
                started_at=100,
                last_event=100,
                hyp_version_at_start=0,
            ),
            ApproachAttempt(
                approach_sig="cmd:b",
                started_at=200,
                last_event=200,
                hyp_version_at_start=0,
            ),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sm = [f for f in result.findings if f.kind == "stale_model"]
        assert len(sm) == 1
        assert sm[0].severity in (
            "informational",
            "warning",
        )  # Authority engine may escalate

    def test_no_fire_when_version_changes(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig="cmd:a",
                started_at=100,
                last_event=100,
                hyp_version_at_start=0,
            ),
            ApproachAttempt(
                approach_sig="cmd:b",
                started_at=200,
                last_event=200,
                hyp_version_at_start=1,
            ),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sm = [f for f in result.findings if f.kind == "stale_model"]
        assert len(sm) == 0

    def test_evidence_includes_approach_streak(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                started_at=100 + i * 10,
                last_event=100 + i * 10,
                hyp_version_at_start=0,
            )
            for i in range(3)
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sm = [f for f in result.findings if f.kind == "stale_model"]
        assert len(sm) == 1
        assert sm[0].evidence.get("approach_streak_at_same_version") == 3


# ── v2: serial_discovery two-stage ──────────────────────────────────


class TestSerialDiscoveryTwoStage:
    def test_stage1_fires_on_first_failure_hyp(self):
        compass = new_compass()
        compass.constraint_check_count_session = 0
        compass.early_nudge_emitted = False
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="test",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd = [f for f in result.findings if f.kind == "serial_discovery"]
        assert len(sd) >= 1
        # Should have stage 1 evidence
        stage1 = [f for f in sd if f.evidence.get("stage") == 1]
        assert len(stage1) == 1

    def test_stage1_suppressed_if_precheck_used(self):
        compass = new_compass()
        compass.constraint_check_count_session = 1
        compass.early_nudge_emitted = False
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="test",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd = [f for f in result.findings if f.kind == "serial_discovery"]
        stage1 = [f for f in sd if f.evidence.get("stage") == 1]
        assert len(stage1) == 0

    def test_stage1_fires_only_once(self):
        compass = new_compass()
        compass.constraint_check_count_session = 0
        compass.early_nudge_emitted = True  # Already emitted
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="test",
                confidence=0.4,
                source="command_failure",
                status="active",
            ),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd = [f for f in result.findings if f.kind == "serial_discovery"]
        stage1 = [f for f in sd if f.evidence.get("stage") == 1]
        assert len(stage1) == 0

    def test_stage2_fires_at_3_failure_hyps(self):
        compass = new_compass()
        compass.constraint_check_count_session = 0
        compass.early_nudge_emitted = True  # Stage 1 already fired
        compass.hypotheses = [
            BehaviorHypothesis(
                id=f"h{i}",
                claim=f"fail {i}",
                confidence=0.4,
                source="command_failure",
                status="active",
            )
            for i in range(3)
        ]
        compass.event_counter = 20  # Past cooldown from stage 1

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd = [f for f in result.findings if f.kind == "serial_discovery"]
        stage2 = [f for f in sd if f.evidence.get("stage") == 2]
        assert len(stage2) == 1


# ── v2: failure_amnesia dual-source ─────────────────────────────────


class TestFailureAmnesiasDualSource:
    def test_detects_from_action_history(self):
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {
                "tool": "Bash",
                "ts": now - 300,
                "sig": "cmd:a",
                "exit": 1,
                "err": "error X",
            },
            {"tool": "Bash", "ts": now, "sig": "cmd:a", "exit": 1, "err": "error X"},
        ]
        compass.intent_history = ["execute", "execute"]
        compass.event_counter = 5

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        amnesia = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia) == 1
        assert "action history" in amnesia[0].message or "seen" in amnesia[0].message

    def test_detects_from_error_memory_outside_window(self):
        compass = new_compass()
        now = time.time()
        latest_error = "Unable to send iBSS"
        compass.action_history = [
            {"tool": "Bash", "ts": now, "sig": "cmd:a", "exit": 1, "err": latest_error},
        ]
        compass.intent_history = ["execute"]
        key = error_memory_key(latest_error)
        compass.error_memory[key] = {
            "count": 2,
            "first_seen": now - 7200,
            "last_seen": now,
            "last_sig": latest_error,
        }
        compass.event_counter = 5

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        amnesia = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia) == 1
        assert "session memory" in amnesia[0].message

    def test_detects_from_hypothesis_evidence(self):
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {"tool": "Bash", "ts": now, "sig": "cmd:a", "exit": 1, "err": "error X"},
        ]
        compass.intent_history = ["execute"]
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="something fails",
                confidence=0.4,
                source="command_failure",
                status="active",
                evidence_for=["exit!=0 with: error X"],
            ),
        ]
        compass.event_counter = 5

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        amnesia = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia) == 1
        assert "hypothesis" in amnesia[0].message.lower()

    def test_evidence_includes_bias_terms(self):
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {
                "tool": "Bash",
                "ts": now - 300,
                "sig": "cmd:a",
                "exit": 1,
                "err": "error X",
            },
            {"tool": "Bash", "ts": now, "sig": "cmd:a", "exit": 1, "err": "error X"},
        ]
        compass.intent_history = ["execute", "execute"]
        compass.event_counter = 5

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        amnesia = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia) == 1
        assert "matched_bias_terms" in amnesia[0].evidence

    def test_hypothesis_source_ignores_generic_substring_matches(self):
        compass = new_compass()
        now = time.time()
        compass.action_history = [
            {"tool": "Bash", "ts": now, "sig": "cmd:a", "exit": 1, "err": "failed"},
        ]
        compass.intent_history = ["execute"]
        compass.constraint_check_count_session = 1  # suppress serial_discovery stage-1 noise
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="something fails",
                confidence=0.7,
                source="command_failure",
                status="active",
                evidence_for=["exit!=0 with: compile failed due missing symbol"],
            ),
        ]
        compass.event_counter = 5

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        amnesia = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia) == 0

    def test_hypothesis_source_matches_truncated_error_signature(self):
        compass = new_compass()
        now = time.time()
        latest_error = (
            "unable to load profile from /tmp/build/output/profile.json due to missing "
            "section header in generated configuration payload"
        )
        truncated = latest_error[:80]
        compass.action_history = [
            {"tool": "Bash", "ts": now, "sig": "cmd:a", "exit": 1, "err": latest_error},
        ]
        compass.intent_history = ["execute"]
        compass.constraint_check_count_session = 1  # suppress serial_discovery stage-1 noise
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="profile load failure",
                confidence=0.7,
                source="command_failure",
                status="active",
                evidence_for=[f"exit!=0 with: {truncated}"],
            ),
        ]
        compass.event_counter = 5

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())
        amnesia = [f for f in result.findings if f.kind == "failure_amnesia"]
        assert len(amnesia) == 1
        assert "hypothesis" in amnesia[0].message.lower()


# ── v2: signal cooldown ─────────────────────────────────────────────


class TestSignalCooldown:
    def test_signal_suppressed_within_cooldown(self):
        compass = new_compass()
        compass.event_counter = 5
        compass.last_fired = {"approach_cycling": 3}  # Fired 2 events ago, cooldown is 10

        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        assert coord.can_fire("approach_cycling") is False

    def test_signal_fires_after_cooldown(self):
        compass = new_compass()
        compass.event_counter = 20
        compass.last_fired = {"approach_cycling": 5}  # 15 events ago, cooldown is 10

        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        assert coord.can_fire("approach_cycling") is True

    def test_first_fire_always_allowed(self):
        compass = new_compass()
        compass.event_counter = 1

        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        assert coord.can_fire("approach_cycling") is True

    def test_configurable_cooldown(self):
        compass = new_compass()
        compass.event_counter = 8
        compass.last_fired = {"approach_cycling": 3}

        thresholds = {**DEFAULT_THRESHOLDS, "signal_cooldown": 4}
        coord = _SignalCoordinator(compass, thresholds)
        assert coord.can_fire("approach_cycling") is True  # 8-3=5 >= 4


# ── v2: precheck nudge dedup ────────────────────────────────────────


class TestPrecheckNudgeDedup:
    def test_only_one_nudge_per_execution(self):
        compass = new_compass()
        compass.event_counter = 100

        ch = BehaviorChannel()
        now = time.time()
        # Setup to trigger both approach_cycling and consecutive_failures
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(3)
        ]
        compass.action_history = [
            {"tool": "Bash", "ts": now - i, "sig": f"cmd:{i}", "exit": 1, "err": "fail"}
            for i in range(5)
        ]

        result = ch.execute(_make_event(compass), _default_config())
        next_actions = result.metrics.get("next_actions", [])
        # At most 1 precheck nudge (dedup'd by priority)
        precheck_nudges = [a for a in next_actions if a.get("tool") == "constraint_check"]
        assert len(precheck_nudges) <= 1

    def test_higher_priority_signal_wins(self):
        compass = new_compass()
        compass.event_counter = 100

        ch = BehaviorChannel()
        now = time.time()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(3)
        ]
        compass.action_history = [
            {"tool": "Bash", "ts": now - i, "sig": f"cmd:{i}", "exit": 1, "err": "fail"}
            for i in range(5)
        ]

        result = ch.execute(_make_event(compass), _default_config())
        next_actions = result.metrics.get("next_actions", [])
        precheck_nudges = [a for a in next_actions if a.get("tool") == "constraint_check"]
        if precheck_nudges:
            # approach_cycling (priority 1) should win over consecutive_failures (priority 4)
            assert "approach_cycling" in precheck_nudges[0].get("reason", "")


# ── v2: escalation ──────────────────────────────────────────────────


class TestEscalation:
    def test_soft_signal_promoted_to_warning_after_threshold(self):
        compass = new_compass()
        compass.event_counter = 100
        compass.signal_fire_counts = {"verification_debt": 2}  # Next fire will be 3rd
        compass.intent_history = ["execute"] * 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        vd = [f for f in result.findings if f.kind == "verification_debt"]
        if vd:
            # After 3+ firings, soft signals get promoted to warning
            assert vd[0].severity == "warning"

    def test_hard_signal_gets_persistent_tag(self):
        compass = new_compass()
        compass.event_counter = 100
        compass.signal_fire_counts = {"approach_cycling": 2}  # Next fire will be 3rd
        now = time.time()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(3)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:2", "exit": 1, "err": ""}]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        cycling = [f for f in result.findings if f.kind == "approach_cycling"]
        if cycling:
            # Authority engine may escalate hard signals to blocking/intervention
            # instead of adding [persistent] tag (which only applies at WARNING level)
            auth = cycling[0].evidence.get("authority", {})
            if auth.get("level") == "intervention":
                assert cycling[0].severity == "blocking"
            else:
                assert "[persistent]" in cycling[0].message


# ── v2: evidence trace ──────────────────────────────────────────────


class TestEvidenceTrace:
    def test_all_findings_have_intent_counts(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 10
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        for f in result.findings:
            assert "intent_counts" in f.evidence, f"Finding {f.kind} missing intent_counts"

    def test_bias_findings_have_score_delta(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 10
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        vd = [f for f in result.findings if f.kind == "verification_debt"]
        if vd:
            assert "score_delta" in vd[0].evidence
            assert "matched_bias_terms" in vd[0].evidence

    def test_compass_delta_in_metrics(self):
        compass = new_compass()
        compass.intent_history = ["execute"] * 10
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        delta = result.metrics.get("behavior_compass_delta", {})
        assert "last_fired" in delta
        assert "signal_fire_counts" in delta
        assert "early_nudge_emitted" in delta

    def test_intent_summary_in_metrics(self):
        compass = new_compass()
        compass.intent_history = ["inspect", "execute", "modify"]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        summary = result.metrics.get("intent_summary", {})
        assert "window" in summary
        assert "intent_counts" in summary

    def test_global_profile_delta_in_metrics(self):
        compass = new_compass()
        compass.signal_fire_counts = {"verification_debt": 2}
        compass.intent_history = ["execute"] * 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        gp_delta = result.metrics.get("global_profile_delta", {})
        assert "signal_fire_counts" in gp_delta
        assert "intent_summary" in gp_delta
        # Per-run delta should only include firings in this execution.
        assert gp_delta["signal_fire_counts"].get("verification_debt") == 1

    def test_compass_delta_includes_nudge_fields(self):
        compass = new_compass()
        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        delta = result.metrics.get("behavior_compass_delta", {})
        assert "pending_nudge_signals" in delta
        assert "pending_nudge_constraint_check_count" in delta
        assert "nudge_outcomes" in delta


# ── Global Priors Integration ──────────────────────────────────────────


class TestGlobalPriorsIntegration:
    """Test that global behavior priors are correctly integrated."""

    def test_scorer_no_change_without_priors(self):
        """Without global_priors, scorer behaves identically to v2."""
        compass = new_compass()
        compass.intent_history = ["execute"] * 10

        scorer = _IntentBiasScorer(compass, {})
        assert scorer._alpha == 0.0
        assert scorer._global_adjustments == {}

    def test_scorer_with_global_priors(self):
        """With global priors enabled, alpha is computed from event_counter."""
        compass = new_compass()
        compass.event_counter = 0
        compass.intent_history = ["execute"] * 10

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"verification_debt": 0.08},
        }
        scorer = _IntentBiasScorer(compass, {}, global_priors=priors)
        assert scorer._alpha == pytest.approx(0.6)
        assert scorer._global_adjustments == {"verification_debt": 0.08}

    def test_alpha_zero_means_no_global_effect(self):
        """At decay horizon, alpha=0 so global adjustments have no effect."""
        compass = new_compass()
        compass.event_counter = 50  # At horizon
        compass.intent_history = ["execute"] * 10

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"verification_debt": 0.08},
        }
        scorer = _IntentBiasScorer(compass, {}, global_priors=priors)
        assert scorer._alpha == pytest.approx(0.0)

        # _effective_bias_weight should return project-only value
        effective = scorer._effective_bias_weight(
            "verification_debt", "verification_debt_bias", 0.20
        )
        assert effective == pytest.approx(0.20)

    def test_alpha_decay_over_events(self):
        """Alpha decays as event_counter grows."""
        compass = new_compass()
        compass.event_counter = 25  # Half of horizon
        compass.intent_history = []

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {},
        }
        scorer = _IntentBiasScorer(compass, {}, global_priors=priors)
        assert scorer._alpha == pytest.approx(0.3)

    def test_evidence_trace_includes_global_alpha(self):
        """When alpha > 0, evidence trace includes global info."""
        compass = new_compass()
        compass.event_counter = 0
        compass.intent_history = ["execute", "modify"]

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"verification_debt": 0.05},
        }
        scorer = _IntentBiasScorer(compass, {}, global_priors=priors)
        trace = scorer.build_evidence_trace()
        assert "global_alpha" in trace
        assert trace["global_alpha"] == pytest.approx(0.6)
        assert "global_adjustments_applied" in trace
        assert trace["global_adjustments_applied"]["verification_debt"] == pytest.approx(0.05)

    def test_evidence_trace_no_global_when_alpha_zero(self):
        """When alpha=0, no global fields in evidence trace."""
        compass = new_compass()
        compass.event_counter = 100  # Way past horizon
        compass.intent_history = []

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"verification_debt": 0.05},
        }
        scorer = _IntentBiasScorer(compass, {}, global_priors=priors)
        trace = scorer.build_evidence_trace()
        assert "global_alpha" not in trace


class TestEffectiveBiasWeight:
    """Test the bias weight merge formula."""

    def test_merge_formula_correct(self):
        """effective = project + alpha * global_adjustment."""
        compass = new_compass()
        compass.event_counter = 0
        compass.intent_history = []

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"verification_debt": 0.08},
        }
        scorer = _IntentBiasScorer(compass, {"verification_debt_bias": 0.20}, global_priors=priors)
        # effective = 0.20 + 0.6 * 0.08 = 0.20 + 0.048 = 0.248
        effective = scorer._effective_bias_weight(
            "verification_debt", "verification_debt_bias", 0.20
        )
        assert effective == pytest.approx(0.248)

    def test_clamp_to_bias_cap(self):
        """Result is clamped to [0, BIAS_CAP]."""
        compass = new_compass()
        compass.event_counter = 0
        compass.intent_history = []

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"verification_debt": 0.10},
        }
        # project=0.25 + 0.6*0.10 = 0.31, but BIAS_CAP=0.25
        scorer = _IntentBiasScorer(compass, {"verification_debt_bias": 0.25}, global_priors=priors)
        effective = scorer._effective_bias_weight(
            "verification_debt", "verification_debt_bias", 0.25
        )
        assert effective == pytest.approx(0.25)  # Clamped

    def test_negative_global_adjustment_reduces_weight(self):
        """Negative adjustment reduces the effective weight."""
        compass = new_compass()
        compass.event_counter = 0
        compass.intent_history = []

        priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"failure_amnesia": -0.10},
        }
        scorer = _IntentBiasScorer(compass, {}, global_priors=priors)
        # effective = 0.15 + 0.6 * (-0.10) = 0.15 - 0.06 = 0.09
        effective = scorer._effective_bias_weight("failure_amnesia", "failure_amnesia_bias", 0.15)
        assert effective == pytest.approx(0.09)

    def test_clamp_to_zero_floor(self):
        """Effective weight never goes below 0."""
        compass = new_compass()
        compass.event_counter = 0
        compass.intent_history = []

        priors = {
            "enabled": True,
            "alpha": 1.0,  # Max alpha
            "decay_horizon": 100,
            "computed_bias_adjustments": {"x": -0.10},
        }
        scorer = _IntentBiasScorer(compass, {"x_bias": 0.05}, global_priors=priors)
        # effective = 0.05 + 1.0 * (-0.10) = -0.05, clamped to 0
        effective = scorer._effective_bias_weight("x", "x_bias", 0.05)
        assert effective == pytest.approx(0.0)


class TestNudgeOutcomeTracking:
    """Test that nudge outcomes are tracked in compass delta."""

    def test_nudge_signals_recorded_when_nudge_produced(self):
        """When a signal produces a precheck nudge, it's recorded."""
        now = time.time()
        compass = new_compass()
        compass.event_counter = 10
        # Set up 3 consecutive failures for consecutive_failures to fire
        compass.action_history = [
            {"tool": "Bash", "ts": now - 3, "sig": "cmd:a", "exit": 1, "err": "err_a"},
            {"tool": "Bash", "ts": now - 2, "sig": "cmd:b", "exit": 1, "err": "err_b"},
            {"tool": "Bash", "ts": now - 1, "sig": "cmd:c", "exit": 1, "err": "err_c"},
        ]
        compass.intent_history = ["execute"] * 3

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        delta = result.metrics.get("behavior_compass_delta", {})
        # pending_nudge_signals should be non-empty if any signal produced a nudge
        assert isinstance(delta.get("pending_nudge_signals"), list)

    def test_nudge_outcomes_accepted_when_precheck_used(self):
        """Previously pending nudges are marked 'accepted' when precheck_count > 0."""
        compass = new_compass()
        compass.pending_nudge_signals = ["verification_debt"]
        compass.constraint_check_count_session = 1  # Precheck was used

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        gp_delta = result.metrics.get("global_profile_delta", {})
        nudge_outcomes = gp_delta.get("nudge_outcomes", {})
        assert nudge_outcomes.get("verification_debt") == "accepted"

    def test_nudge_outcomes_ignored_when_no_precheck(self):
        """Previously pending nudges are marked 'ignored' when precheck_count == 0."""
        compass = new_compass()
        compass.pending_nudge_signals = ["approach_cycling"]
        compass.constraint_check_count_session = 0  # No precheck

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        gp_delta = result.metrics.get("global_profile_delta", {})
        nudge_outcomes = gp_delta.get("nudge_outcomes", {})
        assert nudge_outcomes.get("approach_cycling") == "ignored"

    def test_nudge_outcomes_require_new_precheck_since_nudge(self):
        """Precheck must happen after nudge issuance to count as accepted."""
        compass = new_compass()
        compass.pending_nudge_signals = ["verification_debt"]
        compass.pending_nudge_constraint_check_count = 1
        compass.constraint_check_count_session = 1  # No new precheck since nudge

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        gp_delta = result.metrics.get("global_profile_delta", {})
        nudge_outcomes = gp_delta.get("nudge_outcomes", {})
        assert nudge_outcomes.get("verification_debt") == "ignored"


class TestRepertoireHints:
    def test_attaches_proven_resolution(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=time.time() - 50,
                last_event=time.time(),
                event_count=1,
            )
            for i in range(3)
        ]
        compass.action_history = [
            {"tool": "Bash", "ts": time.time(), "sig": "cmd:2", "exit": 1, "err": ""}
        ]

        event = _make_event(
            compass,
            raw_input={
                "resolution_repertoire": [
                    {
                        "trigger_signature": "approach_cycling",
                        "resolution": "Just fix it.",
                    }
                ]
            },
        )

        ch = BehaviorChannel()
        result = ch.execute(event, _default_config())

        cycling = [f for f in result.findings if f.kind == "approach_cycling"]
        assert len(cycling) == 1
        assert cycling[0].proven_resolution is not None
        assert cycling[0].proven_resolution["repertoire"] == "Just fix it."
        assert "confidence" in cycling[0].proven_resolution


# ── SPEC010: BehaviorChannel.execute output specification ─────────────


class TestBehaviorChannelExecuteSpec:
    """Specify exact output contract for BehaviorChannel.execute."""

    def test_empty_compass_output_structure(self):
        """Empty compass → pass result with full metrics structure."""
        ch = BehaviorChannel()
        compass = new_compass()
        event = _make_event(compass)
        result = ch.execute(event, _default_config())

        assert result.channel == "behavior"
        assert result.status == "pass"
        assert result.severity == "none"
        assert result.findings == []
        assert result.duration_ms >= 0
        assert isinstance(result.metrics, dict)
        # Required metric keys
        assert "compass_summary" in result.metrics
        assert "suppressed_nudges" in result.metrics
        assert result.metrics["suppressed_nudges"] == 0

    def test_compass_summary_metrics_exact_keys(self):
        """Compass summary includes expected behavioral counters."""
        ch = BehaviorChannel()
        compass = new_compass()
        compass.event_counter = 5
        event = _make_event(compass)
        result = ch.execute(event, _default_config())

        summary = result.metrics.get("compass_summary", {})
        assert "approaches_total" in summary
        assert summary["approaches_total"] == 0
        assert "hypotheses_active" in summary
        assert "prediction_recall" in summary

    def test_findings_produce_fail_status(self):
        """When signals fire, status is 'fail' and findings are non-empty."""
        ch = BehaviorChannel()
        now = time.time()
        compass = new_compass()
        compass.event_counter = 15
        compass.approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                started_at=now - 500,
                last_event=now - 100,
                event_count=1,
            )
            for i in range(3)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:2", "exit": 1, "err": ""}]
        event = _make_event(compass)
        result = ch.execute(event, _default_config())

        assert result.status == "fail"
        assert len(result.findings) > 0
        # All findings should have standard attributes
        for f in result.findings:
            assert f.linter == "behavior_channel"
            assert isinstance(f.kind, str)
            assert isinstance(f.message, str)

    def test_behavior_compass_delta_present(self):
        """Execute always includes behavior_compass_delta in metrics."""
        ch = BehaviorChannel()
        compass = new_compass()
        event = _make_event(compass)
        result = ch.execute(event, _default_config())

        delta = result.metrics.get("behavior_compass_delta", {})
        assert isinstance(delta, dict)
        assert "signal_fire_counts" in delta
        assert "last_fired" in delta

    def test_mcp_surface_executes_without_compass_data(self):
        """MCP surface events run even without compass in raw_input."""
        ch = BehaviorChannel()
        event = SupervisionEvent(surface="mcp", project_root="/tmp")
        result = ch.execute(event, _default_config())

        assert result.channel == "behavior"
        assert result.status == "pass"
        assert result.duration_ms >= 0


# ── Mutant-killing: _load_execute_config ──────────────────────────────


class TestLoadExecuteConfig:
    """Pin exact config extraction behavior for _load_execute_config."""

    def test_default_thresholds_returned(self):
        """No config overrides → returns exact copy of DEFAULT_THRESHOLDS."""
        event = _make_event(new_compass())
        config = _default_config()
        thresholds, bias_weights, global_priors, theory_profile, recent_codas = (
            _load_execute_config(event, config)
        )
        assert thresholds == dict(DEFAULT_THRESHOLDS)
        assert bias_weights == {}
        assert global_priors is None
        assert theory_profile is None
        assert recent_codas == {}

    def test_threshold_override_from_channel_settings(self):
        """Channel settings override specific threshold keys."""
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "behavior": ChannelConfig(
                    enabled=True,
                    settings={"approach_cycling_count": 99},
                )
            },
        )
        event = _make_event(new_compass())
        thresholds, _, _, _, _ = _load_execute_config(event, config)
        assert thresholds["approach_cycling_count"] == 99
        # Other keys remain default
        assert thresholds["failure_amnesia_lookback"] == 30

    def test_nested_thresholds_take_precedence(self):
        """settings.thresholds nested dict is applied."""
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "behavior": ChannelConfig(
                    enabled=True,
                    settings={"thresholds": {"signal_cooldown": 42}},
                )
            },
        )
        event = _make_event(new_compass())
        thresholds, _, _, _, _ = _load_execute_config(event, config)
        assert thresholds["signal_cooldown"] == 42

    def test_event_behavior_thresholds_override_all(self):
        """raw_input['behavior_thresholds'] overrides channel config."""
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "behavior": ChannelConfig(
                    enabled=True,
                    settings={"signal_cooldown": 42},
                )
            },
        )
        compass = new_compass()
        raw_input = {"behavior_thresholds": {"signal_cooldown": 7}}
        raw_input["behavior_compass"] = compass.to_dict()
        event = SupervisionEvent(
            surface="hook",
            project_root="/tmp/test",
            tool_name="Bash",
            raw_input=raw_input,
        )
        thresholds, _, _, _, _ = _load_execute_config(event, config)
        assert thresholds["signal_cooldown"] == 7

    def test_bias_weights_extracted(self):
        """bias_weights from channel settings are returned."""
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "behavior": ChannelConfig(
                    enabled=True,
                    settings={"bias_weights": {"verification_debt": 0.30}},
                )
            },
        )
        event = _make_event(new_compass())
        _, bias_weights, _, _, _ = _load_execute_config(event, config)
        assert bias_weights == {"verification_debt": 0.30}

    def test_global_priors_from_event(self):
        """global_priors extracted from raw_input."""
        compass = new_compass()
        raw_input = {"behavior_global_priors": {"some_key": 1.0}}
        raw_input["behavior_compass"] = compass.to_dict()
        event = SupervisionEvent(
            surface="hook",
            project_root="/tmp/test",
            tool_name="Bash",
            raw_input=raw_input,
        )
        config = _default_config()
        _, _, global_priors, _, _ = _load_execute_config(event, config)
        assert global_priors == {"some_key": 1.0}

    def test_theory_profile_gated_by_config(self):
        """theory_profile only loaded when inquiry.theory_grounded_signals is True."""
        compass = new_compass()
        raw_input = {"theory_profile": {"claims": []}}
        raw_input["behavior_compass"] = compass.to_dict()
        event = SupervisionEvent(
            surface="hook",
            project_root="/tmp/test",
            tool_name="Bash",
            raw_input=raw_input,
        )
        # Default config: theory_grounded_signals is False
        config = _default_config()
        _, _, _, theory_profile, _ = _load_execute_config(event, config)
        assert theory_profile is None

    def test_recent_codas_from_compass_data(self):
        """recent_codas extracted from compass _theory_recent_codas."""
        compass = new_compass()
        compass_dict = compass.to_dict()
        compass_dict["_theory_recent_codas"] = {"sig1": "coda text"}
        raw_input = {"behavior_compass": compass_dict}
        event = SupervisionEvent(
            surface="hook",
            project_root="/tmp/test",
            tool_name="Bash",
            raw_input=raw_input,
        )
        config = _default_config()
        _, _, _, _, recent_codas = _load_execute_config(event, config)
        assert recent_codas == {"sig1": "coda text"}


# ── Mutant-killing: _apply_prediction_modulation ──────────────────────


class TestApplyPredictionModulation:
    """Pin exact confidence modulation at boundary values."""

    def _make_finding(self, severity="informational", confidence=0.80):
        from lintgate.types import LintIssue

        return LintIssue(
            linter="behavior_channel",
            kind="test_signal",
            message="test",
            severity=severity,
            confidence=confidence,
        )

    def test_no_modulation_when_tracking_disabled(self):
        """prediction_tracking=False → no confidence change."""
        config = _default_config()
        compass = new_compass()
        finding = self._make_finding(confidence=0.80)
        _apply_prediction_modulation([finding], compass, config)
        assert finding.confidence == 0.80

    def test_high_accuracy_softens_informational(self):
        """Accuracy >0.70 with informational → confidence reduced by 0.15."""
        from lintgate.controlplane.types import InquiryConfig

        config = ControlPlaneConfig(enabled=True, inquiry=InquiryConfig(prediction_tracking=True))
        compass = new_compass()
        # 6 predictions, 5 confirmed → accuracy = 5/6 ≈ 0.833
        compass.prediction_log = [{"status": "confirmed"} for _ in range(5)] + [
            {"status": "falsified"}
        ]
        finding = self._make_finding(severity="informational", confidence=0.80)
        _apply_prediction_modulation([finding], compass, config)
        assert finding.confidence == 0.65  # 0.80 - 0.15

    def test_high_accuracy_does_not_soften_warning(self):
        """Accuracy >0.70 only softens informational, not warning severity."""
        from lintgate.controlplane.types import InquiryConfig

        config = ControlPlaneConfig(enabled=True, inquiry=InquiryConfig(prediction_tracking=True))
        compass = new_compass()
        compass.prediction_log = [{"status": "confirmed"} for _ in range(6)]
        finding = self._make_finding(severity="warning", confidence=0.80)
        _apply_prediction_modulation([finding], compass, config)
        assert finding.confidence == 0.80  # unchanged

    def test_low_accuracy_amplifies_all(self):
        """Accuracy <0.30 → confidence increased by 0.15."""
        from lintgate.controlplane.types import InquiryConfig

        config = ControlPlaneConfig(enabled=True, inquiry=InquiryConfig(prediction_tracking=True))
        compass = new_compass()
        # 6 predictions, 1 confirmed → accuracy = 1/6 ≈ 0.167
        compass.prediction_log = [{"status": "falsified"} for _ in range(5)] + [
            {"status": "confirmed"}
        ]
        finding = self._make_finding(severity="informational", confidence=0.60)
        _apply_prediction_modulation([finding], compass, config)
        assert finding.confidence == 0.75  # 0.60 + 0.15

    def test_confidence_clamped_at_zero(self):
        """Softening doesn't go below 0.0."""
        from lintgate.controlplane.types import InquiryConfig

        config = ControlPlaneConfig(enabled=True, inquiry=InquiryConfig(prediction_tracking=True))
        compass = new_compass()
        compass.prediction_log = [{"status": "confirmed"} for _ in range(6)]
        finding = self._make_finding(severity="informational", confidence=0.10)
        _apply_prediction_modulation([finding], compass, config)
        assert finding.confidence == 0.0  # max(0.0, 0.10 - 0.15)

    def test_confidence_clamped_at_one(self):
        """Amplification doesn't exceed 1.0."""
        from lintgate.controlplane.types import InquiryConfig

        config = ControlPlaneConfig(enabled=True, inquiry=InquiryConfig(prediction_tracking=True))
        compass = new_compass()
        compass.prediction_log = [{"status": "falsified"} for _ in range(6)]
        finding = self._make_finding(severity="warning", confidence=0.95)
        _apply_prediction_modulation([finding], compass, config)
        assert finding.confidence == 1.0  # min(1.0, 0.95 + 0.15)


# ── Mutant-killing: _compute_nudge_outcomes ───────────────────────────


class TestComputeNudgeOutcomes:
    """Pin exact nudge outcome computation."""

    def test_no_pending_returns_empty(self):
        compass = new_compass()
        outcomes = _compute_nudge_outcomes(compass, [])
        assert outcomes == {}
        assert compass.pending_nudge_signals == []

    def test_pending_accepted_when_constraint_check_increased(self):
        compass = new_compass()
        compass.pending_nudge_signals = ["approach_cycling"]
        compass.pending_nudge_constraint_check_count = 5
        compass.constraint_check_count_session = 7  # delta > 0 → accepted
        outcomes = _compute_nudge_outcomes(compass, ["new_signal"])
        assert outcomes == {"approach_cycling": "accepted"}
        assert compass.nudge_outcomes == {"approach_cycling": "accepted"}
        assert compass.pending_nudge_signals == ["new_signal"]
        assert compass.pending_nudge_constraint_check_count == 7

    def test_pending_ignored_when_no_constraint_check(self):
        compass = new_compass()
        compass.pending_nudge_signals = ["stale_model"]
        compass.pending_nudge_constraint_check_count = 3
        compass.constraint_check_count_session = 3  # delta == 0 → ignored
        outcomes = _compute_nudge_outcomes(compass, [])
        assert outcomes == {"stale_model": "ignored"}

    def test_multiple_pending_all_same_outcome(self):
        compass = new_compass()
        compass.pending_nudge_signals = ["sig1", "sig2"]
        compass.pending_nudge_constraint_check_count = 0
        compass.constraint_check_count_session = 0
        outcomes = _compute_nudge_outcomes(compass, [])
        assert outcomes == {"sig1": "ignored", "sig2": "ignored"}


# ── Mutant-killing: _build_channel_result ─────────────────────────────


class TestBuildChannelResult:
    """Pin exact ChannelResult construction."""

    def test_no_findings_produces_pass(self):
        compass = new_compass()
        scorer = _IntentBiasScorer(compass, {})
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        result = _build_channel_result(
            findings=[],
            next_actions=[],
            compass=compass,
            coord=coord,
            scorer=scorer,
            nudge_outcomes={},
            intent_delta={},
            elapsed_ms=1.5,
        )
        assert result.channel == "behavior"
        assert result.status == "pass"
        assert result.severity == "none"
        assert result.findings == []
        assert result.repairs == []
        assert result.duration_ms == 1.5
        assert result.metrics["alert_count"] == 0
        assert result.metrics["hard_alerts"] == 0
        assert result.metrics["soft_alerts"] == 0

    def test_informational_finding_produces_fail_informational(self):
        from lintgate.types import LintIssue

        compass = new_compass()
        scorer = _IntentBiasScorer(compass, {})
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        finding = LintIssue(
            linter="behavior_channel",
            kind="test",
            message="soft signal",
            severity="informational",
        )
        result = _build_channel_result(
            findings=[finding],
            next_actions=[],
            compass=compass,
            coord=coord,
            scorer=scorer,
            nudge_outcomes={},
            intent_delta={},
            elapsed_ms=2.0,
        )
        assert result.status == "fail"
        assert result.severity == "informational"
        assert result.metrics["alert_count"] == 1
        assert result.metrics["hard_alerts"] == 0
        assert result.metrics["soft_alerts"] == 1

    def test_warning_finding_escalates_severity(self):
        from lintgate.types import LintIssue

        compass = new_compass()
        scorer = _IntentBiasScorer(compass, {})
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        findings = [
            LintIssue(
                linter="behavior_channel",
                kind="hard",
                message="hard signal",
                severity="warning",
            ),
            LintIssue(
                linter="behavior_channel",
                kind="soft",
                message="soft signal",
                severity="informational",
            ),
        ]
        result = _build_channel_result(
            findings=findings,
            next_actions=[],
            compass=compass,
            coord=coord,
            scorer=scorer,
            nudge_outcomes={},
            intent_delta={},
            elapsed_ms=3.0,
        )
        assert result.status == "fail"
        assert result.severity == "warning"
        assert result.metrics["hard_alerts"] == 1
        assert result.metrics["soft_alerts"] == 1

    def test_compass_summary_structure(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig="cmd:1",
                outcome="failed",
                started_at=0,
                last_event=0,
                event_count=1,
            )
        ]
        scorer = _IntentBiasScorer(compass, {})
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        result = _build_channel_result(
            findings=[],
            next_actions=[],
            compass=compass,
            coord=coord,
            scorer=scorer,
            nudge_outcomes={},
            intent_delta={},
            elapsed_ms=0.5,
        )
        summary = result.metrics["compass_summary"]
        assert summary["approaches_total"] == 1
        assert summary["hypotheses_active"] == 0
        assert summary["prediction_recall"] == 0.0

    def test_behavior_compass_delta_keys(self):
        compass = new_compass()
        scorer = _IntentBiasScorer(compass, {})
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        result = _build_channel_result(
            findings=[],
            next_actions=[],
            compass=compass,
            coord=coord,
            scorer=scorer,
            nudge_outcomes={},
            intent_delta={},
            elapsed_ms=0.1,
        )
        delta = result.metrics["behavior_compass_delta"]
        assert "last_fired" in delta
        assert "signal_fire_counts" in delta
        assert "early_nudge_emitted" in delta
        assert "pending_nudge_signals" in delta
        assert "pending_nudge_constraint_check_count" in delta
        assert "nudge_outcomes" in delta
        assert "_theory_recent_codas" in delta

    def test_global_profile_delta_structure(self):
        compass = new_compass()
        scorer = _IntentBiasScorer(compass, {})
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        result = _build_channel_result(
            findings=[],
            next_actions=[],
            compass=compass,
            coord=coord,
            scorer=scorer,
            nudge_outcomes={"sig1": "accepted"},
            intent_delta={"execute": 3},
            elapsed_ms=0.1,
        )
        gpd = result.metrics["global_profile_delta"]
        assert gpd["nudge_outcomes"] == {"sig1": "accepted"}
        assert gpd["intent_summary"] == {"execute": 3}
        assert gpd["signal_fire_counts"] == {}


# ── Mutant-killing: SignalCoordinator methods ─────────────────────────


class TestSignalCoordinatorMutantKilling:
    """Pin exact state transitions for SignalCoordinator."""

    def test_can_fire_first_time_always_true(self):
        compass = new_compass()
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        assert coord.can_fire("approach_cycling") is True

    def test_can_fire_blocked_within_cooldown(self):
        compass = new_compass()
        compass.event_counter = 15
        compass.last_fired["approach_cycling"] = 10
        coord = _SignalCoordinator(compass, {"signal_cooldown": 10})
        # 15 - 10 = 5 < 10 cooldown
        assert coord.can_fire("approach_cycling") is False

    def test_can_fire_allowed_after_cooldown(self):
        compass = new_compass()
        compass.event_counter = 20
        compass.last_fired["approach_cycling"] = 10
        coord = _SignalCoordinator(compass, {"signal_cooldown": 10})
        # 20 - 10 = 10 >= 10 cooldown
        assert coord.can_fire("approach_cycling") is True

    def test_record_firing_updates_exact_state(self):
        compass = new_compass()
        compass.event_counter = 42
        coord = _SignalCoordinator(compass, dict(DEFAULT_THRESHOLDS))
        coord.record_firing("verification_debt")
        assert compass.last_fired["verification_debt"] == 42
        assert compass.signal_fire_counts["verification_debt"] == 1
        assert coord.run_fire_counts["verification_debt"] == 1
        # Fire again
        compass.event_counter = 55
        coord.record_firing("verification_debt")
        assert compass.last_fired["verification_debt"] == 55
        assert compass.signal_fire_counts["verification_debt"] == 2
        assert coord.run_fire_counts["verification_debt"] == 2

    def test_add_finding_suppressed_increments_count(self):
        from lintgate.types import LintIssue

        compass = new_compass()
        compass.event_counter = 5
        compass.last_fired["sig"] = 3
        coord = _SignalCoordinator(compass, {"signal_cooldown": 10})
        finding = LintIssue(
            linter="behavior_channel", kind="test", message="test", severity="informational"
        )
        coord.add_finding("sig", finding, is_hard=False)
        assert coord.suppressed_nudge_count == 1
        assert coord.findings == []

    def test_add_finding_with_precheck_nudge_tracks_priority(self):
        from lintgate.types import LintIssue

        compass = new_compass()
        compass.event_counter = 0
        coord = _SignalCoordinator(compass, {"signal_cooldown": 10})
        nudge = {"tool": "constraint_check", "reason": "test"}
        finding = LintIssue(
            linter="behavior_channel", kind="test", message="test", severity="informational"
        )
        coord.add_finding("approach_cycling", finding, is_hard=True, precheck_nudge=nudge)
        assert "approach_cycling" in coord._nudge_signals
        assert coord._pending_precheck == nudge
        assert coord._pending_priority == 1  # approach_cycling priority

    def test_register_nudge_only_tracks_signal(self):
        compass = new_compass()
        compass.event_counter = 0
        coord = _SignalCoordinator(compass, {"signal_cooldown": 10})
        nudge = {"tool": "constraint_check", "reason": "nudge"}
        coord.register_nudge_only("failure_amnesia", nudge)
        assert "failure_amnesia" in coord._nudge_signals
        assert coord._pending_precheck == nudge
        assert coord._pending_priority == 2  # failure_amnesia priority

    def test_finalize_returns_exact_tuple(self):
        compass = new_compass()
        coord = _SignalCoordinator(compass, {"signal_cooldown": 10})
        nudge = {"tool": "constraint_check"}
        coord.register_nudge_only("approach_cycling", nudge)
        findings, next_actions, nudge_signals, suppressed = coord.finalize()
        assert findings == []
        assert next_actions == [nudge]
        assert nudge_signals == ["approach_cycling"]
        assert suppressed == 0
