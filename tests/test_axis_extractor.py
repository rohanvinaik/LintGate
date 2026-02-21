"""Tests for axis_extractor — 7-facet theory -> 4-axis compass mapping."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lintgate.axis_extractor import (
    AXIS_HEADING_SIGNALS,
    _heading_matches_axis,
    _score_claim_relevance,
    build_compass_pack,
    extract_compass,
    query_compass,
)
from lintgate.compass import (
    AXIS_NAMES,
    FACET_TO_AXIS,
    CompassAxis,
    CompassClaim,
    CompassDirective,
    CompassState,
    GapReport,
)

# ── Fixture data ────────────────────────────────────────────────────

_C = lambda t, c=0.9: {"claim": t, "confidence": c}  # noqa: E731

MOCK_THEORY_RESULT: dict = {
    "theory_profile": {
        "core_theory": {
            "claims": [
                _C("Constraints as live hypotheses"),
                _C("Disagreement between channels", 0.85),
            ]
        },
        "problem_solving": {
            "claims": [_C("Debug symptoms not causes"), _C("Approach cycling = model gap", 0.8)]
        },
        "alignment": {"claims": [_C("Hypothesis-with-confidence pattern", 0.85)]},
        "architecture": {"claims": [_C("Lossy channels provide diagnostic disagreement")]},
        "anti_patterns": {"claims": [_C("Brute-force escalation destroys understanding", 0.7)]},
        "abstractions": {"claims": [_C("CompassState is central data model", 0.75)]},
        "enforceable_rules": {"claims": [_C("Never disable lint channels globally", 0.95)]},
    },
    "anti_patterns": [{"pattern": "Brute-force escalation"}],
    "enforceable_rules": [{"pattern": "no global lint disabling", "type": "forbid"}],
}


def _claim(text: str, heading: str = "", facet: str = "") -> CompassClaim:
    return CompassClaim(
        text=text, source="test.md:1", heading=heading, confidence=0.9, origin_facet=facet
    )


def _populated_state() -> CompassState:
    """Build a CompassState with claims on each axis for query tests."""
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                depth=2,
                summary="Constraints are live hypotheses",
                claims=[
                    _claim("Constraints are live hypotheses", "Core Theory", "core_theory"),
                    _claim("Alignment requires confidence tracking", "Alignment", "alignment"),
                ],
            ),
            "solution": CompassAxis(
                name="solution",
                depth=2,
                summary="Debug symptoms not causes",
                claims=[
                    _claim(
                        "Agents debug symptoms rather than root causes",
                        "Problem Solving",
                        "problem_solving",
                    ),
                    _claim(
                        "Lossy channels provide diagnostic power", "Architecture", "architecture"
                    ),
                ],
            ),
            "implementation": CompassAxis(
                name="implementation",
                depth=1,
                summary="CompassState is central",
                claims=[
                    _claim("CompassState is the central data model", "Patterns", "abstractions"),
                ],
            ),
            "world": CompassAxis(name="world", claims=[], summary="", depth=0),
        },
        directives=[
            CompassDirective(kind="toward", text="Use hypothesis-with-confidence"),
            CompassDirective(kind="away", text="Avoid brute-force escalation"),
        ],
        gap_report=GapReport(
            axis_depths={"problem": 2, "solution": 2, "implementation": 1, "world": 0},
            spikiness=0.0,
            sparse_axes=["implementation", "world"],
        ),
        forged_at=1700000000.0,
    )


# ── 1. extract_compass with mocked extract_theory ──────────────────


@patch("lintgate.axis_extractor.extract_theory", return_value=MOCK_THEORY_RESULT)
def test_extract_compass_produces_nonempty_axes(mock_et: object) -> None:
    state = extract_compass("/fake/project")
    assert isinstance(state, CompassState)
    nonempty = [n for n in AXIS_NAMES if state.axes.get(n) and state.axes[n].claims]
    assert len(nonempty) >= 2, f"Expected >=2 non-empty axes, got {nonempty}"
    assert state.axes["problem"] is not None and len(state.axes["problem"].claims) >= 1
    assert state.axes["solution"] is not None and len(state.axes["solution"].claims) >= 1
    assert state.gap_report is not None and state.gap_report.axis_depths


@patch("lintgate.axis_extractor.extract_theory", return_value={"theory_profile": {}})
def test_extract_compass_empty_profile(mock_et: object) -> None:
    state = extract_compass("/fake/project")
    assert isinstance(state, CompassState)
    for name in AXIS_NAMES:
        assert state.axes.get(name) is not None
        assert state.axes[name].depth == 0


# ── 2. Facet mapping completeness ──────────────────────────────────


def test_facet_to_axis_maps_to_valid_axes() -> None:
    valid = set(AXIS_NAMES)
    for facet, axis in FACET_TO_AXIS.items():
        assert axis in valid, f"Facet '{facet}' -> '{axis}' not in {valid}"


def test_facet_to_axis_covers_expected_facets() -> None:
    expected = {
        "core_theory",
        "alignment",
        "problem_solving",
        "architecture",
        "anti_patterns",
        "abstractions",
        "enforceable_rules",
    }
    assert set(FACET_TO_AXIS.keys()) == expected


# ── 3. build_compass_pack ──────────────────────────────────────────


@patch("lintgate.axis_extractor.extract_theory", return_value=MOCK_THEORY_RESULT)
def test_build_compass_pack_has_expected_keys(mock_et: object) -> None:
    pack = build_compass_pack("/fake/project")
    assert isinstance(pack, dict)
    assert {"axes", "directives", "gap_report", "digest_token_estimate"} <= pack.keys()
    assert set(pack["axes"].keys()) == set(AXIS_NAMES)
    for name in AXIS_NAMES:
        e = pack["axes"][name]
        assert "depth" in e and "summary" in e and "claim_count" in e
        assert isinstance(e["depth"], int) and isinstance(e["claim_count"], int)
    assert isinstance(pack["digest_token_estimate"], int) and pack["digest_token_estimate"] >= 0
    assert isinstance(pack["gap_report"], dict)


# ── 4. query_compass ───────────────────────────────────────────────


def test_query_compass_no_filter() -> None:
    result = query_compass(_populated_state())
    assert result["total_matched"] == 5
    assert result["returned_count"] <= 5
    assert result["query"]["axis"] is None and result["query"]["keywords"] is None


def test_query_compass_axis_filter() -> None:
    result = query_compass(_populated_state(), axis="problem")
    assert result["total_matched"] == 2
    assert all(c["axis"] == "problem" for c in result["matched_claims"])


def test_query_compass_keyword_filter() -> None:
    result = query_compass(_populated_state(), keywords=["diagnostic"])
    assert result["total_matched"] == 1
    assert "diagnostic" in result["matched_claims"][0]["text"].lower()


def test_query_compass_axis_and_keyword_filter() -> None:
    result = query_compass(_populated_state(), axis="solution", keywords=["symptoms"])
    assert result["total_matched"] == 1
    assert result["matched_claims"][0]["axis"] == "solution"


def test_query_compass_no_match() -> None:
    result = query_compass(_populated_state(), keywords=["nonexistent_keyword_xyz"])
    assert result["total_matched"] == 0 and result["matched_claims"] == []


def test_query_compass_truncation() -> None:
    result = query_compass(_populated_state(), max_claims=2)
    assert result["returned_count"] <= 2
    assert result["truncated"] == (result["total_matched"] > 2)


def test_query_compass_invalid_max_claims() -> None:
    with pytest.raises(ValueError, match="max_claims must be > 0"):
        query_compass(_populated_state(), max_claims=0)


def test_query_compass_nonexistent_axis_returns_all() -> None:
    result = query_compass(_populated_state(), axis="nonexistent")
    assert result["total_matched"] == 5


# ── 5. _heading_matches_axis ───────────────────────────────────────


def test_heading_matches_axis_positive() -> None:
    assert _heading_matches_axis("Problem Statement", "problem") is True
    assert _heading_matches_axis("Design Approach", "solution") is True
    assert _heading_matches_axis("Coding Conventions", "implementation") is True
    assert _heading_matches_axis("Deployment Environment", "world") is True


def test_heading_matches_axis_case_insensitive() -> None:
    assert _heading_matches_axis("PROBLEM STATEMENT", "problem") is True
    assert _heading_matches_axis("design approach", "solution") is True


def test_heading_matches_axis_negative() -> None:
    assert _heading_matches_axis("Unrelated Heading", "problem") is False
    assert _heading_matches_axis("Random Words", "solution") is False


def test_heading_matches_axis_empty_heading() -> None:
    assert _heading_matches_axis("", "problem") is False


def test_heading_matches_axis_unknown_axis() -> None:
    assert _heading_matches_axis("Problem Statement", "nonexistent") is False


def test_heading_matches_axis_regex_patterns() -> None:
    assert _heading_matches_axis("Trade-off Analysis", "solution") is True
    assert _heading_matches_axis("Tradeoff Analysis", "solution") is True


def test_all_axis_heading_signals_have_entries() -> None:
    for axis_name in AXIS_NAMES:
        if axis_name in AXIS_HEADING_SIGNALS:
            assert len(AXIS_HEADING_SIGNALS[axis_name]) > 0


# ── 6. _score_claim_relevance ──────────────────────────────────────


def test_score_claim_relevance_no_keywords() -> None:
    c = _claim("Any claim text")
    assert _score_claim_relevance(c, None) == 1
    assert _score_claim_relevance(c, []) == 1


def test_score_claim_relevance_single_match() -> None:
    assert _score_claim_relevance(_claim("Uses constraints for validation"), ["constraints"]) == 1


def test_score_claim_relevance_multiple_matches() -> None:
    score = _score_claim_relevance(
        _claim("Constraints and hypotheses drive validation"),
        ["constraints", "hypotheses", "missing"],
    )
    assert score == 2


def test_score_claim_relevance_no_match() -> None:
    assert _score_claim_relevance(_claim("Uses constraints for validation"), ["nonexistent"]) == 0


def test_score_claim_relevance_case_insensitive() -> None:
    c = _claim("The SYSTEM uses Constraints")
    assert _score_claim_relevance(c, ["system"]) == 1
    assert _score_claim_relevance(c, ["CONSTRAINTS"]) == 1
