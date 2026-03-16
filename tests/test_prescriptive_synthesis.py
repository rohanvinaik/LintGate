"""Tests for DSL body synthesizer — 4 fully-determined patterns."""

from __future__ import annotations

import ast

from lintgate.specification.prescriptive_spec import (
    Invariant,
    Predicate,
    PredicateOp,
    PrescriptiveSpec,
    pred_custom,
    pred_pure,
)
from lintgate.specification.prescriptive_synthesis import (
    PatternMatch,
    SynthesisResult,
    _extract_field_from_description,
    _extract_key_attribute,
    _generate_count_aggregation,
    _generate_field_projection,
    _generate_group_aggregation,
    _generate_key_inversion,
    _has_invariant_signal,
    _parse_dict_of_list,
    _parse_dict_type,
    _recognize_count_aggregation,
    _recognize_field_projection,
    _recognize_group_aggregation,
    _recognize_key_inversion,
    _split_top_level,
    synthesize_body,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _spec(
    target: str = "mod::func",
    params: list[dict] | None = None,
    return_type: str = "",
    invariants: list[Invariant] | None = None,
) -> PrescriptiveSpec:
    return PrescriptiveSpec(
        spec_id="test",
        target_key=target,
        problem_class="pure",
        mode="prospective",
        parameters=params or [],
        return_type=return_type,
        invariants=invariants or [],
        prescriptive_sigma=3,
        created_at=1000.0,
    )


# ── Type Parsing ─────────────────────────────────────────────────────


class TestTypeParsing:
    def test_parse_dict_type_simple(self):
        assert _parse_dict_type("dict[str, int]") == ("str", "int")

    def test_parse_dict_type_nested(self):
        assert _parse_dict_type("dict[str, list[int]]") == ("str", "list[int]")

    def test_parse_dict_type_not_dict(self):
        assert _parse_dict_type("list[int]") is None

    def test_parse_dict_type_bare(self):
        assert _parse_dict_type("dict") is None

    def test_parse_dict_of_list(self):
        assert _parse_dict_of_list("dict[str, list[int]]") == ("str", "int")

    def test_parse_dict_of_list_not_list_value(self):
        assert _parse_dict_of_list("dict[str, int]") is None

    def test_split_top_level_simple(self):
        assert _split_top_level("str, int") == ["str", " int"]

    def test_split_top_level_nested(self):
        assert _split_top_level("str, list[int, float]") == ["str", " list[int, float]"]


# ── Key Inversion ────────────────────────────────────────────────────


class TestKeyInversion:
    def test_recognizes_inversion(self):
        spec = _spec(
            params=[{"name": "index", "type": "dict[str, list[int]]"}],
            return_type="dict[int, list[str]]",
        )
        match = _recognize_key_inversion(spec)
        assert match is not None
        assert match.pattern_name == "key_inversion"
        assert match.confidence >= 0.9

    def test_rejects_same_types(self):
        """dict[str, list[str]] → dict[str, list[str]] is not inversion."""
        spec = _spec(
            params=[{"name": "d", "type": "dict[str, list[str]]"}],
            return_type="dict[str, list[str]]",
        )
        assert _recognize_key_inversion(spec) is None

    def test_rejects_multi_params(self):
        spec = _spec(
            params=[
                {"name": "a", "type": "dict[str, list[int]]"},
                {"name": "b", "type": "int"},
            ],
            return_type="dict[int, list[str]]",
        )
        assert _recognize_key_inversion(spec) is None

    def test_rejects_non_dict_input(self):
        spec = _spec(
            params=[{"name": "items", "type": "list[str]"}],
            return_type="dict[int, list[str]]",
        )
        assert _recognize_key_inversion(spec) is None

    def test_generates_valid_body(self):
        match = PatternMatch("key_inversion", 0.95, {"input_param": "index"})
        body = _generate_key_inversion(match)
        ast.parse(f"def f():\n{body}")
        assert "setdefault" in body
        assert "index.items()" in body

    def test_synthesize_body_key_inversion(self):
        spec = _spec(
            target="mod::invert",
            params=[{"name": "idx", "type": "dict[str, list[int]]"}],
            return_type="dict[int, list[str]]",
        )
        result = synthesize_body(spec)
        assert result.success
        assert result.pattern_used == "key_inversion"
        assert "idx.items()" in result.body

    def test_body_executes_correctly(self):
        spec = _spec(
            params=[{"name": "data", "type": "dict[str, list[int]]"}],
            return_type="dict[int, list[str]]",
        )
        result = synthesize_body(spec)
        assert result.success
        # Execute the generated body
        func_code = f"def func(data):\n{result.body}"
        ns: dict = {}
        exec(func_code, ns)  # noqa: S102  # nosec B102 — executing synthesized test bodies
        output = ns["func"]({"a": [1, 2], "b": [2, 3]})
        assert output == {1: ["a"], 2: ["a", "b"], 3: ["b"]}


# ── Count Aggregation ────────────────────────────────────────────────


class TestCountAggregation:
    def test_recognizes_count(self):
        spec = _spec(
            params=[{"name": "words", "type": "list[str]"}],
            return_type="dict[str, int]",
            invariants=[
                Invariant(
                    "c",
                    pred_custom("count occurrences"),
                    "count word occurrences",
                    "src",
                    0.8,
                    "safety",
                ),
            ],
        )
        match = _recognize_count_aggregation(spec)
        assert match is not None
        assert match.pattern_name == "count_aggregation"

    def test_rejects_without_count_signal(self):
        spec = _spec(
            params=[{"name": "words", "type": "list[str]"}],
            return_type="dict[str, int]",
            invariants=[Invariant("p", pred_pure(), "pure", "src", 0.9, "safety")],
        )
        assert _recognize_count_aggregation(spec) is None

    def test_rejects_non_int_value(self):
        spec = _spec(
            params=[{"name": "words", "type": "list[str]"}],
            return_type="dict[str, str]",
            invariants=[
                Invariant("c", pred_custom("count"), "count things", "src", 0.8, "safety"),
            ],
        )
        assert _recognize_count_aggregation(spec) is None

    def test_generates_valid_body(self):
        match = PatternMatch("count_aggregation", 0.85, {"input_param": "words"})
        body = _generate_count_aggregation(match)
        ast.parse(f"def f():\n{body}")
        assert "counts" in body

    def test_body_executes_correctly(self):
        spec = _spec(
            params=[{"name": "items", "type": "list[str]"}],
            return_type="dict[str, int]",
            invariants=[
                Invariant("c", pred_custom("count"), "count occurrences", "src", 0.8, "safety"),
            ],
        )
        result = synthesize_body(spec)
        assert result.success
        ns: dict = {}
        exec(f"def func(items):\n{result.body}", ns)  # noqa: S102  # nosec B102 — executing synthesized test bodies
        assert ns["func"](["a", "b", "a", "c", "b", "a"]) == {"a": 3, "b": 2, "c": 1}


# ── Group Aggregation ────────────────────────────────────────────────


class TestGroupAggregation:
    def test_recognizes_group(self):
        spec = _spec(
            params=[{"name": "events", "type": "list[str]"}],
            return_type="dict[str, list[str]]",
            invariants=[
                Invariant(
                    "g", pred_custom("group by category"), "group events", "src", 0.8, "safety"
                ),
            ],
        )
        match = _recognize_group_aggregation(spec)
        assert match is not None
        assert match.pattern_name == "group_aggregation"

    def test_rejects_without_group_signal(self):
        spec = _spec(
            params=[{"name": "events", "type": "list[str]"}],
            return_type="dict[str, list[str]]",
            invariants=[Invariant("p", pred_pure(), "pure", "src", 0.9, "safety")],
        )
        assert _recognize_group_aggregation(spec) is None

    def test_generates_valid_body(self):
        match = PatternMatch("group_aggregation", 0.80, {"input_param": "events", "key_attr": ""})
        body = _generate_group_aggregation(match)
        ast.parse(f"def f():\n{body}")
        assert "groups" in body

    def test_generates_body_with_key_attr(self):
        match = PatternMatch(
            "group_aggregation", 0.80, {"input_param": "items", "key_attr": "category"}
        )
        body = _generate_group_aggregation(match)
        assert "item.category" in body


# ── Field Projection ─────────────────────────────────────────────────


class TestFieldProjection:
    def test_recognizes_field_projection(self):
        spec = _spec(
            params=[{"name": "data", "type": "dict[str, str]"}],
            return_type="str",
            invariants=[
                Invariant(
                    "has_name",
                    Predicate(op=PredicateOp.HAS_ATTR, value="name"),
                    "extracts name field",
                    "src",
                    0.9,
                    "safety",
                ),
            ],
        )
        match = _recognize_field_projection(spec)
        assert match is not None
        assert match.pattern_name == "field_projection"
        assert match.params["field_name"] == "name"

    def test_rejects_multiple_has_attr(self):
        """Multiple HAS_ATTR invariants → ambiguous, reject."""
        spec = _spec(
            params=[{"name": "data", "type": "dict[str, str]"}],
            return_type="str",
            invariants=[
                Invariant(
                    "a",
                    Predicate(op=PredicateOp.HAS_ATTR, value="name"),
                    "name",
                    "src",
                    0.9,
                    "safety",
                ),
                Invariant(
                    "b",
                    Predicate(op=PredicateOp.HAS_ATTR, value="age"),
                    "age",
                    "src",
                    0.9,
                    "safety",
                ),
            ],
        )
        assert _recognize_field_projection(spec) is None

    def test_rejects_non_scalar_return(self):
        spec = _spec(
            params=[{"name": "data", "type": "dict[str, str]"}],
            return_type="list[str]",
            invariants=[
                Invariant(
                    "a",
                    Predicate(op=PredicateOp.HAS_ATTR, value="name"),
                    "name",
                    "src",
                    0.9,
                    "safety",
                ),
            ],
        )
        assert _recognize_field_projection(spec) is None

    def test_generates_valid_body(self):
        match = PatternMatch(
            "field_projection",
            0.90,
            {
                "input_param": "data",
                "field_name": "name",
                "default_value": '""',
            },
        )
        body = _generate_field_projection(match)
        ast.parse(f"def f():\n{body}")
        assert "data.get('name'" in body

    def test_body_executes_correctly(self):
        spec = _spec(
            params=[{"name": "data", "type": "dict[str, str]"}],
            return_type="str",
            invariants=[
                Invariant(
                    "a",
                    Predicate(op=PredicateOp.HAS_ATTR, value="name"),
                    "extract name",
                    "src",
                    0.9,
                    "safety",
                ),
            ],
        )
        result = synthesize_body(spec)
        assert result.success
        ns: dict = {}
        exec(f"def func(data):\n{result.body}", ns)  # noqa: S102  # nosec B102 — executing synthesized test bodies
        assert ns["func"]({"name": "alice", "age": "30"}) == "alice"
        assert ns["func"]({}) == ""  # default value


# ── Orchestrator ─────────────────────────────────────────────────────


class TestSynthesizeBody:
    def test_no_match_returns_failure(self):
        """Spec with no recognizable pattern returns failure."""
        spec = _spec(
            params=[{"name": "x", "type": "int"}, {"name": "y", "type": "int"}],
            return_type="int",
        )
        result = synthesize_body(spec)
        assert not result.success
        assert result.failure_reason == "no pattern matched"

    def test_result_to_dict(self):
        result = SynthesisResult(success=True, body="pass", pattern_used="test", confidence=0.9)
        d = result.to_dict()
        assert d["success"] is True
        assert d["pattern_used"] == "test"

    def test_all_bodies_parse(self):
        """Every generated body must be valid Python."""
        specs = [
            _spec(
                params=[{"name": "d", "type": "dict[str, list[int]]"}],
                return_type="dict[int, list[str]]",
            ),
            _spec(
                params=[{"name": "w", "type": "list[str]"}],
                return_type="dict[str, int]",
                invariants=[
                    Invariant("c", pred_custom("count"), "count things", "src", 0.8, "safety")
                ],
            ),
            _spec(
                params=[{"name": "d", "type": "dict[str, str]"}],
                return_type="str",
                invariants=[
                    Invariant(
                        "a",
                        Predicate(op=PredicateOp.HAS_ATTR, value="key"),
                        "key",
                        "src",
                        0.9,
                        "safety",
                    ),
                ],
            ),
        ]
        for spec in specs:
            result = synthesize_body(spec)
            if result.success:
                ast.parse(f"def f():\n{result.body}")


# ── Signal Helpers ───────────────────────────────────────────────────


# ── Witness Validation ────────────────────────────────────────────────


class TestValidateAgainstWitnesses:
    def test_no_oracle_witnesses_passes(self):
        """No oracle witnesses → can't invalidate → returns True."""
        from lintgate.specification.prescriptive_backends import WitnessRecord
        from lintgate.specification.prescriptive_synthesis import _validate_against_witnesses

        spec = _spec(
            params=[{"name": "x", "type": "int"}],
            return_type="int",
        )
        witness = WitnessRecord(inputs={"x": "42"}, output=None, has_oracle_value=False)
        assert _validate_against_witnesses(spec, "    return x + 1", [witness], "/tmp") is True

    def test_correct_body_passes_witness(self):
        """Correct body matches oracle output."""
        from lintgate.specification.prescriptive_backends import WitnessRecord
        from lintgate.specification.prescriptive_synthesis import _validate_against_witnesses

        spec = _spec(
            target="mod::double",
            params=[{"name": "x", "type": "int"}],
            return_type="int",
        )
        witness = WitnessRecord(inputs={"x": "5"}, output="10", has_oracle_value=True)
        assert _validate_against_witnesses(spec, "    return x * 2", [witness], "/tmp") is True

    def test_wrong_body_fails_witness(self):
        """Wrong body doesn't match oracle output."""
        from lintgate.specification.prescriptive_backends import WitnessRecord
        from lintgate.specification.prescriptive_synthesis import _validate_against_witnesses

        spec = _spec(
            target="mod::double",
            params=[{"name": "x", "type": "int"}],
            return_type="int",
        )
        witness = WitnessRecord(inputs={"x": "5"}, output="10", has_oracle_value=True)
        assert _validate_against_witnesses(spec, "    return x + 1", [witness], "/tmp") is False


# ── Recognizer Exact-Value Assertions ────────────────────────────────


class TestRecognizerExactValues:
    def test_key_inversion_params(self):
        """Recognizer returns correct params dict."""
        spec = _spec(
            params=[{"name": "idx", "type": "dict[str, list[int]]"}],
            return_type="dict[int, list[str]]",
        )
        match = _recognize_key_inversion(spec)
        assert match is not None
        assert match.params["input_param"] == "idx"
        assert match.params["input_key_type"] == "str"
        assert match.params["input_value_type"] == "int"

    def test_count_aggregation_params(self):
        spec = _spec(
            params=[{"name": "tokens", "type": "list[str]"}],
            return_type="dict[str, int]",
            invariants=[
                Invariant("c", pred_custom("count"), "count frequency", "src", 0.8, "safety")
            ],
        )
        match = _recognize_count_aggregation(spec)
        assert match is not None
        assert match.params["input_param"] == "tokens"
        assert match.confidence == 0.85

    def test_group_aggregation_params(self):
        spec = _spec(
            params=[{"name": "events", "type": "list[str]"}],
            return_type="dict[str, list[str]]",
            invariants=[
                Invariant(
                    "g", pred_custom("group by category"), "group events", "src", 0.8, "safety"
                ),
                Invariant(
                    "k",
                    Predicate(op=PredicateOp.HAS_ATTR, value="category"),
                    "cat",
                    "src",
                    0.9,
                    "safety",
                ),
            ],
        )
        match = _recognize_group_aggregation(spec)
        assert match is not None
        assert match.params["input_param"] == "events"
        assert match.params["key_attr"] == "category"

    def test_field_projection_params(self):
        spec = _spec(
            params=[{"name": "record", "type": "dict[str, int]"}],
            return_type="int",
            invariants=[
                Invariant(
                    "a",
                    Predicate(op=PredicateOp.HAS_ATTR, value="score"),
                    "score",
                    "src",
                    0.9,
                    "safety",
                ),
            ],
        )
        match = _recognize_field_projection(spec)
        assert match is not None
        assert match.params["field_name"] == "score"
        assert match.params["default_value"] == "0"
        assert match.params["input_param"] == "record"


# ── Signal Helpers ───────────────────────────────────────────────────


class TestSignalHelpers:
    def test_has_invariant_signal(self):
        spec = _spec(
            invariants=[
                Invariant(
                    "c", pred_custom("count words"), "count word frequency", "src", 0.8, "safety"
                ),
            ]
        )
        assert _has_invariant_signal(spec, {"count", "frequency"})
        assert not _has_invariant_signal(spec, {"group", "bucket"})

    def test_extract_key_attribute_from_has_attr(self):
        spec = _spec(
            invariants=[
                Invariant(
                    "a",
                    Predicate(op=PredicateOp.HAS_ATTR, value="category"),
                    "cat",
                    "src",
                    0.9,
                    "safety",
                ),
            ]
        )
        assert _extract_key_attribute(spec) == "category"

    def test_extract_key_attribute_from_description(self):
        spec = _spec(
            invariants=[
                Invariant(
                    "a",
                    pred_custom("group by status"),
                    "group by status field",
                    "src",
                    0.8,
                    "safety",
                ),
            ]
        )
        assert _extract_key_attribute(spec) == "status"

    def test_extract_field_from_description(self):
        assert _extract_field_from_description("extract name from dict") == "name"
        assert _extract_field_from_description("get age value") == "age"
        assert _extract_field_from_description("no match here") is None
