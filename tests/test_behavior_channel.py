"""Tests for behavior channel — behavioral drift detection rules."""

from __future__ import annotations

import time

import pytest

from lintgate.channels.behavior_channel import (
    BehaviorChannel,
    _IntentBiasScorer,
    _SignalCoordinator,
)
from lintgate.controlplane.behavior_compass import (
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    DEFAULT_THRESHOLDS,
    error_memory_key,
    new_compass,
    record_tool_event,
)
from lintgate.controlplane.types import (
    ChannelConfig,
    ChannelResult,
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
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:attempt2", "exit": 1, "err": "fail"}]

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
            ApproachAttempt(approach_sig=f"cmd:{i}", outcome="failed", started_at=now - 500, last_event=now - 100, event_count=1)
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
            ApproachAttempt(approach_sig=f"cmd:{i}", outcome="failed", started_at=now - 500, last_event=now - 100, event_count=1)
            for i in range(4)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:3", "exit": 1, "err": ""}]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        next_actions = result.metrics.get("next_actions", [])
        assert any(a["tool"] == "behavior_precheck" for a in next_actions)


# ── failure_amnesia ──────────────────────────────────────────────────


class TestFailureAmnesia:
    def test_fires_on_repeated_error(self):
        compass = new_compass()
        now = time.time()

        compass.action_history = [
            {"tool": "Bash", "ts": now - 600, "sig": "idevicerestore:restore", "exit": 1, "err": "Unable to send iBSS"},
            {"tool": "Read", "ts": now - 400, "sig": "", "exit": None, "err": ""},
            {"tool": "Bash", "ts": now - 200, "sig": "idevicerestore:restore", "exit": 1, "err": "Unable to send iBSS"},
            {"tool": "Bash", "ts": now, "sig": "idevicerestore:restore", "exit": 1, "err": "Unable to send iBSS"},
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
            {"tool": "Bash", "ts": now - 300, "sig": "cmd:a", "exit": 1, "err": "error A"},
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
            {"tool": "Bash", "ts": now - i, "sig": f"cmd:{i}", "exit": 1 if i < 6 else 0, "err": "fail" if i < 6 else ""}
            for i in range(8)
        ]
        compass.coverage = CoverageMetrics(bash_count_recent=8, read_count_recent=0)

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        pa_findings = [f for f in result.findings if f.kind == "premature_action"]
        assert len(pa_findings) == 1
        assert pa_findings[0].severity == "informational"  # Soft signal

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
            BehaviorHypothesis(id=f"h{i}", claim=f"fail {i}", confidence=0.4, source="command_failure", status="active")
            for i in range(3)
        ]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd_findings = [f for f in result.findings if f.kind == "serial_discovery"]
        # v2: two-stage serial discovery — stage 1 (early nudge) + stage 2 (3+ failure-sourced)
        assert len(sd_findings) == 2
        stages = {f.evidence.get("stage") for f in sd_findings}
        assert stages == {1, 2}
        assert all(f.severity == "informational" for f in sd_findings)

    def test_no_fire_with_precheck_declared(self):
        compass = new_compass()
        # v2: set precheck_count_session so stage 1 is suppressed
        compass.precheck_count_session = 1
        compass.hypotheses = [
            BehaviorHypothesis(id="h0", claim="fail", confidence=0.4, source="command_failure", status="active"),
            BehaviorHypothesis(id="h1", claim="fail", confidence=0.4, source="command_failure", status="active"),
            BehaviorHypothesis(id="h2", claim="fail", confidence=0.4, source="command_failure", status="active"),
            BehaviorHypothesis(id="h3", claim="declared", confidence=0.5, source="precheck_declared", status="active"),
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
            {"tool": "Bash", "ts": now - i * 60, "sig": "irecovery:query", "exit": 0, "err": ""}
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
            ApproachAttempt(approach_sig=f"cmd:{i}", outcome="failed", started_at=now - 500, last_event=now - 100, event_count=1)
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
        compass.precheck_count_session = 0
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="test", confidence=0.4, source="command_failure", status="active"),
        ]
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.serial_discovery_bias()
        assert bias > 0
        assert "precheck=0" in terms[0]

    def test_serial_discovery_no_bias_with_precheck(self):
        compass = new_compass()
        compass.precheck_count_session = 1
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="test", confidence=0.4, source="command_failure", status="active"),
        ]
        scorer = _IntentBiasScorer(compass, {})
        bias, terms = scorer.serial_discovery_bias()
        assert bias == 0.0

    def test_stale_model_bias_fires_on_approach_streak(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(approach_sig="cmd:a", started_at=100, last_event=100, hyp_version_at_start=0),
            ApproachAttempt(approach_sig="cmd:b", started_at=200, last_event=200, hyp_version_at_start=0),
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
        assert vd[0].severity == "informational"
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
            ApproachAttempt(approach_sig="cmd:a", started_at=100, last_event=100, hyp_version_at_start=0),
            ApproachAttempt(approach_sig="cmd:b", started_at=200, last_event=200, hyp_version_at_start=0),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sm = [f for f in result.findings if f.kind == "stale_model"]
        assert len(sm) == 1
        assert sm[0].severity == "informational"

    def test_no_fire_when_version_changes(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(approach_sig="cmd:a", started_at=100, last_event=100, hyp_version_at_start=0),
            ApproachAttempt(approach_sig="cmd:b", started_at=200, last_event=200, hyp_version_at_start=1),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sm = [f for f in result.findings if f.kind == "stale_model"]
        assert len(sm) == 0

    def test_evidence_includes_approach_streak(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(approach_sig=f"cmd:{i}", started_at=100 + i * 10, last_event=100 + i * 10, hyp_version_at_start=0)
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
        compass.precheck_count_session = 0
        compass.early_nudge_emitted = False
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="test", confidence=0.4, source="command_failure", status="active"),
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
        compass.precheck_count_session = 1
        compass.early_nudge_emitted = False
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="test", confidence=0.4, source="command_failure", status="active"),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd = [f for f in result.findings if f.kind == "serial_discovery"]
        stage1 = [f for f in sd if f.evidence.get("stage") == 1]
        assert len(stage1) == 0

    def test_stage1_fires_only_once(self):
        compass = new_compass()
        compass.precheck_count_session = 0
        compass.early_nudge_emitted = True  # Already emitted
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="test", confidence=0.4, source="command_failure", status="active"),
        ]
        compass.event_counter = 10

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        sd = [f for f in result.findings if f.kind == "serial_discovery"]
        stage1 = [f for f in sd if f.evidence.get("stage") == 1]
        assert len(stage1) == 0

    def test_stage2_fires_at_3_failure_hyps(self):
        compass = new_compass()
        compass.precheck_count_session = 0
        compass.early_nudge_emitted = True  # Stage 1 already fired
        compass.hypotheses = [
            BehaviorHypothesis(id=f"h{i}", claim=f"fail {i}", confidence=0.4, source="command_failure", status="active")
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
            {"tool": "Bash", "ts": now - 300, "sig": "cmd:a", "exit": 1, "err": "error X"},
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
                id="h1", claim="something fails", confidence=0.4,
                source="command_failure", status="active",
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
            {"tool": "Bash", "ts": now - 300, "sig": "cmd:a", "exit": 1, "err": "error X"},
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
        compass.precheck_count_session = 1  # suppress serial_discovery stage-1 noise
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
        compass.precheck_count_session = 1  # suppress serial_discovery stage-1 noise
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
            ApproachAttempt(approach_sig=f"cmd:{i}", outcome="failed", started_at=now - 500, last_event=now - 100, event_count=1)
            for i in range(3)
        ]
        compass.action_history = [
            {"tool": "Bash", "ts": now - i, "sig": f"cmd:{i}", "exit": 1, "err": "fail"}
            for i in range(5)
        ]

        result = ch.execute(_make_event(compass), _default_config())
        next_actions = result.metrics.get("next_actions", [])
        # At most 1 precheck nudge (dedup'd by priority)
        precheck_nudges = [a for a in next_actions if a.get("tool") == "behavior_precheck"]
        assert len(precheck_nudges) <= 1

    def test_higher_priority_signal_wins(self):
        compass = new_compass()
        compass.event_counter = 100

        ch = BehaviorChannel()
        now = time.time()
        compass.approaches = [
            ApproachAttempt(approach_sig=f"cmd:{i}", outcome="failed", started_at=now - 500, last_event=now - 100, event_count=1)
            for i in range(3)
        ]
        compass.action_history = [
            {"tool": "Bash", "ts": now - i, "sig": f"cmd:{i}", "exit": 1, "err": "fail"}
            for i in range(5)
        ]

        result = ch.execute(_make_event(compass), _default_config())
        next_actions = result.metrics.get("next_actions", [])
        precheck_nudges = [a for a in next_actions if a.get("tool") == "behavior_precheck"]
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
            ApproachAttempt(approach_sig=f"cmd:{i}", outcome="failed", started_at=now - 500, last_event=now - 100, event_count=1)
            for i in range(3)
        ]
        compass.action_history = [{"tool": "Bash", "ts": now, "sig": "cmd:2", "exit": 1, "err": ""}]

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        cycling = [f for f in result.findings if f.kind == "approach_cycling"]
        if cycling:
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
        assert "pending_nudge_precheck_count" in delta
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
        effective = scorer._effective_bias_weight("verification_debt", "verification_debt_bias", 0.20)
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
        effective = scorer._effective_bias_weight("verification_debt", "verification_debt_bias", 0.20)
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
        effective = scorer._effective_bias_weight("verification_debt", "verification_debt_bias", 0.25)
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
        compass.precheck_count_session = 1  # Precheck was used

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        gp_delta = result.metrics.get("global_profile_delta", {})
        nudge_outcomes = gp_delta.get("nudge_outcomes", {})
        assert nudge_outcomes.get("verification_debt") == "accepted"

    def test_nudge_outcomes_ignored_when_no_precheck(self):
        """Previously pending nudges are marked 'ignored' when precheck_count == 0."""
        compass = new_compass()
        compass.pending_nudge_signals = ["approach_cycling"]
        compass.precheck_count_session = 0  # No precheck

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        gp_delta = result.metrics.get("global_profile_delta", {})
        nudge_outcomes = gp_delta.get("nudge_outcomes", {})
        assert nudge_outcomes.get("approach_cycling") == "ignored"

    def test_nudge_outcomes_require_new_precheck_since_nudge(self):
        """Precheck must happen after nudge issuance to count as accepted."""
        compass = new_compass()
        compass.pending_nudge_signals = ["verification_debt"]
        compass.pending_nudge_precheck_count = 1
        compass.precheck_count_session = 1  # No new precheck since nudge

        ch = BehaviorChannel()
        result = ch.execute(_make_event(compass), _default_config())

        gp_delta = result.metrics.get("global_profile_delta", {})
        nudge_outcomes = gp_delta.get("nudge_outcomes", {})
        assert nudge_outcomes.get("verification_debt") == "ignored"
