"""Mutation-targeted tests for prescriptive spec serialization.

Targets surviving SWAP + VALUE mutants in to_dict/from_dict methods.
"""

from __future__ import annotations

import tempfile

from lintgate.specification.prescriptive.spec import (
    ForbiddenBehavior,
    GenerationConstraint,
    Invariant,
    Predicate,
    PredicateOp,
    PrescriptiveSpec,
    PrescriptiveSpecComposer,
    PrescriptiveWorkflowRecord,
    RefinementObligation,
    StateTransition,
    StateVariable,
    TestObligation,
    _target_hash,
    load_workflow_record,
    pred_custom,
    pred_eq,
    pred_pure,
    pred_true,
    save_workflow_record,
)

# ── Predicate.to_dict VALUE ──────────────────────────────────────────


class TestPredicateToDict:
    def test_exact_fields(self):
        p = Predicate(op=PredicateOp.EQ, subject="x", value=42, description="x is 42")
        d = p.to_dict()
        assert d["op"] == "eq"
        assert d["subject"] == "x"
        assert d["value"] == 42
        assert d["description"] == "x is 42"
        assert "target" not in d  # empty target omitted
        assert "operands" not in d  # empty operands omitted

    def test_empty_fields_omitted(self):
        """Empty subject/target/value/operands/description should not appear in dict."""
        p = Predicate(op=PredicateOp.TRUE)
        d = p.to_dict()
        assert d == {"op": "true"}
        assert "subject" not in d
        assert "target" not in d
        assert "value" not in d
        assert "operands" not in d
        assert "description" not in d

    def test_target_field_preserved(self):
        """Predicate with non-empty target must have 'target' key in dict."""
        p = Predicate(op=PredicateOp.EQ, subject="x", target="y", value=1)
        d = p.to_dict()
        assert "target" in d
        assert d["target"] == "y"

    def test_none_value_omitted(self):
        p = Predicate(op=PredicateOp.EQ, subject="x", value=None)
        d = p.to_dict()
        assert "value" not in d

    def test_compound_predicate(self):
        p = Predicate(
            op=PredicateOp.AND,
            operands=[
                Predicate(op=PredicateOp.EQ, subject="a", value=1),
                Predicate(op=PredicateOp.GT, subject="b", value=2),
            ],
            description="compound",
        )
        d = p.to_dict()
        assert d["op"] == "and"
        assert len(d["operands"]) == 2
        assert d["operands"][0]["op"] == "eq"
        assert d["operands"][1]["op"] == "gt"


# ── StateVariable round-trip SWAP + VALUE ────────────────────────────


class TestStateVariableRoundTrip:
    def test_to_dict_exact(self):
        sv = StateVariable("count", "int", "0", "a counter")
        d = sv.to_dict()
        assert d == {
            "name": "count",
            "type_hint": "int",
            "initial_value": "0",
            "description": "a counter",
        }

    def test_from_dict_exact(self):
        d = {"name": "count", "type_hint": "int", "initial_value": "0", "description": "a counter"}
        sv = StateVariable.from_dict(d)
        assert sv.name == "count"
        assert sv.type_hint == "int"
        assert sv.initial_value == "0"
        assert sv.description == "a counter"

    def test_round_trip(self):
        sv = StateVariable("size", "float", "1.0", "the size")
        assert StateVariable.from_dict(sv.to_dict()).name == "size"
        assert StateVariable.from_dict(sv.to_dict()).type_hint == "float"

    def test_swap_name_type(self):
        """name and type_hint are not interchangeable."""
        d = {"name": "alpha", "type_hint": "str", "initial_value": "", "description": ""}
        sv = StateVariable.from_dict(d)
        assert sv.name == "alpha"
        assert sv.type_hint == "str"
        assert sv.name != sv.type_hint

    def test_missing_keys_use_defaults(self):
        sv = StateVariable.from_dict({})
        assert sv.name == ""
        assert sv.type_hint == ""
        assert sv.initial_value == ""
        assert sv.description == ""


# ── StateTransition.to_dict VALUE ────────────────────────────────────


class TestStateTransitionToDict:
    def test_exact_fields(self):
        st = StateTransition("go", pred_true("pre"), pred_eq("x", 1, "post"), "transition", "src")
        d = st.to_dict()
        assert d["name"] == "go"
        assert d["description"] == "transition"
        assert d["source_claim"] == "src"
        assert d["precondition"]["op"] == "true"
        assert d["postcondition"]["op"] == "eq"


# ── Invariant.to_dict VALUE ──────────────────────────────────────────


class TestInvariantToDict:
    def test_exact_fields(self):
        inv = Invariant(
            "bounded", pred_eq("x", 10, "eq10"), "x must be 10", "theory", 0.9, "safety"
        )
        d = inv.to_dict()
        assert d["name"] == "bounded"
        assert d["description"] == "x must be 10"
        assert d["source"] == "theory"
        assert d["confidence"] == 0.9
        assert d["kind"] == "safety"
        assert d["predicate"]["op"] == "eq"


# ── ForbiddenBehavior.to_dict VALUE ──────────────────────────────────


class TestForbiddenBehaviorToDict:
    def test_exact_fields(self):
        fb = ForbiddenBehavior(pred_custom("no loops"), "no loops", "compass", "hard")
        d = fb.to_dict()
        assert d["description"] == "no loops"
        assert d["source"] == "compass"
        assert d["severity"] == "hard"
        assert d["predicate"]["op"] == "custom"


# ── GenerationConstraint round-trip SWAP + VALUE ─────────────────────


class TestGenerationConstraintRoundTrip:
    def test_to_dict_exact(self):
        gc = GenerationConstraint("must_use", pred_pure(), "must be pure", 1)
        d = gc.to_dict()
        assert d["constraint_type"] == "must_use"
        assert d["description"] == "must be pure"
        assert d["priority"] == 1
        assert d["predicate"]["op"] == "pure"

    def test_from_dict_exact(self):
        d = {
            "constraint_type": "must_not_use",
            "description": "no globals",
            "priority": 2,
            "predicate": {"op": "custom", "description": "no globals"},
        }
        gc = GenerationConstraint.from_dict(d)
        assert gc.constraint_type == "must_not_use"
        assert gc.description == "no globals"
        assert gc.priority == 2
        assert gc.predicate is not None
        assert gc.predicate.op == PredicateOp.CUSTOM

    def test_swap_type_description(self):
        """constraint_type and description are not interchangeable."""
        gc = GenerationConstraint("must_use", None, "be pure", 3)
        assert gc.constraint_type == "must_use"
        assert gc.description == "be pure"
        assert gc.constraint_type != gc.description

    def test_missing_keys_use_defaults(self):
        gc = GenerationConstraint.from_dict({})
        assert gc.constraint_type == ""
        assert gc.description == ""
        assert gc.priority == 5
        assert gc.predicate is None


# ── RefinementObligation round-trip SWAP + VALUE ─────────────────────


class TestRefinementObligationRoundTrip:
    def test_to_dict_exact(self):
        ro = RefinementObligation("VALUE", True, "value mutants survived")
        d = ro.to_dict()
        assert d == {
            "category": "VALUE",
            "expected_kill": True,
            "rationale": "value mutants survived",
        }

    def test_from_dict_exact(self):
        d = {"category": "BOUNDARY", "expected_kill": False, "rationale": "boundary ok"}
        ro = RefinementObligation.from_dict(d)
        assert ro.category == "BOUNDARY"
        assert ro.expected_kill is False
        assert ro.rationale == "boundary ok"

    def test_swap_category_rationale(self):
        d = {"category": "SWAP", "expected_kill": True, "rationale": "swap survived"}
        ro = RefinementObligation.from_dict(d)
        assert ro.category == "SWAP"
        assert ro.rationale == "swap survived"
        assert ro.category != ro.rationale

    def test_missing_keys_use_defaults(self):
        """Missing keys must use correct defaults — not mutated values."""
        ro = RefinementObligation.from_dict({})
        assert ro.category == ""
        assert ro.expected_kill is False
        assert ro.rationale == ""

    def test_missing_expected_kill_defaults_false(self):
        """expected_kill default is False, not True."""
        ro = RefinementObligation.from_dict({"category": "VALUE"})
        assert ro.expected_kill is False

    def test_expected_kill_key_name_matters(self):
        """The key must be exactly 'expected_kill', not empty string."""
        ro = RefinementObligation.from_dict({"expected_kill": True})
        assert ro.expected_kill is True


# ── TestObligation round-trip SWAP + VALUE ───────────────────────────


class TestTestObligationRoundTrip:
    def test_from_dict_exact(self):
        d = {
            "kind": "exact_value",
            "description": "pin output",
            "estimated_info_gain": 0.5,
            "suggested_assertion": "assert x == 1",
            "targets_function": "mod::f",
        }
        to = TestObligation.from_dict(d)
        assert to.kind == "exact_value"
        assert to.description == "pin output"
        assert to.estimated_info_gain == 0.5
        assert to.suggested_assertion == "assert x == 1"
        assert to.targets_function == "mod::f"

    def test_swap_kind_description(self):
        to = TestObligation("boundary", "off-by-one", 0.3, "assert x >= 0", "mod::g")
        assert to.kind == "boundary"
        assert to.description == "off-by-one"
        assert to.kind != to.description

    def test_missing_keys_use_defaults(self):
        to = TestObligation.from_dict({})
        assert to.kind == ""
        assert to.description == ""
        assert to.estimated_info_gain == 0.0
        assert to.suggested_assertion == ""
        assert to.targets_function == ""


# ── PrescriptiveSpec.to_dict VALUE ───────────────────────────────────


class TestPrescriptiveSpecToDict:
    def test_key_fields(self):
        spec = PrescriptiveSpec(
            spec_id="abc",
            target_key="mod::f",
            problem_class="pure",
            mode="prospective",
            return_type="int",
            prescriptive_sigma=5,
            created_at=1000.0,
        )
        d = spec.to_dict()
        assert d["spec_id"] == "abc"
        assert d["target_key"] == "mod::f"
        assert d["problem_class"] == "pure"
        assert d["mode"] == "prospective"
        assert d["return_type"] == "int"
        assert d["prescriptive_sigma"] == 5


# ── PrescriptiveWorkflowRecord round-trip SWAP + VALUE ───────────────


class TestWorkflowRecordRoundTrip:
    def test_to_dict_exact(self):
        r = PrescriptiveWorkflowRecord(spec_id="s1", target_key="mod::f", state="compiled")
        d = r.to_dict()
        assert d["spec_id"] == "s1"
        assert d["target_key"] == "mod::f"
        assert d["state"] == "compiled"

    def test_from_dict_exact(self):
        d = {
            "spec_id": "s2",
            "target_key": "mod::g",
            "state": "verifying",
            "generation_mode": "symbolic_only",
        }
        r = PrescriptiveWorkflowRecord.from_dict(d)
        assert r.spec_id == "s2"
        assert r.target_key == "mod::g"
        assert r.state == "verifying"
        assert r.generation_mode == "symbolic_only"

    def test_swap_spec_id_target_key(self):
        r = PrescriptiveWorkflowRecord(spec_id="aaa", target_key="bbb")
        assert r.spec_id == "aaa"
        assert r.target_key == "bbb"
        assert r.spec_id != r.target_key

    def test_missing_keys_use_defaults(self):
        r = PrescriptiveWorkflowRecord.from_dict({})
        assert r.spec_id == ""
        assert r.target_key == ""
        assert r.state == "composed"
        assert r.generation_mode == ""
        assert r.compiled_targets_path == ""

    def test_persistence_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = PrescriptiveWorkflowRecord(spec_id="p1", target_key="mod::h", state="composed")
            save_workflow_record(tmp, r)
            loaded = load_workflow_record(tmp, "mod::h")
            assert loaded is not None
            assert loaded.spec_id == "p1"
            assert loaded.target_key == "mod::h"
            assert loaded.state == "composed"


# ── PrescriptiveSpecComposer._classify_problem_class VALUE ───────────


class TestClassifyProblemClass:
    def test_pure_from_hint(self):
        composer = PrescriptiveSpecComposer()
        result = composer._classify_problem_class(None, {"problem_class": "pure"})
        assert result == "pure"

    def test_stateful_from_hint(self):
        composer = PrescriptiveSpecComposer()
        result = composer._classify_problem_class(None, {"problem_class": "stateful"})
        assert result == "stateful"

    def test_distributed_from_hint(self):
        composer = PrescriptiveSpecComposer()
        result = composer._classify_problem_class(None, {"problem_class": "distributed"})
        assert result == "distributed"

    def test_default_pure(self):
        composer = PrescriptiveSpecComposer()
        result = composer._classify_problem_class(None, None)
        assert result == "pure"

    def test_invalid_hint_ignored(self):
        composer = PrescriptiveSpecComposer()
        result = composer._classify_problem_class(None, {"problem_class": "invalid"})
        assert result == "pure"

    def test_empty_hint_defaults_pure(self):
        composer = PrescriptiveSpecComposer()
        result = composer._classify_problem_class(None, {})
        assert result == "pure"

    def test_pure_is_exact_string(self):
        """Default must be exactly 'pure', not any other string."""
        composer = PrescriptiveSpecComposer()
        result = composer._classify_problem_class(None, None)
        assert result == "pure"
        assert len(result) == 4

    def test_pure_from_func_spec(self):
        """func_spec with is_pure=True → 'pure'."""
        from types import SimpleNamespace

        composer = PrescriptiveSpecComposer()
        fs = SimpleNamespace(
            core=SimpleNamespace(is_pure=True), testability=SimpleNamespace(is_stateful=False)
        )
        result = composer._classify_problem_class(fs, None)
        assert result == "pure"

    def test_stateful_from_func_spec(self):
        """func_spec with is_stateful=True → 'stateful'."""
        from types import SimpleNamespace

        composer = PrescriptiveSpecComposer()
        fs = SimpleNamespace(
            core=SimpleNamespace(is_pure=False), testability=SimpleNamespace(is_stateful=True)
        )
        result = composer._classify_problem_class(fs, None)
        assert result == "stateful"

    def test_not_pure_not_stateful_defaults_pure(self):
        """func_spec with is_pure=False, is_stateful=False → 'pure' (default)."""
        from types import SimpleNamespace

        composer = PrescriptiveSpecComposer()
        fs = SimpleNamespace(
            core=SimpleNamespace(is_pure=False), testability=SimpleNamespace(is_stateful=False)
        )
        result = composer._classify_problem_class(fs, None)
        assert result == "pure"

    def test_func_spec_missing_is_pure_attr(self):
        """func_spec.core without is_pure attr → getattr default False → not pure from core."""
        from types import SimpleNamespace

        composer = PrescriptiveSpecComposer()
        fs = SimpleNamespace(core=SimpleNamespace(), testability=SimpleNamespace())
        result = composer._classify_problem_class(fs, None)
        assert result == "pure"  # falls through to default

    def test_func_spec_missing_is_stateful_attr(self):
        """func_spec.testability without is_stateful → getattr default False → not stateful."""
        from types import SimpleNamespace

        composer = PrescriptiveSpecComposer()
        fs = SimpleNamespace(core=SimpleNamespace(), testability=SimpleNamespace())
        result = composer._classify_problem_class(fs, None)
        assert result == "pure"

    def test_hint_overrides_func_spec(self):
        """interface_hint takes priority over func_spec."""
        from types import SimpleNamespace

        composer = PrescriptiveSpecComposer()
        fs = SimpleNamespace(
            core=SimpleNamespace(is_pure=True), testability=SimpleNamespace(is_stateful=False)
        )
        result = composer._classify_problem_class(fs, {"problem_class": "distributed"})
        assert result == "distributed"


# ── _target_hash VALUE ───────────────────────────────────────────────


class TestTargetHash:
    def test_deterministic(self):
        h1 = _target_hash("mod::func")
        h2 = _target_hash("mod::func")
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        h1 = _target_hash("mod::func_a")
        h2 = _target_hash("mod::func_b")
        assert h1 != h2

    def test_returns_string(self):
        assert isinstance(_target_hash("anything"), str)
