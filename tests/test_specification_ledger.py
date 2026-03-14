"""Tests for the specification ledger — construction, cache, traceability."""

from __future__ import annotations

from lintgate.specification.types import (
    ASTMetrics,
    FunctionSpecification,
    RiskProfile,
    SpecCore,
    SpecificationLedger,
    Traceability,
)


class TestSpecificationLedger:
    def test_empty_ledger(self):
        ledger = SpecificationLedger()
        ledger.update_metrics()
        assert ledger.specification_coverage == 0.0
        assert ledger.total_sigma == 0
        assert ledger.mean_testability == 0.0
        assert ledger.stop_criteria_met_count == 0

    def test_single_function(self):
        ledger = SpecificationLedger()
        ledger.functions["mod::func"] = FunctionSpecification(
            function_key="mod::func",
            core=SpecCore(
                estimated_sigma=5,
                specification_level=0.6,
                regime="A",
                phase="transition",
                is_pure=True,
            ),
            risk=RiskProfile(risk_score=0.3, priority_band="P2"),
        )
        ledger.update_metrics()
        assert ledger.specification_coverage == 0.6
        assert ledger.total_sigma == 5
        assert ledger.regime_distribution.get("A", 0) == 1

    def test_multiple_functions(self):
        ledger = SpecificationLedger()
        ledger.functions["a::f1"] = FunctionSpecification(
            function_key="a::f1",
            core=SpecCore(specification_level=0.4, regime="A", estimated_sigma=3),
            risk=RiskProfile(priority_band="P1"),
        )
        ledger.functions["b::f2"] = FunctionSpecification(
            function_key="b::f2",
            core=SpecCore(specification_level=0.8, regime="B", estimated_sigma=10),
            risk=RiskProfile(priority_band="P0"),
        )
        ledger.update_metrics()
        assert abs(ledger.specification_coverage - 0.6) < 1e-9
        assert ledger.total_sigma == 13
        assert ledger.regime_distribution["A"] == 1
        assert ledger.regime_distribution["B"] == 1
        assert ledger.risk_distribution["P0"] == 1
        assert ledger.risk_distribution["P1"] == 1

    def test_stop_criteria_count(self):
        ledger = SpecificationLedger()
        ledger.functions["a::f1"] = FunctionSpecification(
            function_key="a::f1",
            stop_criteria_met=True,
        )
        ledger.functions["a::f2"] = FunctionSpecification(
            function_key="a::f2",
            stop_criteria_met=False,
        )
        ledger.update_metrics()
        assert ledger.stop_criteria_met_count == 1


class TestLedgerSerialization:
    def test_round_trip(self):
        ledger = SpecificationLedger()
        ledger.functions["mod::func"] = FunctionSpecification(
            function_key="mod::func",
            source_file="mod.py",
            core=SpecCore(
                estimated_sigma=5,
                specification_level=0.6,
                regime="A",
                phase="transition",
                is_pure=True,
                semantic_ratio=0.5,
            ),
            ast_metrics=ASTMetrics(parameter_count=2, branch_count=3),
            risk=RiskProfile(risk_score=0.3, priority_band="P2"),
            traceability=Traceability(
                requirement_tags=["REQ-001"],
                covering_tests=["test_func"],
            ),
            optimization_hints=["cacheable"],
            stop_criteria_met=True,
        )
        ledger.update_metrics()

        d = ledger.to_dict()
        assert d["schema_version"] == "3"
        assert "mod::func" in d["functions"]
        func_data = d["functions"]["mod::func"]
        assert func_data["estimated_sigma"] == 5
        assert func_data["is_pure"] is True
        assert func_data["requirement_tags"] == ["REQ-001"]

    def test_to_dict_empty(self):
        ledger = SpecificationLedger()
        ledger.update_metrics()
        d = ledger.to_dict()
        assert d["functions"] == {}
        assert d["total_sigma"] == 0


class TestFunctionSpecification:
    def test_to_dict(self):
        fs = FunctionSpecification(
            function_key="mod::func",
            source_file="mod.py",
            core=SpecCore(
                estimated_sigma=3,
                regime="A",
                specification_level=0.5,
                is_pure=True,
            ),
        )
        d = fs.to_dict()
        assert d["function_key"] == "mod::func"
        assert d["estimated_sigma"] == 3
        assert d["is_pure"] is True
        assert d["regime"] == "A"


class TestTestCoverageMap:
    def test_preserves_class_qualified_test_names(self, tmp_path):
        from lintgate.specification.ledger import _build_test_coverage_map

        test_file = tmp_path / "test_demo.py"
        test_file.write_text(
            "\n".join(
                [
                    "class TestAlpha:",
                    "    def test_duplicate_name(self):",
                    "        target_alpha()",
                    "",
                    "class TestBeta:",
                    "    def test_duplicate_name(self):",
                    "        target_alpha()",
                ]
            ),
            encoding="utf-8",
        )

        coverage, file_coverage = _build_test_coverage_map([str(test_file)])

        assert coverage["target_alpha"] == [
            "TestAlpha.test_duplicate_name",
            "TestBeta.test_duplicate_name",
        ]
        assert file_coverage["target_alpha"] == {str(test_file)}


class TestFindFuncNode:
    """Tests for _find_func_node — qualified name resolution including nested classes."""

    def test_top_level_function(self, tmp_path):
        from lintgate.specification.ledger import _AST_TREE_CACHE, _find_func_node

        src = tmp_path / "mod.py"
        src.write_text("def greet(name):\n    return f'Hello {name}'\n")
        _AST_TREE_CACHE.pop(str(src), None)

        node = _find_func_node(str(src), "mod.py::greet")
        assert node is not None
        assert node.name == "greet"

    def test_class_method(self, tmp_path):
        from lintgate.specification.ledger import _AST_TREE_CACHE, _find_func_node

        src = tmp_path / "mod.py"
        src.write_text("class Calc:\n    def add(self, a, b):\n        return a + b\n")
        _AST_TREE_CACHE.pop(str(src), None)

        node = _find_func_node(str(src), "mod.py::Calc.add")
        assert node is not None
        assert node.name == "add"

    def test_nested_class_method(self, tmp_path):
        from lintgate.specification.ledger import _AST_TREE_CACHE, _find_func_node

        src = tmp_path / "mod.py"
        src.write_text(
            "class Outer:\n    class Inner:\n        def process(self):\n            pass\n"
        )
        _AST_TREE_CACHE.pop(str(src), None)

        node = _find_func_node(str(src), "mod.py::Outer.Inner.process")
        assert node is not None
        assert node.name == "process"

    def test_missing_function_returns_none(self, tmp_path):
        from lintgate.specification.ledger import _AST_TREE_CACHE, _find_func_node

        src = tmp_path / "mod.py"
        src.write_text("def greet():\n    pass\n")
        _AST_TREE_CACHE.pop(str(src), None)

        assert _find_func_node(str(src), "mod.py::nonexistent") is None

    def test_missing_class_returns_none(self, tmp_path):
        from lintgate.specification.ledger import _AST_TREE_CACHE, _find_func_node

        src = tmp_path / "mod.py"
        src.write_text("def greet():\n    pass\n")
        _AST_TREE_CACHE.pop(str(src), None)

        assert _find_func_node(str(src), "mod.py::NoClass.method") is None

    def test_async_function(self, tmp_path):
        from lintgate.specification.ledger import _AST_TREE_CACHE, _find_func_node

        src = tmp_path / "mod.py"
        src.write_text("async def fetch(url):\n    pass\n")
        _AST_TREE_CACHE.pop(str(src), None)

        node = _find_func_node(str(src), "mod.py::fetch")
        assert node is not None
        assert node.name == "fetch"

    def test_nonexistent_file_returns_none(self):
        from lintgate.specification.ledger import _find_func_node

        assert _find_func_node("/nonexistent/path.py", "func") is None
