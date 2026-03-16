"""Prescriptive spec tests for _compute_confidence.

Target: dependency_clustering::_compute_confidence
Spec claims: exact additive confidence bonuses, base 0.50, cap 0.85.
These pin every VALUE and BOUNDARY mutant the platonic system couldn't kill.
"""

from __future__ import annotations

import ast

import pytest

from lintgate.linters.structure_checks.dependency_clustering import (
    _compute_confidence,
    _StmtInfo,
)


def _make_block(n: int) -> list[_StmtInfo]:
    """Create n dummy _StmtInfo entries."""
    return [
        _StmtInfo(
            index=i,
            stmt=ast.Pass(lineno=i, col_offset=0),
            reads=frozenset(),
            writes=frozenset(),
            has_exit=False,
        )
        for i in range(n)
    ]


# ── Claim: base confidence is exactly 0.50 ──────────────────────────


class TestBaseConfidence:
    def test_minimal_block_no_bonuses(self):
        """3 stmts, 3 inputs, 1 output, CC=3 → only base 0.50."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 3)
        assert result == pytest.approx(0.50)

    def test_four_inputs_no_input_bonus(self):
        """4 inputs → no input bonus (threshold is <=2)."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c", "d"}, {"x"}, 3)
        assert result == pytest.approx(0.50)


# ── Claim: inputs<=2 adds exactly 0.10 ──────────────────────────────


class TestInputBonus:
    def test_two_inputs(self):
        """2 inputs → +0.10 = 0.60."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b"}, {"x"}, 3)
        assert result == pytest.approx(0.60)

    def test_one_input(self):
        """1 input → +0.10 = 0.60."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a"}, {"x"}, 3)
        assert result == pytest.approx(0.60)

    def test_zero_inputs(self):
        """0 inputs → +0.10 = 0.60."""
        block = _make_block(3)
        result = _compute_confidence(block, set(), {"x"}, 3)
        assert result == pytest.approx(0.60)

    def test_three_inputs_no_bonus(self):
        """3 inputs → no bonus, base 0.50."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 3)
        assert result == pytest.approx(0.50)


# ── Claim: zero outputs adds exactly 0.10 ────────────────────────────


class TestOutputBonus:
    def test_zero_outputs(self):
        """0 outputs, 3 inputs → base + output bonus = 0.60."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, set(), 3)
        assert result == pytest.approx(0.60)

    def test_one_output_no_bonus(self):
        """1 output → no output bonus."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 3)
        assert result == pytest.approx(0.50)

    def test_two_outputs_no_bonus(self):
        """2 outputs → no output bonus."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x", "y"}, 3)
        assert result == pytest.approx(0.50)


# ── Claim: block length>=5 adds exactly 0.05 ─────────────────────────


class TestLengthBonus:
    def test_five_stmts(self):
        """5 stmts, 3 inputs, 1 output, CC=3 → base + length = 0.55."""
        block = _make_block(5)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 3)
        assert result == pytest.approx(0.55)

    def test_four_stmts_no_bonus(self):
        """4 stmts → no length bonus."""
        block = _make_block(4)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 3)
        assert result == pytest.approx(0.50)

    def test_ten_stmts(self):
        """10 stmts → +0.05."""
        block = _make_block(10)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 3)
        assert result == pytest.approx(0.55)


# ── Claim: block_cc>=8 adds exactly 0.10 ─────────────────────────────


class TestCCBonus:
    def test_cc_8(self):
        """CC=8, 3 inputs, 1 output, 3 stmts → base + CC = 0.60."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 8)
        assert result == pytest.approx(0.60)

    def test_cc_7_no_bonus(self):
        """CC=7 → no CC bonus."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 7)
        assert result == pytest.approx(0.50)

    def test_cc_20(self):
        """CC=20 → +0.10."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 20)
        assert result == pytest.approx(0.60)


# ── Claim: result never exceeds 0.85 (cap) ───────────────────────────


class TestCap:
    def test_all_bonuses_applied(self):
        """All bonuses: 0.50 + 0.10 + 0.10 + 0.05 + 0.10 = 0.85."""
        block = _make_block(6)  # >=5
        result = _compute_confidence(block, {"a"}, set(), 10)  # <=2 inputs, 0 outputs, CC>=8
        assert result == pytest.approx(0.85)

    def test_cap_not_exceeded(self):
        """Even with all conditions maxed, never > 0.85."""
        block = _make_block(20)
        result = _compute_confidence(block, set(), set(), 100)
        assert result == pytest.approx(0.85)
        assert result <= 0.85


# ── Claim: additive combination ──────────────────────────────────────


class TestCombinations:
    def test_input_plus_output(self):
        """<=2 inputs + 0 outputs = 0.50 + 0.10 + 0.10 = 0.70."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a"}, set(), 3)
        assert result == pytest.approx(0.70)

    def test_input_plus_length(self):
        """<=2 inputs + >=5 stmts = 0.50 + 0.10 + 0.05 = 0.65."""
        block = _make_block(5)
        result = _compute_confidence(block, {"a"}, {"x"}, 3)
        assert result == pytest.approx(0.65)

    def test_output_plus_cc(self):
        """0 outputs + CC>=8 = 0.50 + 0.10 + 0.10 = 0.70."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, set(), 8)
        assert result == pytest.approx(0.70)

    def test_three_bonuses(self):
        """<=2 inputs + 0 outputs + CC>=8 = 0.50 + 0.10 + 0.10 + 0.10 = 0.80."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a"}, set(), 8)
        assert result == pytest.approx(0.80)

    def test_three_bonuses_with_length(self):
        """<=2 inputs + 0 outputs + >=5 stmts = 0.50 + 0.10 + 0.10 + 0.05 = 0.75."""
        block = _make_block(5)
        result = _compute_confidence(block, {"a"}, set(), 3)
        assert result == pytest.approx(0.75)


# ── SWAP discrimination: inputs vs outputs are not interchangeable ───


class TestSwapDiscrimination:
    def test_many_inputs_zero_outputs(self):
        """3 inputs, 0 outputs → only output bonus (0.10), not input bonus.
        Result: 0.50 + 0.10 = 0.60."""
        block = _make_block(3)
        result = _compute_confidence(block, {"a", "b", "c"}, set(), 3)
        assert result == pytest.approx(0.60)

    def test_zero_inputs_many_outputs(self):
        """0 inputs, 3 outputs → only input bonus (0.10), not output bonus.
        Result: 0.50 + 0.10 = 0.60."""
        block = _make_block(3)
        result = _compute_confidence(block, set(), {"x", "y", "z"}, 3)
        assert result == pytest.approx(0.60)

    def test_asymmetric_swap_detection(self):
        """2 inputs, 1 output → input bonus only. Swapping would give output=0 bonus.
        Correct: 0.50 + 0.10 = 0.60. Swapped would be: 0.50 + 0.10 = 0.60 (same!).
        Use 1 input, 2 outputs to create asymmetry."""
        block = _make_block(3)
        # 1 input (<=2 → +0.10), 2 outputs (>0 → no bonus) = 0.60
        result_correct = _compute_confidence(block, {"a"}, {"x", "y"}, 3)
        assert result_correct == pytest.approx(0.60)
        # If inputs/outputs were swapped: 2 inputs (<=2 → +0.10), 1 output (>0 → no bonus)
        # Same result! Need different asymmetry.
        # 0 inputs (<=2 → +0.10), 3 outputs (>0 → no bonus) = 0.60
        # 3 inputs (>2 → no bonus), 0 outputs (=0 → +0.10) = 0.60
        # The bonuses are different conditions but same magnitude — hard to distinguish by value alone.
        # Use a case where only ONE bonus applies:
        # 3 inputs, 0 outputs → output bonus only → 0.60
        # Swapped: 0 inputs, 3 outputs → input bonus only → 0.60
        # Same value. The SWAP survives because the bonuses have equal magnitude.
        # This is a legitimate equivalent mutant — swapping inputs/outputs in the len() checks
        # produces the same numerical result when both checks add 0.10.
        # (Verified asymmetry in test_input_output_boundary_asymmetry below.)

    def test_input_output_boundary_asymmetry(self):
        """The only way to kill a SWAP(inputs, outputs) is if the conditions differ.
        inputs checks len<=2, outputs checks len==0. These ARE different:
        2 inputs + 1 output: input bonus YES, output bonus NO → 0.60
        If swapped (2 as outputs, 1 as inputs): input bonus YES, output bonus NO → 0.60
        Same! But try: 0 inputs + 0 outputs:
        input bonus YES + output bonus YES → 0.70
        Swapped: still 0 inputs and 0 outputs → 0.70. Same.
        The SWAP mutant may be equivalent. Let's verify with exact boundary:
        3 inputs, 0 outputs → no input bonus, yes output bonus → 0.60
        0 inputs, 3 outputs → yes input bonus, no output bonus → 0.60
        These give the same value. The mutant IS equivalent for same-magnitude bonuses.
        """
        # Confirm: the asymmetry is in the CONDITION (<=2 vs ==0), not the bonus value
        block = _make_block(3)
        # 2 inputs, 0 outputs: both bonuses → 0.70
        r1 = _compute_confidence(block, {"a", "b"}, set(), 3)
        assert r1 == pytest.approx(0.70)
        # 0 inputs, 2 outputs: input bonus only → 0.60
        r2 = _compute_confidence(block, set(), {"x", "y"}, 3)
        assert r2 == pytest.approx(0.60)
        # These differ! A SWAP mutant would produce r2 where r1 is expected.
        assert r1 != pytest.approx(r2)
