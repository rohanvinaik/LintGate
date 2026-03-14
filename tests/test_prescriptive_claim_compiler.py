"""Tests for claim compiler, new AST ops, evidence split, and graph projection."""

from __future__ import annotations

import json
import os
import tempfile

from lintgate.specification.prescriptive_spec import (
    Predicate,
    PredicateOp,
    compile_claim,
    pred_no_raise,
    pred_param_count_lte,
    pred_pure,
    pred_raises,
    pred_returns_non_null,
)


# ── Claim compiler ────────────────────────────────────────────────────


class TestClaimCompiler:
    """compile_claim turns natural language into typed predicates."""

    def test_must_return_int(self):
        p = compile_claim("must return int")
        assert p.op == PredicateOp.IS_TYPE
        assert p.value == "int"

    def test_returns_a_list(self):
        p = compile_claim("returns a list of items")
        assert p.op == PredicateOp.IS_TYPE
        assert p.value == "list"

    def test_should_return_bool(self):
        p = compile_claim("should always return bool")
        assert p.op == PredicateOp.IS_TYPE
        assert p.value == "bool"

    def test_must_be_pure(self):
        p = compile_claim("must be pure")
        assert p.op == PredicateOp.PURE

    def test_no_side_effects(self):
        p = compile_claim("no side effects allowed")
        assert p.op == PredicateOp.PURE

    def test_side_effect_free(self):
        p = compile_claim("side-effect-free computation")
        assert p.op == PredicateOp.PURE

    def test_must_not_return_none(self):
        p = compile_claim("must not return None")
        assert p.op == PredicateOp.RETURNS_NON_NULL

    def test_never_returns_none(self):
        p = compile_claim("never returns None")
        assert p.op == PredicateOp.RETURNS_NON_NULL

    def test_must_raise_valueerror(self):
        p = compile_claim("must raise ValueError on invalid input")
        assert p.op == PredicateOp.RAISES
        assert p.value == "ValueError"

    def test_raises_typeerror(self):
        p = compile_claim("raises TypeError for wrong type")
        assert p.op == PredicateOp.RAISES
        assert p.value == "TypeError"

    def test_must_not_raise(self):
        p = compile_claim("must not raise exceptions")
        assert p.op == PredicateOp.NO_RAISE

    def test_no_exceptions(self):
        p = compile_claim("no exceptions should be thrown")
        assert p.op == PredicateOp.NO_RAISE

    def test_must_call_validate(self):
        p = compile_claim("must call validate before processing")
        assert p.op == PredicateOp.CALLS
        assert p.subject == "validate"

    def test_must_not_mutate(self):
        """'must not mutate' maps to PURE as best approximation."""
        p = compile_claim("must not mutate the input list")
        assert p.op == PredicateOp.PURE

    def test_at_most_3_params(self):
        p = compile_claim("at most 3 parameters")
        assert p.op == PredicateOp.PARAM_COUNT_LTE
        assert p.value == 3

    def test_compound_claim(self):
        """Claims matching multiple patterns produce AND."""
        p = compile_claim("must return int and must be pure")
        assert p.op == PredicateOp.AND
        ops = {child.op for child in p.operands}
        assert PredicateOp.IS_TYPE in ops
        assert PredicateOp.PURE in ops

    def test_unrecognized_falls_back_to_custom(self):
        p = compile_claim("the algorithm should converge in O(n log n)")
        assert p.op == PredicateOp.CUSTOM

    def test_case_insensitive(self):
        p = compile_claim("MUST RETURN INT")
        assert p.op == PredicateOp.IS_TYPE


# ── New AST checkers ──────────────────────────────────────────────────


class TestPureChecker:
    def test_pure_function_passes(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def add(x, y):\n    return x + y\n"
        inv = Invariant("pure", pred_pure(), "must be pure", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "add", [inv])
        assert results[0].status == "pass"

    def test_global_fails(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "counter = 0\ndef inc():\n    global counter\n    counter += 1\n"
        inv = Invariant("pure", pred_pure(), "must be pure", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "inc", [inv])
        assert results[0].status == "fail"
        assert "global" in results[0].reason

    def test_print_fails(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def noisy(x):\n    print(x)\n    return x\n"
        inv = Invariant("pure", pred_pure(), "must be pure", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "noisy", [inv])
        assert results[0].status == "fail"


class TestReturnsNonNull:
    def test_non_null_passes(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def f(x):\n    return x + 1\n"
        inv = Invariant("nn", pred_returns_non_null(), "no None", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "pass"

    def test_bare_return_fails(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def f(x):\n    if x:\n        return\n    return 1\n"
        inv = Invariant("nn", pred_returns_non_null(), "no None", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "fail"

    def test_return_none_fails(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def f(x):\n    return None\n"
        inv = Invariant("nn", pred_returns_non_null(), "no None", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "fail"

    def test_no_return_fails(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def f(x):\n    x + 1\n"
        inv = Invariant("nn", pred_returns_non_null(), "no None", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "fail"


class TestRaisesChecker:
    def test_raises_found(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def validate(x):\n    if not x:\n        raise ValueError('bad')\n"
        inv = Invariant("raises", pred_raises("ValueError"), "raises VE", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "validate", [inv])
        assert results[0].status == "pass"

    def test_raises_wrong_type(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def validate(x):\n    if not x:\n        raise TypeError('bad')\n"
        inv = Invariant("raises", pred_raises("ValueError"), "raises VE", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "validate", [inv])
        assert results[0].status == "fail"

    def test_no_raise_passes(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def safe(x):\n    return x + 1\n"
        inv = Invariant("nr", pred_no_raise(), "no raise", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "safe", [inv])
        assert results[0].status == "pass"

    def test_no_raise_fails(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def unsafe(x):\n    raise RuntimeError('boom')\n"
        inv = Invariant("nr", pred_no_raise(), "no raise", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "unsafe", [inv])
        assert results[0].status == "fail"


class TestParamCount:
    def test_within_limit(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def f(a, b):\n    return a + b\n"
        inv = Invariant("pc", pred_param_count_lte(3), "max 3", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "pass"

    def test_exceeds_limit(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "def f(a, b, c, d):\n    pass\n"
        inv = Invariant("pc", pred_param_count_lte(2), "max 2", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "fail"

    def test_self_excluded(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant

        source = "class C:\n    def method(self, a, b):\n        pass\n"
        inv = Invariant("pc", pred_param_count_lte(2), "max 2", "src", 0.8, "safety")
        results = check_invariants_against_ast(source, "method", [inv])
        assert results[0].status == "pass"


# ── Evidence split in verify_refinement ───────────────────────────────


class TestEvidenceSplit:
    def test_structural_evidence_included(self):
        """verify_refinement returns structural_evidence from AST checks."""
        from lintgate.specification.prescriptive_backends import (
            PrescriptiveAdapter,
            PureBackend,
        )
        from lintgate.specification.prescriptive_spec import (
            Invariant,
            PrescriptiveSpec,
            pred_type,
        )

        spec = PrescriptiveSpec(
            spec_id="t",
            target_key="mod::compute",
            problem_class="pure",
            mode="prospective",
            invariants=[
                Invariant("typed", pred_type("result", "int"), "returns int", "src", 0.8, "safety"),
            ],
            prescriptive_sigma=2,
            created_at=1000.0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            # Write source file with matching return type
            (open(os.path.join(tmp, "mod.py"), "w")).write("def compute(x: int) -> int:\n    return x + 1\n")

            targets = PureBackend().compile(spec)
            adapter = PrescriptiveAdapter()
            verdict = adapter.verify_refinement(spec, targets, tmp, "mod.py", function="compute")

            assert "structural_evidence" in verdict
            assert len(verdict["structural_evidence"]) == 1
            assert verdict["structural_evidence"][0]["status"] == "pass"
            assert verdict["summary"]["structural"]["pass"] == 1

    def test_structural_fail_reflected(self):
        """Structural failure contributes to overall=fail."""
        from lintgate.specification.prescriptive_backends import (
            PrescriptiveAdapter,
            PureBackend,
        )
        from lintgate.specification.prescriptive_spec import (
            Invariant,
            PrescriptiveSpec,
            pred_type,
        )

        spec = PrescriptiveSpec(
            spec_id="t",
            target_key="mod::compute",
            problem_class="pure",
            mode="prospective",
            invariants=[
                Invariant("typed", pred_type("result", "str"), "returns str", "src", 0.8, "safety"),
            ],
            prescriptive_sigma=2,
            created_at=1000.0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            (open(os.path.join(tmp, "mod.py"), "w")).write("def compute(x: int) -> int:\n    return x + 1\n")

            targets = PureBackend().compile(spec)
            adapter = PrescriptiveAdapter()
            verdict = adapter.verify_refinement(spec, targets, tmp, "mod.py", function="compute")

            assert verdict["overall"] == "fail"
            assert verdict["summary"]["structural"]["fail"] == 1


# ── Claim compiler → AST checker integration ─────────────────────────


class TestClaimToAST:
    """End-to-end: natural language claim → compiled predicate → AST check."""

    def test_must_return_int_checked(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant, compile_claim

        pred = compile_claim("must return int")
        inv = Invariant("typed", pred, "must return int", "src", 0.8, "safety")

        source = "def f(x) -> int:\n    return x\n"
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "pass"

        source_bad = "def f(x) -> str:\n    return str(x)\n"
        results = check_invariants_against_ast(source_bad, "f", [inv])
        assert results[0].status == "fail"

    def test_must_be_pure_checked(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant, compile_claim

        pred = compile_claim("no side effects")
        inv = Invariant("pure", pred, "no side effects", "src", 0.8, "safety")

        source_good = "def f(x):\n    return x * 2\n"
        results = check_invariants_against_ast(source_good, "f", [inv])
        assert results[0].status == "pass"

        source_bad = "def f(x):\n    print(x)\n    return x\n"
        results = check_invariants_against_ast(source_bad, "f", [inv])
        assert results[0].status == "fail"

    def test_custom_claim_still_skips(self):
        from lintgate.specification.prescriptive_ast_checker import check_invariants_against_ast
        from lintgate.specification.prescriptive_spec import Invariant, compile_claim

        pred = compile_claim("the algorithm should converge quickly")
        inv = Invariant("perf", pred, "converge", "src", 0.8, "safety")

        source = "def f(x):\n    return x\n"
        results = check_invariants_against_ast(source, "f", [inv])
        assert results[0].status == "skip"


# ── Graph projection ─────────────────────────────────────────────────


class TestGraphProjection:
    def test_build_and_save_load(self):
        from lintgate.specification.prescriptive_projection import (
            FunctionProjection,
            build_projection_from_ledger,
            load_projection,
            save_projection,
        )

        ledger_data = {
            "mod::func_a": {
                "coupling_surface": 3,
                "covering_tests": ["test_a"],
                "priority_band": "P0",
                "is_pure": True,
                "estimated_sigma": 8,
            },
            "mod::func_b": {
                "coupling_surface": 1,
                "covering_tests": [],
                "priority_band": "P2",
                "is_pure": False,
                "estimated_sigma": 3,
            },
        }
        call_graph = {"mod::func_a": ["mod::func_b"]}

        projections = build_projection_from_ledger(ledger_data, call_graph)
        assert len(projections) == 2
        assert projections["mod::func_a"].fan_out == 1
        assert projections["mod::func_b"].fan_in == 1
        assert projections["mod::func_a"].is_pure is True
        assert "mod::func_b" in projections["mod::func_a"].neighbor_keys

        with tempfile.TemporaryDirectory() as tmp:
            save_projection(tmp, projections)
            loaded = load_projection(tmp)
            assert len(loaded) == 2
            assert loaded["mod::func_a"].fan_out == 1
            assert loaded["mod::func_a"].priority_band == "P0"

    def test_load_single(self):
        from lintgate.specification.prescriptive_projection import (
            FunctionProjection,
            load_single_projection,
            save_projection,
        )

        with tempfile.TemporaryDirectory() as tmp:
            save_projection(tmp, {
                "mod::f": FunctionProjection(function_key="mod::f", fan_in=2, fan_out=1),
            })
            proj = load_single_projection(tmp, "mod::f")
            assert proj is not None
            assert proj.fan_in == 2

            assert load_single_projection(tmp, "mod::missing") is None

    def test_empty_project(self):
        from lintgate.specification.prescriptive_projection import load_projection

        with tempfile.TemporaryDirectory() as tmp:
            assert load_projection(tmp) == {}
