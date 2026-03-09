"""Tests for lintgate.specification.file_analyzer — single-file spec analysis."""

from __future__ import annotations

import pytest

from lintgate.specification.file_analyzer import analyze_file


@pytest.fixture(autouse=True)
def _clear_ast_cache():
    """Clear the module-level AST cache to prevent test pollution."""
    from lintgate.specification.ledger import _AST_TREE_CACHE

    _AST_TREE_CACHE.clear()
    yield
    _AST_TREE_CACHE.clear()


class TestAnalyzeFile:
    def test_simple_file(self, tmp_path):
        src = tmp_path / "calc.py"
        src.write_text(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n\n"
            "def subtract(a: int, b: int) -> int:\n"
            "    return a - b\n"
        )
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is None
        assert len(result.functions) >= 1
        assert result.total_sigma > 0

    def test_with_prescriptions(self, tmp_path):
        src = tmp_path / "calc.py"
        src.write_text("def multiply(a: int, b: int) -> int:\n    return a * b\n")
        result = analyze_file(str(src), str(tmp_path), include_prescriptions=True)

        assert result.error is None
        assert len(result.functions) >= 1
        # Pure function in bulk phase should get prescriptions
        assert isinstance(result.prescriptions, list)

    def test_nonexistent_file(self, tmp_path):
        result = analyze_file(str(tmp_path / "missing.py"), str(tmp_path))

        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_non_python_file(self, tmp_path):
        src = tmp_path / "readme.md"
        src.write_text("# Hello\n")
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is not None
        assert "Not a Python file" in result.error

    def test_to_dict_structure(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def greet(name: str) -> str:\n    return f'Hello {name}'\n")
        result = analyze_file(str(src), str(tmp_path))

        d = result.to_dict()
        assert "file" in d
        assert "total_functions" in d
        assert "total_sigma" in d
        assert "mean_spec_level" in d
        assert "functions" in d

    def test_regime_and_risk_distribution(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text(
            "def pure_func(x):\n    return x * 2\n\n"
            "def impure_func(x):\n    print(x)\n    return x\n"
        )
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is None
        assert len(result.regime_distribution) >= 1
        assert len(result.risk_distribution) >= 1

    def test_empty_file(self, tmp_path):
        src = tmp_path / "empty.py"
        src.write_text("# just a comment\n")
        result = analyze_file(str(src), str(tmp_path))

        assert result.error is None
        assert len(result.functions) == 0
        assert result.total_sigma == 0


class TestSymbolicBaseline:
    """Tests for enrich=False (AST-only) analysis mode."""

    def test_symbolic_produces_functions(self, tmp_path):
        src = tmp_path / "calc.py"
        src.write_text(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n\n"
            "def sub(a: int, b: int) -> int:\n"
            "    return a - b\n"
        )
        result = analyze_file(str(src), str(tmp_path), enrich=False)

        assert result.error is None
        assert len(result.functions) == 2
        assert result.total_sigma > 0

    def test_symbolic_has_regime_rationale(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def f(x):\n    return x * 2\n")
        result = analyze_file(str(src), str(tmp_path), enrich=False)

        func_data = list(result.functions.values())[0]
        assert "regime_rationale" in func_data
        assert func_data["regime_rationale"]  # non-empty

    def test_symbolic_has_trajectory(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def f(x):\n    return x * 2\n")
        result = analyze_file(str(src), str(tmp_path), enrich=False)

        func_data = list(result.functions.values())[0]
        assert "trajectory" in func_data
        assert "convergence_rate" in func_data["trajectory"]
        assert "estimated_remaining" in func_data["trajectory"]

    def test_symbolic_defaults_risk_to_p2(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def f(x):\n    return x\n")
        result = analyze_file(str(src), str(tmp_path), enrich=False)

        func_data = list(result.functions.values())[0]
        assert func_data["priority_band"] == "P2"
        assert func_data["risk_score"] == 0.0
        assert func_data["is_pure"] is False  # unknown without manifests

    def test_symbolic_empty_file(self, tmp_path):
        src = tmp_path / "empty.py"
        src.write_text("# no functions\nX = 42\n")
        result = analyze_file(str(src), str(tmp_path), enrich=False)

        assert result.error is None
        assert len(result.functions) == 0

    def test_symbolic_nonexistent_file(self, tmp_path):
        result = analyze_file(str(tmp_path / "missing.py"), str(tmp_path), enrich=False)
        assert result.error is not None

    def test_enriched_has_regime_rationale(self, tmp_path):
        """Enriched mode also populates regime_rationale."""
        src = tmp_path / "mod.py"
        src.write_text("def f(x):\n    return x * 2\n")
        result = analyze_file(str(src), str(tmp_path), enrich=True)

        assert result.functions, "Expected at least one function from enriched analysis"
        func_data = list(result.functions.values())[0]
        assert "regime_rationale" in func_data

    def test_symbolic_class_method_qualified_keys(self, tmp_path):
        """Two classes each defining process() must produce distinct keys."""
        src = tmp_path / "services.py"
        src.write_text(
            "class A:\n"
            "    def process(self, x):\n"
            "        return x + 1\n\n"
            "class B:\n"
            "    def process(self, x):\n"
            "        return x * 2\n"
        )
        result = analyze_file(str(src), str(tmp_path), enrich=False)

        assert result.error is None
        assert len(result.functions) == 2
        keys = list(result.functions.keys())
        assert any("A.process" in k for k in keys)
        assert any("B.process" in k for k in keys)

    def test_symbolic_nested_class_qualified_keys(self, tmp_path):
        """Nested class methods produce fully qualified keys."""
        src = tmp_path / "nested.py"
        src.write_text(
            "class Outer:\n"
            "    class Inner:\n"
            "        def run(self):\n"
            "            return 1\n"
            "    def run(self):\n"
            "        return 2\n"
        )
        result = analyze_file(str(src), str(tmp_path), enrich=False)

        assert result.error is None
        assert len(result.functions) == 2
        keys = list(result.functions.keys())
        assert any("Outer.Inner.run" in k for k in keys)
        assert any("Outer.run" in k for k in keys)
