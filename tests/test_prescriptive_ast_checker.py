"""Tests for PrescriptiveSpec AST-level invariant checker (PSPEC001)."""

from __future__ import annotations

from lintgate.specification.prescriptive_ast_checker import (
    check_invariants_against_ast,
)
from lintgate.specification.prescriptive_spec import (
    Invariant,
    Predicate,
    PredicateOp,
    pred_and,
    pred_custom,
    pred_gt,
    pred_not,
    pred_true,
    pred_type,
)


def _inv(name: str, predicate: Predicate, kind: str = "safety") -> Invariant:
    return Invariant(name, predicate, predicate.description or name, "test", 0.8, kind)


SOURCE_TYPED = '''\
def compute(x: int) -> int:
    return x + 1
'''

SOURCE_UNTYPED = '''\
def compute(x):
    return x + 1
'''

SOURCE_WITH_CALL = '''\
def process(data):
    validate(data)
    return transform(data)
'''

SOURCE_WITH_ASSERT = '''\
def bounded(x: int) -> int:
    result = x * 2
    assert result > 0
    return result
'''

SOURCE_WITH_ATTR = '''\
def setup(self):
    self.initialized = True
    self.count = 0
'''


class TestISType:
    def test_return_type_matches(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("typed_return", pred_type("result", "int", "returns int"))],
        )
        assert len(results) == 1
        assert results[0].status == "pass"

    def test_return_type_mismatch(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("typed_return", pred_type("result", "str", "returns str"))],
        )
        assert results[0].status == "fail"
        assert "str" in results[0].reason

    def test_missing_return_annotation(self):
        results = check_invariants_against_ast(
            SOURCE_UNTYPED, "compute",
            [_inv("typed_return", pred_type("result", "int", "returns int"))],
        )
        assert results[0].status == "fail"
        assert "no return type" in results[0].reason


class TestCalls:
    def test_call_found(self):
        results = check_invariants_against_ast(
            SOURCE_WITH_CALL, "process",
            [_inv("calls_validate", Predicate(op=PredicateOp.CALLS, subject="validate", description="calls validate"))],
        )
        assert results[0].status == "pass"

    def test_call_not_found(self):
        results = check_invariants_against_ast(
            SOURCE_WITH_CALL, "process",
            [_inv("calls_check", Predicate(op=PredicateOp.CALLS, subject="check_input", description="calls check_input"))],
        )
        assert results[0].status == "fail"


class TestHasAttr:
    def test_attr_found(self):
        results = check_invariants_against_ast(
            SOURCE_WITH_ATTR, "setup",
            [_inv("has_count", Predicate(op=PredicateOp.HAS_ATTR, value="count", description="has count"))],
        )
        assert results[0].status == "pass"

    def test_attr_not_found_is_skip(self):
        """Missing attr is skip (not fail) — could be dynamic."""
        results = check_invariants_against_ast(
            SOURCE_WITH_ATTR, "setup",
            [_inv("has_missing", Predicate(op=PredicateOp.HAS_ATTR, value="missing", description="has missing"))],
        )
        assert results[0].status == "skip"


class TestComparisonGuard:
    def test_assert_guard_found(self):
        results = check_invariants_against_ast(
            SOURCE_WITH_ASSERT, "bounded",
            [_inv("positive", pred_gt("result", 0, "result positive"))],
        )
        assert results[0].status == "pass"

    def test_no_guard_is_skip(self):
        """No guard is skip, not fail — may be enforced elsewhere."""
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("positive", pred_gt("result", 0, "result positive"))],
        )
        assert results[0].status == "skip"


class TestCompound:
    def test_and_all_pass(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("both", pred_and(
                pred_type("result", "int", "int"),
                pred_true("ok"),
                desc="both hold",
            ))],
        )
        assert results[0].status == "pass"

    def test_and_one_fail(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("one_fails", pred_and(
                pred_type("result", "int", "int"),
                pred_type("result", "str", "str"),
                desc="one fails",
            ))],
        )
        assert results[0].status == "fail"

    def test_not_inverts(self):
        """NOT(pass) → fail."""
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("not_int", pred_not(pred_type("result", "int", "int"), desc="not int"))],
        )
        assert results[0].status == "fail"


class TestCustomAndEdgeCases:
    def test_custom_skipped(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("custom", pred_custom("Must be efficient"))],
        )
        assert results[0].status == "skip"
        assert "CUSTOM" in results[0].reason

    def test_function_not_found(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "nonexistent",
            [_inv("typed", pred_type("result", "int"))],
        )
        assert results[0].status == "skip"
        assert "not found" in results[0].reason

    def test_syntax_error(self):
        results = check_invariants_against_ast(
            "def broken(:\n", "broken",
            [_inv("typed", pred_type("result", "int"))],
        )
        assert results[0].status == "skip"

    def test_true_always_passes(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [_inv("tautology", pred_true("always"))],
        )
        assert results[0].status == "pass"

    def test_multiple_invariants(self):
        results = check_invariants_against_ast(
            SOURCE_TYPED, "compute",
            [
                _inv("typed", pred_type("result", "int", "int")),
                _inv("custom", pred_custom("semantic")),
                _inv("wrong_type", pred_type("result", "str", "str")),
            ],
        )
        assert len(results) == 3
        assert results[0].status == "pass"
        assert results[1].status == "skip"
        assert results[2].status == "fail"
