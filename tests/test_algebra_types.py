"""Tests for lintgate/linters/performance_checks/algebra_types.py — algebraic property types."""

from __future__ import annotations

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    BoundSpec,
    FunctionProperties,
    PropertyKind,
    PurityResult,
    SideEffect,
)

# --- PropertyKind ---


def test_property_kind_values():
    assert PropertyKind.PURE.value == "pure"
    assert PropertyKind.BOUNDED.value == "bounded"
    assert PropertyKind.MONOTONIC.value == "monotonic"
    assert PropertyKind.IDEMPOTENT.value == "idempotent"
    assert PropertyKind.COMMUTATIVE.value == "commutative"
    assert PropertyKind.ASSOCIATIVE.value == "associative"


def test_property_kind_from_string():
    assert PropertyKind("pure") == PropertyKind.PURE
    assert PropertyKind("bounded") == PropertyKind.BOUNDED
    assert PropertyKind("monotonic") == PropertyKind.MONOTONIC
    assert PropertyKind("idempotent") == PropertyKind.IDEMPOTENT


# --- SideEffect ---


def test_side_effect_to_dict():
    se = SideEffect(kind="global_write", node_type="Assign", line=42, detail="writes to global X")
    d = se.to_dict()
    assert d == {
        "kind": "global_write",
        "node_type": "Assign",
        "line": 42,
        "detail": "writes to global X",
    }


def test_side_effect_from_dict():
    data = {"kind": "io_call", "node_type": "Call", "line": 10, "detail": "calls print()"}
    se = SideEffect.from_dict(data)
    assert se.kind == "io_call"
    assert se.node_type == "Call"
    assert se.line == 10
    assert se.detail == "calls print()"


def test_side_effect_frozen():
    se = SideEffect(kind="mutation", node_type="AugAssign", line=5, detail="mutates list")
    import pytest

    with pytest.raises(AttributeError):
        se.kind = "other"  # type: ignore[misc]


# --- BoundSpec ---


def test_bound_spec_to_dict():
    bs = BoundSpec(lower=0.0, upper=1.0, source="clamp")
    d = bs.to_dict()
    assert d == {"lower": 0.0, "upper": 1.0, "source": "clamp"}


def test_bound_spec_from_dict():
    data = {"lower": -10.0, "upper": None, "source": "annotation"}
    bs = BoundSpec.from_dict(data)
    assert bs.lower == -10.0
    assert bs.upper is None
    assert bs.source == "annotation"


def test_bound_spec_none_bounds():
    bs = BoundSpec(lower=None, upper=None, source="ratio")
    assert bs.lower is None
    assert bs.upper is None
    assert bs.source == "ratio"


# --- PurityResult ---


def test_purity_result_pure_function():
    pr = PurityResult(
        function_name="add",
        qualified_name="math_utils.add",
        line=5,
        is_pure=True,
        confidence=1.0,
        side_effects=(),
        parameter_count=2,
        return_annotation="int",
    )
    assert pr.is_pure is True
    assert pr.confidence == 1.0
    assert len(pr.side_effects) == 0
    assert pr.parameter_count == 2


def test_purity_result_impure_function():
    se = SideEffect(kind="io_call", node_type="Call", line=12, detail="calls open()")
    pr = PurityResult(
        function_name="read_file",
        qualified_name="io_utils.read_file",
        line=10,
        is_pure=False,
        confidence=0.95,
        side_effects=(se,),
        parameter_count=1,
        return_annotation="str",
    )
    assert pr.is_pure is False
    assert len(pr.side_effects) == 1
    assert pr.side_effects[0].kind == "io_call"


def test_purity_result_to_dict_roundtrip():
    se = SideEffect(kind="global_write", node_type="Assign", line=3, detail="writes X")
    pr = PurityResult(
        function_name="f",
        qualified_name="m.f",
        line=1,
        is_pure=False,
        confidence=0.8,
        side_effects=(se,),
        parameter_count=0,
        return_annotation=None,
    )
    d = pr.to_dict()
    assert d["function_name"] == "f"
    assert d["is_pure"] is False
    assert len(d["side_effects"]) == 1
    assert d["side_effects"][0]["kind"] == "global_write"

    restored = PurityResult.from_dict(d)
    assert restored.function_name == "f"
    assert restored.is_pure is False
    assert len(restored.side_effects) == 1
    assert restored.side_effects[0].kind == "global_write"


# --- AlgebraicProperty ---


def test_algebraic_property_to_dict_no_bound():
    ap = AlgebraicProperty(
        kind=PropertyKind.PURE,
        confidence=0.95,
        evidence="no side effects detected",
    )
    d = ap.to_dict()
    assert d["kind"] == "pure"
    assert d["confidence"] == 0.95
    assert d["bound_spec"] is None
    assert "type_context" not in d


def test_algebraic_property_to_dict_with_bound():
    bs = BoundSpec(lower=0.0, upper=1.0, source="clamp")
    ap = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=0.9,
        evidence="clamp(x, 0, 1)",
        bound_spec=bs,
    )
    d = ap.to_dict()
    assert d["kind"] == "bounded"
    assert d["bound_spec"]["lower"] == 0.0
    assert d["bound_spec"]["upper"] == 1.0


def test_algebraic_property_to_dict_with_type_context():
    ap = AlgebraicProperty(
        kind=PropertyKind.COMMUTATIVE,
        confidence=0.7,
        evidence="symmetric usage",
        type_context={"a": "int", "b": "int"},
    )
    d = ap.to_dict()
    assert d["type_context"] == {"a": "int", "b": "int"}


def test_algebraic_property_from_dict():
    data = {
        "kind": "monotonic",
        "confidence": 0.85,
        "evidence": "sorted input",
        "bound_spec": None,
    }
    ap = AlgebraicProperty.from_dict(data)
    assert ap.kind is PropertyKind.MONOTONIC
    assert ap.confidence == 0.85
    assert ap.bound_spec is None


def test_algebraic_property_from_dict_with_bound():
    data = {
        "kind": "bounded",
        "confidence": 0.9,
        "evidence": "clamp",
        "bound_spec": {"lower": -1.0, "upper": 1.0, "source": "min_max"},
        "type_context": {"x": "float"},
    }
    ap = AlgebraicProperty.from_dict(data)
    assert ap.kind is PropertyKind.BOUNDED
    assert ap.bound_spec is not None
    assert ap.bound_spec.lower == -1.0
    assert ap.bound_spec.upper == 1.0
    assert ap.type_context == {"x": "float"}


# --- FunctionProperties ---


def test_function_properties_to_dict():
    pr = PurityResult(
        function_name="f",
        qualified_name="m.f",
        line=1,
        is_pure=True,
        confidence=1.0,
        side_effects=(),
        parameter_count=1,
        return_annotation="int",
    )
    ap = AlgebraicProperty(
        kind=PropertyKind.PURE,
        confidence=1.0,
        evidence="no side effects",
    )
    fp = FunctionProperties(
        purity=pr,
        properties=(ap,),
        optimization_hints=("cacheable",),
        source_file="src/mod.py",
        extraction_safety="safe",
    )
    d = fp.to_dict()
    assert d["purity"]["function_name"] == "f"
    assert d["purity"]["is_pure"] is True
    assert len(d["properties"]) == 1
    assert d["properties"][0]["kind"] == "pure"
    assert d["optimization_hints"] == ["cacheable"]
    assert d["source_file"] == "src/mod.py"
    assert d["extraction_safety"] == "safe"


def test_function_properties_from_dict():
    data = {
        "purity": {
            "function_name": "g",
            "qualified_name": "m.g",
            "line": 5,
            "is_pure": True,
            "confidence": 0.9,
            "side_effects": [],
            "parameter_count": 2,
            "return_annotation": "float",
        },
        "properties": [
            {"kind": "idempotent", "confidence": 0.8, "evidence": "repeated calls same result"}
        ],
        "optimization_hints": ["cacheable", "parallelizable"],
        "source_file": "lib.py",
        "extraction_safety": "needs_module_state",
    }
    fp = FunctionProperties.from_dict(data)
    assert fp.purity.function_name == "g"
    assert fp.purity.is_pure is True
    assert len(fp.properties) == 1
    assert fp.properties[0].kind is PropertyKind.IDEMPOTENT
    assert fp.optimization_hints == ("cacheable", "parallelizable")
    assert fp.source_file == "lib.py"
    assert fp.extraction_safety == "needs_module_state"


def test_function_properties_no_source_file():
    pr = PurityResult(
        function_name="h",
        qualified_name="m.h",
        line=1,
        is_pure=True,
        confidence=1.0,
        side_effects=(),
        parameter_count=0,
        return_annotation=None,
    )
    fp = FunctionProperties(purity=pr, properties=(), optimization_hints=())
    d = fp.to_dict()
    assert "source_file" not in d  # None is omitted


def test_function_properties_from_dict_defaults():
    data = {
        "purity": {
            "function_name": "x",
            "qualified_name": "x",
            "line": 1,
            "is_pure": True,
            "confidence": 1.0,
            "side_effects": [],
            "parameter_count": 0,
            "return_annotation": None,
        },
    }
    fp = FunctionProperties.from_dict(data)
    assert fp.properties == ()
    assert fp.optimization_hints == ()
    assert fp.extraction_safety == "safe"
    assert fp.source_file is None
