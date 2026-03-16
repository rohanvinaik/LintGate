"""Tests for PrescriptiveSpec backend compilers and adapter."""

from __future__ import annotations

import json
import os
import tempfile

from lintgate.specification.prescriptive_backends import (
    CompilationTargets,
    DistributedBackend,
    PrescriptiveAdapter,
    PureBackend,
    StatefulBackend,
    SynthesisGateResult,
    WitnessRecord,
    _build_synthesis_profile,
    _classify_return_type,
    _has_only_custom_predicates,
    check_synthesis_gate,
    select_backend,
)
from lintgate.specification.prescriptive_spec import (
    ForbiddenBehavior,
    Invariant,
    Predicate,
    PredicateOp,
    PrescriptiveSpec,
    RefinementObligation,
    StateTransition,
    StateVariable,
    pred_custom,
    pred_eq,
    pred_gt,
    pred_pure,
    pred_true,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_pure_spec(**overrides) -> PrescriptiveSpec:
    defaults = {
        "spec_id": "test_pure",
        "target_key": "mod::compute",
        "problem_class": "pure",
        "mode": "prospective",
        "parameters": [{"name": "x", "type": "int", "description": "input"}],
        "return_type": "int",
        "invariants": [
            Invariant(
                "bounded", pred_gt("result", 0, "positive"), "positive result", "src", 0.8, "safety"
            ),
        ],
        "forbidden_behaviors": [
            ForbiddenBehavior(pred_custom("no mutation"), "no mutation", "src", "hard"),
        ],
        "algebraic_laws": [{"name": "idempotent", "property_name": "idempotent"}],
        "refinement_obligations": [
            RefinementObligation("VALUE", True, "value mutants survived"),
            RefinementObligation("BOUNDARY", True, "boundary mutants survived"),
        ],
        "generation_constraints": [],
        "prescriptive_sigma": 5,
        "created_at": 1000.0,
    }
    defaults.update(overrides)
    return PrescriptiveSpec(**defaults)


def _make_stateful_spec() -> PrescriptiveSpec:
    return PrescriptiveSpec(
        spec_id="test_stateful",
        target_key="mod::Cache",
        problem_class="stateful",
        mode="prospective",
        state_variables=[
            StateVariable("store", "dict", "{}", "backing store"),
            StateVariable("size", "int", "0", "current size"),
        ],
        allowed_transitions=[
            StateTransition(
                "put", pred_true("always"), pred_gt("size", 0, "non-empty"), "insert", "src"
            ),
            StateTransition(
                "get",
                pred_gt("size", 0, "has items"),
                pred_eq("result", "value", "found"),
                "lookup",
                "src",
            ),
        ],
        invariants=[
            Invariant(
                "non_negative_size",
                pred_gt("size", -1, "size >= 0"),
                "size never negative",
                "src",
                0.9,
                "safety",
            ),
        ],
        generation_constraints=[],
        prescriptive_sigma=8,
        created_at=1000.0,
    )


def _make_distributed_spec() -> PrescriptiveSpec:
    return PrescriptiveSpec(
        spec_id="test_dist",
        target_key="mod::Protocol",
        problem_class="distributed",
        mode="prospective",
        allowed_transitions=[
            StateTransition(
                "handshake",
                pred_true(),
                pred_eq("state", "connected", "connected"),
                "connect",
                "src",
            ),
        ],
        invariants=[
            Invariant(
                "ordering", pred_custom("messages delivered in order"), "FIFO", "src", 0.8, "safety"
            ),
        ],
        generation_constraints=[],
        prescriptive_sigma=6,
        created_at=1000.0,
    )


# ── Pure Backend ──────────────────────────────────────────────────────


class TestPureBackend:
    def test_algebraic_laws_to_property_tests(self):
        """Algebraic laws → Hypothesis property skeletons."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        props = [t for t in targets.property_tests if t["type"] == "hypothesis"]
        assert len(props) == 1
        assert "idempotent" in props[0]["name"]
        assert "hypothesis" in props[0]["skeleton"].lower() or "@given" in props[0]["skeleton"]

    def test_invariants_to_assertions(self):
        """Invariants with Predicate IR → exact-value test skeletons."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        inv_tests = [t for t in targets.scenario_tests if t["type"] == "exact_value"]
        assert len(inv_tests) == 1
        assert "bounded" in inv_tests[0]["name"]
        assert "positive result" in inv_tests[0]["skeleton"]

    def test_forbidden_to_negative_tests(self):
        """Forbidden behaviors → negative test skeletons."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        neg_tests = [t for t in targets.scenario_tests if t["type"] == "negative"]
        assert len(neg_tests) == 1
        assert "no mutation" in neg_tests[0]["skeleton"]

    def test_expected_kill_set(self):
        """Refinement obligations → category kill expectations."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        assert targets.expected_kill_set == {"VALUE": True, "BOUNDARY": True}

    def test_compass_gate_assertions(self):
        """Compass gate assertions populated from invariants."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        assert len(targets.compass_gate_assertions) == 1
        assert targets.compass_gate_assertions[0]["invariant"] == "bounded"


# ── Stateful Backend ──────────────────────────────────────────────────


class TestStatefulBackend:
    def test_transitions_to_test_skeletons(self):
        """Transitions → transition test skeletons with pre/postcondition checks."""
        spec = _make_stateful_spec()
        targets = StatefulBackend().compile(spec)

        trans_tests = [t for t in targets.scenario_tests if t["type"] == "state_transition"]
        assert len(trans_tests) == 2
        assert "put" in trans_tests[0]["name"]
        assert "get" in trans_tests[1]["name"]

    def test_invariant_monitors(self):
        """Invariant-checking wrapper skeletons."""
        spec = _make_stateful_spec()
        targets = StatefulBackend().compile(spec)

        inv_tests = [t for t in targets.scenario_tests if t["type"] == "state_invariant"]
        assert len(inv_tests) == 1
        assert "non_negative_size" in inv_tests[0]["name"]

    def test_state_init_tests(self):
        """State variable initialization tests."""
        spec = _make_stateful_spec()
        targets = StatefulBackend().compile(spec)

        init_tests = [t for t in targets.scenario_tests if t["type"] == "state_init"]
        assert len(init_tests) == 2
        assert any("store" in t["name"] for t in init_tests)
        assert any("size" in t["name"] for t in init_tests)


# ── Distributed Backend ───────────────────────────────────────────────


class TestDistributedBackend:
    def test_message_sequences(self):
        """Protocol conformance test skeletons."""
        spec = _make_distributed_spec()
        targets = DistributedBackend().compile(spec)

        proto_tests = [t for t in targets.scenario_tests if t["type"] == "protocol_conformance"]
        assert len(proto_tests) == 1
        assert "handshake" in proto_tests[0]["name"]

    def test_protocol_monitors(self):
        """Protocol monitor obligations."""
        spec = _make_distributed_spec()
        targets = DistributedBackend().compile(spec)

        monitors = [t for t in targets.scenario_tests if t["type"] == "protocol_monitor"]
        assert len(monitors) == 1
        assert "ordering" in monitors[0]["name"]


# ── Backend selection ─────────────────────────────────────────────────


class TestSelectBackend:
    def test_routing(self):
        """Pure/stateful/distributed dispatch."""
        assert isinstance(select_backend(_make_pure_spec()), PureBackend)
        assert isinstance(select_backend(_make_stateful_spec()), StatefulBackend)
        assert isinstance(select_backend(_make_distributed_spec()), DistributedBackend)


# ── CompilationTargets ────────────────────────────────────────────────


class TestCompilationTargets:
    def test_round_trip(self):
        """to_dict round-trip."""
        targets = PureBackend().compile(_make_pure_spec())
        d = targets.to_dict()
        restored = CompilationTargets.from_dict(d)

        assert len(restored.property_tests) == len(targets.property_tests)
        assert len(restored.scenario_tests) == len(targets.scenario_tests)
        assert restored.expected_kill_set == targets.expected_kill_set

    def test_json_serializable(self):
        """Output is JSON-serializable."""
        targets = PureBackend().compile(_make_pure_spec())
        serialized = json.dumps(targets.to_dict())
        assert isinstance(serialized, str)

    def test_generation_constraints_in_output(self):
        """CompilationTargets includes generation constraints when spec has them."""
        from lintgate.specification.prescriptive_spec import GenerationConstraint

        spec = _make_pure_spec(
            generation_constraints=[
                GenerationConstraint("must_use", None, "Use bounded arithmetic", 1),
            ]
        )
        targets = PureBackend().compile(spec)
        assert len(targets.generation_constraints) == 1
        assert targets.generation_constraints[0]["description"] == "Use bounded arithmetic"


# ── Adapter ───────────────────────────────────────────────────────────


class TestPrescriptiveAdapter:
    def test_materialize_test_file(self):
        """Writes valid pytest file discoverable by discover_test_files."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "tests", "test_prescriptive_compute.py")
            adapter = PrescriptiveAdapter()
            result = adapter.materialize_test_file(targets, spec, out_path)

            assert os.path.isfile(result)
            with open(result) as f:
                content = f.read()
            assert "import pytest" in content
            assert "def test_" in content
            # File starts with test_ so discover_test_files finds it
            assert os.path.basename(result).startswith("test_")

    def test_persist_kill_expectations(self):
        """Writes to correct path, loadable."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        with tempfile.TemporaryDirectory() as tmp:
            adapter = PrescriptiveAdapter()
            adapter.persist_kill_expectations(spec, targets, tmp)

            # Check file exists
            from lintgate.specification.prescriptive_spec import _SPEC_DIR, _target_hash

            h = _target_hash(spec.target_key)
            exp_path = os.path.join(tmp, _SPEC_DIR, f"{h}_expectations.json")
            assert os.path.isfile(exp_path)

            with open(exp_path) as f:
                data = json.load(f)
            assert data["expected_kill_set"] == {"VALUE": True, "BOUNDARY": True}

    def test_verify_refinement_no_cached_state(self):
        """Missing cached state → overall=unknown."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        with tempfile.TemporaryDirectory() as tmp:
            adapter = PrescriptiveAdapter()
            verdict = adapter.verify_refinement(spec, targets, tmp, "mod.py")

            assert verdict["overall"] in ("unknown", "partial")
            assert verdict["convergence"] is None

    def test_verify_refinement_with_mutation_cache(self):
        """Reads cached mutation state, compares to expectations."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)

        with tempfile.TemporaryDirectory() as tmp:
            # Create fake mutation cache
            mut_dir = os.path.join(tmp, ".lintgate", "mutation")
            os.makedirs(mut_dir)
            with open(os.path.join(mut_dir, "abc.json"), "w") as f:
                json.dump(
                    {
                        "function_key": "mod::compute",
                        "per_category": [
                            {"category": "VALUE", "survived": 0, "killed": 5},
                            {"category": "BOUNDARY", "survived": 2, "killed": 3},
                        ],
                    },
                    f,
                )

            adapter = PrescriptiveAdapter()
            verdict = adapter.verify_refinement(spec, targets, tmp, "mod.py")

            # VALUE: expected kill=True, survived=0 → pass
            # BOUNDARY: expected kill=True, survived=2 → fail
            assert verdict["overall"] == "fail"
            obs = {o["category"]: o["status"] for o in verdict["behavioral_evidence"]}
            assert obs["VALUE"] == "pass"
            assert obs["BOUNDARY"] == "fail"
            # Evidence-class split: summary has both structural and behavioral
            assert "structural" in verdict["summary"]
            assert "behavioral" in verdict["summary"]


# ── Return Type Classification ───────────────────────────────────────


class TestClassifyReturnType:
    def test_scalars(self):
        for rt in ("int", "float", "bool", "str", "bytes", "None"):
            assert _classify_return_type(rt) == "scalar", f"Expected scalar for {rt}"

    def test_simple_containers(self):
        for rt in ("dict[str, int]", "list[str]", "set[int]", "tuple[str, int]"):
            assert _classify_return_type(rt) == "simple_container", (
                f"Expected simple_container for {rt}"
            )

    def test_complex_nested(self):
        for rt in ("dict[str, list[int]]", "list[dict[str, int]]"):
            assert _classify_return_type(rt) == "complex", f"Expected complex for {rt}"

    def test_optional_unwrap(self):
        assert _classify_return_type("Optional[int]") == "scalar"
        assert _classify_return_type("Optional[dict[str, int]]") == "simple_container"

    def test_unknown(self):
        assert _classify_return_type("MyClass") == "unknown"
        assert _classify_return_type("") == "unknown"

    def test_union_complex(self):
        assert _classify_return_type("Union[int, str]") == "complex"


# ── Synthesis Profile ────────────────────────────────────────────────


class TestBuildSynthesisProfile:
    def test_pure_simple_eligible(self):
        """Pure function with <=3 params and simple return is gate-eligible."""
        spec = _make_pure_spec(
            parameters=[{"name": "x", "type": "int"}],
            return_type="int",
            invariants=[
                Invariant("pure", pred_pure(), "must be pure", "src", 0.9, "safety"),
            ],
        )
        profile = _build_synthesis_profile(spec)
        assert profile["gate_eligible"] is True
        assert profile["return_type_category"] == "scalar"
        assert profile["problem_class"] == "pure"
        assert profile["gate_reasons"] == []

    def test_stateful_not_eligible(self):
        spec = _make_stateful_spec()
        profile = _build_synthesis_profile(spec)
        assert profile["gate_eligible"] is False
        assert any("not pure" in r for r in profile["gate_reasons"])

    def test_too_many_params(self):
        spec = _make_pure_spec(
            parameters=[
                {"name": "a", "type": "int"},
                {"name": "b", "type": "int"},
                {"name": "c", "type": "int"},
                {"name": "d", "type": "int"},
            ],
        )
        profile = _build_synthesis_profile(spec)
        assert profile["gate_eligible"] is False
        assert any("param_count" in r for r in profile["gate_reasons"])

    def test_complex_return_not_eligible(self):
        spec = _make_pure_spec(return_type="dict[str, list[int]]")
        profile = _build_synthesis_profile(spec)
        assert profile["gate_eligible"] is False
        assert profile["return_type_category"] == "complex"

    def test_custom_only_invariants_not_eligible(self):
        """All CUSTOM predicates with no structural predicates → ineligible."""
        spec = _make_pure_spec(
            parameters=[{"name": "x", "type": "int"}],
            return_type="int",
            invariants=[
                Invariant("c1", pred_custom("do something"), "semantic", "src", 0.8, "safety"),
            ],
            forbidden_behaviors=[],
            algebraic_laws=[],
            refinement_obligations=[],
        )
        profile = _build_synthesis_profile(spec)
        assert profile["gate_eligible"] is False
        assert any("CUSTOM" in r for r in profile["gate_reasons"])

    def test_side_effects_not_eligible(self):
        spec = _make_pure_spec(
            parameters=[{"name": "x", "type": "int"}],
            return_type="int",
            allowed_side_effects=["writes to disk"],
        )
        profile = _build_synthesis_profile(spec)
        assert profile["gate_eligible"] is False

    def test_calls_predicate_not_eligible(self):
        spec = _make_pure_spec(
            parameters=[{"name": "x", "type": "int"}],
            return_type="int",
            invariants=[
                Invariant(
                    "calls_foo",
                    Predicate(op=PredicateOp.CALLS, value="foo"),
                    "must call foo",
                    "src",
                    0.9,
                    "safety",
                ),
            ],
        )
        profile = _build_synthesis_profile(spec)
        assert profile["gate_eligible"] is False
        assert any("CALLS" in r for r in profile["gate_reasons"])


# ── Has Only Custom Predicates ───────────────────────────────────────


class TestHasOnlyCustomPredicates:
    def test_all_custom(self):
        spec = _make_pure_spec(
            invariants=[
                Invariant("a", pred_custom("foo"), "foo", "src", 0.8, "safety"),
                Invariant("b", pred_custom("bar"), "bar", "src", 0.8, "safety"),
            ],
        )
        assert _has_only_custom_predicates(spec) is True

    def test_mixed(self):
        spec = _make_pure_spec(
            invariants=[
                Invariant("a", pred_custom("foo"), "foo", "src", 0.8, "safety"),
                Invariant("b", pred_pure(), "pure", "src", 0.9, "safety"),
            ],
        )
        assert _has_only_custom_predicates(spec) is False

    def test_empty(self):
        spec = _make_pure_spec(invariants=[])
        assert _has_only_custom_predicates(spec) is False


# ── Synthesis Gate ───────────────────────────────────────────────────


class TestSynthesisGate:
    def test_eligible_with_witnesses(self):
        """Gate passes when all conditions met including witnesses."""
        spec = _make_pure_spec(
            parameters=[{"name": "x", "type": "int"}],
            return_type="int",
            invariants=[
                Invariant("pure", pred_pure(), "must be pure", "src", 0.9, "safety"),
            ],
        )
        targets = PureBackend().compile(spec)
        # Simulate witnesses existing
        targets.synthesis_profile["has_executable_witnesses"] = True
        result = check_synthesis_gate(spec, targets)
        assert result.eligible is True
        assert result.reasons == []

    def test_ineligible_no_witnesses(self):
        """Gate fails without executable witnesses."""
        spec = _make_pure_spec(
            parameters=[{"name": "x", "type": "int"}],
            return_type="int",
            invariants=[
                Invariant("pure", pred_pure(), "must be pure", "src", 0.9, "safety"),
            ],
        )
        targets = PureBackend().compile(spec)
        result = check_synthesis_gate(spec, targets)
        assert result.eligible is False
        assert any("witness" in r for r in result.reasons)

    def test_ineligible_stateful(self):
        spec = _make_stateful_spec()
        targets = StatefulBackend().compile(spec)
        result = check_synthesis_gate(spec, targets)
        assert result.eligible is False

    def test_to_dict(self):
        result = SynthesisGateResult(eligible=True, reasons=[], profile={"gate_eligible": True})
        d = result.to_dict()
        assert d["eligible"] is True
        assert d["profile"]["gate_eligible"] is True


# ── Witness Record ───────────────────────────────────────────────────


class TestWitnessRecord:
    def test_round_trip(self):
        w = WitnessRecord(
            inputs={"x": "42", "y": '"hello"'},
            output="84",
            has_oracle_value=True,
            imports=["import os"],
        )
        d = w.to_dict()
        w2 = WitnessRecord.from_dict(d)
        assert w2.inputs == w.inputs
        assert w2.output == w.output
        assert w2.has_oracle_value is True
        assert w2.imports == ["import os"]

    def test_from_dict_defaults(self):
        w = WitnessRecord.from_dict({})
        assert w.inputs == {}
        assert w.output is None
        assert w.has_oracle_value is False
        assert w.imports == []


# ── PureBackend Stub Generation ──────────────────────────────────────


class TestPureBackendStubs:
    def test_implementation_stub_populated(self):
        """PureBackend.compile() populates implementation_stub."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)
        assert targets.implementation_stub != ""
        assert "def compute(" in targets.implementation_stub
        assert "pass  # TODO" in targets.implementation_stub

    def test_docstring_stub_from_invariants(self):
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)
        assert targets.docstring_stub != ""
        assert '"""' in targets.docstring_stub

    def test_synthesis_profile_populated(self):
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)
        assert targets.synthesis_profile != {}
        assert "problem_class" in targets.synthesis_profile
        assert "return_type_category" in targets.synthesis_profile

    def test_return_type_in_signature(self):
        spec = _make_pure_spec(return_type="dict[str, int]")
        targets = PureBackend().compile(spec)
        assert "-> dict[str, int]" in targets.implementation_stub

    def test_compilation_targets_round_trip_with_stubs(self):
        """New fields survive to_dict/from_dict round-trip."""
        spec = _make_pure_spec()
        targets = PureBackend().compile(spec)
        d = targets.to_dict()
        restored = CompilationTargets.from_dict(d)
        assert restored.implementation_stub == targets.implementation_stub
        assert restored.docstring_stub == targets.docstring_stub
        assert restored.body_slot == targets.body_slot
        assert restored.synthesis_profile == targets.synthesis_profile
