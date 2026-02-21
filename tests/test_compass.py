"""Tests for compass.py and compass_io.py."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.compass import (
    CompassAxis,
    CompassClaim,
    CompassDirective,
    CompassState,
    GapReport,
    compute_axis_depth,
    compute_compass_hash,
    compute_gap_report,
    compute_staleness,
)
from lintgate.compass_io import (
    load_compass,
    migrate_from_theory_profile,
    reset_compass,
    save_compass,
)

# ── Serialization Round-Trips ────────────────────────────────────────


def test_compass_claim_roundtrip():
    claim = CompassClaim(
        text="channels disagree",
        source="design.md:42",
        heading="Arch",
        confidence=0.85,
        provenance="inferred",
        origin_facet="architecture",
    )
    restored = CompassClaim.from_dict(claim.to_dict())
    for attr in ("text", "source", "heading", "confidence", "provenance", "origin_facet"):
        assert getattr(restored, attr) == getattr(claim, attr)


def test_compass_claim_from_empty_dict():
    c = CompassClaim.from_dict({})
    assert c.text == "" and c.confidence == 1.0 and c.provenance == "parsed"


def test_compass_axis_roundtrip():
    axis = CompassAxis(
        name="solution",
        claims=[CompassClaim(text="composition"), CompassClaim(text="because lossy")],
        summary="composition",
        depth=2,
    )
    restored = CompassAxis.from_dict(axis.to_dict())
    assert restored.name == "solution"
    assert len(restored.claims) == 2
    assert restored.summary == "composition" and restored.depth == 2


def test_compass_directive_roundtrip():
    d = CompassDirective(kind="forbidden", text="no mocks", source="rules")
    restored = CompassDirective.from_dict(d.to_dict())
    assert (restored.kind, restored.text, restored.source) == ("forbidden", "no mocks", "rules")


def test_compass_directive_from_empty_dict():
    d = CompassDirective.from_dict({})
    assert d.kind == "toward" and d.text == ""


def test_gap_report_roundtrip():
    report = GapReport(
        axis_depths={"problem": 3, "solution": 2, "implementation": 1, "world": 0},
        spikiness=0.1667,
        sparse_axes=["implementation", "world"],
        interview_recommended=False,
    )
    restored = GapReport.from_dict(report.to_dict())
    assert restored.axis_depths == report.axis_depths
    assert restored.spikiness == report.spikiness
    assert restored.sparse_axes == report.sparse_axes
    assert restored.interview_recommended is False


def test_gap_report_from_empty_dict():
    r = GapReport.from_dict({})
    assert r.axis_depths == {} and r.spikiness == 0.0


def test_compass_state_roundtrip():
    state = CompassState(
        version=1,
        axes={
            "problem": CompassAxis(name="problem", claims=[CompassClaim(text="test")], depth=1),
            "solution": CompassAxis(name="solution", depth=0),
        },
        directives=[CompassDirective(kind="toward", text="test", source="solution")],
        gap_report=GapReport(axis_depths={"problem": 1}, spikiness=0.0),
        forged_at=1700000000.0,
        frozen=True,
        frozen_hash="abc123",
    )
    restored = CompassState.from_dict(state.to_dict())
    assert restored.version == 1 and "problem" in restored.axes
    assert restored.axes["problem"].claims[0].text == "test"
    assert len(restored.directives) == 1
    assert restored.forged_at == 1700000000.0
    assert restored.frozen is True and restored.frozen_hash == "abc123"


def test_compass_state_from_empty_dict():
    s = CompassState.from_dict({})
    assert s.version == 1 and s.axes == {} and s.directives == []


# ── compute_axis_depth ───────────────────────────────────────────────


def test_depth_empty():
    assert compute_axis_depth([]) == 0


def test_depth_surface():
    claims = [CompassClaim(text="simple fact")]
    assert compute_axis_depth(claims) == 1

    claims_3 = [CompassClaim(text=f"fact {i}") for i in range(3)]
    assert compute_axis_depth(claims_3) == 1


def test_depth_structural_by_count():
    claims = [CompassClaim(text=f"claim {i}") for i in range(4)]
    assert compute_axis_depth(claims) == 2

    claims_8 = [CompassClaim(text=f"claim {i}") for i in range(8)]
    assert compute_axis_depth(claims_8) == 2


def test_depth_structural_by_causal():
    claims = [CompassClaim(text="this works because channels converge")]
    assert compute_axis_depth(claims) == 2


def test_depth_structural_by_contrastive():
    claims = [CompassClaim(text="however this differs from the old approach")]
    assert compute_axis_depth(claims) == 2


def test_depth_deep_by_count():
    claims = [CompassClaim(text=f"claim {i}") for i in range(9)]
    assert compute_axis_depth(claims) == 3


def test_depth_deep_by_causal_count():
    claims = [
        CompassClaim(text="because A"),
        CompassClaim(text="therefore B"),
        CompassClaim(text="consequently C"),
    ]
    assert compute_axis_depth(claims) == 3


# ── compute_gap_report ───────────────────────────────────────────────


def _make_axis(name: str, depth: int) -> CompassAxis:
    return CompassAxis(name=name, depth=depth)


def test_gap_report_balanced():
    state = CompassState(
        axes={
            "problem": _make_axis("problem", 3),
            "solution": _make_axis("solution", 3),
            "implementation": _make_axis("implementation", 2),
            "world": _make_axis("world", 2),
        }
    )
    report = compute_gap_report(state)
    assert report.spikiness == 0.0
    assert report.interview_recommended is False
    assert report.sparse_axes == []


def test_gap_report_spiky():
    state = CompassState(
        axes={
            "problem": _make_axis("problem", 3),
            "solution": _make_axis("solution", 0),
            "implementation": _make_axis("implementation", 0),
            "world": _make_axis("world", 0),
        }
    )
    report = compute_gap_report(state)
    assert report.spikiness > 0.3
    assert report.interview_recommended is True
    assert "solution" in report.sparse_axes


def test_gap_report_all_empty():
    state = CompassState(axes={})
    report = compute_gap_report(state)
    assert all(d == 0 for d in report.axis_depths.values())
    assert report.interview_recommended is True
    assert len(report.sparse_axes) == 4


# ── migrate_from_theory_profile ──────────────────────────────────────


def test_migrate_from_theory_profile_parity():
    theory = {
        "core_theory": {"claims": [{"claim": "lossy channels converge", "confidence": 0.9}]},
        "alignment": {"claims": [{"claim": "hypothesis-with-confidence", "confidence": 0.8}]},
        "problem_solving": {"claims": [{"claim": "debug symptom not cause"}]},
        "architecture": {
            "claims": [{"claim": "cross-channel because channels converge", "confidence": 0.9}],
        },
        "anti_patterns": {"claims": [{"claim": "approach cycling", "confidence": 0.7}]},
        "abstractions": {"claims": [{"claim": "CompassState as model", "confidence": 0.75}]},
        "enforceable_rules": {"claims": [{"claim": "no global mocks", "confidence": 0.95}]},
    }
    full_result = {
        "anti_patterns": [{"pattern": "approach cycling without model update"}],
        "enforceable_rules": [{"pattern": "no debugger statements", "type": "forbid"}],
    }
    state = migrate_from_theory_profile(theory, full_result)

    # core_theory + alignment -> problem axis
    prob = [c.text for c in state.axes["problem"].claims]
    assert "lossy channels converge" in prob
    assert "hypothesis-with-confidence" in prob
    # problem_solving + architecture + anti_patterns -> solution axis
    sol = [c.text for c in state.axes["solution"].claims]
    assert "debug symptom not cause" in sol
    assert any("cross-channel" in t for t in sol)
    # abstractions + enforceable_rules -> implementation axis
    impl = [c.text for c in state.axes["implementation"].claims]
    assert "CompassState as model" in impl
    # origin_facet preserved
    assert state.axes["problem"].claims[0].origin_facet == "core_theory"
    # directives populated from full_result
    assert any(d.kind == "away" for d in state.directives)
    assert any(d.kind == "forbidden" for d in state.directives)
    # depths scored and gap report computed
    assert state.axes["problem"].depth >= 1
    assert state.gap_report.axis_depths != {}
    assert state.forged_at > 0


def test_migrate_empty_profile():
    state = migrate_from_theory_profile({})
    assert all(len(state.axes[a].claims) == 0 for a in state.axes)
    assert state.gap_report.interview_recommended is True


# ── load_compass / save_compass ──────────────────────────────────────


def test_load_save_roundtrip(tmp_path: Path):
    state = CompassState(
        version=1,
        axes={
            "problem": CompassAxis(
                name="problem",
                claims=[CompassClaim(text="test first")],
                summary="test first",
                depth=1,
            )
        },
        directives=[CompassDirective(kind="toward", text="test first")],
        gap_report=GapReport(axis_depths={"problem": 1}),
        forged_at=1700000000.0,
    )
    save_compass(str(tmp_path), state)
    loaded = load_compass(str(tmp_path))
    assert loaded is not None
    assert loaded.version == 1 and "problem" in loaded.axes
    assert loaded.axes["problem"].claims[0].text == "test first"
    assert loaded.forged_at == 1700000000.0


def test_load_compass_missing(tmp_path: Path):
    assert load_compass(str(tmp_path)) is None


def test_save_creates_directory(tmp_path: Path):
    nested = tmp_path / "deep" / "nested"
    state = CompassState(forged_at=1.0)
    path = save_compass(str(nested), state)
    assert path.exists()


# ── compute_staleness ────────────────────────────────────────────────


def test_staleness_fresh():
    state = CompassState(forged_at=time.time())
    staleness = compute_staleness(state)
    assert staleness < 0.01


def test_staleness_old():
    state = CompassState(forged_at=time.time() - 48 * 3600)
    staleness = compute_staleness(state, max_age_hours=24.0)
    assert staleness == 1.0


def test_staleness_never_forged():
    state = CompassState(forged_at=0.0)
    assert compute_staleness(state) == 1.0


# ── compute_compass_hash ────────────────────────────────────────────


def test_hash_deterministic():
    state = CompassState(
        forged_at=1700000000.0,
        axes={"problem": CompassAxis(name="problem", claims=[CompassClaim(text="x")])},
    )
    h1 = compute_compass_hash(state)
    h2 = compute_compass_hash(state)
    assert h1 == h2
    assert len(h1) == 16


def test_hash_changes_on_mutation():
    state = CompassState(
        forged_at=1700000000.0,
        axes={"problem": CompassAxis(name="problem", claims=[CompassClaim(text="x")])},
    )
    h1 = compute_compass_hash(state)

    state.axes["problem"].claims.append(CompassClaim(text="y"))
    h2 = compute_compass_hash(state)
    assert h1 != h2


# ── reset_compass ────────────────────────────────────────────────────


def test_reset_compass_deletes(tmp_path: Path):
    state = CompassState(forged_at=1.0)
    save_compass(str(tmp_path), state)
    path = tmp_path / ".claude" / "compass.yaml"
    assert path.exists()

    result = reset_compass(str(tmp_path))
    assert result is not None
    assert not path.exists()


def test_reset_compass_missing(tmp_path: Path):
    result = reset_compass(str(tmp_path))
    assert result is None
