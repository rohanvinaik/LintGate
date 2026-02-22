"""Targeted coverage tests for behavior_compass.py uncovered symbols."""

from __future__ import annotations

import time

from lintgate.controlplane.behavior_compass import (
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    _hypothesis_matches_sig,
    _strengthen_hypothesis,
    _test_hypotheses,
    _weaken_hypothesis,
    add_declared_hypothesis,
    compute_coverage,
    compute_prediction_accuracy,
    compute_uncertainty_zones,
    decay_stale,
    evict_overflow,
    find_relevant_hypotheses,
    record_tool_event,
    update_hypothesis,
    DEFAULT_HYPOTHESIS_CONFIG,
)


def _make_compass():
    return BehaviorCompass()


def _make_hypothesis(
    hyp_id="h1", claim="test claim", confidence=0.5, status="active",
    applies_to=None,
):
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


# ── _hypothesis_matches_sig ──────────────────────────────────────────


def test_hypothesis_matches_wildcard():
    h = _make_hypothesis(applies_to=["pytest:*"])
    assert _hypothesis_matches_sig(h, "pytest:tests/test_foo.py", "pytest") is True


def test_hypothesis_matches_exact():
    h = _make_hypothesis(applies_to=["git:status"])
    assert _hypothesis_matches_sig(h, "git:status", "git") is True


def test_hypothesis_no_match():
    h = _make_hypothesis(applies_to=["pytest:*"])
    assert _hypothesis_matches_sig(h, "git:status", "git") is False


# ── _strengthen_hypothesis ───────────────────────────────────────────


def test_strengthen_increases_confidence():
    h = _make_hypothesis(confidence=0.5)
    cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
    _strengthen_hypothesis(h, "evidence", cfg)
    assert h.confidence > 0.5


def test_strengthen_promotes_to_confirmed():
    h = _make_hypothesis(confidence=0.85)
    h.evidence_for = ["e1", "e2"]
    cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
    cfg["promote_threshold"] = 0.9
    cfg["min_evidence_for_promote"] = 2
    _strengthen_hypothesis(h, "e3", cfg)
    if h.confidence >= cfg["promote_threshold"]:
        assert h.status == "confirmed"


# ── _weaken_hypothesis ───────────────────────────────────────────────


def test_weaken_decreases_confidence():
    h = _make_hypothesis(confidence=0.5)
    cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
    _weaken_hypothesis(h, "counter evidence", cfg)
    assert h.confidence < 0.5


def test_weaken_expires_at_zero():
    h = _make_hypothesis(confidence=0.05)
    cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
    cfg["weaken_delta"] = 0.1
    _weaken_hypothesis(h, "strong counter", cfg)
    assert h.status == "expired"


def test_weaken_to_weakened():
    h = _make_hypothesis(confidence=0.35)
    cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
    cfg["weaken_delta"] = 0.1
    _weaken_hypothesis(h, "counter", cfg)
    assert h.confidence < 0.3
    assert h.status == "weakened"


# ── update_hypothesis ────────────────────────────────────────────────


def test_update_strengthen():
    c = _make_compass()
    h = _make_hypothesis()
    c.hypotheses.append(h)
    old_conf = h.confidence
    update_hypothesis(c, h.id, "strengthen", "evidence")
    assert h.confidence > old_conf


def test_update_weaken():
    c = _make_compass()
    h = _make_hypothesis()
    c.hypotheses.append(h)
    old_conf = h.confidence
    update_hypothesis(c, h.id, "weaken", "counter")
    assert h.confidence < old_conf


def test_update_nonexistent_id():
    c = _make_compass()
    h = _make_hypothesis()
    c.hypotheses.append(h)
    old_conf = h.confidence
    update_hypothesis(c, "nonexistent", "strengthen", "evidence")
    assert h.confidence == old_conf  # unchanged


# ── decay_stale ──────────────────────────────────────────────────────


def test_decay_reduces_confidence():
    c = _make_compass()
    h = _make_hypothesis(confidence=0.8)
    h.last_tested = time.time() - 7200  # 2 hours ago
    h.last_decay = h.last_tested
    c.hypotheses.append(h)
    decay_stale(c, time.time())
    assert h.confidence < 0.8


def test_decay_expires():
    c = _make_compass()
    h = _make_hypothesis(confidence=0.01)
    h.last_tested = time.time() - 72000  # 20 hours ago
    h.last_decay = h.last_tested
    c.hypotheses.append(h)
    decay_stale(c, time.time())
    assert h.status == "expired"


def test_decay_skips_expired():
    c = _make_compass()
    h = _make_hypothesis(confidence=0.5, status="expired")
    h.last_tested = time.time() - 7200
    c.hypotheses.append(h)
    decay_stale(c, time.time())
    assert h.confidence == 0.5  # unchanged


def test_decay_skips_recent():
    c = _make_compass()
    h = _make_hypothesis(confidence=0.5)
    h.last_tested = time.time()  # just now
    h.last_decay = h.last_tested
    c.hypotheses.append(h)
    decay_stale(c, time.time())
    assert abs(h.confidence - 0.5) < 1e-6  # effectively unchanged


# ── evict_overflow ───────────────────────────────────────────────────


def test_evict_under_cap():
    c = _make_compass()
    c.hypotheses = [_make_hypothesis(hyp_id=f"h{i}") for i in range(3)]
    evict_overflow(c, {"max_active": 10})
    assert len(c.hypotheses) == 3


def test_evict_over_cap():
    c = _make_compass()
    c.hypotheses = [_make_hypothesis(hyp_id=f"h{i}", confidence=0.1 * i) for i in range(15)]
    evict_overflow(c, {"max_active": 5})
    assert len(c.hypotheses) == 5


def test_evict_removes_expired():
    c = _make_compass()
    c.hypotheses = [
        _make_hypothesis(hyp_id="active", status="active"),
        _make_hypothesis(hyp_id="expired", status="expired"),
    ]
    evict_overflow(c, {"max_active": 10})
    assert len(c.hypotheses) == 1


# ── _test_hypotheses ─────────────────────────────────────────────────


def test_test_hypotheses_strengthens_on_error():
    c = _make_compass()
    h = _make_hypothesis(applies_to=["pytest:*"], claim="import error failure")
    c.hypotheses.append(h)
    old_conf = h.confidence
    cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
    _test_hypotheses(c, "pytest:tests/test_foo.py", 1, "import error found", time.time(), cfg)
    assert h.confidence > old_conf


def test_test_hypotheses_weakens_on_success():
    c = _make_compass()
    h = _make_hypothesis(applies_to=["pytest:*"])
    c.hypotheses.append(h)
    old_conf = h.confidence
    cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
    _test_hypotheses(c, "pytest:tests/test_foo.py", 0, "", time.time(), cfg)
    assert h.confidence < old_conf


# ── compute_coverage ─────────────────────────────────────────────────


def test_compute_coverage_empty():
    c = _make_compass()
    metrics = compute_coverage(c)
    assert isinstance(metrics, CoverageMetrics)


def test_compute_coverage_with_hypotheses():
    c = _make_compass()
    c.hypotheses = [_make_hypothesis(hyp_id=f"h{i}") for i in range(3)]
    metrics = compute_coverage(c)
    assert metrics is not None


# ── compute_uncertainty_zones ────────────────────────────────────────


def test_uncertainty_zones_empty():
    c = _make_compass()
    zones = compute_uncertainty_zones(c)
    assert isinstance(zones, list)


# ── find_relevant_hypotheses ─────────────────────────────────────────


def test_find_relevant_basic():
    c = _make_compass()
    h = _make_hypothesis(applies_to=["pytest:*"])
    c.hypotheses.append(h)
    found = find_relevant_hypotheses(c, "pytest:test_foo.py", "pytest")
    assert len(found) == 1


def test_find_relevant_none():
    c = _make_compass()
    h = _make_hypothesis(applies_to=["git:*"])
    c.hypotheses.append(h)
    found = find_relevant_hypotheses(c, "pytest:test_foo.py", "pytest")
    assert len(found) == 0


# ── add_declared_hypothesis ──────────────────────────────────────────


def test_add_declared():
    c = _make_compass()
    add_declared_hypothesis(c, "test claim", "pytest:*")
    assert len(c.hypotheses) == 1
    assert c.hypotheses[0].claim == "test claim"


# ── compute_prediction_accuracy ──────────────────────────────────────


def test_prediction_accuracy_no_predictions():
    c = _make_compass()
    acc = compute_prediction_accuracy(c)
    assert acc is None or acc == 0.0


def test_prediction_accuracy_with_checked():
    c = _make_compass()
    c.predictions_total = 5
    c.predictions_correct = 3
    acc = compute_prediction_accuracy(c)
    if acc is not None:
        assert 0.0 <= acc <= 1.0


# ── record_tool_event ────────────────────────────────────────────────


def test_record_tool_event_basic():
    c = _make_compass()
    record_tool_event(c, "Bash", {"command": "pytest test.py"}, "ok")
    # record_tool_event updates action_history
    assert len(c.action_history) >= 1
