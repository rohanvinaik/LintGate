"""Tests for behavioral compass — data model, hypothesis management, and detection rules. (Part 2/2)"""

from __future__ import annotations

import pytest

from lintgate.controlplane.behavior_compass import (
    DEFAULT_HYPOTHESIS_CONFIG,
    INTENT_CATEGORIES,
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    _hypothesis_matches_sig,
    _strengthen_hypothesis,
    _test_hypotheses,
    _weaken_hypothesis,
    add_declared_hypothesis,
    compute_coverage,
    compute_uncertainty_zones,
    decay_stale,
    find_relevant_hypotheses,
    new_compass,
    record_tool_event,
    resolve_intent,
    update_hypothesis,
)

# ── Coverage and uncertainty ─────────────────────────────────────────


class TestCoverage:
    def test_basic_coverage(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="a", confidence=0.8, status="confirmed"),
            BehaviorHypothesis(id="h2", claim="b", confidence=0.3, status="active"),
        ]
        compass.approaches = [
            ApproachAttempt(approach_sig="cmd:a", outcome="failed", event_count=2),
            ApproachAttempt(approach_sig="cmd:b", outcome="success", event_count=1),
        ]

        coverage = compute_coverage(compass)
        assert coverage.constraints_verified == 1  # Only h1 above threshold
        assert coverage.approaches_attempted == 2
        assert coverage.approach_success_rate == 0.5

    def test_prediction_recall(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="predicted",
                claim="p",
                confidence=0.8,
                source="precheck_declared",
                status="confirmed",
            ),
            BehaviorHypothesis(
                id="surprise",
                claim="s",
                confidence=0.6,
                source="command_failure",
                status="active",
            ),
        ]

        coverage = compute_coverage(compass)
        # predicted=1 (precheck_declared + high confidence), surprise=1 (command_failure + >=0.5)
        # recall = 1 / (1 + 1) = 0.5
        assert coverage.prediction_recall == pytest.approx(0.5)


class TestUncertaintyZones:
    def test_failed_approach_without_hypothesis(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig="idevicerestore:restore",
                outcome="failed",
                error_sigs=["ASR signature failed"],
            ),
        ]
        # No hypotheses cover this approach
        zones = compute_uncertainty_zones(compass)
        assert len(zones) >= 1
        assert "idevicerestore:restore" in zones[0]

    def test_low_confidence_hypothesis(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="low confidence thing",
                confidence=0.2,
                status="active",
            ),
        ]
        zones = compute_uncertainty_zones(compass)
        assert any("low confidence" in z.lower() for z in zones)


# ── find_relevant_hypotheses ─────────────────────────────────────────


class TestFindRelevant:
    def test_matches_wildcard_sig(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="idevicerestore fails",
                confidence=0.5,
                status="active",
                applies_to_sigs=["idevicerestore:*"],
            ),
            BehaviorHypothesis(
                id="h2",
                claim="git issue",
                confidence=0.5,
                status="active",
                applies_to_sigs=["git:*"],
            ),
        ]
        relevant = find_relevant_hypotheses(compass, "idevicerestore:restore")
        assert len(relevant) == 1
        assert relevant[0].id == "h1"

    def test_matches_tool_filter(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="bash thing",
                confidence=0.5,
                status="active",
                applies_to_tools=["Bash"],
            ),
        ]
        relevant = find_relevant_hypotheses(compass, tool="Bash")
        assert len(relevant) == 1

    def test_excludes_expired(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="expired",
                confidence=0.0,
                status="expired",
                applies_to_sigs=["git:*"],
            ),
        ]
        relevant = find_relevant_hypotheses(compass, "git:status")
        assert len(relevant) == 0


# ── add_declared_hypothesis ──────────────────────────────────────────


class TestDeclaredHypothesis:
    def test_adds_new(self):
        compass = new_compass()
        hyp = add_declared_hypothesis(
            compass, "Device requires signed iBSS", "idevicerestore:restore", now=1000.0
        )
        assert hyp.source == "precheck_declared"
        assert hyp.confidence == 0.5
        assert len(compass.hypotheses) == 1

    def test_strengthens_existing(self):
        compass = new_compass()
        hyp1 = add_declared_hypothesis(
            compass, "Device requires signed iBSS", "idevicerestore:restore", now=1000.0
        )
        initial = hyp1.confidence

        # Re-declare same claim
        add_declared_hypothesis(
            compass, "Device requires signed iBSS", "idevicerestore:restore", now=1001.0
        )
        assert len(compass.hypotheses) == 1  # No duplicate
        assert compass.hypotheses[0].confidence > initial  # Strengthened


# ── Serialization ────────────────────────────────────────────────────


class TestSerialization:
    def test_compass_roundtrip(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="h1",
                claim="test",
                confidence=0.5,
                evidence_for=["ev1"],
                created_at=1000.0,
                last_tested=1000.0,
                applies_to_sigs=["git:*"],
                applies_to_tools=["Bash"],
            )
        )
        compass.approaches.append(
            ApproachAttempt(
                approach_sig="git:push",
                exit_codes=[0, 1],
                started_at=1000.0,
                last_event=1001.0,
            )
        )
        compass.action_history.append(
            {"tool": "Bash", "ts": 1000.0, "sig": "git:push", "exit": 0, "err": ""}
        )
        compass.error_memory = {
            "err01": {"count": 2, "first_seen": 900.0, "last_seen": 1000.0, "last_sig": "error X"},
        }

        data = compass.to_dict()
        restored = BehaviorCompass.from_dict(data)

        assert restored.hypotheses[0].id == "h1"
        assert restored.hypotheses[0].applies_to_sigs == ["git:*"]
        assert len(restored.approaches) == 1
        assert restored.approaches[0].approach_sig == "git:push"
        assert len(restored.action_history) == 1
        assert restored.error_memory["err01"]["count"] == 2

    def test_empty_compass_from_dict(self):
        compass = BehaviorCompass.from_dict({})
        assert compass.hypotheses == []
        assert compass.approaches == []

    def test_empty_compass_from_none(self):
        compass = BehaviorCompass.from_dict(None)  # type: ignore
        assert compass.hypotheses == []


# ── v2: resolve_intent ──────────────────────────────────────────────────


class TestResolveIntent:
    def test_read_is_inspect(self):
        assert resolve_intent("Read", "") == "inspect"

    def test_write_is_modify(self):
        assert resolve_intent("Write", "") == "modify"

    def test_edit_is_modify(self):
        assert resolve_intent("Edit", "") == "modify"

    def test_bash_pytest_is_verify(self):
        assert resolve_intent("Bash", "pytest:tests") == "verify"

    def test_bash_git_status_is_inspect(self):
        assert resolve_intent("Bash", "git:status") == "inspect"

    def test_bash_git_commit_is_modify(self):
        assert resolve_intent("Bash", "git:commit") == "modify"

    def test_bash_no_sig_is_execute(self):
        """Bash with no command_sig falls through to execute."""
        assert resolve_intent("Bash", "") == "execute"

    def test_unknown_tool_is_unknown(self):
        assert resolve_intent("SomeUnknownTool", "") == "unknown"

    def test_all_categories_valid(self):
        """All resolved intents must be in INTENT_CATEGORIES."""
        test_cases = [
            ("Read", ""),
            ("Write", ""),
            ("Bash", "pytest:tests"),
            ("Bash", "git:status"),
            ("Bash", ""),
        ]
        for tool, sig in test_cases:
            result = resolve_intent(tool, sig)
            assert result in INTENT_CATEGORIES


# ── v2: intent history tracking ─────────────────────────────────────────


class TestIntentHistory:
    def test_intent_appended_on_record(self):
        compass = new_compass()
        record_tool_event(compass, "Read", {}, "", now=100.0)
        assert compass.intent_history == ["inspect"]

    def test_intent_appended_for_bash(self):
        compass = new_compass()
        record_tool_event(compass, "Bash", {"command": "pytest tests/"}, "exit_code: 0", now=100.0)
        assert len(compass.intent_history) == 1
        assert compass.intent_history[0] == "verify"

    def test_intent_rolls_at_max(self):
        compass = new_compass()
        for i in range(35):
            record_tool_event(compass, "Read", {}, "", now=100.0 + i)
        assert len(compass.intent_history) <= 30


# ── v2: hypothesis_version tracking ─────────────────────────────────────


class TestHypothesisVersion:
    def test_version_increments_on_auto_generate(self):
        compass = new_compass()
        record_tool_event(
            compass,
            "Bash",
            {"command": "idevicerestore -e foo.ipsw"},
            "ERROR: unable to send iBSS\nexit_code: 1",
            now=100.0,
        )
        assert compass.hypothesis_version >= 1

    def test_version_increments_on_strengthen(self):
        compass = new_compass()
        hyp = BehaviorHypothesis(
            id="test1234",
            claim="test fails",
            confidence=0.4,
            source="command_failure",
            created_at=100.0,
            last_tested=100.0,
            last_decay=100.0,
        )
        compass.hypotheses.append(hyp)
        v_before = compass.hypothesis_version
        update_hypothesis(compass, "test1234", "strengthen", "evidence", now=101.0)
        assert compass.hypothesis_version == v_before + 1

    def test_version_increments_on_decay_expire(self):
        compass = new_compass()
        hyp = BehaviorHypothesis(
            id="test1234",
            claim="test fails",
            confidence=0.01,
            source="command_failure",
            created_at=100.0,
            last_tested=100.0,
            last_decay=100.0,
        )
        compass.hypotheses.append(hyp)
        v_before = compass.hypothesis_version
        # Decay enough to expire
        decay_stale(compass, 100.0 + 3600 * 10)  # 10 hours later
        assert compass.hypothesis_version > v_before


# ── v2: approach hyp_version tracking ───────────────────────────────────


class TestApproachHypVersionTracking:
    def test_new_approach_captures_version(self):
        compass = new_compass()
        compass.hypothesis_version = 5
        record_tool_event(
            compass,
            "Bash",
            {"command": "make build"},
            "exit_code: 0",
            now=100.0,
        )
        assert len(compass.approaches) == 1
        assert compass.approaches[0].hyp_version_at_start == 5


# ── v2: event_counter tracking ──────────────────────────────────────────


class TestEventCounter:
    def test_counter_increments(self):
        compass = new_compass()
        assert compass.event_counter == 0
        record_tool_event(compass, "Read", {}, "", now=100.0)
        assert compass.event_counter == 1


# ── v2: constraint_check_count_session ──────────────────────────────────────────


class TestPrecheckCountSession:
    def test_not_incremented_by_declared_hypothesis_add(self):
        compass = new_compass()
        add_declared_hypothesis(compass, "test constraint", "test:cmd", now=100.0)
        assert compass.constraint_check_count_session == 0


# ── v2: serialization of new fields ─────────────────────────────────────


class TestSerializationV2:
    def test_new_fields_roundtrip(self):
        compass = new_compass()
        compass.intent_history = ["inspect", "modify", "execute"]
        compass.hypothesis_version = 7
        compass.constraint_check_count_session = 3
        compass.event_counter = 42
        compass.last_fired = {"approach_cycling": 10}
        compass.signal_fire_counts = {"approach_cycling": 2}
        compass.early_nudge_emitted = True
        compass.error_memory = {
            "err01": {"count": 2, "first_seen": 10.0, "last_seen": 20.0, "last_sig": "error sig"},
        }

        data = compass.to_dict()
        restored = BehaviorCompass.from_dict(data)

        assert restored.intent_history == ["inspect", "modify", "execute"]
        assert restored.hypothesis_version == 7
        assert restored.constraint_check_count_session == 3
        assert restored.event_counter == 42
        assert restored.last_fired == {"approach_cycling": 10}
        assert restored.signal_fire_counts == {"approach_cycling": 2}
        assert restored.early_nudge_emitted is True
        assert restored.error_memory["err01"]["count"] == 2


# ── Targeted Coverage Fixes ──────────────────────────────────────────


def _make_compass_cov():
    return BehaviorCompass()


def _make_hypothesis_cov(
    hyp_id="h1",
    claim="test claim",
    confidence=0.5,
    status="active",
    applies_to=None,
):
    import time

    h = BehaviorHypothesis(
        id=hyp_id,
        claim=claim,
        confidence=confidence,
        status=status,
        applies_to_sigs=applies_to or ["pytest:*"],
        created_at=time.time(),
        last_tested=time.time(),
    )
    return h


class TestCoverageInternal:
    def test_hypothesis_matches_wildcard(self):
        h = _make_hypothesis_cov(applies_to=["pytest:*"])
        assert _hypothesis_matches_sig(h, "pytest:tests/test_foo.py", "pytest") is True

    def test_hypothesis_matches_exact(self):
        h = _make_hypothesis_cov(applies_to=["git:status"])
        assert _hypothesis_matches_sig(h, "git:status", "git") is True

    def test_hypothesis_no_match(self):
        h = _make_hypothesis_cov(applies_to=["pytest:*"])
        assert _hypothesis_matches_sig(h, "git:status", "git") is False

    def test_strengthen_increases_confidence(self):
        h = _make_hypothesis_cov(confidence=0.5)
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        _strengthen_hypothesis(h, "evidence", cfg)
        assert h.confidence > 0.5

    def test_weaken_decreases_confidence(self):
        h = _make_hypothesis_cov(confidence=0.5)
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        _weaken_hypothesis(h, "counter evidence", cfg)
        assert h.confidence < 0.5

    def test_update_nonexistent_id(self):
        c = _make_compass_cov()
        h = _make_hypothesis_cov()
        c.hypotheses.append(h)
        old_conf = h.confidence
        update_hypothesis(c, "nonexistent", "strengthen", "evidence")
        assert h.confidence == old_conf

    def test_decay_skips_expired(self):
        import time

        c = _make_compass_cov()
        h = _make_hypothesis_cov(confidence=0.5, status="expired")
        h.last_tested = time.time() - 7200
        c.hypotheses.append(h)
        decay_stale(c, time.time())
        assert h.confidence == 0.5

    def test_decay_skips_recent(self):
        import time

        c = _make_compass_cov()
        h = _make_hypothesis_cov(confidence=0.5)
        h.last_tested = time.time()
        h.last_decay = h.last_tested
        c.hypotheses.append(h)
        decay_stale(c, time.time())
        assert abs(h.confidence - 0.5) < 1e-6

    def test_test_hypotheses_strengthens_on_error(self):
        import time

        c = _make_compass_cov()
        h = _make_hypothesis_cov(applies_to=["pytest:*"], claim="import error failure")
        c.hypotheses.append(h)
        old_conf = h.confidence
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        _test_hypotheses(c, "pytest:tests/test_foo.py", 1, "import error found", time.time(), cfg)
        assert h.confidence > old_conf

    def test_test_hypotheses_weakens_on_success(self):
        import time

        c = _make_compass_cov()
        h = _make_hypothesis_cov(applies_to=["pytest:*"])
        c.hypotheses.append(h)
        old_conf = h.confidence
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        _test_hypotheses(c, "pytest:tests/test_foo.py", 0, "", time.time(), cfg)
        assert h.confidence < old_conf
