"""Tests for cognitive mode state, transitions, and execution compass."""

from __future__ import annotations

import json

from lintgate.compass import CompassAxis, CompassClaim, CompassDirective, CompassState
from lintgate.modes.execution_compass import ExecutionCompass
from lintgate.modes.mode_state import CognitiveMode, ModeState

# ── CognitiveMode enum ─────────────────────────────────────────────


def test_cognitive_mode_values() -> None:
    assert CognitiveMode.NORMAL.value == "normal"
    assert CognitiveMode.THEORY.value == "theory"
    assert CognitiveMode.HABIT.value == "habit"


def test_cognitive_mode_from_string() -> None:
    assert CognitiveMode("normal") is CognitiveMode.NORMAL
    assert CognitiveMode("theory") is CognitiveMode.THEORY
    assert CognitiveMode("habit") is CognitiveMode.HABIT


# ── ModeState transitions ──────────────────────────────────────────


def test_normal_to_theory_allowed() -> None:
    ms = ModeState()
    label = ms.transition(CognitiveMode.THEORY)
    assert label == "normal->theory"
    assert ms.current is CognitiveMode.THEORY


def test_theory_to_normal_allowed() -> None:
    ms = ModeState(current=CognitiveMode.THEORY)
    label = ms.transition(CognitiveMode.NORMAL)
    assert label == "theory->normal"
    assert ms.current is CognitiveMode.NORMAL


def test_normal_to_habit_allowed() -> None:
    ms = ModeState()
    label = ms.transition(CognitiveMode.HABIT)
    assert label == "normal->habit"
    assert ms.current is CognitiveMode.HABIT


def test_habit_to_normal_allowed() -> None:
    ms = ModeState(current=CognitiveMode.HABIT)
    label = ms.transition(CognitiveMode.NORMAL)
    assert label == "habit->normal"
    assert ms.current is CognitiveMode.NORMAL


def test_theory_to_habit_blocked() -> None:
    ms = ModeState(current=CognitiveMode.THEORY)
    label = ms.transition(CognitiveMode.HABIT)
    assert label is None
    assert ms.current is CognitiveMode.THEORY  # unchanged


def test_habit_to_theory_blocked() -> None:
    ms = ModeState(current=CognitiveMode.HABIT)
    label = ms.transition(CognitiveMode.THEORY)
    assert label is None
    assert ms.current is CognitiveMode.HABIT  # unchanged


def test_same_mode_transition_returns_none() -> None:
    ms = ModeState()
    label = ms.transition(CognitiveMode.NORMAL)
    assert label is None


# ── enter_theory / freeze_theory / cancel_theory ────────────────────


def test_enter_theory_resets_metadata() -> None:
    ms = ModeState()
    ms.theory_frozen = True
    ms.frozen_compass_hash = "old_hash"
    ms.exploration_claims_added = 5
    label = ms.enter_theory()
    assert label is not None
    assert ms.current is CognitiveMode.THEORY
    assert not ms.theory_frozen
    assert ms.frozen_compass_hash == ""
    assert ms.exploration_claims_added == 0


def test_freeze_theory_records_hash() -> None:
    ms = ModeState(current=CognitiveMode.THEORY)
    label = ms.freeze_theory("abc123")
    assert label == "theory->normal"
    assert ms.current is CognitiveMode.NORMAL
    assert ms.theory_frozen
    assert ms.frozen_compass_hash == "abc123"


def test_freeze_theory_blocked_when_not_in_theory() -> None:
    ms = ModeState()  # NORMAL mode
    label = ms.freeze_theory("abc123")
    assert label is None
    assert not ms.theory_frozen


def test_cancel_theory_no_freeze() -> None:
    ms = ModeState(current=CognitiveMode.THEORY)
    label = ms.cancel_theory()
    assert label == "theory->normal"
    assert ms.current is CognitiveMode.NORMAL
    assert not ms.theory_frozen
    assert ms.frozen_compass_hash == ""


def test_cancel_theory_blocked_when_not_in_theory() -> None:
    ms = ModeState()  # NORMAL mode
    label = ms.cancel_theory()
    assert label is None


# ── ModeState serialization ────────────────────────────────────────


def test_mode_state_round_trip() -> None:
    ms = ModeState(
        current=CognitiveMode.THEORY,
        entered_at=1234567890.0,
        theory_frozen=True,
        frozen_compass_hash="deadbeef",
        exploration_claims_added=3,
    )
    data = ms.to_dict()
    restored = ModeState.from_dict(data)
    assert restored.current is CognitiveMode.THEORY
    assert restored.entered_at == 1234567890.0
    assert restored.theory_frozen is True
    assert restored.frozen_compass_hash == "deadbeef"
    assert restored.exploration_claims_added == 3


def test_mode_state_from_empty_dict() -> None:
    ms = ModeState.from_dict({})
    assert ms.current is CognitiveMode.NORMAL


def test_mode_state_from_invalid_mode_string() -> None:
    ms = ModeState.from_dict({"current": "nonexistent"})
    assert ms.current is CognitiveMode.NORMAL


# ── ExecutionCompass.from_compass_state ─────────────────────────────


def _sample_compass_state() -> CompassState:
    return CompassState(
        axes={
            "problem": CompassAxis(
                name="problem",
                summary="Keep feedback loops tight.",
                claims=[CompassClaim(text="real-time quality checks")],
                depth=2,
            ),
        },
        directives=[
            CompassDirective(kind="toward", text="Prefer deterministic checks"),
            CompassDirective(kind="away", text="Avoid ambiguous heuristics"),
            CompassDirective(kind="forbidden", text="Never disable linting globally"),
        ],
        frozen_hash="hash123",
    )


def test_from_compass_state_categorizes_directives() -> None:
    ec = ExecutionCompass.from_compass_state(_sample_compass_state())
    assert "Prefer deterministic checks" in ec.toward
    assert "Avoid ambiguous heuristics" in ec.away
    assert "Never disable linting globally" in ec.forbidden
    assert ec.true_north == "Keep feedback loops tight."
    assert ec.compass_hash == "hash123"


def test_from_compass_state_skips_empty_directives() -> None:
    state = CompassState(
        directives=[
            CompassDirective(kind="toward", text="  "),
            CompassDirective(kind="toward", text="valid"),
        ],
    )
    ec = ExecutionCompass.from_compass_state(state)
    assert len(ec.toward) == 1
    assert ec.toward[0] == "valid"


# ── check_alignment ────────────────────────────────────────────────


def test_check_alignment_no_match_is_aligned() -> None:
    ec = ExecutionCompass(
        away=["Avoid heuristics"],
        forbidden=["Never disable linting"],
    )
    result = ec.check_alignment("git commit -m 'fix typo'")
    assert result["aligned"] is True
    assert result["violations"] == []
    assert result["warnings"] == []


def test_check_alignment_forbidden_produces_violation() -> None:
    ec = ExecutionCompass(forbidden=["Never disable linting globally"])
    result = ec.check_alignment("disable linting for this file")
    assert result["aligned"] is False
    assert len(result["violations"]) == 1


def test_check_alignment_away_produces_warning() -> None:
    ec = ExecutionCompass(away=["Avoid ambiguous heuristics"])
    result = ec.check_alignment("using ambiguous heuristics here")
    assert result["aligned"] is True  # warnings don't break alignment
    assert len(result["warnings"]) == 1


# ── ExecutionCompass serialization ──────────────────────────────────


def test_execution_compass_round_trip_dict() -> None:
    ec = ExecutionCompass(
        toward=["goal1"],
        away=["avoid1"],
        forbidden=["block1"],
        true_north="north star",
        compass_hash="h1",
    )
    data = ec.to_dict()
    restored = ExecutionCompass.from_dict(data)
    assert restored.toward == ec.toward
    assert restored.away == ec.away
    assert restored.forbidden == ec.forbidden
    assert restored.true_north == ec.true_north
    assert restored.compass_hash == ec.compass_hash


def test_execution_compass_compact_json_parseable() -> None:
    ec = ExecutionCompass(toward=["a"], away=["b"], forbidden=["c"], true_north="tn")
    compact = ec.to_compact_json()
    parsed = json.loads(compact)
    assert parsed["true_north"] == "tn"
    assert parsed["toward"] == ["a"]


def test_execution_compass_from_empty_dict() -> None:
    ec = ExecutionCompass.from_dict({})
    assert ec.toward == []
    assert ec.true_north == ""


def test_from_dict_partial_data_uses_defaults() -> None:
    """Kill VALUE_4/6: from_dict must default true_north and compass_hash to '' for missing keys."""
    ec = ExecutionCompass.from_dict({"toward": ["goal1"]})
    assert ec.toward == ["goal1"]
    assert ec.away == []
    assert ec.forbidden == []
    assert ec.true_north == ""
    assert ec.compass_hash == ""


# ── Prescriptive: from_compass_state default paths ───────────────────


def test_from_compass_state_no_problem_axis_defaults_true_north_empty() -> None:
    """Kill VALUE_3: true_north = '' must not become 'mutated' when no problem axis."""
    state = CompassState(
        directives=[CompassDirective(kind="toward", text="some goal")],
        frozen_hash="abc",
    )
    ec = ExecutionCompass.from_compass_state(state)
    assert ec.true_north == ""


def test_from_compass_state_falsy_frozen_hash_defaults_compass_hash_empty() -> None:
    """Kill VALUE_5: compass_hash must be '' when frozen_hash is falsy."""
    state = CompassState(
        axes={
            "problem": CompassAxis(name="problem", summary="test", claims=[], depth=1),
        },
        directives=[CompassDirective(kind="toward", text="goal")],
        frozen_hash="",
    )
    ec = ExecutionCompass.from_compass_state(state)
    assert ec.compass_hash == ""


# ── Prescriptive: check_alignment boundary & value ───────────────────


def test_check_alignment_three_letter_forbidden_word_does_not_match() -> None:
    """Kill BOUNDARY_0: len(w) > 3 vs >= 3. A 3-char word must NOT trigger."""
    ec = ExecutionCompass(forbidden=["bad"])  # "bad" is 3 chars
    result = ec.check_alignment("this is bad code")
    assert result["aligned"] is True
    assert result["violations"] == []


def test_check_alignment_four_letter_forbidden_word_matches() -> None:
    """Kill VALUE_0/1: A 4-char word must trigger (confirms > 3 boundary)."""
    ec = ExecutionCompass(forbidden=["halt"])  # "halt" is 4 chars
    result = ec.check_alignment("we should halt execution")
    assert result["aligned"] is False
    assert len(result["violations"]) == 1


def test_check_alignment_three_letter_away_word_does_not_match() -> None:
    """Same boundary test on away directives."""
    ec = ExecutionCompass(away=["use old api"])  # "old", "use", "api" all <=3
    result = ec.check_alignment("use old api directly")
    assert result["warnings"] == []


def test_check_alignment_four_letter_away_word_matches() -> None:
    """Away directive with 4-char word must produce warning."""
    ec = ExecutionCompass(away=["skip tests"])  # "skip"=4, "tests"=5
    result = ec.check_alignment("we skip tests here")
    assert len(result["warnings"]) == 1


# ── Prescriptive: check_alignment_with_specs (0% → targeted) ────────


class _FakeForbiddenBehavior:
    def __init__(self, description: str):
        self.description = description


class _FakeSpec:
    def __init__(self, forbidden_behaviors: list[_FakeForbiddenBehavior]):
        self.forbidden_behaviors = forbidden_behaviors


def test_check_alignment_with_specs_none_specs_returns_base() -> None:
    """Invariant: returns base check_alignment result when specs is None."""
    ec = ExecutionCompass(forbidden=["Never mock databases"])
    result = ec.check_alignment_with_specs("mock databases in test", specs=None)
    assert result["aligned"] is False
    assert "prescriptive_violations" not in result


def test_check_alignment_with_specs_empty_specs_returns_base() -> None:
    """Invariant: returns base check_alignment result when specs is empty list."""
    ec = ExecutionCompass()
    result = ec.check_alignment_with_specs("some action", specs=[])
    assert result["aligned"] is True
    assert "prescriptive_violations" not in result


def test_check_alignment_with_specs_matching_sets_aligned_false() -> None:
    """Kill VALUE_3: result['aligned'] must be False when prescriptive violations found."""
    ec = ExecutionCompass()
    spec = _FakeSpec([_FakeForbiddenBehavior("Never delete production data")])
    result = ec.check_alignment_with_specs("delete production data now", specs=[spec])
    assert result["aligned"] is False


def test_check_alignment_with_specs_adds_prescriptive_violations_key() -> None:
    """Kill VALUE_5: 'prescriptive_violations' key must appear in result."""
    ec = ExecutionCompass()
    spec = _FakeSpec([_FakeForbiddenBehavior("Never delete production data")])
    result = ec.check_alignment_with_specs("delete production data now", specs=[spec])
    assert "prescriptive_violations" in result
    assert "Never delete production data" in result["prescriptive_violations"]


def test_check_alignment_with_specs_extends_violations_list() -> None:
    """Kill VALUE_2/4: prescriptive violations extend existing violations list."""
    ec = ExecutionCompass(forbidden=["Never disable linting globally"])
    spec = _FakeSpec([_FakeForbiddenBehavior("Never disable formatting globally")])
    result = ec.check_alignment_with_specs(
        "disable linting globally and disable formatting globally",
        specs=[spec],
    )
    assert result["aligned"] is False
    assert len(result["violations"]) >= 2
    assert "Never disable linting globally" in result["violations"]
    assert "Never disable formatting globally" in result["violations"]


def test_check_alignment_with_specs_getattr_reads_forbidden_behaviors() -> None:
    """Kill VALUE_0/SWAP_0: getattr(spec, 'forbidden_behaviors', []) must read correct attr."""
    ec = ExecutionCompass()
    spec = _FakeSpec([_FakeForbiddenBehavior("Never truncate logs silently")])
    result = ec.check_alignment_with_specs("truncate logs silently", specs=[spec])
    assert result["aligned"] is False
    assert len(result["prescriptive_violations"]) == 1


def test_check_alignment_with_specs_swap_setdefault_arg_order() -> None:
    """Kill SWAP_1: result.setdefault('violations', []) — args must be in correct order."""
    ec = ExecutionCompass()
    spec = _FakeSpec([_FakeForbiddenBehavior("Avoid caching stale results")])
    result = ec.check_alignment_with_specs("caching stale results here", specs=[spec])
    assert isinstance(result["violations"], list)
    assert "Avoid caching stale results" in result["violations"]


def test_check_alignment_with_specs_boundary_three_char_word_no_match() -> None:
    """Kill BOUNDARY_0: 3-char words in spec forbidden_behaviors must NOT match."""
    ec = ExecutionCompass()
    spec = _FakeSpec([_FakeForbiddenBehavior("use bad api")])  # all <=3 chars
    result = ec.check_alignment_with_specs("use bad api directly", specs=[spec])
    assert result["aligned"] is True
    assert result.get("prescriptive_violations") is None


def test_check_alignment_with_specs_nonmatching_stays_aligned() -> None:
    """No match: prescriptive_violations should not appear, aligned stays True."""
    ec = ExecutionCompass()
    spec = _FakeSpec([_FakeForbiddenBehavior("Never delete production data")])
    result = ec.check_alignment_with_specs("git commit -m fix", specs=[spec])
    assert result["aligned"] is True
    assert "prescriptive_violations" not in result
