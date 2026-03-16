"""Tests for PrescriptiveSpec IR, composer, persistence, and target resolution."""

from __future__ import annotations

import json
import tempfile

from lintgate.specification.prescriptive_sigma import (
    compute_convergence_signal,
    estimate_prescriptive_sigma,
)
from lintgate.specification.prescriptive_spec import (
    ForbiddenBehavior,
    Invariant,
    Predicate,
    PredicateOp,
    PrescriptiveSpec,
    PrescriptiveSpecComposer,
    StateTransition,
    StateVariable,
    load_all_specs,
    load_spec,
    load_spec_index,
    pred_and,
    pred_custom,
    pred_eq,
    pred_gt,
    pred_lt,
    pred_not,
    pred_or,
    pred_true,
    pred_type,
    resolve_targets,
    save_spec,
    spec_coverage,
)

# ── Predicate IR tests ────────────────────────────────────────────────


class TestPredicateIR:
    def test_predicate_ir_round_trip_leaf(self):
        """Leaf predicates round-trip through to_dict/from_dict."""
        for op in PredicateOp:
            p = Predicate(op=op, subject="x", value=42, description=f"test {op.value}")
            restored = Predicate.from_dict(p.to_dict())
            assert restored.op == p.op
            assert restored.subject == p.subject
            assert restored.value == p.value
            assert restored.description == p.description

    def test_predicate_ir_round_trip_compound(self):
        """Compound predicates (AND/OR/NOT) round-trip."""
        inner_a = pred_eq("x", 1, "x is 1")
        inner_b = pred_lt("y", 10, "y under 10")
        compound = pred_and(inner_a, inner_b, desc="both hold")

        d = compound.to_dict()
        restored = Predicate.from_dict(d)
        assert restored.op == PredicateOp.AND
        assert len(restored.operands) == 2
        assert restored.operands[0].subject == "x"
        assert restored.operands[1].subject == "y"

    def test_predicate_normalize_sorts_operands(self):
        """pred_and(b, a).normalize() == pred_and(a, b).normalize()"""
        a = pred_eq("a", 1, "a is 1")
        b = pred_eq("b", 2, "b is 2")

        fwd = pred_and(a, b).normalize()
        rev = pred_and(b, a).normalize()
        assert fwd == rev

    def test_predicate_normalize_flattens_nested(self):
        """Nested AND(AND(a, b), c) flattens to AND(a, b, c)."""
        a = pred_eq("a", 1, "a")
        b = pred_eq("b", 2, "b")
        c = pred_eq("c", 3, "c")

        nested = pred_and(pred_and(a, b), c)
        flat = nested.normalize()
        assert flat.op == PredicateOp.AND
        assert len(flat.operands) == 3

    def test_predicate_equality_structural(self):
        """Structural equality on normalized form."""
        p1 = pred_and(pred_eq("x", 1, "x"), pred_eq("y", 2, "y"))
        p2 = pred_and(pred_eq("y", 2, "y"), pred_eq("x", 1, "x"))
        assert p1 == p2

    def test_predicate_inequality(self):
        p1 = pred_eq("x", 1)
        p2 = pred_eq("x", 2)
        assert p1 != p2

    def test_predicate_custom_not_evaluable(self):
        """CUSTOM op is marked correctly and preserves description."""
        p = pred_custom("This function must not mutate its input")
        assert p.op == PredicateOp.CUSTOM
        assert "mutate" in p.description
        assert p.subject == ""

    def test_predicate_hash(self):
        """Predicates are hashable and usable in sets."""
        p1 = pred_eq("x", 1, "x is 1")
        p2 = pred_eq("x", 1, "x is 1")
        assert hash(p1) == hash(p2)
        assert len({p1, p2}) == 1

    def test_predicate_from_dict_empty(self):
        """Empty dict returns TRUE predicate."""
        p = Predicate.from_dict({})
        assert p.op == PredicateOp.TRUE

    def test_convenience_constructors(self):
        """All convenience constructors produce correct ops."""
        assert pred_eq("x", 1).op == PredicateOp.EQ
        assert pred_lt("x", 1).op == PredicateOp.LT
        assert pred_gt("x", 1).op == PredicateOp.GT
        assert pred_type("x", "int").op == PredicateOp.IS_TYPE
        assert pred_and().op == PredicateOp.AND
        assert pred_or().op == PredicateOp.OR
        assert pred_not(pred_eq("x", 1)).op == PredicateOp.NOT
        assert pred_true().op == PredicateOp.TRUE


# ── PrescriptiveSpec round-trip ───────────────────────────────────────


class TestPrescriptiveSpec:
    def _make_spec(self, **overrides):
        defaults = {
            "spec_id": "abc123",
            "target_key": "module::func",
            "problem_class": "pure",
            "mode": "prospective",
            "parameters": [{"name": "x", "type": "int", "description": "input"}],
            "return_type": "int",
            "return_description": "the result",
            "invariants": [
                Invariant(
                    name="bounded",
                    predicate=pred_gt("result", 0, "positive result"),
                    description="Result must be positive",
                    source="compass:toward:0",
                    confidence=0.8,
                    kind="safety",
                )
            ],
            "forbidden_behaviors": [
                ForbiddenBehavior(
                    predicate=pred_custom("must not mutate input"),
                    description="No input mutation",
                    source="compass:forbidden:0",
                    severity="hard",
                )
            ],
            "prescriptive_sigma": 5,
            "created_at": 1000.0,
        }
        defaults.update(overrides)
        return PrescriptiveSpec(**defaults)

    def test_prescriptive_spec_round_trip(self):
        """Full PrescriptiveSpec to_dict/from_dict cycle."""
        spec = self._make_spec()
        d = spec.to_dict()
        restored = PrescriptiveSpec.from_dict(d)

        assert restored.spec_id == spec.spec_id
        assert restored.target_key == spec.target_key
        assert restored.problem_class == spec.problem_class
        assert restored.mode == spec.mode
        assert len(restored.invariants) == 1
        assert restored.invariants[0].name == "bounded"
        assert len(restored.forbidden_behaviors) == 1
        assert restored.forbidden_behaviors[0].severity == "hard"
        assert restored.prescriptive_sigma == 5

    def test_prescriptive_spec_round_trip_stateful(self):
        """Stateful spec with state variables and transitions."""
        spec = self._make_spec(
            problem_class="stateful",
            state_variables=[
                StateVariable(
                    name="count", type_hint="int", initial_value="0", description="counter"
                )
            ],
            allowed_transitions=[
                StateTransition(
                    name="increment",
                    precondition=pred_true("always"),
                    postcondition=pred_gt("count", 0, "positive"),
                    description="Increment counter",
                    source_claim="compass:toward:1",
                )
            ],
        )
        d = spec.to_dict()
        restored = PrescriptiveSpec.from_dict(d)
        assert len(restored.state_variables) == 1
        assert restored.state_variables[0].name == "count"
        assert len(restored.allowed_transitions) == 1
        assert restored.allowed_transitions[0].name == "increment"

    def test_json_serializable(self):
        """to_dict output is JSON-serializable."""
        spec = self._make_spec()
        serialized = json.dumps(spec.to_dict())
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["spec_id"] == "abc123"


# ── Composer tests ────────────────────────────────────────────────────


class _FakeDirective:
    def __init__(self, kind, text, source=""):
        self.kind = kind
        self.text = text
        self.source = source


class _FakeClaim:
    def __init__(self, text, confidence=0.8, source=""):
        self.text = text
        self.confidence = confidence
        self.source = source


class _FakeAxis:
    def __init__(self, name, claims=None):
        self.name = name
        self.claims = claims or []


class _FakeCompass:
    def __init__(self, directives=None, axes=None, frozen_hash="abc"):
        self.directives = directives or []
        self.axes = axes or {}
        self.frozen_hash = frozen_hash


class _FakeTestability:
    def __init__(self, is_stateful=False):
        self.is_stateful = is_stateful


class _FakeCore:
    def __init__(self, is_pure=True, estimated_sigma=5):
        self.is_pure = is_pure
        self.estimated_sigma = estimated_sigma


class _FakeTraceability:
    def __init__(self, assertion_count=2):
        self.assertion_count = assertion_count


class _FakeFuncSpec:
    def __init__(
        self, function_key="mod::func", is_pure=True, is_stateful=False, sigma=5, assertions=2
    ):
        self.function_key = function_key
        self.core = _FakeCore(is_pure=is_pure, estimated_sigma=sigma)
        self.testability = _FakeTestability(is_stateful=is_stateful)
        self.traceability = _FakeTraceability(assertion_count=assertions)


class TestComposer:
    def test_compose_prospective_pure(self):
        """Compass toward → invariants, away/forbidden → ForbiddenBehavior."""
        compass = _FakeCompass(
            directives=[
                _FakeDirective("toward", "Output must be bounded"),
                _FakeDirective("forbidden", "Must not mutate input"),
                _FakeDirective("away", "Avoid global state"),
            ]
        )
        composer = PrescriptiveSpecComposer()
        spec = composer.compose_prospective(
            target_key="mod::func",
            compass=compass,
            theory_profile={},
        )

        assert spec.problem_class == "pure"
        assert spec.mode == "prospective"
        assert len(spec.invariants) == 1
        assert spec.invariants[0].description == "Output must be bounded"
        assert len(spec.forbidden_behaviors) == 2
        assert spec.forbidden_behaviors[0].severity == "hard"
        assert spec.forbidden_behaviors[1].severity == "soft"

    def test_compose_prospective_stateful(self):
        """State variables + transitions from interface_hint."""
        compass = _FakeCompass(directives=[])
        composer = PrescriptiveSpecComposer()
        spec = composer.compose_prospective(
            target_key="mod::cache",
            compass=compass,
            theory_profile={},
            interface_hint={
                "problem_class": "stateful",
                "parameters": [{"name": "key", "type": "str", "description": "cache key"}],
                "return_type": "Any",
                "state_variables": [
                    {
                        "name": "store",
                        "type_hint": "dict",
                        "initial_value": "{}",
                        "description": "backing store",
                    }
                ],
                "transitions": [
                    {
                        "name": "put",
                        "precondition": {"op": "true", "description": "always"},
                        "postcondition": {
                            "op": "has_attr",
                            "subject": "store",
                            "value": "key",
                            "description": "key present after put",
                        },
                        "description": "Insert key",
                        "source_claim": "compass:toward:0",
                    }
                ],
            },
        )

        assert spec.problem_class == "stateful"
        assert len(spec.state_variables) == 1
        assert spec.state_variables[0].name == "store"
        assert len(spec.allowed_transitions) == 1
        assert spec.parameters[0]["name"] == "key"

    def test_compose_retrospective_enrichment(self):
        """FunctionSpec + theory → enriched PrescriptiveSpec."""
        compass = _FakeCompass(directives=[_FakeDirective("toward", "Keep functions pure")])
        theory = {
            "core_theory": {
                "claims": [
                    {
                        "text": "Because purity enables caching, prefer pure functions",
                        "confidence": 0.9,
                    }
                ]
            }
        }
        func_spec = _FakeFuncSpec(sigma=10, assertions=3)
        composer = PrescriptiveSpecComposer()
        spec = composer.compose_retrospective(
            func_spec=func_spec,
            compass=compass,
            theory_profile=theory,
        )

        assert spec.mode == "retrospective"
        assert spec.problem_class == "pure"
        # 1 from compass toward + 1 from theory
        assert len(spec.invariants) >= 2
        # Test obligation for sigma gap
        assert len(spec.test_obligations) == 1
        assert "sigma=10" in spec.test_obligations[0].description

    def test_invariant_extraction_from_toward(self):
        """Toward directives become Invariant with typed Predicates."""
        compass = _FakeCompass(
            directives=[
                _FakeDirective("toward", "All outputs must be validated"),
                _FakeDirective("toward", "Functions should be deterministic"),
            ]
        )
        composer = PrescriptiveSpecComposer()
        invariants = composer._extract_invariants_from_compass(compass)
        assert len(invariants) == 2
        assert all(inv.kind == "alignment" for inv in invariants)
        assert all(inv.predicate.op == PredicateOp.CUSTOM for inv in invariants)
        assert invariants[0].source == "compass:toward:0"
        assert invariants[1].source == "compass:toward:1"

    def test_forbidden_extraction(self):
        """Away + forbidden → ForbiddenBehavior with severity mapping."""
        compass = _FakeCompass(
            directives=[
                _FakeDirective("forbidden", "Never use eval()"),
                _FakeDirective("away", "Avoid mutable defaults"),
            ]
        )
        composer = PrescriptiveSpecComposer()
        forbidden = composer._extract_forbidden_from_compass(compass)
        assert len(forbidden) == 2
        assert forbidden[0].severity == "hard"
        assert forbidden[1].severity == "soft"

    def test_theory_claim_to_invariant_confidence_gate(self):
        """Claims below 0.6 confidence are rejected."""
        theory = {
            "core_theory": {
                "claims": [
                    {"text": "High confidence claim", "confidence": 0.9},
                    {"text": "Low confidence claim", "confidence": 0.4},
                ]
            }
        }
        composer = PrescriptiveSpecComposer()
        invariants = composer._theory_to_invariants(theory, "mod::func")
        assert len(invariants) == 1
        assert "High confidence" in invariants[0].description

    def test_theory_claim_causal_marker_boost(self):
        """Claims with causal markers get confidence boost."""
        theory = {
            "core_theory": {
                "claims": [
                    {
                        "text": "Because caching reduces latency, we use memoization",
                        "confidence": 0.7,
                    }
                ]
            }
        }
        composer = PrescriptiveSpecComposer()
        invariants = composer._theory_to_invariants(theory, "mod::func")
        assert len(invariants) == 1
        assert abs(invariants[0].confidence - 0.8) < 1e-9  # 0.7 + 0.1 boost

    def test_generation_constraint_composition(self):
        """Constraints from invariants + forbidden + algebraic laws."""
        compass = _FakeCompass(
            directives=[
                _FakeDirective("toward", "Output bounded"),
                _FakeDirective("forbidden", "No mutation"),
            ]
        )
        composer = PrescriptiveSpecComposer()
        spec = composer.compose_prospective(
            target_key="mod::f",
            compass=compass,
            theory_profile={},
        )
        # Should have constraints from invariant + forbidden
        assert len(spec.generation_constraints) >= 2
        types = {c.constraint_type for c in spec.generation_constraints}
        assert "must_not_use" in types

    def test_problem_class_classification(self):
        """Pure/stateful/distributed routing."""
        composer = PrescriptiveSpecComposer()

        # From interface hint
        assert (
            composer._classify_problem_class(None, {"problem_class": "distributed"})
            == "distributed"
        )

        # From func_spec pure
        fs_pure = _FakeFuncSpec(is_pure=True)
        assert composer._classify_problem_class(fs_pure, None) == "pure"

        # From func_spec stateful
        fs_stateful = _FakeFuncSpec(is_pure=False, is_stateful=True)
        assert composer._classify_problem_class(fs_stateful, None) == "stateful"

        # Default
        assert composer._classify_problem_class(None, None) == "pure"


# ── Sigma tests ───────────────────────────────────────────────────────


class TestPrescriptiveSigma:
    def test_prescriptive_sigma_estimation(self):
        """σ from spec structure."""
        spec = PrescriptiveSpec(
            spec_id="test",
            target_key="mod::func",
            problem_class="pure",
            mode="prospective",
            parameters=[{"name": "x", "type": "int", "description": ""}],
            invariants=[
                Invariant("inv1", pred_eq("x", 1), "test", "src", 0.8, "safety"),
                Invariant("inv2", pred_gt("x", 0), "test", "src", 0.8, "safety"),
            ],
            forbidden_behaviors=[
                ForbiddenBehavior(pred_custom("no mut"), "no mut", "src", "hard"),
            ],
        )
        sigma = estimate_prescriptive_sigma(spec)
        # 0 state × 1 + 2 invariants + 1 forbidden + 1 param + 0 alg = 4
        assert sigma == 4

    def test_prescriptive_sigma_stateful(self):
        """Stateful spec has higher sigma due to state × transitions."""
        spec = PrescriptiveSpec(
            spec_id="test",
            target_key="mod::cache",
            problem_class="stateful",
            mode="prospective",
            state_variables=[
                StateVariable("s1", "int", "0", "state"),
                StateVariable("s2", "str", '""', "state"),
            ],
            allowed_transitions=[
                StateTransition("t1", pred_true(), pred_eq("s1", 1), "init", "src"),
                StateTransition("t2", pred_eq("s1", 1), pred_eq("s1", 2), "next", "src"),
            ],
            invariants=[Invariant("inv1", pred_gt("s1", -1), "non-neg", "src", 0.8, "safety")],
        )
        sigma = estimate_prescriptive_sigma(spec)
        # 2 state × 2 transitions + 1 invariant = 5
        assert sigma == 5

    def test_prescriptive_sigma_empty(self):
        """Empty spec returns 0."""
        spec = PrescriptiveSpec(
            spec_id="test", target_key="mod::f", problem_class="pure", mode="prospective"
        )
        assert estimate_prescriptive_sigma(spec) == 0

    def test_sigma_convergence_signal_converged(self):
        """Converged when ratio between 0.5 and 2.0."""
        result = compute_convergence_signal(10, 12)
        assert result["assessment"] == "converged"
        assert result["delta"] == 2

    def test_sigma_convergence_signal_under_specified(self):
        """Under-specified when retrospective >> prescriptive."""
        result = compute_convergence_signal(5, 20)
        assert result["assessment"] == "under_specified"

    def test_sigma_convergence_signal_over_specified(self):
        """Over-specified when retrospective << prescriptive."""
        result = compute_convergence_signal(20, 5)
        assert result["assessment"] == "over_specified"

    def test_sigma_convergence_no_prescriptive(self):
        result = compute_convergence_signal(0, 10)
        assert result["assessment"] == "no_prescriptive_spec"


# ── Persistence tests ─────────────────────────────────────────────────


class TestPersistence:
    def test_persistence_save_load_index(self):
        """Save/load/index cycle on temp dir."""
        with tempfile.TemporaryDirectory() as tmp:
            spec = PrescriptiveSpec(
                spec_id="test123",
                target_key="mod::func_a",
                problem_class="pure",
                mode="prospective",
                prescriptive_sigma=5,
                created_at=1000.0,
            )
            save_spec(tmp, spec)

            # Load by target key
            loaded = load_spec(tmp, "mod::func_a")
            assert loaded is not None
            assert loaded.spec_id == "test123"
            assert loaded.prescriptive_sigma == 5

            # Load index
            index = load_spec_index(tmp)
            assert "mod::func_a" in index
            assert index["mod::func_a"] == "test123"

            # Load all
            all_specs = load_all_specs(tmp)
            assert "mod::func_a" in all_specs

    def test_persistence_missing_target(self):
        """Loading nonexistent target returns None."""
        with tempfile.TemporaryDirectory() as tmp:
            assert load_spec(tmp, "nonexistent::func") is None

    def test_persistence_empty_project(self):
        """Empty project has empty index."""
        with tempfile.TemporaryDirectory() as tmp:
            assert load_spec_index(tmp) == {}
            assert load_all_specs(tmp) == {}

    def test_spec_coverage_computation(self):
        """Correct coverage ratio calculation."""
        with tempfile.TemporaryDirectory() as tmp:
            save_spec(
                tmp,
                PrescriptiveSpec(
                    spec_id="s1",
                    target_key="mod::a",
                    problem_class="pure",
                    mode="prospective",
                    created_at=1000.0,
                ),
            )
            save_spec(
                tmp,
                PrescriptiveSpec(
                    spec_id="s2",
                    target_key="mod::b",
                    problem_class="pure",
                    mode="prospective",
                    created_at=1000.0,
                ),
            )

            cov = spec_coverage(tmp, ["mod::a", "mod::b", "mod::c"])
            assert cov["total_functions"] == 3
            assert cov["covered"] == 2
            assert abs(cov["coverage_ratio"] - 2 / 3) < 0.01
            assert cov["uncovered"] == ["mod::c"]


# ── Workflow Record tests ─────────────────────────────────────────────


class TestPrescriptiveWorkflowRecord:
    def test_round_trip(self):
        """to_dict/from_dict cycle preserves all fields."""
        from lintgate.specification.prescriptive_spec import PrescriptiveWorkflowRecord

        record = PrescriptiveWorkflowRecord(
            spec_id="spec123",
            target_key="mod::func",
            state="compiled",
            projected_claims=[{"source": "compass:toward:0", "action": "included"}],
            compiled_targets_path="/tmp/targets.json",
            materialized_test_path="/tmp/test.py",
            expected_kill_set={"VALUE": True, "SWAP": True},
            structural_evidence=[{"status": "pass"}],
            behavioral_evidence=[{"status": "unknown"}],
            convergence_signal={"assessment": "converged"},
            recommended_next_action="prescriptive_spec_verify",
            recommended_next_args={"path": "/proj", "target": "mod::func"},
            created_at=1000.0,
            updated_at=2000.0,
        )
        d = record.to_dict()
        restored = PrescriptiveWorkflowRecord.from_dict(d)

        assert restored.spec_id == "spec123"
        assert restored.target_key == "mod::func"
        assert restored.state == "compiled"
        assert len(restored.projected_claims) == 1
        assert restored.compiled_targets_path == "/tmp/targets.json"
        assert restored.expected_kill_set == {"VALUE": True, "SWAP": True}
        assert restored.recommended_next_action == "prescriptive_spec_verify"
        assert restored.created_at == 1000.0

    def test_json_serializable(self):
        """to_dict output is JSON-serializable."""
        from lintgate.specification.prescriptive_spec import PrescriptiveWorkflowRecord

        record = PrescriptiveWorkflowRecord(spec_id="s1", target_key="m::f")
        serialized = json.dumps(record.to_dict())
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["spec_id"] == "s1"

    def test_persistence_save_load(self):
        """Save/load cycle on temp dir."""
        from lintgate.specification.prescriptive_spec import (
            PrescriptiveWorkflowRecord,
            load_workflow_record,
            save_workflow_record,
        )

        with tempfile.TemporaryDirectory() as tmp:
            record = PrescriptiveWorkflowRecord(
                spec_id="abc",
                target_key="mod::func_a",
                state="composed",
            )
            save_workflow_record(tmp, record)
            loaded = load_workflow_record(tmp, "mod::func_a")
            assert loaded is not None
            assert loaded.spec_id == "abc"
            assert loaded.state == "composed"
            assert loaded.updated_at > 0

    def test_persistence_missing_returns_none(self):
        """Loading nonexistent workflow returns None."""
        from lintgate.specification.prescriptive_spec import load_workflow_record

        with tempfile.TemporaryDirectory() as tmp:
            assert load_workflow_record(tmp, "nonexistent::func") is None

    def test_defaults(self):
        """Default values are sensible."""
        from lintgate.specification.prescriptive_spec import PrescriptiveWorkflowRecord

        record = PrescriptiveWorkflowRecord(spec_id="s", target_key="t")
        assert record.state == "composed"
        assert record.projected_claims == []
        assert record.expected_kill_set == {}
        assert record.recommended_next_action == ""
        assert record.created_at > 0


# ── Target resolution tests ──────────────────────────────────────────


class TestResolveTargets:
    def test_resolve_targets_explicit(self):
        """Explicit targets return confidence=1.0."""
        compass = _FakeCompass()
        results = resolve_targets(compass, {}, "/tmp/fake", explicit_targets=["mod::func"])
        assert len(results) == 1
        assert results[0].confidence == 1.0
        assert results[0].source == "explicit"

    def test_resolve_targets_stub_annotation(self, tmp_path):
        """# PSPEC: stubs return confidence=0.8."""
        # Create a stub file
        pkg = tmp_path / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text(
            "# PSPEC: toward:bounded_output\n"
            "def transform_data(records):\n"
            "    raise NotImplementedError\n"
        )

        compass = _FakeCompass()
        results = resolve_targets(compass, {}, str(tmp_path))
        stubs = [r for r in results if r.source == "stub"]
        assert len(stubs) == 1
        assert stubs[0].confidence == 0.8
        assert "transform_data" in stubs[0].target_key

    def test_resolve_targets_claim_match(self, tmp_path):
        """Claim-to-symbol matching with confidence gating."""
        # Create a source file with a function
        pkg = tmp_path / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "utils.py").write_text("def compute_metrics(data):\n    return len(data)\n")

        compass = _FakeCompass(
            axes={
                "problem": _FakeAxis(
                    "problem",
                    [
                        _FakeClaim(
                            "We need compute_metrics to handle large datasets", confidence=0.8
                        )
                    ],
                )
            }
        )
        results = resolve_targets(compass, {}, str(tmp_path))
        claim_matches = [r for r in results if r.source == "claim_match"]
        assert len(claim_matches) >= 1
        assert any("compute_metrics" in r.target_key for r in claim_matches)

    def test_resolve_targets_dedup(self):
        """Same target from multiple strategies is not duplicated."""
        compass = _FakeCompass()
        results = resolve_targets(
            compass,
            {},
            "/tmp/fake",
            explicit_targets=["mod::func", "mod::func"],
        )
        assert len(results) == 1
