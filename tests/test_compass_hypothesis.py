"""Tests for lintgate.controlplane.behavior.compass_hypothesis.

Covers hypothesis matching, strengthening/weakening, decay, eviction,
coverage computation, uncertainty zone detection, precheck support,
and edge cases (empty input, boundary values).
"""

from __future__ import annotations

import hashlib

from lintgate.controlplane.behavior.compass_hypothesis import (
    _find_conflicting_hypotheses,
    _find_low_confidence_hypotheses,
    _find_uncovered_approaches,
    _hypothesis_matches_sig,
    _strengthen_hypothesis,
    _test_hypotheses,
    _weaken_hypothesis,
    add_declared_hypothesis,
    compute_coverage,
    compute_uncertainty_zones,
    decay_stale,
    evict_overflow,
    find_relevant_hypotheses,
    update_hypothesis,
)
from lintgate.controlplane.behavior.types import (
    DEFAULT_HYPOTHESIS_CONFIG,
    MAX_EVIDENCE_ITEMS,
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    make_hypothesis_id,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_hyp(
    *,
    claim: str = "test claim",
    confidence: float = 0.5,
    status: str = "active",
    source: str = "command_failure",
    applies_to_sigs: list[str] | None = None,
    applies_to_tools: list[str] | None = None,
    evidence_for: list[str] | None = None,
    evidence_against: list[str] | None = None,
    created_at: float = 1000.0,
    last_tested: float = 1000.0,
    last_decay: float = 1000.0,
) -> BehaviorHypothesis:
    sig = applies_to_sigs[0] if applies_to_sigs else "unknown:unknown"
    return BehaviorHypothesis(
        id=make_hypothesis_id(claim, sig),
        claim=claim,
        confidence=confidence,
        evidence_for=evidence_for or [],
        evidence_against=evidence_against or [],
        created_at=created_at,
        last_tested=last_tested,
        last_decay=last_decay,
        source=source,
        status=status,
        applies_to_sigs=applies_to_sigs or [],
        applies_to_tools=applies_to_tools or [],
    )


def _fresh_compass(**kwargs) -> BehaviorCompass:
    return BehaviorCompass(**kwargs)


# ── _hypothesis_matches_sig ──────────────────────────────────────────────


class TestHypothesisMatchesSig:
    def test_exact_match(self):
        hyp = _make_hyp(applies_to_sigs=["pytest:run"])
        assert _hypothesis_matches_sig(hyp, "pytest:run", "pytest") is True

    def test_wildcard_match(self):
        hyp = _make_hyp(applies_to_sigs=["pytest:*"])
        assert _hypothesis_matches_sig(hyp, "pytest:test", "pytest") is True

    def test_wildcard_mismatch_binary(self):
        hyp = _make_hyp(applies_to_sigs=["ruff:*"])
        assert _hypothesis_matches_sig(hyp, "pytest:run", "pytest") is False

    def test_no_match_different_sig(self):
        hyp = _make_hyp(applies_to_sigs=["git:commit"])
        assert _hypothesis_matches_sig(hyp, "git:push", "git") is False

    def test_empty_applies_to_sigs(self):
        hyp = _make_hyp(applies_to_sigs=[])
        assert _hypothesis_matches_sig(hyp, "pytest:run", "pytest") is False

    def test_multiple_sigs_first_matches(self):
        hyp = _make_hyp(applies_to_sigs=["pytest:run", "ruff:check"])
        assert _hypothesis_matches_sig(hyp, "pytest:run", "pytest") is True

    def test_multiple_sigs_second_matches(self):
        hyp = _make_hyp(applies_to_sigs=["ruff:check", "pytest:run"])
        assert _hypothesis_matches_sig(hyp, "pytest:run", "pytest") is True

    def test_wildcard_pattern_does_not_match_partial(self):
        """Wildcard 'py:*' should not match binary 'pytest'."""
        hyp = _make_hyp(applies_to_sigs=["py:*"])
        assert _hypothesis_matches_sig(hyp, "pytest:run", "pytest") is False

    def test_sig_without_colon(self):
        """When command_sig has no colon, binary equals the full sig."""
        hyp = _make_hyp(applies_to_sigs=["ls"])
        assert _hypothesis_matches_sig(hyp, "ls", "ls") is True


# ── _strengthen_hypothesis ───────────────────────────────────────────────


class TestStrengthenHypothesis:
    def test_increases_confidence(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.3)
        _strengthen_hypothesis(hyp, "evidence1", cfg)
        assert hyp.confidence == 0.3 + cfg["strengthen_delta"]

    def test_caps_at_1(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.95)
        _strengthen_hypothesis(hyp, "ev", cfg)
        assert hyp.confidence == 1.0

    def test_appends_evidence(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(evidence_for=["old"])
        _strengthen_hypothesis(hyp, "new", cfg)
        assert hyp.evidence_for == ["old", "new"]

    def test_evidence_capped_at_max(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(evidence_for=[f"e{i}" for i in range(MAX_EVIDENCE_ITEMS)])
        _strengthen_hypothesis(hyp, "overflow", cfg)
        assert len(hyp.evidence_for) == MAX_EVIDENCE_ITEMS
        assert hyp.evidence_for[-1] == "overflow"
        # First item should have been evicted
        assert hyp.evidence_for[0] == "e1"

    def test_promotes_when_threshold_met(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        # Need confidence >= promote_threshold AND enough evidence
        promote_threshold = cfg["promote_threshold"]
        min_evidence = cfg["min_evidence_for_promote"]
        starting_conf = promote_threshold - cfg["strengthen_delta"] + 0.01
        hyp = _make_hyp(
            confidence=starting_conf,
            evidence_for=[f"e{i}" for i in range(min_evidence - 1)],
        )
        _strengthen_hypothesis(hyp, "final_evidence", cfg)
        assert hyp.confidence >= promote_threshold
        assert len(hyp.evidence_for) >= min_evidence
        assert hyp.status == "confirmed"

    def test_no_promote_insufficient_evidence(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        promote_threshold = cfg["promote_threshold"]
        hyp = _make_hyp(confidence=promote_threshold, evidence_for=[])
        _strengthen_hypothesis(hyp, "only_one", cfg)
        # Only 1 evidence, min_evidence_for_promote=2 by default
        assert hyp.status == "active"


# ── _weaken_hypothesis ───────────────────────────────────────────────────


class TestWeakenHypothesis:
    def test_decreases_confidence(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.5)
        _weaken_hypothesis(hyp, "counter", cfg)
        assert hyp.confidence == 0.5 - cfg["weaken_delta"]

    def test_floors_at_0(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.05)
        _weaken_hypothesis(hyp, "counter", cfg)
        assert hyp.confidence == 0.0

    def test_appends_evidence_against(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(evidence_against=["a"])
        _weaken_hypothesis(hyp, "b", cfg)
        assert hyp.evidence_against == ["a", "b"]

    def test_evidence_against_capped(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(evidence_against=[f"e{i}" for i in range(MAX_EVIDENCE_ITEMS)])
        _weaken_hypothesis(hyp, "overflow", cfg)
        assert len(hyp.evidence_against) == MAX_EVIDENCE_ITEMS
        assert hyp.evidence_against[-1] == "overflow"

    def test_expires_at_zero(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.05)
        _weaken_hypothesis(hyp, "ev", cfg)
        assert hyp.status == "expired"

    def test_weakened_below_03(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.35)
        _weaken_hypothesis(hyp, "ev", cfg)
        expected = 0.35 - cfg["weaken_delta"]
        assert hyp.confidence == expected
        assert hyp.status == "weakened"

    def test_no_status_change_above_03(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.5)
        _weaken_hypothesis(hyp, "ev", cfg)
        assert hyp.status == "active"


# ── _test_hypotheses ─────────────────────────────────────────────────────


class TestTestHypotheses:
    def test_strengthens_on_error(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.3, applies_to_sigs=["git:push"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(
            compass, "git:push", exit_code=1, error_sig="remote rejected", now=2000.0, cfg=cfg
        )
        assert hyp.confidence == 0.3 + cfg["strengthen_delta"]
        assert hyp.last_tested == 2000.0

    def test_weakens_on_success(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.5, applies_to_sigs=["git:push"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(compass, "git:push", exit_code=0, error_sig="", now=2000.0, cfg=cfg)
        assert hyp.confidence == 0.5 - cfg["weaken_delta"]

    def test_skips_expired(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.0, status="expired", applies_to_sigs=["git:push"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(compass, "git:push", exit_code=1, error_sig="err", now=2000.0, cfg=cfg)
        assert hyp.confidence == 0.0  # Unchanged

    def test_skips_unmatched_sig(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.5, applies_to_sigs=["ruff:check"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(compass, "git:push", exit_code=1, error_sig="err", now=2000.0, cfg=cfg)
        assert hyp.confidence == 0.5  # Unchanged

    def test_no_change_on_none_exit_code(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.5, applies_to_sigs=["git:push"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(compass, "git:push", exit_code=None, error_sig="", now=2000.0, cfg=cfg)
        # last_tested updated, but confidence unchanged
        assert hyp.confidence == 0.5
        assert hyp.last_tested == 2000.0

    def test_no_strengthen_on_error_without_error_sig(self):
        """Non-zero exit with empty error_sig should not strengthen."""
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.5, applies_to_sigs=["git:push"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(compass, "git:push", exit_code=1, error_sig="", now=2000.0, cfg=cfg)
        assert hyp.confidence == 0.5

    def test_wildcard_sig_matching(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.3, applies_to_sigs=["git:*"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(
            compass, "git:push", exit_code=1, error_sig="rejected", now=2000.0, cfg=cfg
        )
        assert hyp.confidence == 0.3 + cfg["strengthen_delta"]

    def test_binary_extraction_no_colon(self):
        """command_sig without colon uses itself as binary."""
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG)
        hyp = _make_hyp(confidence=0.5, applies_to_sigs=["ls"])
        compass = _fresh_compass(hypotheses=[hyp])
        _test_hypotheses(compass, "ls", exit_code=0, error_sig="", now=2000.0, cfg=cfg)
        assert hyp.confidence == 0.5 - cfg["weaken_delta"]


# ── update_hypothesis ────────────────────────────────────────────────────


class TestUpdateHypothesis:
    def test_strengthen_by_id(self):
        hyp = _make_hyp(confidence=0.4)
        compass = _fresh_compass(hypotheses=[hyp])
        update_hypothesis(compass, hyp.id, "strengthen", "new evidence", now=5000.0)
        assert hyp.confidence == 0.4 + DEFAULT_HYPOTHESIS_CONFIG["strengthen_delta"]
        assert hyp.last_tested == 5000.0
        assert hyp.last_decay == 5000.0
        assert compass.hypothesis_version == 1

    def test_weaken_by_id(self):
        hyp = _make_hyp(confidence=0.4)
        compass = _fresh_compass(hypotheses=[hyp])
        update_hypothesis(compass, hyp.id, "weaken", "counter evidence", now=5000.0)
        assert hyp.confidence == 0.4 - DEFAULT_HYPOTHESIS_CONFIG["weaken_delta"]
        assert compass.hypothesis_version == 1

    def test_unknown_id_no_crash(self):
        compass = _fresh_compass(hypotheses=[])
        # Should not raise
        update_hypothesis(compass, "nonexistent", "strengthen", "ev", now=5000.0)
        assert compass.hypothesis_version == 0

    def test_unknown_direction_no_crash(self):
        hyp = _make_hyp(confidence=0.5)
        compass = _fresh_compass(hypotheses=[hyp])
        update_hypothesis(compass, hyp.id, "invaliddir", "ev", now=5000.0)
        # Confidence unchanged, but version incremented and timestamps updated
        assert hyp.confidence == 0.5
        assert compass.hypothesis_version == 1

    def test_custom_cfg(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, strengthen_delta=0.5)
        hyp = _make_hyp(confidence=0.3)
        compass = _fresh_compass(hypotheses=[hyp])
        update_hypothesis(compass, hyp.id, "strengthen", "big boost", now=5000.0, cfg=cfg)
        assert hyp.confidence == 0.8


# ── decay_stale ──────────────────────────────────────────────────────────


class TestDecayStale:
    def test_decays_after_one_hour(self):
        hyp = _make_hyp(confidence=0.5, last_tested=1000.0, last_decay=1000.0)
        compass = _fresh_compass(hypotheses=[hyp])
        now = 1000.0 + 3600.0  # 1 hour later
        decay_stale(compass, now)
        expected = 0.5 - DEFAULT_HYPOTHESIS_CONFIG["decay_per_hour"]
        assert abs(hyp.confidence - expected) < 1e-9
        assert hyp.last_decay == now

    def test_decays_proportionally(self):
        hyp = _make_hyp(confidence=0.5, last_tested=1000.0, last_decay=1000.0)
        compass = _fresh_compass(hypotheses=[hyp])
        now = 1000.0 + 7200.0  # 2 hours later
        decay_stale(compass, now)
        expected = 0.5 - 2 * DEFAULT_HYPOTHESIS_CONFIG["decay_per_hour"]
        assert abs(hyp.confidence - expected) < 1e-9

    def test_no_decay_if_now_equals_anchor(self):
        hyp = _make_hyp(confidence=0.5, last_tested=1000.0, last_decay=1000.0)
        compass = _fresh_compass(hypotheses=[hyp])
        decay_stale(compass, 1000.0)
        assert hyp.confidence == 0.5

    def test_no_decay_before_anchor(self):
        hyp = _make_hyp(confidence=0.5, last_tested=1000.0, last_decay=1000.0)
        compass = _fresh_compass(hypotheses=[hyp])
        decay_stale(compass, 500.0)
        assert hyp.confidence == 0.5

    def test_skips_expired(self):
        hyp = _make_hyp(confidence=0.0, status="expired", last_tested=1000.0, last_decay=1000.0)
        compass = _fresh_compass(hypotheses=[hyp])
        decay_stale(compass, 1000.0 + 3600.0)
        assert hyp.confidence == 0.0

    def test_expires_on_decay_to_zero(self):
        decay_rate = DEFAULT_HYPOTHESIS_CONFIG["decay_per_hour"]
        # Set confidence so exactly 1 hour of decay brings it to 0
        hyp = _make_hyp(confidence=decay_rate, last_tested=1000.0, last_decay=1000.0)
        compass = _fresh_compass(hypotheses=[hyp])
        decay_stale(compass, 1000.0 + 3600.0)
        assert hyp.confidence == 0.0
        assert hyp.status == "expired"
        assert compass.hypothesis_version == 1

    def test_uses_max_of_last_tested_and_last_decay_as_anchor(self):
        """Decay anchor is max(last_tested, last_decay)."""
        hyp = _make_hyp(confidence=0.5, last_tested=2000.0, last_decay=3000.0)
        compass = _fresh_compass(hypotheses=[hyp])
        # Anchor should be 3000.0
        now = 3000.0 + 3600.0
        decay_stale(compass, now)
        expected = 0.5 - DEFAULT_HYPOTHESIS_CONFIG["decay_per_hour"]
        assert abs(hyp.confidence - expected) < 1e-9

    def test_fallback_when_decay_anchor_is_zero(self):
        """When both are zero (anchor <= 0), fallback to last_tested."""
        hyp = _make_hyp(confidence=0.5, last_tested=0.0, last_decay=0.0)
        compass = _fresh_compass(hypotheses=[hyp])
        # max(0, 0) = 0, which triggers fallback to last_tested = 0
        # hours_stale = (3600 - 0) / 3600 = 1.0
        now = 3600.0
        decay_stale(compass, now)
        expected = 0.5 - DEFAULT_HYPOTHESIS_CONFIG["decay_per_hour"]
        assert abs(hyp.confidence - expected) < 1e-9

    def test_default_cfg_used_when_none(self):
        hyp = _make_hyp(confidence=0.5, last_tested=0.0, last_decay=0.0)
        compass = _fresh_compass(hypotheses=[hyp])
        decay_stale(compass, 3600.0, cfg=None)
        expected = 0.5 - DEFAULT_HYPOTHESIS_CONFIG["decay_per_hour"]
        assert abs(hyp.confidence - expected) < 1e-9


# ── evict_overflow ───────────────────────────────────────────────────────


class TestEvictOverflow:
    def test_no_eviction_under_cap(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, max_active=5)
        hyps = [_make_hyp(claim=f"c{i}", confidence=0.5) for i in range(3)]
        compass = _fresh_compass(hypotheses=hyps)
        evict_overflow(compass, cfg)
        assert len(compass.hypotheses) == 3

    def test_no_eviction_at_cap(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, max_active=3)
        hyps = [_make_hyp(claim=f"c{i}", confidence=0.5) for i in range(3)]
        compass = _fresh_compass(hypotheses=hyps)
        evict_overflow(compass, cfg)
        assert len(compass.hypotheses) == 3

    def test_evicts_lowest_confidence(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, max_active=2)
        low = _make_hyp(claim="low", confidence=0.1, created_at=100.0)
        mid = _make_hyp(claim="mid", confidence=0.5, created_at=100.0)
        high = _make_hyp(claim="high", confidence=0.9, created_at=100.0)
        compass = _fresh_compass(hypotheses=[low, mid, high])
        evict_overflow(compass, cfg)
        assert len(compass.hypotheses) == 2
        claims = {h.claim for h in compass.hypotheses}
        assert "low" not in claims
        assert "mid" in claims
        assert "high" in claims

    def test_evicts_expired_first(self):
        """Expired hypotheses are removed before the cap check."""
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, max_active=2)
        exp = _make_hyp(claim="expired", confidence=0.0, status="expired")
        a = _make_hyp(claim="a", confidence=0.5)
        b = _make_hyp(claim="b", confidence=0.6)
        compass = _fresh_compass(hypotheses=[exp, a, b])
        evict_overflow(compass, cfg)
        assert len(compass.hypotheses) == 2
        assert all(h.status != "expired" for h in compass.hypotheses)

    def test_tiebreak_by_created_at(self):
        """When confidence ties, older hypotheses (lower created_at) are evicted."""
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, max_active=1)
        old = _make_hyp(claim="old", confidence=0.5, created_at=100.0)
        new = _make_hyp(claim="new", confidence=0.5, created_at=200.0)
        compass = _fresh_compass(hypotheses=[old, new])
        evict_overflow(compass, cfg)
        assert len(compass.hypotheses) == 1
        assert compass.hypotheses[0].claim == "new"

    def test_empty_hypotheses(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, max_active=5)
        compass = _fresh_compass(hypotheses=[])
        evict_overflow(compass, cfg)
        assert compass.hypotheses == []


# ── compute_coverage ─────────────────────────────────────────────────────


class TestComputeCoverage:
    def test_empty_compass(self):
        compass = _fresh_compass()
        cov = compute_coverage(compass)
        assert cov.constraints_verified == 0
        assert cov.constraints_predicted == 0
        assert cov.approaches_attempted == 0
        assert cov.approach_success_rate == 0.0
        assert cov.bash_count_recent == 0
        assert cov.read_count_recent == 0
        assert cov.prediction_recall == 0.0

    def test_counts_verified_above_promote(self):
        threshold = DEFAULT_HYPOTHESIS_CONFIG["promote_threshold"]
        above = _make_hyp(confidence=threshold, status="active")
        below = _make_hyp(claim="below", confidence=threshold - 0.1, status="active")
        compass = _fresh_compass(hypotheses=[above, below])
        cov = compute_coverage(compass)
        assert cov.constraints_verified == 1

    def test_counts_predicted_from_precheck(self):
        threshold = DEFAULT_HYPOTHESIS_CONFIG["promote_threshold"]
        precheck = _make_hyp(confidence=threshold, source="precheck_declared", status="active")
        auto = _make_hyp(
            claim="auto", confidence=threshold, source="command_failure", status="active"
        )
        compass = _fresh_compass(hypotheses=[precheck, auto])
        cov = compute_coverage(compass)
        assert cov.constraints_predicted == 1

    def test_skips_expired(self):
        threshold = DEFAULT_HYPOTHESIS_CONFIG["promote_threshold"]
        expired = _make_hyp(confidence=threshold, status="expired")
        compass = _fresh_compass(hypotheses=[expired])
        cov = compute_coverage(compass)
        assert cov.constraints_verified == 0

    def test_approach_success_rate(self):
        approaches = [
            ApproachAttempt(approach_sig="a", outcome="success"),
            ApproachAttempt(approach_sig="b", outcome="failed"),
            ApproachAttempt(approach_sig="c", outcome="success"),
        ]
        compass = _fresh_compass(approaches=approaches)
        cov = compute_coverage(compass)
        assert cov.approaches_attempted == 3
        assert abs(cov.approach_success_rate - 2 / 3) < 1e-9

    def test_bash_and_read_counts(self):
        history = [
            {"tool": "Bash"},
            {"tool": "Read"},
            {"tool": "Bash"},
            {"tool": "Grep"},
            {"tool": "Glob"},
            {"tool": "Edit"},
        ]
        compass = _fresh_compass(action_history=history)
        cov = compute_coverage(compass)
        assert cov.bash_count_recent == 2
        assert cov.read_count_recent == 3  # Read + Grep + Glob

    def test_uses_last_10_history_entries(self):
        history = [{"tool": "Bash"} for _ in range(15)]
        compass = _fresh_compass(action_history=history)
        cov = compute_coverage(compass)
        assert cov.bash_count_recent == 10  # Only last 10

    def test_prediction_recall(self):
        threshold = DEFAULT_HYPOTHESIS_CONFIG["promote_threshold"]
        predicted = _make_hyp(
            claim="predicted", confidence=threshold, source="precheck_declared", status="active"
        )
        surprise = _make_hyp(
            claim="surprise", confidence=0.6, source="command_failure", status="active"
        )
        compass = _fresh_compass(hypotheses=[predicted, surprise])
        cov = compute_coverage(compass)
        # predicted=1, surprise=1, total_discovered=2, recall=0.5
        assert abs(cov.prediction_recall - 0.5) < 1e-9

    def test_recall_zero_when_no_discoveries(self):
        compass = _fresh_compass(hypotheses=[])
        cov = compute_coverage(compass)
        assert cov.prediction_recall == 0.0


# ── Uncertainty zone helpers ─────────────────────────────────────────────


class TestFindUncoveredApproaches:
    def test_empty(self):
        compass = _fresh_compass()
        assert _find_uncovered_approaches(compass) == []

    def test_failed_approach_no_hypothesis(self):
        approach = ApproachAttempt(
            approach_sig="git:push",
            outcome="failed",
            error_sigs=["remote rejected"],
        )
        compass = _fresh_compass(approaches=[approach])
        zones = _find_uncovered_approaches(compass)
        assert len(zones) == 1
        assert "git:push" in zones[0]

    def test_failed_approach_with_covering_hypothesis(self):
        approach = ApproachAttempt(
            approach_sig="git:push",
            outcome="failed",
            error_sigs=["remote rejected"],
        )
        hyp = _make_hyp(applies_to_sigs=["git:push"], status="active")
        compass = _fresh_compass(approaches=[approach], hypotheses=[hyp])
        zones = _find_uncovered_approaches(compass)
        assert zones == []

    def test_failed_approach_with_expired_hypothesis(self):
        """Expired hypothesis does not cover an approach."""
        approach = ApproachAttempt(
            approach_sig="git:push",
            outcome="failed",
            error_sigs=["err"],
        )
        hyp = _make_hyp(applies_to_sigs=["git:push"], status="expired")
        compass = _fresh_compass(approaches=[approach], hypotheses=[hyp])
        zones = _find_uncovered_approaches(compass)
        assert len(zones) == 1

    def test_skips_successful_approach(self):
        approach = ApproachAttempt(approach_sig="git:push", outcome="success")
        compass = _fresh_compass(approaches=[approach])
        zones = _find_uncovered_approaches(compass)
        assert zones == []

    def test_failed_with_no_error_sigs(self):
        """Failed approach with empty error_sigs should not appear."""
        approach = ApproachAttempt(
            approach_sig="git:push",
            outcome="failed",
            error_sigs=[],
        )
        compass = _fresh_compass(approaches=[approach])
        zones = _find_uncovered_approaches(compass)
        assert zones == []


class TestFindLowConfidenceHypotheses:
    def test_empty(self):
        compass = _fresh_compass()
        assert _find_low_confidence_hypotheses(compass) == []

    def test_finds_low_confidence(self):
        hyp = _make_hyp(claim="risky claim", confidence=0.2)
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_low_confidence_hypotheses(compass)
        assert len(zones) == 1
        assert "risky claim" in zones[0]
        assert "0.20" in zones[0]

    def test_skips_zero_confidence(self):
        """Confidence exactly 0.0 is excluded (not 0.0 < conf)."""
        hyp = _make_hyp(confidence=0.0)
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_low_confidence_hypotheses(compass)
        assert zones == []

    def test_skips_expired(self):
        hyp = _make_hyp(confidence=0.2, status="expired")
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_low_confidence_hypotheses(compass)
        assert zones == []

    def test_skips_at_04(self):
        """Confidence 0.4 is NOT below 0.4, so not included."""
        hyp = _make_hyp(confidence=0.4)
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_low_confidence_hypotheses(compass)
        assert zones == []

    def test_includes_just_below_04(self):
        hyp = _make_hyp(claim="borderline", confidence=0.39)
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_low_confidence_hypotheses(compass)
        assert len(zones) == 1


class TestFindConflictingHypotheses:
    def test_empty(self):
        compass = _fresh_compass()
        assert _find_conflicting_hypotheses(compass) == []

    def test_finds_conflicting(self):
        hyp = _make_hyp(
            claim="conflicted",
            evidence_for=["a"],
            evidence_against=["b"],
        )
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_conflicting_hypotheses(compass)
        assert len(zones) == 1
        assert "1 for" in zones[0]
        assert "1 against" in zones[0]

    def test_no_conflict_one_side_empty(self):
        hyp = _make_hyp(evidence_for=["a"], evidence_against=[])
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_conflicting_hypotheses(compass)
        assert zones == []

    def test_skips_expired(self):
        hyp = _make_hyp(evidence_for=["a"], evidence_against=["b"], status="expired")
        compass = _fresh_compass(hypotheses=[hyp])
        zones = _find_conflicting_hypotheses(compass)
        assert zones == []


class TestComputeUncertaintyZones:
    def test_empty_compass(self):
        compass = _fresh_compass()
        assert compute_uncertainty_zones(compass) == []

    def test_capped_at_5(self):
        """Even with many issues, zones are capped at 5."""
        hyps = [
            _make_hyp(
                claim=f"low{i}",
                confidence=0.1 + i * 0.01,
                evidence_for=["a"],
                evidence_against=["b"],
            )
            for i in range(10)
        ]
        approaches = [
            ApproachAttempt(approach_sig=f"cmd{i}:run", outcome="failed", error_sigs=["err"])
            for i in range(10)
        ]
        compass = _fresh_compass(hypotheses=hyps, approaches=approaches)
        zones = compute_uncertainty_zones(compass)
        assert len(zones) == 5

    def test_combines_all_three_types(self):
        # Uncovered approach
        approach = ApproachAttempt(approach_sig="cmd:run", outcome="failed", error_sigs=["err"])
        # Low confidence
        low = _make_hyp(claim="low conf", confidence=0.1)
        # Conflicting
        conflict = _make_hyp(
            claim="conflict",
            confidence=0.5,
            evidence_for=["a"],
            evidence_against=["b"],
        )
        compass = _fresh_compass(hypotheses=[low, conflict], approaches=[approach])
        zones = compute_uncertainty_zones(compass)
        assert len(zones) == 3


# ── find_relevant_hypotheses ─────────────────────────────────────────────


class TestFindRelevantHypotheses:
    def test_no_filters_returns_all_non_expired(self):
        active = _make_hyp(claim="active", status="active")
        expired = _make_hyp(claim="expired", status="expired")
        compass = _fresh_compass(hypotheses=[active, expired])
        results = find_relevant_hypotheses(compass)
        assert len(results) == 1
        assert results[0].claim == "active"

    def test_filter_by_command_sig(self):
        matching = _make_hyp(claim="match", applies_to_sigs=["pytest:run"])
        other = _make_hyp(claim="other", applies_to_sigs=["ruff:check"])
        compass = _fresh_compass(hypotheses=[matching, other])
        results = find_relevant_hypotheses(compass, command_sig="pytest:run")
        assert len(results) == 1
        assert results[0].claim == "match"

    def test_filter_by_tool(self):
        bash_hyp = _make_hyp(claim="bash", applies_to_tools=["Bash"])
        read_hyp = _make_hyp(claim="read", applies_to_tools=["Read"])
        compass = _fresh_compass(hypotheses=[bash_hyp, read_hyp])
        results = find_relevant_hypotheses(compass, tool="Bash")
        assert len(results) == 1
        assert results[0].claim == "bash"

    def test_command_sig_takes_priority_over_tool(self):
        """If command_sig matches, tool filter is not needed (but both can match)."""
        hyp = _make_hyp(
            claim="both",
            applies_to_sigs=["pytest:run"],
            applies_to_tools=["Bash"],
        )
        compass = _fresh_compass(hypotheses=[hyp])
        results = find_relevant_hypotheses(compass, command_sig="pytest:run", tool="Read")
        assert len(results) == 1

    def test_tool_fallback_when_sig_doesnt_match(self):
        hyp = _make_hyp(
            claim="tool_match",
            applies_to_sigs=["ruff:check"],
            applies_to_tools=["Bash"],
        )
        compass = _fresh_compass(hypotheses=[hyp])
        results = find_relevant_hypotheses(compass, command_sig="pytest:run", tool="Bash")
        assert len(results) == 1

    def test_no_match(self):
        hyp = _make_hyp(
            claim="unrelated",
            applies_to_sigs=["ruff:check"],
            applies_to_tools=["Read"],
        )
        compass = _fresh_compass(hypotheses=[hyp])
        results = find_relevant_hypotheses(compass, command_sig="pytest:run", tool="Bash")
        assert results == []

    def test_command_sig_without_colon(self):
        """When command_sig has no colon, binary is empty string."""
        hyp = _make_hyp(applies_to_sigs=["ls"], applies_to_tools=[])
        compass = _fresh_compass(hypotheses=[hyp])
        # sig "ls" has no colon => binary=""
        # Exact match "ls" == "ls" should still work
        results = find_relevant_hypotheses(compass, command_sig="ls")
        assert len(results) == 1

    def test_none_command_sig_none_tool(self):
        """Both None returns all non-expired."""
        h1 = _make_hyp(claim="a")
        h2 = _make_hyp(claim="b")
        compass = _fresh_compass(hypotheses=[h1, h2])
        results = find_relevant_hypotheses(compass, command_sig=None, tool=None)
        assert len(results) == 2


# ── add_declared_hypothesis ──────────────────────────────────────────────


class TestAddDeclaredHypothesis:
    def test_creates_new_hypothesis(self):
        compass = _fresh_compass()
        hyp = add_declared_hypothesis(compass, "test will fail", "pytest:run", now=1000.0)
        assert hyp.claim == "test will fail"
        assert hyp.confidence == 0.5
        assert hyp.source == "precheck_declared"
        assert hyp.applies_to_sigs == ["pytest:*"]
        assert hyp.applies_to_tools == ["Bash"]
        assert hyp.trust_score == 0.7
        assert len(compass.hypotheses) == 1
        assert "Declared via precheck" in hyp.evidence_for[0]

    def test_re_declare_strengthens_existing(self):
        compass = _fresh_compass()
        hyp1 = add_declared_hypothesis(compass, "test will fail", "pytest:run", now=1000.0)
        original_conf = hyp1.confidence
        hyp2 = add_declared_hypothesis(compass, "test will fail", "pytest:run", now=2000.0)
        # Should be the same object
        assert hyp2 is hyp1
        assert hyp1.confidence == original_conf + DEFAULT_HYPOTHESIS_CONFIG["strengthen_delta"]
        # Should NOT add a duplicate
        assert len(compass.hypotheses) == 1

    def test_deterministic_id(self):
        compass = _fresh_compass()
        hyp = add_declared_hypothesis(compass, "claim", "sig:cmd", now=1000.0)
        expected_id = hashlib.sha256(b"claim|sig:cmd").hexdigest()[:8]
        assert hyp.id == expected_id

    def test_no_command_sig_defaults(self):
        compass = _fresh_compass()
        hyp = add_declared_hypothesis(compass, "claim", None, now=1000.0)
        assert hyp.applies_to_sigs == []  # "unknown" binary => empty list

    def test_command_sig_without_colon(self):
        compass = _fresh_compass()
        hyp = add_declared_hypothesis(compass, "claim", "ls", now=1000.0)
        assert hyp.applies_to_sigs == ["ls:*"]

    def test_eviction_triggered(self):
        cfg = dict(DEFAULT_HYPOTHESIS_CONFIG, max_active=2)
        compass = _fresh_compass(
            hypotheses=[
                _make_hyp(claim="old1", confidence=0.1, created_at=1.0),
                _make_hyp(claim="old2", confidence=0.2, created_at=2.0),
            ]
        )
        _hyp = add_declared_hypothesis(compass, "new", "cmd:run", now=1000.0, cfg=cfg)
        assert len(compass.hypotheses) == 2
        claims = {h.claim for h in compass.hypotheses}
        # old1 (lowest confidence) should be evicted
        assert "old1" not in claims
        assert "new" in claims

    def test_evidence_contains_decomp_summary(self):
        compass = _fresh_compass()
        hyp = add_declared_hypothesis(compass, "x", "cmd:run", now=1000.0)
        # theory_score=0.8 > 0.5, so "theory alignment" should appear in summary
        assert "theory alignment" in hyp.evidence_for[0]


# ── make_hypothesis_id ───────────────────────────────────────────────────


class TestMakeHypothesisId:
    def test_deterministic(self):
        id1 = make_hypothesis_id("claim", "sig")
        id2 = make_hypothesis_id("claim", "sig")
        assert id1 == id2

    def test_length_8(self):
        hid = make_hypothesis_id("anything", "any:sig")
        assert len(hid) == 8

    def test_different_inputs_different_ids(self):
        id1 = make_hypothesis_id("claim_a", "sig")
        id2 = make_hypothesis_id("claim_b", "sig")
        assert id1 != id2

    def test_empty_inputs(self):
        hid = make_hypothesis_id("", "")
        expected = hashlib.sha256(b"|").hexdigest()[:8]
        assert hid == expected


# ── Compass property-based roundtrip tests ───────────────────────────────


from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from lintgate.compass import (  # noqa: E402
    CompassAxis,
    CompassClaim,
    CompassDirective,
    CompassState,
    GapReport,
)


def _compass_claims():
    return st.builds(
        CompassClaim,
        text=st.text(min_size=1),
        source=st.text(),
        heading=st.text(),
        confidence=st.floats(min_value=0.0, max_value=1.0),
        provenance=st.sampled_from(["parsed", "inferred", "interviewed"]),
        origin_facet=st.text(),
    )


def _compass_axes():
    return st.builds(
        CompassAxis,
        name=st.sampled_from(["problem", "solution", "implementation", "world"]),
        claims=st.lists(_compass_claims()),
        summary=st.text(),
        depth=st.integers(min_value=0, max_value=3),
    )


def _compass_directives():
    return st.builds(
        CompassDirective,
        kind=st.sampled_from(["toward", "away", "forbidden"]),
        text=st.text(min_size=1),
        source=st.text(),
    )


def _gap_reports():
    return st.builds(
        GapReport,
        axis_depths=st.dictionaries(st.text(), st.integers(min_value=0, max_value=3)),
        spikiness=st.floats(min_value=0.0, max_value=1.0).map(lambda x: round(x, 4)),
        sparse_axes=st.lists(st.text()),
        interview_recommended=st.booleans(),
    )


def _compass_states():
    return st.builds(
        CompassState,
        version=st.integers(min_value=1),
        axes=st.dictionaries(st.text(), _compass_axes()),
        directives=st.lists(_compass_directives()),
        gap_report=_gap_reports(),
        forged_at=st.floats(min_value=0.0),
        frozen=st.booleans(),
        frozen_hash=st.text(),
    )


@given(claim=_compass_claims())
def test_compass_claim_property_roundtrip(claim):
    data = claim.to_dict()
    restored = CompassClaim.from_dict(data)
    assert restored == claim


@given(axis=_compass_axes())
def test_compass_axis_property_roundtrip(axis):
    data = axis.to_dict()
    restored = CompassAxis.from_dict(data)
    assert restored == axis


@given(directive=_compass_directives())
def test_compass_directive_property_roundtrip(directive):
    data = directive.to_dict()
    restored = CompassDirective.from_dict(data)
    assert restored == directive


@given(report=_gap_reports())
def test_gap_report_property_roundtrip(report):
    data = report.to_dict()
    restored = GapReport.from_dict(data)
    assert restored.to_dict() == data
    assert restored == report


@given(state=_compass_states())
def test_compass_state_property_roundtrip(state):
    data = state.to_dict()
    restored = CompassState.from_dict(data)
    assert restored.to_dict() == data
    assert restored == state
