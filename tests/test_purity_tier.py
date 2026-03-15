"""Tests for PurityTier classification and performance channel cache scoring."""

from __future__ import annotations

from lintgate.linters.performance_checks.algebra_types import (
    PurityResult,
    PurityTier,
    SideEffect,
    classify_purity_tier,
)


class TestClassifyPurityTier:
    def test_pure_function(self):
        purity = PurityResult(
            function_name="f",
            qualified_name="f",
            line=1,
            is_pure=True,
            confidence=1.0,
            side_effects=(),
            parameter_count=1,
            return_annotation=None,
        )
        assert classify_purity_tier(purity) == PurityTier.PURE

    def test_impure_with_mutation(self):
        purity = PurityResult(
            function_name="f",
            qualified_name="f",
            line=1,
            is_pure=False,
            confidence=0.8,
            side_effects=(
                SideEffect(kind="global_write", node_type="Name", line=5, detail="writes global"),
            ),
            parameter_count=1,
            return_annotation=None,
        )
        assert classify_purity_tier(purity) == PurityTier.STATEFUL

    def test_impure_with_io(self):
        purity = PurityResult(
            function_name="f",
            qualified_name="f",
            line=1,
            is_pure=False,
            confidence=0.8,
            side_effects=(
                SideEffect(kind="io_call", node_type="Call", line=5, detail="calls print"),
            ),
            parameter_count=1,
            return_annotation=None,
        )
        assert classify_purity_tier(purity) == PurityTier.STATEFUL

    def test_impure_call_only_is_stable_read(self):
        purity = PurityResult(
            function_name="f",
            qualified_name="f",
            line=1,
            is_pure=False,
            confidence=0.7,
            side_effects=(
                SideEffect(kind="impure_call", node_type="Call", line=5, detail="calls db.query"),
            ),
            parameter_count=1,
            return_annotation=None,
        )
        assert classify_purity_tier(purity) == PurityTier.STABLE_READ

    def test_mixed_read_and_mutation_is_stateful(self):
        purity = PurityResult(
            function_name="f",
            qualified_name="f",
            line=1,
            is_pure=False,
            confidence=0.7,
            side_effects=(
                SideEffect(kind="impure_call", node_type="Call", line=5, detail="calls db.query"),
                SideEffect(kind="global_write", node_type="Name", line=6, detail="writes global"),
            ),
            parameter_count=1,
            return_annotation=None,
        )
        assert classify_purity_tier(purity) == PurityTier.STATEFUL

    def test_no_side_effects_but_not_pure_is_stateful(self):
        """Conservative: no side effects detected but not marked pure."""
        purity = PurityResult(
            function_name="f",
            qualified_name="f",
            line=1,
            is_pure=False,
            confidence=0.5,
            side_effects=(),
            parameter_count=1,
            return_annotation=None,
        )
        assert classify_purity_tier(purity) == PurityTier.STATEFUL

    def test_enum_values(self):
        assert PurityTier.PURE.value == "pure"
        assert PurityTier.STABLE_READ.value == "stable_read"
        assert PurityTier.STATEFUL.value == "stateful"


class TestFunctionPropertiesRoundTrip:
    def test_purity_tier_serializes(self):
        from lintgate.linters.performance_checks.algebra_types import FunctionProperties

        props = FunctionProperties(
            purity=PurityResult(
                function_name="f",
                qualified_name="f",
                line=1,
                is_pure=True,
                confidence=1.0,
                side_effects=(),
                parameter_count=0,
                return_annotation=None,
            ),
            properties=(),
            optimization_hints=("cacheable",),
            purity_tier=PurityTier.PURE,
        )
        d = props.to_dict()
        assert d["purity_tier"] == "pure"

        restored = FunctionProperties.from_dict(d)
        assert restored.purity_tier == PurityTier.PURE

    def test_purity_tier_default_stateful(self):
        from lintgate.linters.performance_checks.algebra_types import FunctionProperties

        d = {
            "purity": {
                "function_name": "f",
                "qualified_name": "f",
                "line": 1,
                "is_pure": False,
                "confidence": 0.5,
                "side_effects": [],
                "parameter_count": 0,
                "return_annotation": None,
            },
            "properties": [],
            "optimization_hints": [],
        }
        restored = FunctionProperties.from_dict(d)
        assert restored.purity_tier == PurityTier.STATEFUL
