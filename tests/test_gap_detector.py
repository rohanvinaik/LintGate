"""Tests for gap detection, interview building, and answer application."""

from __future__ import annotations

from lintgate.compass import (
    OPTIONAL_AXES,
    REQUIRED_AXES,
    CompassAxis,
    CompassClaim,
    CompassState,
    GapReport,
)
from lintgate.gap_detector import (
    apply_answer,
    build_interview,
    detect_gaps,
    skip_interview,
)

# ── helpers ─────────────────────────────────────────────────────────


def _make_claims(n: int, causal: bool = False) -> list[CompassClaim]:
    """Generate n claims, optionally with causal markers for depth boost."""
    marker = " because it matters" if causal else ""
    return [CompassClaim(text=f"claim {i}{marker}") for i in range(n)]


def _balanced_state(depth: int = 2) -> CompassState:
    """Create a state where all axes have claims at the given depth."""
    count = {0: 0, 1: 2, 2: 5, 3: 10}[depth]
    return CompassState(
        axes={
            name: CompassAxis(name=name, claims=_make_claims(count))
            for name in ("problem", "solution", "implementation", "world")
        }
    )


# ── detect_gaps ─────────────────────────────────────────────────────


def test_detect_gaps_balanced_no_interview() -> None:
    """A balanced state with depth >= 2 everywhere should not need an interview."""
    state = _balanced_state(depth=2)
    report = detect_gaps(state)
    assert report.spikiness < 0.31
    assert not report.interview_recommended


def test_detect_gaps_spiky_recommends_interview() -> None:
    """High problem depth + empty solution produces spikiness and triggers interview."""
    state = CompassState(
        axes={
            "problem": CompassAxis(name="problem", claims=_make_claims(10)),
            "solution": CompassAxis(name="solution", claims=[]),
        }
    )
    report = detect_gaps(state)
    assert report.spikiness > 0.3
    assert report.interview_recommended
    assert "solution" in report.sparse_axes


def test_detect_gaps_empty_required_axis_recommends_interview() -> None:
    """Even with zero spikiness, an empty required axis triggers interview."""
    state = CompassState(
        axes={
            "problem": CompassAxis(name="problem", claims=[]),
            "solution": CompassAxis(name="solution", claims=[]),
        }
    )
    report = detect_gaps(state)
    assert report.interview_recommended


def test_detect_gaps_fills_missing_axes() -> None:
    """detect_gaps should create entries for any missing axis names."""
    state = CompassState(axes={})
    detect_gaps(state)
    assert set(state.axes.keys()) == {"problem", "solution", "implementation", "world"}


# ── build_interview ─────────────────────────────────────────────────


def test_build_interview_priority_ordering() -> None:
    """Required-empty axes (priority 1) should come before optional-empty (priority 3)."""
    report = GapReport(
        axis_depths={"problem": 0, "solution": 1, "implementation": 0, "world": 0},
    )
    entries = build_interview(report)
    priorities = [e["priority"] for e in entries]
    assert priorities == sorted(priorities), "entries should be sorted by priority"
    # First entry should be a required-empty axis
    assert entries[0]["axis"] in REQUIRED_AXES
    assert entries[0]["priority"] == 1


def test_build_interview_respects_max_questions() -> None:
    report = GapReport(axis_depths={"problem": 0, "solution": 0, "implementation": 0, "world": 0})
    entries = build_interview(report, max_questions=3)
    assert len(entries) <= 3


def test_build_interview_skips_deep_axes() -> None:
    """Axes with depth > 1 should not generate questions."""
    report = GapReport(
        axis_depths={"problem": 3, "solution": 3, "implementation": 2, "world": 2},
    )
    entries = build_interview(report)
    assert entries == []


# ── apply_answer ────────────────────────────────────────────────────


def test_apply_answer_creates_claim() -> None:
    state = CompassState(axes={})
    claim = apply_answer(state, "problem", 0, "This project solves X")
    assert claim.provenance == "interviewed"
    assert claim.confidence == 0.9
    assert claim.source == "interview:problem"
    assert "problem" in state.axes
    assert claim in state.axes["problem"].claims


def test_apply_answer_out_of_range_question_idx() -> None:
    state = CompassState(axes={})
    claim = apply_answer(state, "problem", 999, "answer text")
    assert "question#999" in claim.heading


# ── skip_interview ──────────────────────────────────────────────────


def test_skip_interview_clears_recommendation() -> None:
    state = CompassState(
        axes={"problem": CompassAxis(name="problem", claims=[])},
    )
    detect_gaps(state)
    assert state.gap_report.interview_recommended is True
    skip_interview(state)
    assert state.gap_report.interview_recommended is False
    # Idempotent: calling again doesn't error or change result
    skip_interview(state)
    assert state.gap_report.interview_recommended is False


def test_skip_interview_returns_none() -> None:
    """skip_interview returns None (pure side-effect on state)."""
    state = CompassState(
        axes={"problem": CompassAxis(name="problem", claims=[])},
    )
    detect_gaps(state)
    result = skip_interview(state)
    assert result is None


def test_skip_interview_preserves_other_gap_report_fields() -> None:
    """skip_interview only mutates interview_recommended; other fields are unchanged."""
    state = CompassState(
        axes={
            "problem": CompassAxis(name="problem", claims=_make_claims(10)),
            "solution": CompassAxis(name="solution", claims=[]),
        }
    )
    detect_gaps(state)
    # Capture pre-skip values
    pre_spikiness = state.gap_report.spikiness
    pre_axis_depths = dict(state.gap_report.axis_depths)
    pre_sparse_axes = list(state.gap_report.sparse_axes)

    assert state.gap_report.interview_recommended is True
    skip_interview(state)

    # interview_recommended is the only field that changes
    assert state.gap_report.interview_recommended is False
    assert state.gap_report.spikiness == pre_spikiness
    assert state.gap_report.axis_depths == pre_axis_depths
    assert state.gap_report.sparse_axes == pre_sparse_axes


def test_skip_interview_exact_gap_report_values() -> None:
    """Verify skip_interview against exact computed gap report values."""
    # All axes empty → all depths 0, all sparse, spikiness 0.0
    state = CompassState(axes={})
    detect_gaps(state)

    assert state.gap_report.axis_depths == {
        "problem": 0, "solution": 0, "implementation": 0, "world": 0,
    }
    assert state.gap_report.spikiness == 0.0
    assert set(state.gap_report.sparse_axes) == {"problem", "solution", "implementation", "world"}
    assert state.gap_report.interview_recommended is True

    skip_interview(state)

    # Only interview_recommended changes to False
    assert state.gap_report.interview_recommended is False
    assert state.gap_report.axis_depths == {
        "problem": 0, "solution": 0, "implementation": 0, "world": 0,
    }
    assert state.gap_report.spikiness == 0.0
    assert set(state.gap_report.sparse_axes) == {"problem", "solution", "implementation", "world"}


# ── optional axes don't inflate spikiness ───────────────────────────


def test_optional_axes_dont_inflate_spikiness() -> None:
    """Spikiness is only computed on required axes; optional axes should not matter."""
    balanced_required = CompassState(
        axes={
            "problem": CompassAxis(name="problem", claims=_make_claims(5)),
            "solution": CompassAxis(name="solution", claims=_make_claims(5)),
            "implementation": CompassAxis(name="implementation", claims=[]),
            "world": CompassAxis(name="world", claims=[]),
        }
    )
    report = detect_gaps(balanced_required)
    # Required axes are balanced at depth 2, so spikiness should be low
    assert report.spikiness < 0.01
    # Optional axes are empty but should not trigger interview via spikiness
    # (they still count as sparse though)
    for ax in OPTIONAL_AXES:
        assert ax in report.sparse_axes
